import json
import gc
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
import torchaudio

import folder_paths
import comfy.model_management
from tts_runtime import TTSRuntime
from gpu_pool import GPU_POOL

try:
    from piper import PiperVoice, SynthesisConfig
except ImportError:
    PiperVoice = None
try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None
try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None
try:
    from transformers import AutoProcessor, Qwen3ASRForConditionalGeneration, Qwen3ASRForTokenClassification
    from transformers.models.qwen3_asr.processing_qwen3_asr import resolve_language
except ImportError:
    Qwen3ASRForConditionalGeneration = None


AUDIO_MODELS = Path(folder_paths.models_dir) / 'oneapi_audio'
TTS_MODELS = {'qwen3-tts-0.6b': 'Qwen3-TTS-12Hz-0.6B-CustomVoice', 'qwen3-tts-1.7b': 'Qwen3-TTS-12Hz-1.7B-CustomVoice', 'qwen3-tts-clone-0.6b': 'Qwen3-TTS-12Hz-0.6B-Base'}
ASR_MODELS = {'qwen3-asr-0.6b': 'Qwen3-ASR-0.6B-hf', 'qwen3-asr-1.7b': 'Qwen3-ASR-1.7B-hf'}
QWEN_VOICES = ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric', 'Ryan', 'Aiden', 'Ono_Anna', 'Sohee']
TTS_PYTHON = Path(folder_paths.base_path) / 'venv-qwen-tts/bin/python'
TTS_RUNTIME = TTSRuntime(TTS_PYTHON)
if TTS_PYTHON.is_file():
    TTS_RUNTIME.start()
folder_paths.add_model_folder_path('oneapi_piper', str(AUDIO_MODELS / 'piper'))
folder_paths.add_model_folder_path('oneapi_asr', str(AUDIO_MODELS))


def voices():
    return [name for name in folder_paths.get_filename_list('oneapi_piper') if name.endswith('.onnx') and not name.startswith(('zh/', 'en/'))]


class OneAPIPiperSpeech:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'text': ('STRING', {'multiline': True}), 'voice': (voices(),),
                             'speed': ('FLOAT', {'default': 1.0, 'min': 0.25, 'max': 4.0, 'step': 0.05})}}

    RETURN_TYPES = ('AUDIO',)
    FUNCTION = 'synthesize'
    CATEGORY = 'OneAPI/audio'

    def synthesize(self, text, voice, speed):
        if PiperVoice is None:
            raise RuntimeError('Install requirements-local-audio.txt to use local TTS')
        path = folder_paths.get_full_path_or_raise('oneapi_piper', voice)
        engine = PiperVoice.load(path, use_cuda=False)
        chunks = list(engine.synthesize(text, SynthesisConfig(length_scale=1.0 / speed)))
        samples = np.concatenate([chunk.audio_float_array for chunk in chunks])
        return ({'waveform': torch.from_numpy(samples).reshape(1, 1, -1), 'sample_rate': engine.config.sample_rate},)


