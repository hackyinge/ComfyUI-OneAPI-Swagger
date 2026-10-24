import asyncio
import base64
import copy
import io
import json
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
import av
import folder_paths

from comfy.cli_args import args
from server import PromptServer
from gpu_pool import GPU_POOL
from image_inputs import prepare_image_settings
from local_audio import AUDIO_MODELS, PiperVoice, WhisperModel, sherpa_onnx, TTS_MODELS as QWEN_TTS_MODELS, ASR_MODELS as QWEN_ASR_MODELS, QWEN_VOICES, TTS_PYTHON
from media_workflows import DEFAULT_IMAGE_MODEL, IMAGE_MODELS, SPEECH_VOICES, TTS_MODELS, ASR_MODELS, DEFAULT_TTS_MODEL, DEFAULT_ASR_MODEL
from video_api import core, get_job, make_graph, read_settings, upload_media, video_openapi


PREFIX = '/oneapi/v1'


def runtime_properties():
    return {'device': {'type': 'string', 'enum': ['cuda', 'cpu'], 'default': 'cuda', 'description': 'Qwen only; CPU uses float32'},
            'keep_loaded': {'type': 'boolean', 'default': True, 'description': 'Qwen only; allow GPU pool reuse, evict on memory pressure or after 120s idle; false unloads after each call'}}


def speech_properties():
    return {**runtime_properties(), 'language': {'type': 'string', 'description': 'TTS: Chinese / English / Auto; ASR: zh / en / auto'},
            'instruct': {'type': 'string', 'description': 'Qwen 1.7B voice style, emotion and delivery instruction'},
            'seed': {'type': 'integer', 'default': 42},
            'reference_audio': {'type': 'string', 'description': 'Uploaded local filename; model=qwen3-tts-clone-0.6b'},
            'reference_text': {'type': 'string', 'description': 'Accurate transcript of reference audio for cloning'},
            'x_vector_only': {'type': 'boolean', 'default': False, 'description': 'Clone speaker embedding without reference_text; quality may decrease'}}


def comparison_catalog(request, kind):
    owner = request.get('user_id')
    if not owner:
        raise web.HTTPUnauthorized()
    try:
        uuid.UUID(owner)
    except ValueError:
        raise web.HTTPForbidden()
    path = Path(folder_paths.get_user_directory()) / (kind + '_comparison') / owner / 'catalog.json'
    if not path.is_file():
        raise web.HTTPNotFound(text='No comparison results for this user')
    return json.loads(path.read_text())


def image_content_properties():
    part = {'type': 'object', 'properties': {'type': {'type': 'string', 'enum': ['text', 'image_url', 'image', 'input_text', 'input_image', 'image_ref']}, 'text': {'type': 'string'}, 'id': {'type': 'string', 'description': 'Optional stable reference id; use <ref:id> in text'}, 'image': {'type': 'string'}, 'image_url': {'oneOf': [{'type': 'string'}, {'type': 'object', 'properties': {'url': {'type': 'string'}}}]}}}
    content = {'oneOf': [{'type': 'string'}, {'type': 'array', 'items': part}]}
    return {'input_images': {'type': 'array', 'items': {'oneOf': [{'type': 'string'}, {'type': 'object', 'required': ['id', 'image'], 'properties': {'id': {'type': 'string'}, 'image': {'type': 'string'}}}]}, 'description': 'Ordered uploaded filenames or named references. input_image comes first when both are provided.'},
            'content': content,
            'messages': {'type': 'array', 'items': {'type': 'object', 'properties': {'role': {'type': 'string'}, 'content': content}}, 'description': 'OpenAI messages; the last user message is the current reference scope. Reattach references needed from earlier turns.'},
            'reference_megapixels': {'type': 'number', 'default': 1.0, 'description': 'Additional references are encoded at about this many MP with aspect ratio preserved; 0 keeps original pixels. Uploaded originals are preserved; first reference uses requested output size.'}}


