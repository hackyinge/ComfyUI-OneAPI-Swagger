import os
import json
import traceback
from typing import Sequence
import uuid
import time
import copy
import asyncio
import aiohttp
import tempfile
import mimetypes
from urllib.parse import urlparse
import base64
import random
from aiohttp import web
from server import PromptServer
import folder_paths
import execution
from workflow_format import adjust_workflow_format
from openapi_spec import OPENAPI_SPEC
from swagger_ui import SWAGGER_UI_HTML

# Constants
API_WORKFLOWS_DIR = 'api_workflows'

# Node types that require special media upload handling
MEDIA_UPLOAD_NODE_TYPES = {
    'LoadImage',
    'VHS_LoadAudioUpload',
    'VHS_LoadVideo',
}

# Get routes
prompt_server = PromptServer.instance
routes = prompt_server.routes

# Get workflow paths
path_custom_nodes = os.path.dirname(os.path.dirname(__file__))
path_comfyui_root = os.path.dirname(path_custom_nodes)
path_workflows = os.path.join(path_comfyui_root, 'user/default/workflows')

@routes.get('/oneapi/docs')
async def swagger_ui(request):
    """
    Swagger UI interface for API documentation and testing
    """
    return web.Response(text=SWAGGER_UI_HTML, content_type='text/html')

@routes.get('/oneapi/openapi.json')
async def openapi_spec(request):
    """
    OpenAPI specification JSON
    """
    # 动态获取当前请求的地址，避免硬编码
    protocol = request.scheme
    host = request.host
    base_url = f"{protocol}://{host}"
    
    # 将规范中的 localhost 地址动态替换为当前访问地址
    spec_json = json.dumps(OPENAPI_SPEC)
    spec_json = spec_json.replace("http://localhost:8118", base_url)
    spec_json = spec_json.replace("http://localhost:8188", base_url)
    
    spec = json.loads(spec_json)
    
    # 更新 servers 列表
    spec['servers'] = [
        {
            "url": base_url,
            "description": "当前 ComfyUI 服务器"
        }
    ]
    
    return web.json_response(spec)

@routes.post('/oneapi/v1/save-api-workflow')
async def save_api_workflow(request):
    """
    Save API workflow to user's workflow directory.
    """
    data = await request.json()
    name = data.get('name')
    workflow = data.get('workflow')
    overwrite = data.get('overwrite', False)
    
    if not name:
        return web.json_response({"error": "Name is required"}, status=400)
    if not workflow:
        return web.json_response({"error": "Workflow is required"}, status=400)
    
    fmt = adjust_workflow_format(workflow)
    if fmt == 'invalid':
        return web.json_response({"error": "Invalid workflow format"}, status=400)
    if fmt == 'ui':
        return web.json_response({"error": "UI format workflow is not supported. Please convert to API format and try again."}, status=400)

    name_with_json = name if name.endswith('.json') else f'{name}.json'
    relative_path = f'{API_WORKFLOWS_DIR}/{name_with_json}'
    save_path = prompt_server.user_manager.get_request_user_filepath(request, relative_path, create_dir=True)
    if not save_path:
        return web.json_response({"error": "Failed to get save path"}, status=500)
    if os.path.exists(save_path) and not overwrite:
        return web.json_response({"error": "File already exists. Use overwrite=true to overwrite."}, status=400)
    
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)

    return web.json_response({"message": "Workflow saved successfully", "filename": name_with_json})