class OneAPIWhisperTranscribe:
    @classmethod
    def INPUT_TYPES(cls):
        models = [p.name for p in AUDIO_MODELS.iterdir() if p.is_dir() and (p / 'model.bin').is_file()] if AUDIO_MODELS.exists() else []
        return {'required': {'audio': ('AUDIO',), 'model': (models,), 'language': ('STRING', {'default': 'auto'}),
                             'task': (['transcribe', 'translate'],), 'word_timestamps': ('BOOLEAN', {'default': False})}}

    RETURN_TYPES = ('STRING', 'STRING')
    RETURN_NAMES = ('text', 'transcription_json')
    FUNCTION = 'transcribe'
    CATEGORY = 'OneAPI/audio'

    def transcribe(self, audio, model, language, task, word_timestamps):
        if WhisperModel is None:
            raise RuntimeError('Install requirements-local-audio.txt to use local ASR')
        path = (AUDIO_MODELS / model).resolve()
        if not path.is_relative_to(AUDIO_MODELS.resolve()) or not (path / 'model.bin').is_file():
            raise ValueError('ASR model must be an installed directory under models/oneapi_audio')
        waveform = audio['waveform'].mean(dim=1)
        waveform = torchaudio.functional.resample(waveform, audio['sample_rate'], 16000).flatten().cpu().numpy()
        engine = WhisperModel(str(path), device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
        segments, info = engine.transcribe(waveform, language=None if language == 'auto' else language,
                                           task=task, beam_size=5, word_timestamps=word_timestamps, vad_filter=False)
        parts = []
        for segment in segments:
            item = {'id': segment.id, 'start': segment.start, 'end': segment.end, 'text': segment.text}
            if segment.words is not None:
                item['words'] = [{'word': w.word, 'start': w.start, 'end': w.end, 'probability': w.probability} for w in segment.words]
            parts.append(item)
        result = {'text': ''.join(s['text'] for s in parts).strip(), 'language': info.language,
                  'language_probability': info.language_probability, 'duration': info.duration, 'segments': parts}
        return (result['text'], json.dumps(result, ensure_ascii=False))


def run_tts_worker(data, directory):
    started = time.perf_counter()
    data['metadata'] = str(Path(directory) / 'metadata.json')
    model = next(name for name, directory in TTS_MODELS.items() if Path(data['model_path']).name == directory)
    GPU_POOL.prepare_speech('tts', model, data['device'])
    request_path = Path(directory) / 'request.json'
    request_path.write_text(json.dumps(data))
    runtime = TTS_RUNTIME.run(request_path)
    details = json.loads(Path(data['metadata']).read_text())
    details.update(runtime='resident_worker' if data['keep_loaded'] else 'forkserver_imports', checkpoint_loaded_each_call=not data['keep_loaded'],
                   library_preload_seconds=runtime['preload_seconds'], driver_keep_loaded=runtime['driver_keep_loaded'],
                   runtime_overhead_seconds=time.perf_counter() - started - details['load_seconds'] - details['inference_seconds'])
    return details


class OneAPIQwenSpeech:
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'text': ('STRING', {'multiline': True}), 'model': (list(TTS_MODELS),),
                'voice': (QWEN_VOICES,), 'language': ('STRING', {'default': 'Chinese'}),
                'instruct': ('STRING', {'default': '', 'multiline': True}), 'speed': ('FLOAT', {'default': 1.0, 'min': 0.25, 'max': 4.0}),
                'seed': ('INT', {'default': 42, 'min': 0, 'max': 0xffffffffffffffff}),
                'reference_text': ('STRING', {'default': '', 'multiline': True}), 'x_vector_only': ('BOOLEAN', {'default': False})},
                'optional': {'reference_audio': ('AUDIO',), 'device': (['cuda', 'cpu'],), 'keep_loaded': ('BOOLEAN', {'default': True})}}

    RETURN_TYPES = ('AUDIO', 'STRING')
    RETURN_NAMES = ('audio', 'generation_json')
    FUNCTION = 'synthesize'
    CATEGORY = 'OneAPI/audio'

    def synthesize(self, text, model, voice, language, instruct, speed, seed, reference_text, x_vector_only, reference_audio=None, device="cuda", keep_loaded=True):
        if model not in TTS_MODELS:
            raise ValueError('Unknown Qwen TTS model')
        if model == 'qwen3-tts-clone-0.6b' and reference_audio is None:
            raise ValueError('Voice cloning requires reference_audio')
        if model != 'qwen3-tts-clone-0.6b' and reference_audio is not None:
            raise ValueError('Reference audio requires the voice cloning model')
        Path(folder_paths.get_temp_directory()).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='oneapi-speech-', dir=folder_paths.get_temp_directory()) as directory:
            output = str(Path(directory) / 'output.npz')
            data = dict(text=text, model_path=str(AUDIO_MODELS / TTS_MODELS[model]), voice=voice, language=language,
                        instruct=instruct, speed=speed, seed=seed, reference_text=reference_text, x_vector_only=x_vector_only, output=output, device=device, keep_loaded=keep_loaded)
            if reference_audio is not None:
                data['reference'] = str(Path(directory) / 'reference.npz')
                samples = reference_audio['waveform'].mean(dim=1).flatten().cpu().numpy()
                np.savez(data['reference'], waveform=samples, sample_rate=reference_audio['sample_rate'])
            details = run_tts_worker(data, directory)
            with np.load(output) as saved:
                audio = {'waveform': torch.from_numpy(saved['waveform'].copy()).reshape(1, 1, -1), 'sample_rate': int(saved['sample_rate'])}
        return (audio, json.dumps({'model': model, **details}, ensure_ascii=False))


