import asyncio
import json
import mimetypes
import uuid
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from comfy.cli_args import args
from server import PromptServer
from gpu_pool import GPU_POOL
from video_workflows import DEFAULT_VIDEO_SETTINGS, MODES, PROFILES, build_workflow
import video_comparison as comparison
from media_workflows import build_media_workflow
from image_inputs import prepare_image_settings


PREFIX = "/oneapi/v1/video"


async def core(request, path, data=None):
    headers = {k: request.headers[k] for k in ("Authorization", "Cookie", "Comfy-User") if k in request.headers}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        async with session.request("GET" if data is None else "POST", f"http://127.0.0.1:{args.port}{path}", json=data, headers=headers) as response:
            body = await response.json() if response.content_type == "application/json" else {}
            if response.status >= 400:
                if not body:
                    body = {"error": await response.text()}
                error = {400: web.HTTPBadRequest, 401: web.HTTPUnauthorized, 403: web.HTTPForbidden,
                         404: web.HTTPNotFound, 409: web.HTTPConflict, 500: web.HTTPInternalServerError}.get(response.status, web.HTTPBadGateway)
                raise error(text=json.dumps(body), content_type="application/json")
            return body


async def read_settings(request):
    try:
        settings = await asyncio.to_thread(json.loads, await request.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text='{"error":"Expected a JSON object"}', content_type="application/json")
    if not isinstance(settings, dict):
        raise web.HTTPBadRequest(text='{"error":"Expected a JSON object"}', content_type="application/json")
    return settings


def make_graph(settings, media=False):
    try:
        if media and not isinstance(settings.get("prompt_ext_params", {}), dict):
            raise ValueError("prompt_ext_params must be an object")
        graph = settings.get("workflow")
        if graph is None:
            graph = build_media_workflow({k: v for k, v in settings.items() if k != "prompt_ext_params"}) if media else build_workflow({k: v for k, v in settings.items() if k != "client_id"})
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(error)}), content_type="application/json")
    if not isinstance(graph, dict) or not graph:
        raise web.HTTPBadRequest(text='{"error":"workflow must be a nonempty API prompt graph"}', content_type="application/json")
    return graph