@routes.post('/oneapi/v1/execute')
async def execute_workflow(request):
    """
    Execute workflow API
    
    Parameters:
    - workflow: Workflow JSON, filename (under user/default/api_workflows/), or URL
    - params: Parameter mapping dictionary
    - wait_for_result: Whether to wait for results (default True)
    - timeout: Timeout in seconds (default None)
    - prompt_ext_params: Extra parameters for prompt request (optional)
    
    Returns:
    - Return values:
        - status: Status (queued, processing, completed, timeout)
        - prompt_id: Prompt ID
        - images: List of image URLs [string, ...], only present if there are image results
        - images_by_var: Mapped image URLs by variable name {var_name: [string, ...], ...}, only present if there are image results
        - videos: List of video URLs [string, ...], only present if there are video results
        - videos_by_var: Mapped video URLs by variable name {var_name: [string, ...], ...}, only present if there are video results
        - audios: List of audio URLs [string, ...], only present if there are audio results
        - audios_by_var: Mapped audio URLs by variable name {var_name: [string, ...], ...}, only present if there are audio results
        - texts: List of text outputs [string, ...], only present if there are text results
        - texts_by_var: Mapped text outputs by variable name {var_name: [string, ...], ...}, only present if there are text results
    
    Node title markers:
    - Input: Use "$param.field" in node title to map parameter values
    - Output: Use "$output.name" in output node title to specify outputs
      - "$output.name" - Marks an output node with a custom output name (added to "images_by_var[name]" or "videos_by_var[name]" or "texts_by_var[name]")
      - If no explicit output marker is set, the node_id is used as the variable name
      - Any node that produces outputs (images, videos, audios, texts) will be included in results
      - images/images_by_var/videos/videos_by_var/audios/audios_by_var/texts/texts_by_var fields are only included if there are corresponding results
    """
    try:
        # Get request data
        data = await request.json()
        
        # Extract parameters
        workflow = data.get('workflow')
        params = data.get('params', {})
        if 'seed' not in params:
            params['seed'] = random.randint(1, 1125899906842624)

        wait_for_result = data.get('wait_for_result', True)
        timeout = data.get('timeout', None)
        prompt_ext_params = data.get('prompt_ext_params', {})

        # Support workflow as local path or URL
        if isinstance(workflow, dict):
            pass  # Use directly
        elif isinstance(workflow, str):
            if workflow.startswith('http://') or workflow.startswith('https://'):
                workflow = await _load_workflow_from_url(workflow)
            else:
                workflow = _load_workflow_from_local(workflow, request)
        else:
            return web.json_response({"error": "Invalid workflow parameter"}, status=400)
        
        if not workflow:
            return web.json_response({"error": "Workflow data is missing"}, status=400)
        
        # Convert UI format to API format if needed
        fmt = adjust_workflow_format(workflow)
        if fmt == 'invalid':
            return web.json_response({"error": "Invalid workflow format"}, status=400)
        if fmt == 'ui':
            return web.json_response({"error": "UI format workflow is not supported. Please convert to API format and try again."}, status=400)
        
        # Process workflow parameters
        if params:
            workflow = await _apply_params_to_workflow(workflow, params, request)
        
        # Extract and save output node information
        output_id_2_var = await _extract_output_nodes(workflow)
        
        # Generate client ID
        client_id = str(uuid.uuid4())
        
        # Submit workflow to ComfyUI queue
        try:
            prompt_id = await _queue_prompt(workflow, client_id, prompt_ext_params, request)
        except Exception as e:
            error_message = f"Failed to submit workflow: [{type(e)}] {str(e)}"
            print(error_message)
            return web.json_response({"error": error_message}, status=500)
        
        result = {
            "status": "queued",
            "prompt_id": prompt_id,
            "message": "Workflow submitted"
        }
        
        # If not waiting for results, return immediately
        if not wait_for_result:
            return web.json_response(result)
        
        # Poll for results
        result = await _wait_for_results(prompt_id, timeout, request, output_id_2_var)
        return web.json_response(result)
        
    except Exception as e:
        print(f"Error executing workflow: {str(e)}, {traceback.format_exc()}")
        return web.json_response({"error": str(e)}, status=500)

@routes.post('/oneapi/v1/models/{model}:generateContent')
@routes.post('/v1beta/models/{model}:generateContent')
@routes.post('/v1/models/{model}:generateContent')
async def generate_content(request):
    """
    Open API compatible with Gemini generateContent format
    Supports standard Gemini paths for easier integration
    支持文生图和图生图两种模式
    """
    try:
        model = request.match_info.get('model', 'default')
        data = await request.json()
        
        # 1. 从 Gemini 格式提取内容 (支持多模态: 文本 + 图像)
        contents = data.get('contents', [])
        if not contents or not contents[0].get('parts'):
            return web.json_response({"error": "Invalid contents/parts"}, status=400)
        
        parts = contents[0].get('parts', [])
        prompt_texts = []
        image_data = None
        
        # 检查是否有图像输入
        for part in parts:
            if 'text' in part:
                prompt_texts.append(part['text'])
            elif 'inlineData' in part:
                # 获取 Base64 图像数据
                mime_type = part['inlineData'].get('mimeType', 'image/png')
                base64_data = part['inlineData'].get('data', '')
                if base64_data:
                    image_data = f"data:{mime_type};base64,{base64_data}"
        
        prompt_text = "\n".join(prompt_texts).strip()
        if not prompt_text:
            return web.json_response({"error": "Prompt text is empty"}, status=400)
            
        # 2. 根据是否有图片输入选择工作流
        workflow_name = model
        if ':' in workflow_name:
            workflow_name = workflow_name.split(':')[0]
        
        # 如果有图片输入，使用图生图工作流；否则使用文生图工作流
        if image_data:
            workflow_name = f"{workflow_name}-TT"  # 图生图工作流
            print(f"[OneAPI] 检测到图片输入，使用图生图工作流: {workflow_name}")
        else:
            print(f"[OneAPI] 无图片输入，使用文生图工作流: {workflow_name}")
            
        try:
            workflow = _load_workflow_from_local(workflow_name, request)
        except Exception as e:
            msg = str(e)
            # Fallback to try generic name if specific one fails
            try:
                fallback_name = 'default_txt2img-TT' if image_data else 'default_txt2img'
                workflow = _load_workflow_from_local(fallback_name, request)
            except:
                return web.json_response({"error": f"Workflow '{workflow_name}' not found. {msg}"}, status=404)

        # 3. 处理图像上传（如果有）
        uploaded_filename = None
        if image_data:
            try:
                # 解析 Base64 图像并上传
                if image_data.startswith('data:'):
                    header, encoded = image_data.split(",", 1)
                    ext = mimetypes.guess_extension(header.split(';')[0].split(':')[1]) or '.png'
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                        tmp.write(base64.b64decode(encoded))
                        temp_path = tmp.name
                    try:
                        uploaded_filename = await _upload_media(temp_path, request)
                        print(f"[OneAPI] 图片上传成功: {uploaded_filename}")
                    finally:
                        os.unlink(temp_path)
            except Exception as e:
                print(f"[OneAPI] Error processing image: {str(e)}")
                return web.json_response({"error": f"Failed to process image: {str(e)}"}, status=500)

        # 4. Process with execute_workflow internal logic
        # Extract aspectRatio and set width/height
        generation_config = data.get('generationConfig', {})
        image_config = generation_config.get('imageConfig', {})
        aspect_ratio = image_config.get('aspectRatio', '16:9')
        
        width, height = 1280, 720
        if aspect_ratio == "9:16":
            width, height = 720, 1280
            
        params = {
            "prompt": prompt_text,
            "width": width,
            "height": height,
            "seed": random.randint(1, 1125899906842624)
        }
        
        # 如果有上传的图片，添加到参数中
        if uploaded_filename:
            params["image"] = uploaded_filename
        
        workflow = await _apply_params_to_workflow(workflow, params, request)
        output_id_2_var = await _extract_output_nodes(workflow)
        client_id = str(uuid.uuid4())
        
        prompt_id = await _queue_prompt(workflow, client_id, {}, request)
        execution_result = await _wait_for_results(prompt_id, 300, request, output_id_2_var)
        
        if execution_result.get('status') != 'completed':
            return web.json_response({"error": f"Execution failed: {execution_result.get('status')}"}, status=500)
            
        # 5. Construct Gemini response
        parts = []
        parts.append({"text": f"Successfully generated images for: {prompt_text}"})
        
        images = execution_result.get('images', [])
        for img_url in images:
            img_data_base64 = await _fetch_image_base64(img_url)
            if img_data_base64:
                parts.append({
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": img_data_base64
                    }
                })

        response_data = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": parts
                    },
                    "finishReason": "STOP"
                }
            ]
        }
        
        return web.json_response(response_data)
        
    except Exception as e:
        print(f"Error in generateContent: {str(e)}, {traceback.format_exc()}")
        return web.json_response({"error": str(e)}, status=500)



