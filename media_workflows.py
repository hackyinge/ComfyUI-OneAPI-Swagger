import secrets

from video_workflows import build_workflow as build_video_workflow, upload_name


DEFAULT_IMAGE_MODEL = 'qwen-image-2.1-turbo'
QWEN_IMAGE_21_TURBO_SIGMAS = (1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0)
IMAGE_MODELS = {
    'qwen-image-2.1': {'unet': 'qwen_image_2.1_int8_convrot.safetensors', 'clip': 'qwen3vl_8b_int8_convrot.safetensors', 'clip_type': 'qwen_image', 'vae': 'qwen_image_2.1_vae_bf16.safetensors', 'steps': 25, 'cfg': 1.0},
    'qwen-image-2.1-turbo': {'unet': 'qwen_image_2.1_turbo_int8_convrot.safetensors', 'clip': 'qwen3vl_8b_int8_convrot.safetensors', 'clip_type': 'qwen_image', 'vae': 'qwen_image_2.1_vae_bf16.safetensors', 'steps': 8, 'cfg': 1.0, 'sigmas': QWEN_IMAGE_21_TURBO_SIGMAS},
}
SPEECH_VOICES = {'huayan': 'zh_CN-huayan-medium.onnx', 'lessac': 'en_US-lessac-medium.onnx'}
TTS_MODELS = ['piper', 'qwen3-tts-0.6b', 'qwen3-tts-1.7b', 'qwen3-tts-clone-0.6b']
ASR_MODELS = ['faster-whisper-small', 'sensevoice-small-int8', 'qwen3-asr-0.6b', 'qwen3-asr-1.7b']
DEFAULT_TTS_MODEL = 'qwen3-tts-1.7b'
DEFAULT_ASR_MODEL = 'qwen3-asr-0.6b'


def image_seed(value=None):
    seed = -1 if value is None else int(value)
    return secrets.randbits(53) if seed == -1 else seed