def media_openapi():
    video = video_openapi()
    paths = {}
    for suffix in ('/jobs', '/jobs/{job_id}', '/upload', '/nodes', '/workflows/build'):
        paths[PREFIX + suffix] = copy.deepcopy(video['paths']['/oneapi/v1/video' + suffix])
        for operation in paths[PREFIX + suffix].values():
            operation['tags'] = ['media']
            content = operation.get('requestBody', {}).get('content', {}).get('application/json')
            if content:
                content['schema'] = {'$ref': '#/components/schemas/MediaRequest'}
                content['examples'] = {
                    'image': {'value': {'task': 'image', 'model': DEFAULT_IMAGE_MODEL, 'prompt': '一只坐在窗边的橘猫', 'width': 1024, 'height': 1024}},
                    'tts': {'value': {'task': 'tts', 'text': '你好，欢迎使用本地语音服务。', 'model': DEFAULT_TTS_MODEL, 'voice': 'Vivian'}},
                    'asr': {'value': {'task': 'asr', 'input_audio': 'uploaded.wav', 'language': 'auto', 'word_timestamps': True}},
                    'video': {'value': {'task': 'video', 'prompt': 'A woman waves in a sunlit kitchen.'}},
                }
    paths[PREFIX + '/jobs']['post']['responses']['202']['content']['application/json']['example']['status_url'] = PREFIX + '/jobs/prompt-uuid'
    paths[PREFIX + '/capabilities'] = {'get': {'tags': ['media'], 'summary': '本机全部能力、图像模型、本地TTS/ASR和需凭据的云节点', 'responses': {'200': {'description': 'Installed capabilities and validation status'}}}}
    for route, summary, schema, response in (
        ('/images/generations', '同步文生图；返回OpenAI格式URL或base64，复用原生队列', {'type': 'object', 'required': ['prompt'], 'properties': {'model': {'type': 'string', 'enum': list(IMAGE_MODELS), 'default': DEFAULT_IMAGE_MODEL}, 'prompt': {'type': 'string'}, 'negative_prompt': {'type': 'string'}, 'size': {'type': 'string', 'default': '1024x1024'}, 'n': {'type': 'integer', 'default': 1}, 'seed': {'type': 'integer'}, 'response_format': {'type': 'string', 'enum': ['url', 'b64_json'], 'default': 'url'}}}, {'application/json': {'schema': {'type': 'object'}}}),
        ('/audio/speech', '本地中文TTS、音色指令和参考音频声音克隆，返回音频字节', {'type': 'object', 'required': ['input'], 'properties': {'model': {'type': 'string', 'enum': TTS_MODELS, 'default': DEFAULT_TTS_MODEL}, 'input': {'type': 'string'}, 'voice': {'type': 'string', 'enum': list(SPEECH_VOICES) + QWEN_VOICES}, 'speed': {'type': 'number', 'default': 1}, 'response_format': {'type': 'string', 'enum': ['wav', 'flac', 'mp3', 'opus'], 'default': 'wav'}}}, {'audio/wav': {'schema': {'type': 'string', 'format': 'binary'}}, 'audio/flac': {'schema': {'type': 'string', 'format': 'binary'}}, 'audio/mpeg': {'schema': {'type': 'string', 'format': 'binary'}}, 'audio/ogg': {'schema': {'type': 'string', 'format': 'binary'}}}),
    ):
        if route == '/images/generations':
            schema.pop('required')
            schema['properties'].update({'input_image': {'type': 'string', 'description': 'Uploaded reference filename; placed before input_images when both are provided'}, 'input_images': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Ordered uploaded reference filenames. Refer to them as <image1>, <image2>, etc. in the prompt. n controls output count, not reference count'}, 'steps': {'type': 'integer'}, 'cfg': {'type': 'number'}, 'denoise': {'type': 'number'}})
        if route == '/images/generations':
            schema['properties'].update(image_content_properties())
        if route == '/audio/speech':
            schema['properties'].update(speech_properties())
        paths[PREFIX + route] = {'post': {'tags': ['media'], 'summary': summary, 'requestBody': {'required': True, 'content': {'application/json': {'schema': schema}}}, 'responses': {'200': {'description': 'Completed', 'content': response}, '400': {'description': 'Invalid request'}, '401': {'description': 'Login required'}, '504': {'description': 'Timeout; job_id remains queryable'}}}}
    schema = {'type': 'object', 'required': ['file'], 'properties': {'file': {'type': 'string', 'format': 'binary'}, 'model': {'type': 'string', 'enum': ASR_MODELS, 'default': DEFAULT_ASR_MODEL}, 'prompt': {'type': 'string', 'description': 'Qwen ASR context / hotwords'}, 'language': {'type': 'string', 'default': 'auto'}, 'response_format': {'type': 'string', 'enum': ['json', 'verbose_json', 'text', 'srt', 'vtt'], 'default': 'json'}, 'word_timestamps': {'type': 'boolean', 'default': False}}}
    schema['properties'].update(runtime_properties())
    paths[PREFIX + '/audio/transcriptions'] = {'post': {'tags': ['media'], 'summary': '本地Qwen3 / Whisper语音识别，可选强制对齐字级时间戳', 'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': schema}}}, 'responses': {'200': {'description': 'Text or structured transcript'}, '400': {'description': 'Invalid input'}, '401': {'description': 'Login required'}, '504': {'description': 'Timeout; job_id remains queryable'}}}}
    fields = copy.deepcopy(video['components']['schemas']['VideoRequest']['properties'])
    for key in ('width', 'height', 'profile', 'refiner', 'fast_decode'):
        fields[key].pop('default', None)
    fields.update({'prompt_ext_params': {'type': 'object', 'description': 'Existing OneAPI native prompt extra fields; provider credentials may be supplied in extra_data for registered cloud nodes. Never included in the echoed workflow.'}, 'task': {'type': 'string', 'enum': ['image', 'video', 'tts', 'asr'], 'default': 'image'}, 'model': {'type': 'string'},
                   'text': {'type': 'string'}, 'voice': {'type': 'string', 'description': 'Qwen defaults to Vivian; huayan / lessac select Piper when model is omitted'}, 'speed': {'type': 'number', 'default': 1},
                   'format': {'type': 'string', 'enum': ['flac', 'mp3', 'opus'], 'default': 'flac'}, 'input_audio': {'type': 'string'}, 'input_image': {'type': 'string'}, 'input_images': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Ordered reference filenames; input_image, if provided, comes first'},
                   'language': {'type': 'string', 'default': 'auto'}, 'word_timestamps': {'type': 'boolean', 'default': False},
                   'n': {'type': 'integer', 'default': 1}, 'steps': {'type': 'integer'}, 'cfg': {'type': 'number'}, 'denoise': {'type': 'number'}, 'negative_prompt': {'type': 'string'}})
    fields.update(speech_properties())
    fields.update(image_content_properties())
    for path, summary in (('/audio/compare', '中文TTS、声音克隆与ASR试听对比页'), ('/audio/comparison', '当前用户的语音测试记录'), ('/audio/comparison/{record_id}', '当前用户的测试音频')):
        paths[PREFIX + path] = {'get': {'tags': ['media'], 'summary': summary, 'responses': {'200': {'description': 'Local comparison evidence'}, '401': {'description': 'Login required'}, '404': {'description': 'No matching record'}}}}
        if '{record_id}' in path:
            paths[PREFIX + path]['get']['parameters'] = [{'name': 'record_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}]
    for suffix, summary in [('/images/compare', '生图模型：同中文提示词、seed、1024方图对照'), ('/images/comparison', '当前用户生图实测记录'), ('/images/comparison/{record_id}', '当前用户测试原图')]:
        paths[PREFIX + suffix] = {'get': {'tags': ['media'], 'summary': summary, 'responses': {'200': {'description': 'Local comparison evidence'}, '401': {'description': 'Login required'}, '404': {'description': 'No matching record'}}}}
        if '{record_id}' in suffix:
            paths[PREFIX + suffix]['get']['parameters'] = [{'name': 'record_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}]
    paths[PREFIX + '/audio/unload'] = {'post': {'tags': ['media'], 'summary': '按原生队列顺序释放 TTS / ASR 常驻模型，再进行视频等大显存任务', 'responses': {'200': {'description': 'Resident speech models released'}, '401': {'description': 'Login required'}}}}
    paths[PREFIX + '/gpu-pool'] = {'get': {'tags': ['media'], 'summary': 'GPU池：实时显存、可回收模型与本用户任务的调度原因', 'responses': {'200': {'description': 'Capacity-aware GPU pool status'}, '401': {'description': 'Login required'}}}}
    chat_properties = copy.deepcopy(paths[PREFIX + '/images/generations']['post']['requestBody']['content']['application/json']['schema']['properties'])
    chat_properties.pop('response_format')
    chat_properties.update({'model': {'type': 'string', 'default': DEFAULT_IMAGE_MODEL}, 'stream': {'type': 'boolean', 'default': False}})
    for prefix in ('/v1', PREFIX):
        paths[prefix + '/models'] = {'get': {'tags': ['media'], 'summary': 'OpenAI模型列表：本地Qwen生图模型', 'responses': {'200': {'description': 'OpenAI model list'}}}}
        paths[prefix + '/chat/completions'] = {'post': {'tags': ['media'], 'summary': 'OpenAI图文混排转Qwen原生多图工作流，支持SSE', 'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': ['model', 'messages'], 'properties': chat_properties}}}}, 'responses': {'200': {'description': 'chat.completion or SSE chat.completion.chunk; assistant content contains Markdown data:image; images extension also provided; oneapi contains job_id, timings, reference_map and normalized_prompt'}, '400': {'description': 'Invalid or ambiguous reference mapping'}, '413': {'description': 'Request exceeds max-upload-size; upload files separately'}, '422': {'description': 'Native generation failed'}}}}
    return {'paths': paths, 'schemas': {'MediaRequest': {'type': 'object', 'additionalProperties': False, 'properties': fields}}}