async def get_job(request, job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise web.HTTPNotFound()
    queue = await core(request, "/queue")
    history = await core(request, "/history/" + job_id)
    if job_id in history:
        entry = history[job_id]
        files = []
        for node_id, output in entry.get("outputs", {}).items():
            for kind in ("images", "videos", "gifs", "audio", "files"):
                for item in output.get(kind, []):
                    if isinstance(item, dict) and "filename" in item:
                        query = {key: item[key] for key in ("filename", "subfolder", "type") if key in item}
                        media_type = mimetypes.guess_type(item["filename"])[0] or "application/octet-stream"
                        media_kind = "videos" if media_type.startswith("video/") else kind
                        files.append({"node_id": node_id, "kind": media_kind, "media_type": media_type, **query, "url": "/view?" + urlencode(query)})
        state = entry.get("status", {})
        errors = [data for event, data in state.get("messages", []) if event in ("execution_error", "execution_interrupted")]
        timestamps = {event: data["timestamp"] for event, data in state.get("messages", []) if "timestamp" in data}
        elapsed = (timestamps["execution_success"] - timestamps["execution_start"]) / 1000 if "execution_success" in timestamps else None
        return web.json_response({"id": job_id, "status": "completed" if state.get("status_str") == "success" else "failed",
                                  "execution_seconds": elapsed, "gpu_pool": GPU_POOL.decision(job_id), "files": files, "texts": {node_id: output["text"] for node_id, output in entry.get("outputs", {}).items() if "text" in output}, "errors": errors, "history": entry})
    for field, state in (("queue_running", "running"), ("queue_pending", "queued")):
        if any(item[1] == job_id for item in queue.get(field, [])):
            return web.json_response({"id": job_id, "status": state, "gpu_pool": GPU_POOL.decision(job_id)})
    raise web.HTTPNotFound(text='{"error":"Task not found in this user session or history has expired"}', content_type="application/json")


async def upload_media(request):
    headers = {key: request.headers[key] for key in ("Authorization", "Cookie") if key in request.headers}
    form = aiohttp.FormData()
    for key, value in (await request.post()).items():
        if isinstance(value, web.FileField):
            form.add_field("image" if key == "file" else key, value.file, filename=value.filename, content_type=value.content_type)
        else:
            form.add_field(key, str(value))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        async with session.post(f"http://127.0.0.1:{args.port}/upload/image", data=form, headers=headers) as response:
            return web.Response(body=await response.read(), status=response.status, content_type=response.content_type)


def register_video_routes(queue_prompt):
    routes = PromptServer.instance.routes
    @routes.post("/oneapi/v1/upload")
    @routes.post(PREFIX + "/upload")
    async def upload(request):
        return await upload_media(request)

    @routes.get("/oneapi/v1/nodes")
    @routes.get(PREFIX + "/nodes")
    async def node_schemas(request):
        return web.json_response(await core(request, "/object_info"))

    @routes.get(PREFIX + "/capabilities")
    async def capabilities(request):
        info = await core(request, "/object_info")
        models = {}
        for key, node, field in (("diffusion_models", "UNETLoader", "unet_name"), ("text_encoders", "CLIPLoader", "clip_name"), ("vae", "VAELoader", "vae_name"), ("loras", "LoraLoaderModelOnly", "lora_name")):
            models[key] = info.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])[0]
        refiner_graph = build_workflow({"mode": "refine", "input_video": "example.mp4"})
        refiner_missing = sorted({v["class_type"] for v in refiner_graph.values()} - info.keys())
        for item in refiner_graph.values():
            for field, category in (("unet_name", "diffusion_models"), ("clip_name", "text_encoders"), ("vae_name", "vae")):
                if field in item["inputs"] and item["inputs"][field] not in models[category]:
                    refiner_missing.append(item["inputs"][field])
        upscalers = info.get("LatentUpscaleModelLoader", {}).get("input", {}).get("required", {}).get("model_name", [[]])
        upscalers = upscalers[1].get("options", []) if upscalers[0] == "COMBO" else upscalers[0]
        if refiner_graph["refine_upscaler"]["inputs"]["model_name"] not in upscalers:
            refiner_missing.append(refiner_graph["refine_upscaler"]["inputs"]["model_name"])
        profiles = {}
        for name, profile in PROFILES.items():
            graph = build_workflow({"profile": name, "refiner": False, "fast_decode": False, "seed": 0})
            missing = sorted({v["class_type"] for v in graph.values()} - info.keys())
            for node in graph.values():
                for key, category in (("unet_name", "diffusion_models"), ("clip_name", "text_encoders"), ("vae_name", "vae"), ("lora_name", "loras")):
                    value = node["inputs"].get(key)
                    if value and value not in models[category]:
                        missing.append(value)
            profiles[name] = {**profile, "available": not missing, "missing": missing}
        return web.json_response({"defaults": DEFAULT_VIDEO_SETTINGS, "refiner": {"available": not refiner_missing, "missing": refiner_missing, "name": "NVIDIA H3 SoL Refiner LTX-2.5", "steps": 1, "sigma": 0.909375, "quantization": "INT8 ConvRot", "audio": "preserve H3/source audio"}, "modes": MODES, "profiles": profiles, "models": models,
                                  "postprocess": {name: name in info for name in ("RTXVideoSuperResolution", "RTXVideoFrameGeneration", "RTXTrueHDR")},
                                  "nodes": sorted(name for name, schema in info.items() if not schema.get("api_node")),
                                  "availability_check": "registered_nodes_and_model_files", "node_schema_url": PREFIX + "/nodes", "execute_url": "/oneapi/v1/execute",
                                  "upload_url": PREFIX + "/upload", "workflows_url": PREFIX + "/workflows", "openapi_url": "/oneapi/openapi.json", "swagger_url": "/oneapi/docs"})


    @routes.get(PREFIX + "/workflows")
    async def workflows(request):
        return web.json_response({"defaults": DEFAULT_VIDEO_SETTINGS, "refiner_example": {"mode": "t2v", **DEFAULT_VIDEO_SETTINGS}, "profiles": PROFILES, "modes": MODES, "build_url": PREFIX + "/workflows/build",
                                  "description": "POST settings to build an editable API graph; import that JSON into ComfyUI, or submit it through /oneapi/v1/video/jobs."})


    @routes.post("/oneapi/v1/workflows/build")
    @routes.post(PREFIX + "/workflows/build")
    async def build(request):
        settings = await read_settings(request)
        if request.path == "/oneapi/v1/workflows/build":
            settings, _ = await prepare_image_settings(request, settings)
        return web.json_response(make_graph(settings, media=request.path == "/oneapi/v1/workflows/build"))


    @routes.post("/oneapi/v1/jobs")
    @routes.post(PREFIX + "/jobs")
    async def submit(request):
        settings = await read_settings(request)
        references = []
        if request.path == "/oneapi/v1/jobs":
            settings, references = await prepare_image_settings(request, settings)
        graph = make_graph(settings, media=request.path == "/oneapi/v1/jobs")
        client_id = settings.get("client_id", str(uuid.uuid4()))
        job_id = await queue_prompt(graph, client_id, prompt_ext_params=settings.get("prompt_ext_params") if request.path == "/oneapi/v1/jobs" else None, request=request)
        return web.json_response({"id": job_id, "client_id": client_id, "status": "queued", "status_url": ("/oneapi/v1" if request.path == "/oneapi/v1/jobs" else PREFIX) + "/jobs/" + job_id, "workflow": graph, "reference_map": references}, status=202)


    @routes.get("/oneapi/v1/jobs/{job_id}")
    @routes.get(PREFIX + "/jobs/{job_id}")
    async def status(request):
        return await get_job(request, request.match_info["job_id"])


    @routes.delete("/oneapi/v1/jobs/{job_id}")
    @routes.delete(PREFIX + "/jobs/{job_id}")
    async def cancel(request):
        job_id = request.match_info["job_id"]
        queue = await core(request, "/queue")
        if any(item[1] == job_id for item in queue.get("queue_running", [])):
            raise web.HTTPConflict(text='{"error":"Task is running; this endpoint only cancels pending tasks"}', content_type="application/json")
        if not any(item[1] == job_id for item in queue.get("queue_pending", [])):
            raise web.HTTPNotFound()
        await core(request, "/queue", {"delete": [job_id]})
        return web.json_response({"id": job_id, "status": "cancelled"})


    comparison.register(routes, PREFIX)