class ASRModels:
    def __init__(self):
        self.models = {}

    def load(self, path, device, alignment=False):
        key = (path, str(device))
        if key in self.models:
            return (*self.models[key], True)
        # Bound residency to one recognizer and one optional aligner.
        if not alignment:
            self.release()
        processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        kind = Qwen3ASRForTokenClassification if alignment else Qwen3ASRForConditionalGeneration
        engine = kind.from_pretrained(path, local_files_only=True, dtype=torch.bfloat16 if device.type == 'cuda' else torch.float32,
                                      attn_implementation='sdpa').to(device)
        self.models[key] = (processor, engine)
        return processor, engine, False

    def state(self):
        if not self.models:
            return None
        gpu = [(path, engine) for (path, device), (_, engine) in self.models.items() if device.startswith('cuda')]
        names = {directory: model for model, directory in ASR_MODELS.items()}
        return {'device': 'cuda' if gpu else 'cpu',
                'models': [names.get(Path(path).name, 'qwen3-forced-aligner-0.6b') for (path, _), _value in self.models.items()],
                'vram_bytes': sum(p.numel() * p.element_size() for _, engine in gpu for p in engine.parameters())}

    def release(self):
        if self.models:
            self.models.clear()
            gc.collect()
            comfy.model_management.soft_empty_cache()


ASR_MODELS_RUNTIME = ASRModels()


def release_speech_models():
    TTS_RUNTIME.release()
    ASR_MODELS_RUNTIME.release()


class OneAPIReleaseSpeechModels:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {}}

    RETURN_TYPES = ()
    FUNCTION = 'release'
    CATEGORY = 'OneAPI/audio'
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls):
        return float('nan')

    def release(self):
        release_speech_models()
        return ()


