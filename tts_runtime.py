"""Private IPC for the isolated Qwen SDK; one resident worker at most."""
import atexit
import json
import multiprocessing
import os
from pathlib import Path
import traceback
import socket
import subprocess
import sys
import tempfile
import threading
import time


class TTSRuntime:
    def __init__(self, python):
        self.python = python
        self.process = None
        self.channel = None
        self.log = None
        self.lock = threading.Lock()
        self.resident = None
        atexit.register(self.close)

    def start(self):
        self.close()
        parent, child = socket.socketpair()
        self.log = tempfile.TemporaryFile()
        environment = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                       'TOKENIZERS_PARALLELISM': 'false', 'PYTORCH_NVML_BASED_CUDA_CHECK': '1'}
        try:
            self.process = subprocess.Popen([str(self.python), str(Path(__file__)), str(child.fileno())], pass_fds=(child.fileno(),),
                    stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, env=environment)
        finally:
            child.close()
        self.channel = parent.makefile('rwb')
        parent.close()

    def state(self):
        if self.process is None or self.process.poll() is not None:
            return None
        return self.resident

    def release(self):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.close()
                return
            self.channel.write(b'{"release": true}\n')
            self.channel.flush()
            if not self.channel.readline():
                self.close()
            self.resident = None

    def run(self, request_path):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.start()
            self.log.seek(0)
            self.log.truncate()
            self.channel.write(json.dumps({'request_path': str(request_path)}).encode() + b'\n')
            self.channel.flush()
            response = self.channel.readline()
            if not response:
                self.close()
                raise RuntimeError('TTS runtime exited before returning a result')
            result = json.loads(response)
            if result['exitcode']:
                self.resident = None
                self.log.seek(0)
                raise RuntimeError('Local TTS worker failed: ' + self.log.read()[-5000:].decode(errors='replace'))
            data = json.loads(Path(request_path).read_text())
            details = json.loads(Path(data['metadata']).read_text())
            self.resident = {'models': [Path(data['model_path']).name], 'device': data.get('device', 'cuda'),
                             'vram_bytes': details['resident_vram_bytes']} if data.get('keep_loaded', True) else None
            return result

    def close(self):
        self.resident = None
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait()
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None


def generate(channel):
    # Import the optional SDK only inside its isolated child environment.
    from speech_worker import TTSWorker, execute
    worker = TTSWorker()
    with channel:
        while True:
            request_path = channel.recv()
            try:
                execute(worker, request_path)
                channel.send({'exitcode': 0})
            except Exception:
                traceback.print_exc()
                channel.send({'exitcode': 1})
                return


def serve(descriptor):
    multiprocessing.set_forkserver_preload(['torch', 'librosa', 'qwen_tts'])
    context = multiprocessing.get_context('forkserver')
    started = time.perf_counter()
    warmup = context.Process()
    warmup.start()
    warmup.join()
    warmup.close()
    preload_seconds = time.perf_counter() - started
    process, worker_channel, worker_device = None, None, None
    try:
        with socket.socket(fileno=descriptor) as channel, channel.makefile('rwb') as messages:
            for line in messages:
                request = json.loads(line)
                if request.get('release'):
                    if process is not None:
                        process.terminate()
                        process.join()
                        process.close()
                        worker_channel.close()
                        process, worker_channel = None, None
                    messages.write(b'{"exitcode": 0}\n')
                    messages.flush()
                    continue
                data = json.loads(Path(request['request_path']).read_text())
                device = data.get('device', 'cuda')
                if process is None or not process.is_alive() or worker_device != device:
                    if process is not None:
                        if process.is_alive():
                            process.terminate()
                        process.join()
                        process.close()
                        worker_channel.close()
                    worker_channel, child = context.Pipe()
                    process = context.Process(target=generate, args=(child,))
                    process.start()
                    child.close()
                    worker_device = device
                worker_channel.send(request['request_path'])
                result = worker_channel.recv()
                if result['exitcode'] or not data.get('keep_loaded', True):
                    process.terminate()
                    process.join()
                    process.close()
                    worker_channel.close()
                    process, worker_channel = None, None
                messages.write(json.dumps({**result, 'preload_seconds': preload_seconds, 'driver_keep_loaded': data.get('keep_loaded', True)}).encode() + b'\n')
                messages.flush()
    finally:
        if process is not None:
            process.terminate()
            process.join()


if __name__ == '__main__':
    serve(int(sys.argv[1]))