def video_openapi():
    fields = {
        "save_draft": {"type": "boolean", "default": DEFAULT_VIDEO_SETTINGS["save_draft"], "description": "Also encode and save the H3 draft MP4; default false saves encoding time without changing final pixels or audio"},
        "client_id": {"type": "string", "description": "Optional WebSocket clientId for native execution events"},
        "fused_modulation": {"type": "boolean", "default": False, "description": "H3 fused AdaLN/modulation, composable with attention and LoRA"},
        "refiner_decoder": {"type": "string", "enum": ["native", "tae"], "default": "native", "description": "TAE changes only final LTX decoding; approximate preview with softer detail. Original encoding/upscaling stay unchanged."},
        "refiner_tile_size": {"type": "integer", "default": DEFAULT_VIDEO_SETTINGS["refiner_tile_size"], "description": "Spatial tile size for native refiner decode; larger tiles may use more VRAM"},
        "mode": {"type": "string", "enum": list(MODES), "default": "t2v"},
        "profile": {"type": "string", "enum": list(PROFILES), "default": DEFAULT_VIDEO_SETTINGS["profile"]},
        "prompt": {"type": "string"}, "seconds": {"type": "number", "default": 5},
        "width": {"type": "integer", "default": DEFAULT_VIDEO_SETTINGS["width"]}, "height": {"type": "integer", "default": DEFAULT_VIDEO_SETTINGS["height"]},
        "draft_width": {"type": "integer", "description": "H3 stage-one width when refiner=true; default 896x512 for standard output; explicit output dimensions use half size on a 32-pixel grid unless draft dimensions are supplied"},
        "draft_height": {"type": "integer", "description": "H3 stage-one height when refiner=true; default 896x512 for standard output; explicit output dimensions use half size on a 32-pixel grid unless draft dimensions are supplied"},
        "seed": {"type": "integer"}, "input_fps": {"type": "number", "default": 24},
        "refiner": {"type": "boolean", "default": DEFAULT_VIDEO_SETTINGS["refiner"], "description": "Generate an H3 draft, then run public NVIDIA H3 SoL Refiner LTX-2.5 INT8 ConvRot (one step); width/height specify final size. mode=refine processes uploaded video only."},
        "fast_decode": {"type": "boolean", "default": DEFAULT_VIDEO_SETTINGS["fast_decode"], "description": "Decode the H3 draft with TAEH3; final LTX decoding remains native. With refiner=false this defaults to false."},
        **{key: {"type": "string", "description": "Local uploaded filename returned by /upload/image"} for key in ("first_frame", "last_frame", "drive_audio", "input_video")},
        **{key: {"type": "array", "items": {"type": "string"}} for key in ("ref_images", "ref_videos", "ref_audios")},
        "audio_mode": {"type": "string", "enum": ["native", "lock_source", "remix_source", "reference_only"]},
        "audio_denoise_strength": {"type": "number", "default": 0.35},
        "ref_image_size": {"type": "string", "enum": ["match", "max"]},
        "reference_video_audio": {"type": "boolean", "default": False},
        "postprocess": {"type": "object", "additionalProperties": False, "properties": {
            **{key: {"type": "boolean", "default": False} for key in ("upscale", "denoise", "deblur", "hdr")},
            "scale": {"type": "number", "default": 2}, "quality": {"type": "string", "default": "HIGHBITRATE_HIGH"},
            "fps_multiplier": {"type": "integer", "default": 1}}},
        "workflow": {"type": "object", "description": "Any native API prompt graph; supports every installed local node"},
    }
    paths = {}
    for path, method, summary in (("/comparison", "get", "List this user's saved benchmark videos, timing and acceleration metadata"), ("/capabilities", "get", "List installed nodes, models, profiles and postprocessing"),
                                  ("/workflows", "get", "List workflow builders"), ("/workflows/build", "post", "Build an editable native API graph"),
                                  ("/jobs", "post", "Submit video settings or any native API graph"),
                                  ("/jobs/{job_id}", "get", "Get task status, errors, history and output download URLs"),
                                  ("/jobs/{job_id}", "delete", "Cancel a pending task owned by the current user")):
        operation = {"summary": summary, "responses": {"202" if path == "/jobs" else "200": {"description": "Success"}, "400": {"description": "Invalid request or native graph validation failed"}, "401": {"description": "Login required"}, "404": {"description": "Task not found"}}}
        if "{job_id}" in path:
            operation["parameters"] = [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}]
        if method == "post":
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/VideoRequest"}}}}
        paths.setdefault(PREFIX + path, {})[method] = operation
    examples = {
        "default": {"summary": "默认平衡方案 · 768p/5秒", "value": {"prompt": "A woman waves in a sunlit kitchen.", "seed": 20261040}},
        "h3_sol_768p": {"summary": "明确指定平衡方案", "value": {**DEFAULT_VIDEO_SETTINGS, "seed": 20261040, "prompt": "A woman waves in a sunlit kitchen."}},
        "uploaded_refine": {"summary": "精修通过本插件上传的视频", "value": {"mode": "refine", "input_video": "uploaded.mp4", "width": 1344, "height": 768, "seconds": 5, "seed": 42, "prompt": "A woman waves in a sunlit kitchen."}},
        "nvidia_post": {"summary": "NVIDIA 插帧/去模糊", "value": {"mode": "postprocess", "input_video": "uploaded.mp4", "seconds": 5, "postprocess": {"fps_multiplier": 2, "deblur": True}}},
    }
    for path in ("/jobs", "/workflows/build"):
        paths[PREFIX + path]["post"]["requestBody"]["content"]["application/json"]["examples"] = examples
    paths[PREFIX + "/jobs"]["post"]["responses"]["202"]["content"] = {"application/json": {"example": {"id": "prompt-uuid", "client_id": "client-uuid", "status": "queued", "status_url": PREFIX + "/jobs/prompt-uuid", "workflow": {}}}}
    paths[PREFIX + "/comparison/{kind}/{record_id}"] = {"get": {"summary": "下载本用户的测试视频、缩略图或工作流", "parameters": [{"name": "kind", "in": "path", "required": True, "schema": {"type": "string", "enum": ["media", "poster", "workflow"]}}, {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": {"200": {"description": "Local media or workflow"}, "206": {"description": "Partial content for video playback"}, "401": {"description": "Login required"}, "404": {"description": "Unknown record"}}}}
    paths[PREFIX + "/nodes"] = {"get": {"summary": "全部本地节点输入输出 Schema", "responses": {"200": {"description": "Native node schemas"}}}}
    paths[PREFIX + "/upload"] = {"post": {"summary": "上传当前用户的图像、视频或音频", "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {"type": "object", "required": ["image"], "properties": {"image": {"type": "string", "format": "binary"}, "overwrite": {"type": "string", "enum": ["true", "false"], "default": "false"}}}}}}, "responses": {"200": {"description": "Uploaded filename, subfolder and type"}, "400": {"description": "Invalid upload"}}}}
    return {"openapi": "3.0.3", "info": {"title": "Local ComfyUI Video API", "version": "1.1.0"},
                              "servers": [{"url": "/"}], "security": [{"bearerAuth": []}], "paths": paths,
                              "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
                                             "schemas": {"VideoRequest": {"type": "object", "additionalProperties": False, "properties": fields}}}}