class OneAPIQwenTranscribe:
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'audio': ('AUDIO',), 'model': (list(ASR_MODELS),), 'language': ('STRING', {'default': 'auto'}),
                             'prompt': ('STRING', {'default': '', 'multiline': True}), 'word_timestamps': ('BOOLEAN', {'default': False})},
                'optional': {'device': (['cuda', 'cpu'],), 'keep_loaded': ('BOOLEAN', {'default': True})}}

    RETURN_TYPES = ('STRING', 'STRING')
    RETURN_NAMES = ('text', 'transcription_json')
    FUNCTION = 'transcribe'
    CATEGORY = 'OneAPI/audio'

    def transcribe(self, audio, model, language, prompt, word_timestamps, device="cuda", keep_loaded=True):
        if model not in ASR_MODELS:
            raise ValueError('Unknown Qwen ASR model')
        if Qwen3ASRForConditionalGeneration is None:
            raise RuntimeError('Qwen ASR requires Transformers >= 5.13')
        device = comfy.model_management.get_torch_device() if device == 'cuda' else torch.device('cpu')
        dtype = torch.bfloat16 if device.type == 'cuda' else torch.float32
        GPU_POOL.prepare_speech('asr', model, 'cuda' if device.type == 'cuda' else 'cpu', word_timestamps)
        samples = torchaudio.functional.resample(audio['waveform'].mean(dim=1).flatten().cpu(), audio['sample_rate'], 16000).numpy()
        path = str(AUDIO_MODELS / ASR_MODELS[model])
        started = time.perf_counter()
        if not keep_loaded:
            ASR_MODELS_RUNTIME.release()
        processor, engine, cache_hit = ASR_MODELS_RUNTIME.load(path, device)
        if device.type == 'cuda':
            torch.cuda.synchronize(device)
        loaded = time.perf_counter()
        parts, languages = [], []
        for offset in range(0, len(samples), 30 * 16000):
            chunk = samples[offset:offset + 30 * 16000]
            hint = None if language == 'auto' else language
            if prompt:
                context = '\n'.join(s for s in (resolve_language(hint), prompt) if s)
                inputs = processor.apply_chat_template([{'role': 'system', 'content': [{'type': 'text', 'text': context}]},
                            {'role': 'user', 'content': [{'type': 'audio', 'audio': chunk}]}], tokenize=True, add_generation_prompt=True, return_dict=True)
            else:
                inputs = processor.apply_transcription_request(audio=chunk, language=hint)
            inputs = inputs.to(device, dtype)
            output = engine.generate(**inputs, do_sample=False, max_new_tokens=1024)
            parsed = processor.decode(output[:, inputs['input_ids'].shape[1]:], return_format='parsed')[0]
            parts.append({'id': len(parts), 'start': offset / 16000, 'end': (offset + len(chunk)) / 16000, 'text': parsed['transcription']})
            languages.append(parsed['language'])
        del engine, inputs, output
        alignment_seconds = 0
        if word_timestamps and any(p['text'] for p in parts):
            align_started = time.perf_counter()
            path = str(AUDIO_MODELS / 'Qwen3-ForcedAligner-0.6B-hf')
            align_processor, aligner, _ = ASR_MODELS_RUNTIME.load(path, device, alignment=True)
            for part, detected_language in zip(parts, languages):
                if not part['text']:
                    continue
                chunk = samples[round(part['start'] * 16000):round(part['end'] * 16000)]
                inputs, words = align_processor.prepare_forced_aligner_inputs(audio=chunk, transcript=part['text'], language=detected_language or language)
                inputs = inputs.to(device, dtype)
                output = aligner(**inputs)
                aligned = align_processor.decode_forced_alignment(logits=output.logits, input_ids=inputs['input_ids'],
                            word_lists=words, timestamp_token_id=aligner.config.timestamp_token_id)[0]
                part['words'] = [{'word': w['text'], 'start': w['start_time'] + part['start'], 'end': w['end_time'] + part['start']} for w in aligned]
            if device.type == 'cuda':
                torch.cuda.synchronize(device)
            alignment_seconds = time.perf_counter() - align_started
            del aligner, inputs, output
        if device.type == 'cuda':
            torch.cuda.synchronize(device)
        details = {'model': model, 'text': ''.join(p['text'] for p in parts), 'language': languages[0], 'duration': len(samples) / 16000, 'segments': parts,
                   'segment_timing': '30-second windows; word times use the forced aligner' if word_timestamps else '30-second window boundaries, not speech alignment',
                   'load_seconds': loaded - started, 'inference_seconds': time.perf_counter() - loaded, 'alignment_seconds': alignment_seconds,
                   'runtime': 'native_comfyui', 'checkpoint_loaded_each_call': not keep_loaded, 'model_cache_hit': cache_hit,
                   'keep_loaded': keep_loaded, 'device': str(device), 'dtype': str(dtype), 'cpu_threads': torch.get_num_threads()}
        if not keep_loaded:
            ASR_MODELS_RUNTIME.release()
        comfy.model_management.soft_empty_cache()
        return (details['text'], json.dumps(details, ensure_ascii=False))