async def _fetch_image_base64(url):
    """Fetch image and return base64 encoded string"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.read()
                    return base64.b64encode(data).decode('utf-8')
    except Exception as e:
        print(f"Failed to fetch image for base64: {e}")
    return None


@routes.post('/v1/chat/completions')
async def chat_completions(request):
    """
    OpenAI 兼容接口 - 支持图生视频
    
    请求格式:
    {
        "model": "LTX2-SWZ",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "视频描述文本"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                ]
            }
        ]
    }
    """
    try:
        data = await request.json()
        model = data.get('model', 'LTX2-SWZ')
        messages = data.get('messages', [])
        
        if not messages:
            return web.json_response({"error": "Messages are required"}, status=400)
        
        # 1. 提取内容 (从最后一条消息中提取)
        last_message = messages[-1]
        content = last_message.get('content', '')
        
        prompt_texts = []
        image_data_list = []  # 改为列表，支持多张图片
        
        if isinstance(content, str):
            prompt_texts.append(content)
        elif isinstance(content, list):
            for part in content:
                p_type = part.get('type', '')
                if p_type == 'text' or 'text' in part:
                    txt = part.get('text', '')
                    if txt: prompt_texts.append(txt)
                elif p_type == 'image_url' or 'image_url' in part:
                    image_info = part.get('image_url', {})
                    if isinstance(image_info, str):
                        image_data_list.append(image_info)
                    else:
                        url = image_info.get('url', '')
                        if url:
                            image_data_list.append(url)
        
        prompt_text = "\n".join(prompt_texts).strip()
        
        print(f"[OneAPI] Processing Video Request:")
        print(f"  - Model: {model}")
        print(f"  - Prompt: {prompt_text[:100]}..." if len(prompt_text) > 100 else f"  - Prompt: {prompt_text}")
        print(f"  - Images Count: {len(image_data_list)}")
        for idx, img_data in enumerate(image_data_list):
            img_disp = img_data[:50] + "..." if len(img_data) > 50 else img_data
            print(f"  - Image {idx+1} Type: {'URL' if img_data.startswith('http') else 'Base64/Local'}")
            print(f"  - Image {idx+1} Preview: {img_disp}")

        if not prompt_text:
            return web.json_response({"error": "Prompt text is empty"}, status=400)
        
        if not image_data_list:
            return web.json_response({"error": "At least one image is required for video generation"}, status=400)
        
        # 2. 确定工作流
        workflow_name = model
        if ':' in workflow_name:
            workflow_name = workflow_name.split(':')[0]
        
        # 优先使用 model 参数指定的工作流文件名称
        try:
            workflow = _load_workflow_from_local(workflow_name, request)
        except Exception:
            # 如果找不到, 尝试使用默认的 LTX2-SWZ 工作流作为 fallback
            try:
                workflow = _load_workflow_from_local('LTX2-SWZ', request)
            except Exception as e:
                return web.json_response({"error": f"Workflow '{workflow_name}' or 'LTX2-SWZ' not found. {str(e)}"}, status=404)
        
        # 3. 处理图片数据 - 支持多张图片
        uploaded_filenames = []
        for image_data in image_data_list:
            uploaded_filename = None
            if image_data.startswith('data:image'):
                try:
                    header, encoded = image_data.split(',', 1)
                    image_bytes = base64.b64decode(encoded)
                    mime_type = header.split(';')[0].split(':')[1]
                    ext = mimetypes.guess_extension(mime_type) or '.png'
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                        tmp.write(image_bytes)
                        temp_path = tmp.name
                    
                    try:
                        uploaded_filename = await _upload_media(temp_path, request)
                    finally:
                        os.unlink(temp_path)
                except Exception as e:
                    return web.json_response({"error": f"Failed to process base64 image: {str(e)}"}, status=400)
            elif image_data.startswith('http'):
                try:
                    uploaded_filename = await _upload_media_from_source(image_data, request)
                except Exception as e:
                    return web.json_response({"error": f"Failed to upload image from URL: {str(e)}"}, status=400)
            else:
                uploaded_filename = image_data
            
            if uploaded_filename:
                uploaded_filenames.append(uploaded_filename)
        
        print(f"[OneAPI] Uploaded {len(uploaded_filenames)} images: {uploaded_filenames}")
        
        # 4. 设置参数并提交
        # 注意：工作流中使用 $param.xxx 格式，所以参数需要嵌套在 param 对象下
        params = {
            "param": {
                "text": prompt_text,
                "seed": random.randint(1, 1125899906842624)
            }
        }
        
        # 设置图片参数
        if len(uploaded_filenames) >= 2:
            # 有两张或更多图片：第一张作为首帧，第二张作为尾帧
            params["param"]["image"] = uploaded_filenames[0]
            params["param"]["image2"] = uploaded_filenames[1]
            print(f"[OneAPI] Using 2 images: image={uploaded_filenames[0]}, image2={uploaded_filenames[1]}")
        elif len(uploaded_filenames) == 1:
            # 只有一张图片：同时用作首帧和尾帧
            params["param"]["image"] = uploaded_filenames[0]
            params["param"]["image2"] = uploaded_filenames[0]
            print(f"[OneAPI] Using 1 image for both: image={uploaded_filenames[0]}, image2={uploaded_filenames[0]}")
        else:
            # 没有上传图片：使用默认值或报错
            print(f"[OneAPI] ❌ No images uploaded!")
            return web.json_response({
                "error": "No images provided in request"
            }, status=400)
        
        print(f"[OneAPI] Final params structure: {params}")
        
        workflow = await _apply_params_to_workflow(workflow, params, request)
        output_id_2_var = await _extract_output_nodes(workflow)
        client_id = str(uuid.uuid4())
        
        prompt_id = await _queue_prompt(workflow, client_id, {}, request)
        
        # 5. 等待结果
        execution_result = await _wait_for_results(prompt_id, 600, request, output_id_2_var)
        
        if execution_result.get('status') != 'completed':
            error_msg = execution_result.get('error', execution_result.get('status', 'Unknown error'))
            return web.json_response({"error": f"Execution failed: {error_msg}"}, status=500)
        
        # 6. 构建 OpenAI 格式响应
        videos = execution_result.get('videos', [])
        response_content = f"Successfully generated video for: {prompt_text}"
        if videos:
            response_content += f"\n\nGenerated Video URL: {videos[0]}"
        
        response_data = {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_content
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
        
        return web.json_response(response_data)
        
    except Exception as e:
        print(f"Error in chatCompletions: {str(e)}, {traceback.format_exc()}")
        return web.json_response({"error": str(e)}, status=500)



async def _fetch_image_base64(url):
    """Fetch image and return base64 encoded string"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.read()
                    return base64.b64encode(data).decode('utf-8')
    except Exception as e:
        print(f"Failed to fetch image for base64: {e}")
    return None