async def wait_job(request, job_id):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = await get_job(request, job_id)
        result = json.loads(response.body)
        if result['status'] == 'failed':
            raise web.HTTPUnprocessableEntity(text=json.dumps({'error': 'Execution failed', 'job_id': job_id, 'errors': result['errors']}), content_type='application/json')
        if result['status'] == 'completed':
            return result
        await asyncio.sleep(0.3)
    raise web.HTTPGatewayTimeout(text=json.dumps({'error': 'Execution timed out; task was not cancelled', 'job_id': job_id, 'status_url': PREFIX + '/jobs/' + job_id}), content_type='application/json')


async def output_bytes(request, url):
    headers = {k: request.headers[k] for k in ('Authorization', 'Cookie', 'Comfy-User') if k in request.headers}
    async with aiohttp.ClientSession() as session:
        async with session.get(f'http://127.0.0.1:{args.port}' + url, headers=headers) as response:
            response.raise_for_status()
            return await response.read()


async def qwen_chat_completion(request, data):
    fields = ('model', 'messages', 'content', 'prompt', 'input_image', 'input_images', 'size', 'width', 'height', 'n', 'seed', 'steps', 'cfg', 'denoise', 'negative_prompt', 'reference_megapixels')
    payload = {key: data[key] for key in fields if key in data}
    payload['response_format'] = 'b64_json'
    headers = {key: request.headers[key] for key in ('Authorization', 'Cookie', 'Comfy-User') if key in request.headers}

    async def generate():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=660)) as session:
            async with session.post(f'http://127.0.0.1:{args.port}' + PREFIX + '/images/generations', json=payload, headers=headers) as response:
                body = await response.json() if response.content_type == 'application/json' else {'error': await response.text()}
                if response.status >= 400:
                    error = {400: web.HTTPBadRequest, 401: web.HTTPUnauthorized, 403: web.HTTPForbidden, 404: web.HTTPNotFound, 422: web.HTTPUnprocessableEntity, 504: web.HTTPGatewayTimeout}.get(response.status, web.HTTPBadGateway)
                    raise error(text=json.dumps(body))
                return body

    stream = None
    task = asyncio.create_task(generate())
    try:
        if data.get('stream'):
            stream = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
            await stream.prepare(request)
            while not task.done():
                await asyncio.wait({task}, timeout=10)
                if not task.done():
                    await stream.write(b': image generation in progress\n\n')
        result = await task
        images = [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + item['b64_json']}} for item in result['data']]
        content = '\n'.join(f"![生成图片{i + 1}]({image['image_url']['url']})" for i, image in enumerate(images))
        metadata = {key: result[key] for key in ('job_id', 'execution_seconds', 'reference_map', 'normalized_prompt')}
        common = {'id': 'chatcmpl-' + result['job_id'], 'created': result['created'], 'model': payload.get('model', DEFAULT_IMAGE_MODEL)}
        if stream is None:
            return web.json_response({**common, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content, 'images': images}, 'finish_reason': 'stop'}], 'oneapi': metadata})
        for delta, finish in [({'role': 'assistant', 'content': content, 'images': images}, None), ({}, 'stop')]:
            chunk = {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}], 'oneapi': metadata}
            await stream.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode())
        await stream.write(b'data: [DONE]\n\n')
        return stream
    except web.HTTPException as error:
        if stream is None:
            return web.Response(status=error.status, text=error.text, content_type='application/json')
        await stream.write(('data: ' + json.dumps({'error': error.text}) + '\n\ndata: [DONE]\n\n').encode())
        return stream
    finally:
        if not task.done():
            task.cancel()