class OneAPISenseVoiceTranscribe:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'audio': ('AUDIO',), 'language': ('STRING', {'default': 'auto'}),
                             'word_timestamps': ('BOOLEAN', {'default': False})}}

    RETURN_TYPES = ('STRING', 'STRING')
    RETURN_NAMES = ('text', 'transcription_json')
    FUNCTION = 'transcribe'
    CATEGORY = 'OneAPI/audio'

    def transcribe(self, audio, language, word_timestamps):
        if sherpa_onnx is None:
            raise RuntimeError('Install requirements-local-audio.txt to use SenseVoice')
        started = time.perf_counter()
        path = AUDIO_MODELS / 'sensevoice-small-int8'
        language = {'auto': '', 'Chinese': 'zh', 'English': 'en', 'Cantonese': 'yue', 'Japanese': 'ja', 'Korean': 'ko'}.get(language, language)
        engine = sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(path / 'model.int8.onnx'), tokens=str(path / 'tokens.txt'),
                    num_threads=4, use_itn=True, language=language, provider='cpu')
        waveform = torchaudio.functional.resample(audio['waveform'].mean(dim=1).flatten().cpu(), audio['sample_rate'], 16000).numpy()
        loaded = time.perf_counter()
        segments = []
        language, emotion, event = None, None, None
        for offset in range(0, len(waveform), 30 * 16000):
            chunk = waveform[offset:offset + 30 * 16000]
            stream = engine.create_stream()
            stream.accept_waveform(16000, chunk)
            engine.decode_stream(stream)
            result = stream.result
            part = {'id': len(segments), 'start': offset / 16000, 'end': (offset + len(chunk)) / 16000, 'text': result.text}
            if word_timestamps:
                part['words'] = [{'word': token, 'start': stamp + offset / 16000,
                    'end': (result.timestamps[i + 1] + offset / 16000) if i + 1 < len(result.timestamps) else part['end']}
                    for i, (token, stamp) in enumerate(zip(result.tokens, result.timestamps))]
            segments.append(part)
            language, emotion, event = result.lang.strip('<|>'), result.emotion.strip('<|>'), result.event.strip('<|>')
        details = {'model': 'sensevoice-small-int8', 'text': ''.join(s['text'] for s in segments), 'language': language,
                   'emotion': emotion, 'event': event, 'duration': len(waveform) / 16000, 'segments': segments,
                   'load_seconds': loaded - started, 'inference_seconds': time.perf_counter() - loaded,
                   'segment_timing': '30-second window boundaries; CTC token starts; token ends use the next start'}
        return (details['text'], json.dumps(details, ensure_ascii=False))


def tts_pool_state():
    state = TTS_RUNTIME.state()
    if state is None:
        return None
    names = {directory: model for model, directory in TTS_MODELS.items()}
    return {**state, 'models': [names[name] for name in state['models']]}


GPU_POOL.register('tts', tts_pool_state, TTS_RUNTIME.release)
GPU_POOL.register('asr', ASR_MODELS_RUNTIME.state, ASR_MODELS_RUNTIME.release)


NODE_CLASS_MAPPINGS = {'OneAPIReleaseSpeechModels': OneAPIReleaseSpeechModels, 'OneAPIPiperSpeech': OneAPIPiperSpeech, 'OneAPIWhisperTranscribe': OneAPIWhisperTranscribe,
                       'OneAPIQwenSpeech': OneAPIQwenSpeech, 'OneAPIQwenTranscribe': OneAPIQwenTranscribe, 'OneAPISenseVoiceTranscribe': OneAPISenseVoiceTranscribe}
NODE_DISPLAY_NAME_MAPPINGS = {'OneAPIPiperSpeech': 'OneAPI · Local Piper TTS (CPU)', 'OneAPIWhisperTranscribe': 'OneAPI · Local Whisper ASR (CPU)',
                              'OneAPIQwenSpeech': 'OneAPI · Qwen3 TTS / Voice Clone', 'OneAPIQwenTranscribe': 'OneAPI · Qwen3 ASR',
                              'OneAPISenseVoiceTranscribe': 'OneAPI · SenseVoice INT8 ASR (CPU)'}