async def _apply_params_to_workflow(workflow, params, request=None):
    """
    Apply parameters to the workflow
    
    Handles two types of parameter mappings:
    1. LoadImage node: $image_param
    2. Regular node: $param.field
    """
    workflow = copy.deepcopy(workflow)
    
    print(f"[DEBUG] _apply_params_to_workflow called with {len(workflow)} nodes")
    print(f"[DEBUG] params: {params}")
    
    for node_id, node_data in workflow.items():
        # Skip nodes that don't meet criteria
        if not _is_valid_node(node_data):
            print(f"[DEBUG] Skipping invalid node {node_id}")
            continue
        
        title = node_data.get("_meta", {}).get("title", "")
        print(f"[DEBUG] Processing node {node_id}, title: {title}")
            
        # Process parameter markers in the node
        await _process_node_params(node_data, params, request)
    
    print(f"[DEBUG] _apply_params_to_workflow completed")
    return workflow

def _is_valid_node(node_data):
    """Check if node is valid and contains a title"""
    return (isinstance(node_data, dict) and 
            "_meta" in node_data and 
            "title" in node_data["_meta"])

async def _process_node_params(node_data, params, request=None):
    """Process parameter markers in the node's inputs and title"""
    if "inputs" not in node_data:
        return
    
    inputs = node_data["inputs"]
    node_class_type = node_data.get('class_type', '')
    
    # ==========================================
    # 方式1：遍历 inputs，查找以 $ 开头的值
    # ==========================================
    for input_name, input_value in list(inputs.items()):
        if not isinstance(input_value, str):
            continue
        
        new_value = await _resolve_placeholder(input_value, params, node_class_type, node_data, input_name, request)
        if new_value is not None:
            inputs[input_name] = new_value
    
    # ==========================================
    # 方式2：处理 title 中的占位符（旧格式兼容）
    # ==========================================
    if "_meta" in node_data and "title" in node_data["_meta"]:
        title = node_data["_meta"]["title"]
        parts = title.split(',')
        for part in parts:
            part = part.strip()
            if not part.startswith('$'):
                continue
            
            # 解析 $xxx.yyy 格式
            if '.' not in part:
                continue
                
            var_name, input_field = part[1:].split('.', 1)
            
            # 从 params 中获取值
            param_value = None
            if var_name in params:
                param_container = params[var_name]
                if isinstance(param_container, dict) and input_field in param_container:
                    param_value = param_container[input_field]
                elif not isinstance(param_container, dict):
                    param_value = param_container
            
            if param_value is not None:
                print(f"[DEBUG] Title marker ${var_name}.{input_field} -> {param_value}")
                # 设置到 input_field 对应的输入
                if node_class_type in MEDIA_UPLOAD_NODE_TYPES:
                    await _handle_media_upload(node_data, input_field, param_value, request)
                else:
                    inputs[input_field] = param_value