def register_media_routes(queue_prompt):
    routes = PromptServer.instance.routes

    async def start_gpu_pool(app):
        GPU_POOL.install(PromptServer.instance.prompt_queue)

    PromptServer.instance.app.on_startup.append(start_gpu_pool)

    @routes.get('/v1/models')
    @routes.get(PREFIX + '/models')
    async def image_models(request):
        return web.json_response({'object': 'list', 'data': [{'id': model, 'object': 'model', 'created': 0, 'owned_by': 'local-comfyui'} for model in IMAGE_MODELS]})

    @routes.get(PREFIX + '/gpu-pool')
    async def gpu_pool_status(request):
        return web.json_response(GPU_POOL.status(request.get('user_id')), headers={'Cache-Control': 'no-store'})

    @routes.post(PREFIX + '/audio/unload')
    async def unload_speech(request):
        graph = {'release': {'class_type': 'OneAPIReleaseSpeechModels', 'inputs': {}}}
        job_id = await queue_prompt(graph, str(uuid.uuid4()), request=request)
        result = await wait_job(request, job_id)
        return web.json_response({'id': job_id, 'released': True, 'execution_seconds': result['execution_seconds']})

    @routes.get(PREFIX + '/images/compare')
    async def image_compare(request):
        comparison_catalog(request, 'image')
        return web.FileResponse(Path(__file__).parent / 'image_compare.html')

    @routes.get(PREFIX + '/images/comparison')
    async def image_records(request):
        catalog = comparison_catalog(request, 'image')
        records = [{**{k: v for k, v in r.items() if k != 'media_path'}, 'image_url': PREFIX + '/images/comparison/' + r['id']} for r in catalog['records']]
        return web.json_response({**catalog, 'records': records}, headers={'Cache-Control': 'no-store'})

    @routes.get(PREFIX + '/images/comparison/{record_id}')
    async def image_evidence(request):
        catalog = comparison_catalog(request, 'image')
        item = next((r for r in catalog['records'] if r['id'] == request.match_info['record_id']), None)
        if item is None:
            raise web.HTTPNotFound()
        base = Path(folder_paths.get_output_directory()).resolve()
        path = (base / item['media_path']).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    @routes.get(PREFIX + '/audio/compare')
    async def audio_compare(request):
        comparison_catalog(request, 'audio')
        return web.FileResponse(Path(__file__).parent / 'audio_compare.html')

    @routes.get(PREFIX + '/audio/comparison')
    async def audio_records(request):
        catalog = comparison_catalog(request, 'audio')
        records = [{**{k: v for k, v in r.items() if k != 'audio_path'}, 'audio_url': PREFIX + '/audio/comparison/' + r['id']} for r in catalog['records']]
        return web.json_response({**catalog, 'records': records}, headers={'Cache-Control': 'no-store'})

    @routes.get(PREFIX + '/audio/comparison/{record_id}')
    async def audio_evidence(request):
        catalog = comparison_catalog(request, 'audio')
        item = next((r for r in catalog['records'] if r['id'] == request.match_info['record_id']), None)
        if item is None:
            raise web.HTTPNotFound()
        base = Path(folder_paths.get_output_directory()).resolve()
        path = (base / item['audio_path']).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def execute(request, settings):
        graph = make_graph(settings, media=True)
        job_id = await queue_prompt(graph, str(uuid.uuid4()), request=request)
        return job_id, await wait_job(request, job_id)

    @routes.get(PREFIX + '/capabilities')
    async def capabilities(request):
        info = await core(request, '/object_info')
        combos = {field: info[node]['input']['required'][field][0] for node, field in [('UNETLoader', 'unet_name'), ('CLIPLoader', 'clip_name'), ('VAELoader', 'vae_name'), ('CheckpointLoaderSimple', 'ckpt_name')]}
        images = {}
        proof_path = AUDIO_MODELS.parent.parent / 'user' / 'oneapi_validation.json'
        proof = json.loads(proof_path.read_text()) if proof_path.is_file() else {}
        for name, profile in IMAGE_MODELS.items():
            fields = [('checkpoint', 'ckpt_name')] if 'checkpoint' in profile else [('unet', 'unet_name'), ('clip', 'clip_name'), ('vae', 'vae_name')]
            missing = [profile[key] for key, combo in fields if profile[key] not in combos[combo]]
            images[name] = {**profile, 'available': not missing, 'missing': missing, 'tested': proof.get(name, {}).get('status') == 'completed', 'image_to_image': True, 'semantic_edit': name in ('qwen-image-2.1', 'qwen-image-2.1-turbo'), 'multi_reference_images': True, 'native_reference_slots': len(info['TextEncodeQwenImage21']['input']['required']['images'][1]['template']['names'])}
        cloud = [{'node': name, 'category': data.get('category'), 'requires_service_credentials': True, 'tested': False} for name, data in info.items() if data.get('api_node')]
        speech = [name for name in info if 'TextToSpeech' in name]
        asr = [name for name in info if 'SpeechToText' in name]
        local_tts = PiperVoice is not None and all((AUDIO_MODELS / 'piper' / name).is_file() for name in SPEECH_VOICES.values())
        local_asr = WhisperModel is not None and all((AUDIO_MODELS / 'faster-whisper-small' / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'))
        video = await core(request, '/oneapi/v1/video/capabilities')
        tts_models = {'piper': {'available': local_tts, 'device': 'cpu', 'voices': SPEECH_VOICES, 'tested': proof.get('tts', {}).get('status') == 'completed', 'voice_cloning': False}}
        for name, directory in QWEN_TTS_MODELS.items():
            tts_models[name] = {'available': TTS_PYTHON.is_file() and all((AUDIO_MODELS / directory / file).is_file() for file in ('model.safetensors', 'speech_tokenizer/model.safetensors')),
                                'device': 'cuda', 'devices': ['cuda', 'cpu'], 'keep_loaded_default': True, 'voices': QWEN_VOICES if 'clone' not in name else [], 'voice_cloning': 'clone' in name,
                                'tested': proof.get(name, {}).get('status') == 'completed'}
        asr_models = {'faster-whisper-small': {'available': local_asr, 'device': 'cpu', 'tested': proof.get('asr', {}).get('status') == 'completed'}}
        asr_models['sensevoice-small-int8'] = {'available': sherpa_onnx is not None and all((AUDIO_MODELS / 'sensevoice-small-int8' / name).is_file() for name in ('model.int8.onnx', 'tokens.txt')),
                'device': 'cpu', 'compute_type': 'int8', 'tested': proof.get('sensevoice-small-int8', {}).get('status') == 'completed', 'word_timestamps': 'CTC token onsets',
                'features': ['Chinese', 'Cantonese', 'English', 'Japanese', 'Korean', 'punctuation', 'ITN', 'emotion', 'audio_events']}
        for name, directory in QWEN_ASR_MODELS.items():
            asr_models[name] = {'available': (AUDIO_MODELS / directory / 'model.safetensors').is_file(), 'device': 'cuda', 'devices': ['cuda', 'cpu'], 'keep_loaded_default': True,
                                'tested': proof.get(name, {}).get('status') == 'completed', 'hotwords': True,
                                'word_timestamps': (AUDIO_MODELS / 'Qwen3-ForcedAligner-0.6B-hf/model.safetensors').is_file()}
        return web.json_response({'image': {'default': DEFAULT_IMAGE_MODEL, 'models': images, 'comparison_url': PREFIX + '/images/compare'}, 'tts': {'default': DEFAULT_TTS_MODEL, 'models': tts_models, 'comparison_url': PREFIX + '/audio/compare', 'local': {'model': 'piper', 'available': local_tts, 'device': 'cpu', 'voices': SPEECH_VOICES, 'tested': proof.get('tts', {}).get('status') == 'completed', 'voice_cloning': False}, 'cloud_nodes': speech},
                                  'asr': {'default': DEFAULT_ASR_MODEL, 'models': asr_models, 'local': {'model': 'faster-whisper-small', 'available': local_asr, 'device': 'cpu', 'compute_type': 'int8', 'tested': proof.get('asr', {}).get('status') == 'completed', 'features': ['language_detection', 'segments', 'word_timestamps', 'txt', 'srt', 'vtt'], 'speaker_diarization': False}, 'cloud_nodes': asr},
                                  'video': video, 'music': {'available': any('ace' in name.lower() for name in combos['unet_name']), 'availability_check': 'installed_diffusion_model', 'requires': 'ACE-Step compatible diffusion weights; native audio nodes alone do not make a working model'}, 'registered_audio_generation_nodes': [n for n in info if 'AceStep' in n or 'StableAudio' in n], 'cloud_nodes': cloud,
                                  'local_node_categories': {category: [name for name, data in info.items() if not data.get('api_node') and (data.get('category') or 'uncategorized') == category] for category in sorted({data.get('category') or 'uncategorized' for data in info.values() if not data.get('api_node')})},
                                  'native_nodes': {'local_count': sum(not d.get('api_node', False) for d in info.values()), 'cloud_count': len(cloud), 'schema_url': PREFIX + '/nodes'},
                                  'execution': {'jobs': PREFIX + '/jobs', 'upload': PREFIX + '/upload', 'build': PREFIX + '/workflows/build', 'max_request_mib': args.max_upload_size, 'recommended_large_image_transport': 'upload each file then submit filenames', 'all_registered_nodes_accept_native_workflow': True}, 'availability_check': 'nodes_and_model_files; tested is separate from registered'})

    @routes.post(PREFIX + '/images/generations')
    async def image(request):
        data = await read_settings(request)
        response_format = data.pop('response_format', 'url')
        if response_format not in ('url', 'b64_json'):
            raise web.HTTPBadRequest(text='response_format must be url or b64_json')
        size = data.pop('size', '1024x1024')
        try:
            width, height = map(int, size.split('x'))
        except (ValueError, AttributeError):
            raise web.HTTPBadRequest(text='size must be WIDTHxHEIGHT')
        settings, references = await prepare_image_settings(request, {'task': 'image', 'width': width, 'height': height, **data})
        job_id, result = await execute(request, settings)
        images = [f for f in result['files'] if f['media_type'].startswith('image/')]
        output = []
        for item in images:
            output.append({'b64_json': base64.b64encode(await output_bytes(request, item['url'])).decode()} if response_format == 'b64_json' else {'url': str(request.url.origin()) + item['url']})
        return web.json_response({'created': int(time.time()), 'data': output, 'job_id': job_id, 'execution_seconds': result['execution_seconds'], 'reference_map': references, 'normalized_prompt': settings['prompt']})

    @routes.post(PREFIX + '/audio/speech')
    async def speech(request):
        data = await read_settings(request)
        output_format = data.pop('response_format', 'wav')
        if output_format not in ('wav', 'flac', 'mp3', 'opus'):
            raise web.HTTPBadRequest(text='response_format must be wav, flac, mp3 or opus')
        settings = {'task': 'tts', 'text': data.pop('input', ''), 'format': 'flac' if output_format == 'wav' else output_format, **data}
        job_id, result = await execute(request, settings)
        item = next(f for f in result['files'] if f['media_type'].startswith('audio/'))
        body = await output_bytes(request, item['url'])
        if output_format == 'wav':
            buffer = io.BytesIO()
            with av.open(io.BytesIO(body)) as source, av.open(buffer, mode='w', format='wav') as target:
                stream = target.add_stream('pcm_s16le', rate=source.streams.audio[0].rate)
                stream.layout = source.streams.audio[0].layout
                for frame in source.decode(audio=0):
                    for packet in stream.encode(frame):
                        target.mux(packet)
                for packet in stream.encode(None):
                    target.mux(packet)
            body = buffer.getvalue()
        content_type = {'wav': 'audio/wav', 'mp3': 'audio/mpeg', 'flac': 'audio/flac', 'opus': 'audio/ogg'}[output_format]
        return web.Response(body=body, content_type=content_type, headers={'X-OneAPI-Job-ID': job_id, 'X-Execution-Seconds': str(result['execution_seconds'])})

    @routes.post(PREFIX + '/audio/transcriptions')
    async def transcribe(request):
        form = await request.post()
        output_format = form.get('response_format', 'json')
        if output_format not in ('json', 'verbose_json', 'text', 'srt', 'vtt'):
            raise web.HTTPBadRequest(text='response_format must be json, verbose_json, text, srt or vtt')
        if not isinstance(form.get('file'), web.FileField):
            raise web.HTTPBadRequest(text='file is required')
        uploaded = await upload_media(request)
        if uploaded.status != 200:
            return uploaded
        filename = json.loads(uploaded.body)
        name = '/'.join(x for x in (filename.get('subfolder'), filename['name']) if x)
        model = form.get('model', DEFAULT_ASR_MODEL)
        job_id, result = await execute(request, {'task': 'asr', 'model': 'faster-whisper-small' if model == 'whisper-1' else model, 'input_audio': name,
                                                'language': form.get('language', 'auto'), 'prompt': form.get('prompt', ''),
                                                'word_timestamps': str(form.get('word_timestamps', 'false')).lower() == 'true',
                                                **({'device': form['device']} if 'device' in form else {}),
                                                **({'keep_loaded': str(form['keep_loaded']).lower() == 'true'} if 'keep_loaded' in form else {})})
        details = json.loads(result['texts']['transcript_details'][0])
        headers = {'X-OneAPI-Job-ID': job_id, 'X-Execution-Seconds': str(result['execution_seconds'])}
        if output_format in ('json', 'verbose_json'):
            return web.json_response(details if output_format == 'verbose_json' else {'text': details['text']}, headers=headers)
        if output_format == 'text':
            return web.Response(text=details['text'], headers=headers)
        def timestamp(seconds):
            milliseconds = round(seconds * 1000)
            hours, milliseconds = divmod(milliseconds, 3600000)
            minutes, milliseconds = divmod(milliseconds, 60000)
            seconds, milliseconds = divmod(milliseconds, 1000)
            separator = ',' if output_format == 'srt' else '.'
            return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}'
        lines = ['WEBVTT\n'] if output_format == 'vtt' else []
        for i, segment in enumerate(details['segments'], 1):
            lines.append(f"{i}\n{timestamp(segment['start'])} --> {timestamp(segment['end'])}\n{segment['text'].strip()}\n")
        return web.Response(text='\n'.join(lines), content_type='text/vtt' if output_format == 'vtt' else 'text/plain', headers=headers)