def build_media_workflow(settings):
    data = {k: v for k, v in settings.items() if k not in ('task', 'client_id')}
    task = settings.get('task', 'image')
    if 'workflow' in data:
        graph = data['workflow']
        if not isinstance(graph, dict) or not graph:
            raise ValueError('workflow must be a nonempty native API graph')
        return graph
    if task == 'video':
        return build_video_workflow(data)
    allowed = {
        'image': {'model', 'prompt', 'negative_prompt', 'width', 'height', 'n', 'steps', 'cfg', 'seed', 'input_image', 'input_images', 'reference_megapixels', 'denoise'},
        'tts': {'model', 'text', 'voice', 'speed', 'format', 'language', 'instruct', 'seed', 'reference_audio', 'reference_text', 'x_vector_only', 'device', 'keep_loaded'},
        'asr': {'model', 'input_audio', 'language', 'word_timestamps', 'prompt', 'device', 'keep_loaded'},
    }
    if task not in allowed:
        raise ValueError('task must be image, video, tts or asr; other nodes can be submitted as workflow')
    unknown = set(data) - allowed[task]
    if unknown:
        raise ValueError('Unknown request fields: ' + ', '.join(sorted(unknown)))
    if task in ('tts', 'asr'):
        if data.get('device', 'cuda') not in ('cuda', 'cpu'):
            raise ValueError('device must be cuda or cpu')
        if not isinstance(data.get('keep_loaded', True), bool):
            raise ValueError('keep_loaded must be a boolean')
    graph = {}

    def node(key, kind, **inputs):
        graph[key] = {'class_type': kind, 'inputs': inputs}
        return [key, 0]

    prefix = 'oneapi/' + secrets.token_hex(8)
    if task == 'tts':
        model = data.get('model', 'piper' if data.get('voice') in (*SPEECH_VOICES, *SPEECH_VOICES.values()) else DEFAULT_TTS_MODEL)
        if model not in TTS_MODELS:
            raise ValueError('Unknown local TTS model: ' + str(model))
        if model == 'piper':
            if 'device' in data or 'keep_loaded' in data:
                raise ValueError('device / keep_loaded controls apply to Qwen models')
            if data.get('reference_audio'):
                raise ValueError('Voice cloning requires qwen3-tts-clone-0.6b')
            voice = data.get('voice', 'huayan')
            audio = node('speech', 'OneAPIPiperSpeech', text=data.get('text', ''), voice=SPEECH_VOICES.get(voice, voice), speed=float(data.get('speed', 1.0)))
        else:
            options = {'device': data.get('device', 'cuda'), 'keep_loaded': data.get('keep_loaded', True)}
            if data.get('reference_audio'):
                options['reference_audio'] = node('reference', 'LoadAudio', audio=upload_name(data['reference_audio']))
            if model == 'qwen3-tts-clone-0.6b' and not data.get('reference_audio'):
                raise ValueError('Voice cloning requires reference_audio from the upload endpoint')
            if model == 'qwen3-tts-clone-0.6b' and not data.get('reference_text') and not data.get('x_vector_only'):
                raise ValueError('Voice cloning requires reference_text or x_vector_only=true')
            if model != 'qwen3-tts-clone-0.6b' and data.get('reference_audio'):
                raise ValueError('Reference audio requires qwen3-tts-clone-0.6b')
            audio = node('speech', 'OneAPIQwenSpeech', model=model, text=data.get('text', ''), voice=data.get('voice', 'Vivian'),
                         language=data.get('language', 'Chinese'), instruct=data.get('instruct', ''), speed=float(data.get('speed', 1.0)),
                         seed=int(data.get('seed', 42)), reference_text=data.get('reference_text', ''), x_vector_only=bool(data.get('x_vector_only', False)), **options)
            node('speech_details', 'SaveText', text=['speech', 1], filename_prefix=prefix + '_generation', format='json')
        output_format = data.get('format', 'flac')
        if output_format == 'flac':
            node('save', 'SaveAudio', audio=audio, filename_prefix=prefix)
        elif output_format == 'mp3':
            node('save', 'SaveAudioMP3', audio=audio, filename_prefix=prefix, quality='V0')
        elif output_format == 'opus':
            node('save', 'SaveAudioOpus', audio=audio, filename_prefix=prefix, quality='128k')
        else:
            raise ValueError('Saved audio format must be flac, mp3 or opus')
        return graph
    if task == 'asr':
        audio = node('audio', 'LoadAudio', audio=upload_name(data['input_audio']))
        model = data.get('model', DEFAULT_ASR_MODEL)
        if model not in ASR_MODELS:
            raise ValueError('Unknown local ASR model: ' + str(model))
        if model in ('sensevoice-small-int8', 'faster-whisper-small') and ('device' in data or 'keep_loaded' in data):
            raise ValueError('device / keep_loaded controls apply to Qwen models')
        if model == 'sensevoice-small-int8':
            if data.get('prompt'):
                raise ValueError('Context / hotwords require a Qwen ASR model')
            node('transcribe', 'OneAPISenseVoiceTranscribe', audio=audio, language=data.get('language', 'auto'), word_timestamps=bool(data.get('word_timestamps', False)))
        else:
            node('transcribe', 'OneAPIWhisperTranscribe' if model == 'faster-whisper-small' else 'OneAPIQwenTranscribe', audio=audio, model=model,
                 language=data.get('language', 'auto'), word_timestamps=bool(data.get('word_timestamps', False)),
                 **({'task': 'transcribe'} if model == 'faster-whisper-small' else {'prompt': data.get('prompt', ''), 'device': data.get('device', 'cuda'), 'keep_loaded': data.get('keep_loaded', True)}))
        node('transcript', 'SaveText', text=['transcribe', 0], filename_prefix=prefix, format='txt')
        node('transcript_details', 'SaveText', text=['transcribe', 1], filename_prefix=prefix + '_segments', format='json')
        return graph
    model_name = data.get('model', DEFAULT_IMAGE_MODEL)
    if model_name not in IMAGE_MODELS:
        raise ValueError('Unknown image model: ' + str(model_name))
    profile = IMAGE_MODELS[model_name]
    width, height, batch = int(data.get('width', 1024)), int(data.get('height', 1024)), int(data.get('n', 1))
    steps, cfg, seed = int(data.get('steps', profile['steps'])), float(data.get('cfg', profile['cfg'])), image_seed(data.get('seed'))
    model = node('model', 'UNETLoader', unet_name=profile['unet'], weight_dtype='default')
    clip = node('clip', 'CLIPLoader', clip_name=profile['clip'], type=profile['clip_type'], device='default')
    vae = node('vae', 'VAELoader', vae_name=profile['vae'])
    references = data.get('input_images', [])
    if not isinstance(references, list):
        raise ValueError('input_images must be a list of uploaded filenames')
    references = ([data['input_image']] if data.get('input_image') else []) + references
    options = {'vae': vae, 'resolution': 0 if references else 1024}
    for i, filename in enumerate(references, 1):
        source = node('source' if i == 1 else f'source_{i}', 'LoadImage', image=upload_name(filename))
        if i == 1:
            source = node('resize', 'ImageScale', image=source, upscale_method='lanczos', width=width, height=height, crop='disabled')
        elif float(data.get('reference_megapixels', 1.0)) > 0:
            source = node(f'reference_size_{i}', 'ImageScaleToTotalPixels', image=source, upscale_method='lanczos', megapixels=float(data.get('reference_megapixels', 1.0)), resolution_steps=32)
        options[f'images.image_{i}'] = source
    negative_prompt = data.get('negative_prompt', '')
    node('condition', 'TextEncodeQwenImage21', clip=clip, prompt=data.get('prompt', ''), negative_prompt=negative_prompt, **options)
    latent = ['condition', 2]
    if not references:
        latent = node('latent', 'EmptyLatentImage', width=width, height=height, batch_size=batch)
    elif batch > 1:
        latent = node('batch', 'RepeatLatentBatch', samples=latent, amount=batch)
    if profile.get('sigmas'):
        if steps < 1:
            raise ValueError('steps must be at least 1')
        schedule = profile['sigmas']
        values = []
        for i in range(steps):
            position = i * (len(schedule) - 1) / steps
            index = int(position)
            values.append(schedule[index] + (schedule[index + 1] - schedule[index]) * (position - index))
        values.append(0.0)
        noise = node('noise', 'RandomNoise', noise_seed=seed)
        guider = node('guider', 'CFGGuider', model=model, positive=['condition', 0], negative=['condition', 1], cfg=cfg)
        sampler = node('sampler', 'KSamplerSelect', sampler_name='euler')
        sigmas = node('sigmas', 'ManualSigmas', sigmas=', '.join(str(value) for value in values))
        if float(data.get('denoise', 1.0)) != 1.0:
            node('denoise', 'SplitSigmasDenoise', sigmas=sigmas, denoise=float(data['denoise']))
            sigmas = ['denoise', 1]
        samples = node('sample', 'SamplerCustomAdvanced', noise=noise, guider=guider, sampler=sampler, sigmas=sigmas, latent_image=latent)
    else:
        samples = node('sample', 'KSampler', model=model, positive=['condition', 0], negative=['condition', 1],
                       latent_image=latent, seed=seed, steps=steps, cfg=cfg, sampler_name='euler', scheduler='simple',
                       denoise=float(data.get('denoise', 1.0)))
    images = node('decode', 'VAEDecode', samples=samples, vae=vae)
    node('save', 'SaveImage', images=images, filename_prefix=prefix)
    return graph