async def _resolve_placeholder(input_value, params, node_class_type, node_data, input_name, request):
    """Resolve a placeholder value and return the new value, or None if not a placeholder"""
    
    # 处理 $param.xxx 格式
    if input_value.startswith('$param.'):
        param_key = input_value[7:]  # 去掉 '$param.' 前缀
        print(f"[DEBUG] Found $param.{param_key} in input '{input_name}'")
        
        if "param" in params and param_key in params["param"]:
            new_value = params["param"][param_key]
            print(f"[DEBUG] Replacing with: {new_value}")
            
            if node_class_type in MEDIA_UPLOAD_NODE_TYPES:
                await _handle_media_upload(node_data, input_name, new_value, request)
                return None  # _handle_media_upload 已经设置了值
            return new_value
        else:
            print(f"[DEBUG] ❌ param key '{param_key}' not found in params['param']")
    
    # 处理 $seed.xxx 格式
    elif input_value.startswith('$seed.'):
        seed_key = input_value[6:]
        print(f"[DEBUG] Found $seed.{seed_key} in input '{input_name}'")
        
        if "seed" in params:
            print(f"[DEBUG] Replacing with seed: {params['seed']}")
            return params["seed"]
        elif "param" in params and "seed" in params["param"]:
            print(f"[DEBUG] Replacing with param.seed: {params['param']['seed']}")
            return params["param"]["seed"]
        else:
            print(f"[DEBUG] ❌ seed not found in params")
    
    # 处理 $image.xxx 格式
    elif input_value.startswith('$image.'):
        image_key = input_value[7:]
        print(f"[DEBUG] Found $image.{image_key} in input '{input_name}'")
        
        if "image" in params:
            new_value = params["image"]
            print(f"[DEBUG] Replacing with image: {new_value}")
            if node_class_type in MEDIA_UPLOAD_NODE_TYPES:
                await _handle_media_upload(node_data, input_name, new_value, request)
                return None
            return new_value
        elif "param" in params and "image" in params["param"]:
            new_value = params["param"]["image"]
            print(f"[DEBUG] Replacing with param.image: {new_value}")
            if node_class_type in MEDIA_UPLOAD_NODE_TYPES:
                await _handle_media_upload(node_data, input_name, new_value, request)
                return None
            return new_value
        else:
            print(f"[DEBUG] ❌ image not found in params")
    
    return None

async def _extract_output_nodes(workflow):
    """
    Extract output nodes and their output variable names from workflow
    
    Args:
        workflow: Workflow JSON object
        
    Returns:
        Dictionary mapping node_id to output variable name (only for nodes with explicit $output.name markers)
    
    Note:
        Any node that produces outputs will be included in results.
        Output fields (images, videos, texts, *_by_var) are only included in the response if there are corresponding results.
    """
    output_id_2_var = {}
    
    for node_id, node_data in workflow.items():
        # Skip nodes that don't meet criteria
        if not _is_valid_node(node_data):
            continue
            
        # Get node title
        title = node_data["_meta"]["title"]
        
        # Check for $output marker in the title
        output_var = None
        parts = title.split(',')
        for part in parts:
            part = part.strip()
            if part.startswith('$output'):
                # Parse output marker
                if '.' in part:
                    # Format: $output.name - Specify output name
                    output_var = part.split('.', 1)[1]
                    if not output_var:
                        raise Exception(f"Invalid output marker format (empty name): {part}")
                else:
                    # Simple $output without variable name is not valid
                    raise Exception(f"Invalid output marker format (missing name): {part}. Use $output.name format.")
        
        # Only register in output_id_2_var if there's an explicit output marker
        if output_var:
            output_id_2_var[node_id] = output_var
    
    return output_id_2_var

