"""Local Qwen TTS worker with one explicitly owned resident model."""
import os
import gc
import json
import time
from pathlib import Path

import librosa
import numpy as np
import torch

from qwen_tts import Qwen3TTSModel

class TTSWorker:
    def __init__(self):
        self.model = None
        self.key = None

    def run(self, data):
        key = (data["model_path"], data.get("device", "cuda"))
        hit = self.key == key and data.get("keep_loaded", True)
        if not hit:
            self.model = None
            gc.collect()
            if torch.cuda.is_initialized():
                torch.cuda.empty_cache()
            self.key = None
        result = tts(data, self.model)
        self.model, details = result
        self.key = key
        details.update(model_cache_hit=hit, keep_loaded=data.get("keep_loaded", True), device=key[1], dtype="bfloat16" if key[1] == "cuda" else "float32")
        return details


def tts(data, model=None):
    started = time.perf_counter()
    device = data.get('device', 'cuda')
    if model is None:
        model = Qwen3TTSModel.from_pretrained(data['model_path'], device_map='cuda:0' if device == 'cuda' else 'cpu',
                    dtype=torch.bfloat16 if device == 'cuda' else torch.float32, attn_implementation='sdpa', local_files_only=True)
    if device == 'cuda':
        torch.cuda.synchronize()
    loaded = time.perf_counter()
    language = {'zh': 'Chinese', 'en': 'English', 'auto': 'Auto'}.get(data['language'], data['language'])
    options = {'text': data['text'], 'language': language, 'max_new_tokens': 4096}
    if data.get('reference'):
        with np.load(data['reference']) as reference:
            options.update(ref_audio=(reference['waveform'], int(reference['sample_rate'])), ref_text=data['reference_text'],
                           x_vector_only_mode=data['x_vector_only'])
        waves, rate = model.generate_voice_clone(**options)
    else:
        waves, rate = model.generate_custom_voice(**options, speaker=data['voice'], instruct=data['instruct'])
    samples = waves[0]
    if data['speed'] != 1.0:
        samples = librosa.effects.time_stretch(samples, rate=data['speed'])
    if device == 'cuda':
        torch.cuda.synchronize()
    np.savez(data['output'], waveform=samples, sample_rate=rate)
    return model, {'load_seconds': loaded - started, 'inference_seconds': time.perf_counter() - loaded,
            'duration': len(samples) / rate, 'sample_rate': rate, 'cpu_threads': torch.get_num_threads(), 'worker_pid': os.getpid()}


def execute(worker, request_path):
    data = json.loads(Path(request_path).read_text())
    torch.set_num_threads(4)
    torch.manual_seed(data.get('seed', 42))
    if data.get('device', 'cuda') == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    result = worker.run(data)
    result['peak_vram_mb'] = torch.cuda.max_memory_allocated() / 1024 ** 2 if data.get('device', 'cuda') == 'cuda' else 0
    result['resident_vram_bytes'] = torch.cuda.memory_reserved() + 512 * 1024 ** 2 if data.get('device', 'cuda') == 'cuda' else 0
    Path(data['metadata']).write_text(json.dumps(result, ensure_ascii=False))