async def _process_param_marker(node_data, var_spec, params, request=None):
    """
    Process individual parameter marker
    
    Format: param_name.field_name
    - param_name: Parameter name, corresponding to key in params
    - field_name: Node input field name
    
    Special handling for media upload node types defined in MEDIA_UPLOAD_NODE_TYPES
    """
    # Must have field separator
    if '.' not in var_spec:
        print(f"Parameter marker format error, should be '$param.field': {var_spec}")
        return
        
    # Parse parameter name and field name
    var_name, input_field = var_spec.split('.', 1)
    
    print(f"[DEBUG] Processing marker: ${var_spec}")
    print(f"[DEBUG]   var_name={var_name}, input_field={input_field}")
    print(f"[DEBUG]   Available params keys: {list(params.keys())}")
    
    # Check if parameter exists
    if var_name not in params:
        print(f"[DEBUG]   ❌ var_name '{var_name}' not found in params")
        return
        
    # Get parameter value
    # 支持嵌套结构：如果 params[var_name] 是字典，则从中提取 input_field
    param_container = params[var_name]
    print(f"[DEBUG]   param_container type: {type(param_container)}")
    
    if isinstance(param_container, dict):
        # 嵌套结构：params["param"]["image"]
        print(f"[DEBUG]   param_container keys: {list(param_container.keys())}")
        if input_field not in param_container:
            print(f"[DEBUG]   ❌ input_field '{input_field}' not found in param_container")
            return
        param_value = param_container[input_field]
        print(f"[DEBUG]   ✅ Found param_value: {param_value}")
    else:
        # 扁平结构（向后兼容）
        param_value = param_container
        print(f"[DEBUG]   ✅ Using param_container as value: {param_value}")
    
    # Check if this node type requires special media upload handling
    node_class_type = node_data.get('class_type')
    if node_class_type in MEDIA_UPLOAD_NODE_TYPES:
        await _handle_media_upload(node_data, input_field, param_value, request)
    else:
        # Regular parameter setting
        await _set_node_param(node_data, input_field, param_value)

async def _handle_media_upload(node_data, input_field, param_value, request=None):
    """
    Handle media upload for nodes in MEDIA_UPLOAD_NODE_TYPES
    
    Args:
        node_data: Node data
        input_field: Input field name
        param_value: Parameter value
        request: HTTP request object for getting server URL
    """
    # Ensure inputs exists
    if "inputs" not in node_data:
        node_data["inputs"] = {}
    
    # If parameter value is a URL starting with http, upload the media first
    if isinstance(param_value, str) and param_value.startswith(('http://', 'https://')):
        try:
            # Upload media and get uploaded media name
            media_value = await _upload_media_from_source(param_value, request)
            # Use uploaded media name as node's input value
            await _set_node_param(node_data, input_field, media_value)
            print(f"Media uploaded: {media_value}")
        except Exception as e:
            print(f"Failed to upload media: {str(e)}")
            # Throw exception on upload failure
            raise Exception(f"Media upload failed: {str(e)}")
    else:
        # Use parameter value directly as media name
        await _set_node_param(node_data, input_field, param_value)

async def _set_node_param(node_data, input_field, param_value):
    """
    Set node parameter
    
    Args:
        node_data: Node data
        input_field: Input field name
        param_value: Parameter value
    """
    # Ensure inputs exists
    if "inputs" not in node_data:
        node_data["inputs"] = {}
    # Set parameter value
    node_data["inputs"][input_field] = param_value

async def _upload_media_from_source(media_url, request=None) -> str:
    """
    Upload media from URL
    
    Args:
        media_url: Media URL
        request: HTTP request object for getting server URL
            
    Returns:
        Upload media file name
    """
    # Download media from URL
    async with aiohttp.ClientSession() as session:
        async with session.get(media_url) as response:
            if response.status != 200:
                raise Exception(f"Failed to download media: HTTP {response.status}")
            
            # Extract filename from URL
            parsed_url = urlparse(media_url)
            filename = os.path.basename(parsed_url.path)
            if not filename:
                filename = f"temp_media_{hash(media_url)}.jpg"
            
            # Get media data
            media_data = await response.read()
            
            # Save to temporary file
            suffix = os.path.splitext(filename)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(media_data)
                temp_path = tmp.name
    
    try:
        # Upload temporary file to ComfyUI
        return await _upload_media(temp_path, request)
    finally:
        # Delete temporary file
        os.unlink(temp_path)

async def _upload_media(media_path, request=None) -> str:
    """
    Upload media to ComfyUI
    
    Args:
        media_path: Media path
        request: HTTP request object for getting server URL
            
    Returns:
        Upload media file name
    """
    # Read media data
    with open(media_path, 'rb') as f:
        media_data = f.read()
    
    # Extract filename
    filename = os.path.basename(media_path)
    
    # Auto-detect file MIME type
    mime_type = mimetypes.guess_type(filename)[0]
    if mime_type is None:
        # Default to generic image type
        mime_type = 'application/octet-stream'
    
    # Prepare form data
    data = aiohttp.FormData()
    data.add_field('image', media_data, 
                   filename=filename, 
                   content_type=mime_type)
    
    # Upload media using dynamic server URL
    internal_url = _get_internal_api_url(request)
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{internal_url}/upload/image", data=data) as response:
            if response.status != 200:
                raise Exception(f"Failed to upload media: HTTP {response.status}")
            
            # Get upload result
            result = await response.json()
            return result.get('name', '')

async def _queue_prompt(workflow, client_id, prompt_ext_params=None, request=None):
    """Submit workflow to queue using HTTP API"""
    prompt_data = {
        "prompt": workflow,
        "client_id": client_id
    }
    
    # Update prompt_data with all parameters from prompt_ext_params
    if prompt_ext_params:
        prompt_data.update(prompt_ext_params)
    
    json_data = json.dumps(prompt_data)
    
    # Use aiohttp to send request with dynamic server URL
    internal_url = _get_internal_api_url(request)
    async with aiohttp.ClientSession() as session:
        async with session.post(
                f"{internal_url}/prompt", 
                data=json_data,
                headers={"Content-Type": "application/json"}
            ) as response:
            if response.status != 200:
                response_text = await response.text()
                raise Exception(f"Failed to submit workflow: [{response.status}] {response_text}")
            
            result = await response.json()
            prompt_id = result.get("prompt_id")
            if not prompt_id:
                raise Exception(f"Failed to get prompt_id: {result}")
            print(f"Task submitted: {prompt_id}")
            return prompt_id

async def _get_base_url(request):
    """
    Get base URL for building image URLs
    
    Args:
        request: HTTP request object
        
    Returns:
        Base URL string
    """
    # Default base URL for local access
    base_url = "http://127.0.0.1:8188"
    
    if request:
        host = request.headers.get('Host')
        if host:
            # Try multiple methods to get request protocol
            scheme = request.headers.get('X-Forwarded-Proto') or \
                     request.headers.get('X-Scheme') or \
                     request.headers.get('X-Forwarded-Scheme') or \
                     (request.headers.get('X-Forwarded-Ssl') == 'on' and 'https') or \
                     (request.headers.get('X-Forwarded-Protocol') == 'https' and 'https') or \
                     ('https' if request.url.scheme == 'https' else 'http')
            
            # Build base URL
            base_url = f"{scheme}://{host}"
    
    return base_url

def _get_internal_api_url(request):
    """
    获取 ComfyUI 内部 API 的基础 URL。
    优先从当前运行的服务器实例中获取端口，避免硬编码。
    """
    # 尝试从 PromptServer 获取端口配置
    port = getattr(prompt_server.instance, "port", 8188)
    if not port and hasattr(prompt_server.instance, "app"):
        # 兜底：从 aiohttp 配置中尝试
        pass
    
    internal_url = f"http://127.0.0.1:{port}"
    
    # 如果有 request 且 Host 头可用，可以更精准地定位（主要用于反向代理透传）
    if request:
        host = request.headers.get('Host')
        if host:
            # 内部分发请求始终建议用 http
            # 如果 Host 包含端口则直接使用，否则拼接默认
            if ':' in host:
                internal_url = f"http://{host}"
            else:
                internal_url = f"http://{host}" # 保持原样，aiohttp 会处理或者已含端口
    
    return internal_url

# Helper: map outputs by variable name
def _map_outputs_by_var(output_id_2_var, output_id_2_media):
    """
    Map outputs by variable name using output_id_2_var mapping.
    Args:
        output_id_2_var: Dict mapping node_id to variable name (for nodes with explicit markers)
        output_id_2_media: Dict mapping node_id to list of URLs/data
    Returns:
        Dict mapping variable name to list of URLs/data
    """
    result = {}
    for node_id, media_data in output_id_2_media.items():
        # Use explicit variable name if available, otherwise use node_id
        var_name = output_id_2_var.get(node_id, str(node_id))
        result[var_name] = media_data
    return result

# Helper: flatten all lists in a dict into a single list
def _extend_flat_list_from_dict(media_dict):
    """
    Flatten all lists in a dict into a single list.
    Args:
        media_dict: Dict of lists
    Returns:
        Flat list of all items
    """
    flat = []
    for items in media_dict.values():
        flat.extend(items)
    return flat

def _split_media_by_suffix(node_output, base_url):
    """
    Split all media entries in node_output into images/videos/audios by file extension.
    Args:
        node_output: Output dict for a node
        base_url: Base URL for constructing file URLs
    Returns:
        (images: list, videos: list, audios: list) - lists of URLs
    """
    image_exts = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff'}
    video_exts = {'.mp4', '.mov', '.avi', '.webm', '.gif'}
    audio_exts = {'.mp3', '.wav', '.flac', '.ogg', '.aac', '.m4a', '.wma', '.opus'}
    images = []
    videos = []
    audios = []
    for media_key in ("images", "gifs", "audio"):
        for media_data in node_output.get(media_key, []):
            if (isinstance(media_data, list) or isinstance(media_data, tuple)) and len(media_data) == 2:
                subfolder = ""
                filename, media_type = media_data
            elif isinstance(media_data, dict):
                filename = media_data.get("filename")
                subfolder = media_data.get("subfolder", "")
                media_type = media_data.get("type", "output")
            else:
                print(f"Invalid media data: {media_key} | {media_data}")
                continue
            
            url = f"{base_url}/view?filename={filename}"
            if subfolder:
                url += f"&subfolder={subfolder}"
            if media_type:
                url += f"&type={media_type}"
            ext = os.path.splitext(filename)[1].lower()
            if ext in image_exts:
                images.append(url)
            elif ext in video_exts:
                videos.append(url)
            elif ext in audio_exts:
                audios.append(url)
    return images, videos, audios

async def _wait_for_results(prompt_id, timeout=None, request=None, output_id_2_var=None):
    """Wait for workflow execution results, get history using HTTP API"""
    start_time = time.time()
    result = {
        "status": "processing",
        "prompt_id": prompt_id,
        "images": [],
        "images_by_var": {},
        "videos": [],
        "videos_by_var": {},
        "audios": [],
        "audios_by_var": {},
        "texts": [],
        "texts_by_var": {}
    }

    # Get base URL for image/video URLs
    base_url = await _get_base_url(request)

    while True:
        # Check timeout
        if timeout is not None and timeout > 0:
            duration = time.time() - start_time
            if duration > timeout:
                print(f"Timeout: {duration} seconds")
                result["duration"] = duration
                result["status"] = "timeout"
                return result

        # Get history using HTTP API with dynamic server URL
        internal_url = _get_internal_api_url(request)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{internal_url}/history/{prompt_id}") as response:
                if response.status != 200:
                    await asyncio.sleep(1.0)
                    continue
                history_data = await response.json()
                if prompt_id not in history_data:
                    await asyncio.sleep(1.0)
                    continue
                
                prompt_history = history_data[prompt_id]
                status = prompt_history.get("status")
                if status and status.get("status_str") == "error":
                    result["status"] = "error"
                    messages = status.get("messages")
                    if messages:
                        errors = [
                            body.get("exception_message")
                            for type, body in messages
                            if type == "execution_error"
                        ]
                        error_message = "\n".join(errors)
                    else:
                        error_message = "Unknown error"
                    result["error"] = error_message
                    return result
                
                if "outputs" in prompt_history:
                    result["outputs"] = prompt_history["outputs"]
                    result["status"] = "completed"

                    # Collect all image, video, audio and text outputs by file extension
                    output_id_2_images = {}
                    output_id_2_videos = {}
                    output_id_2_audios = {}
                    output_id_2_texts = {}
                    for node_id, node_output in prompt_history["outputs"].items():
                        images, videos, audios = _split_media_by_suffix(node_output, base_url)
                        if images:
                            output_id_2_images[node_id] = images
                        if videos:
                            output_id_2_videos[node_id] = videos
                        if audios:
                            output_id_2_audios[node_id] = audios
                        # Collect text outputs
                        if "text" in node_output:
                            # Handle text field as string or list
                            texts = node_output["text"]
                            if isinstance(texts, str):
                                texts = [texts]
                            elif not isinstance(texts, list):
                                texts = [str(texts)]
                            output_id_2_texts[node_id] = texts

                    # Map by variable name if mapping is available
                    if output_id_2_images:
                        result["images_by_var"] = _map_outputs_by_var(output_id_2_var, output_id_2_images)
                        result["images"] = _extend_flat_list_from_dict(result["images_by_var"])

                    if output_id_2_videos:
                        result["videos_by_var"] = _map_outputs_by_var(output_id_2_var, output_id_2_videos)
                        result["videos"] = _extend_flat_list_from_dict(result["videos_by_var"])

                    if output_id_2_audios:
                        result["audios_by_var"] = _map_outputs_by_var(output_id_2_var, output_id_2_audios)
                        result["audios"] = _extend_flat_list_from_dict(result["audios_by_var"])

                    # Handle texts/texts_by_var
                    if output_id_2_texts:
                        result["texts_by_var"] = _map_outputs_by_var(output_id_2_var, output_id_2_texts)
                        result["texts"] = _extend_flat_list_from_dict(result["texts_by_var"])

                    # Remove empty fields for images/videos/audios/texts
                    if not result.get("images"):
                        result.pop("images", None)
                    if not result.get("images_by_var"):
                        result.pop("images_by_var", None)
                    if not result.get("videos"):
                        result.pop("videos", None)
                    if not result.get("videos_by_var"):
                        result.pop("videos_by_var", None)
                    if not result.get("audios"):
                        result.pop("audios", None)
                    if not result.get("audios_by_var"):
                        result.pop("audios_by_var", None)
                    if not result.get("texts"):
                        result.pop("texts", None)
                    if not result.get("texts_by_var"):
                        result.pop("texts_by_var", None)

                    return result
        await asyncio.sleep(1.0)

# New: Load workflow from local file
def _load_workflow_from_local(filename, request=None):
    """
    Load workflow JSON from user's workflow directory or plugin's workflows directory
    """
    name_with_json = filename if filename.endswith('.json') else f'{filename}.json'
    
    # 1. Try user directory (Priority 1)
    if request and prompt_server.user_manager:
        relative_path = f'{API_WORKFLOWS_DIR}/{name_with_json}'
        user_workflow_path = prompt_server.user_manager.get_request_user_filepath(request, relative_path, create_dir=False)
        if user_workflow_path and os.path.isfile(user_workflow_path):
            with open(user_workflow_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    
    # 2. Try plugin directory (Priority 2)
    plugin_workflows_dir = os.path.join(os.path.dirname(__file__), 'workflows')
    plugin_workflow_path = os.path.join(plugin_workflows_dir, name_with_json)
    if os.path.isfile(plugin_workflow_path):
        with open(plugin_workflow_path, 'r', encoding='utf-8') as f:
            return json.load(f)
            
    # 3. Fallback to path_workflows (original global folder)
    fallback_path = os.path.join(path_workflows, name_with_json)
    if os.path.isfile(fallback_path):
        with open(fallback_path, 'r', encoding='utf-8') as f:
            return json.load(f)
            
    raise Exception(f"Workflow file not found: {filename} (searched in user dir, plugin dir, and fallback dir)")

# New: Load workflow from URL
async def _load_workflow_from_url(url):
    """
    Download workflow JSON from URL
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"Failed to download workflow: HTTP {response.status}")
            text = await response.text()
            try:
                return json.loads(text)
            except Exception as e:
                raise Exception(f"Invalid workflow JSON from url: {e}")

print("ComfyUI-OneAPI-Swagger routes registered")