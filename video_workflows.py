import math
import secrets
from pathlib import PurePosixPath, PureWindowsPath


PROFILES = {
    "base20": {"steps": 20, "lora": None, "attention": "default"},
    "light4": {"steps": 4, "lora": "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors", "attention": "default"},
    "fast4": {"steps": 4, "lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", "attention": "default"},
    "sol4": {"steps": 4, "lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", "attention": "cute"},
    "triton4": {"steps": 4, "lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", "attention": "triton"},
    "quality8": {"steps": 8, "lora": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "attention": "default"},
    "balanced8": {"steps": 8, "lora": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "attention": "triton"},
    "vdn4": {"steps": 4, "lora": None, "attention": "vdn"},
}
MODES = ("t2v", "i2v", "fl2v", "ref2v", "audio2v", "postprocess", "refine")
DEFAULT_VIDEO_SETTINGS = {
    "profile": "triton4", "seconds": 5, "width": 1344, "height": 768,
    "draft_width": 896, "draft_height": 512, "refiner": True,
    "fast_decode": True, "refiner_decoder": "native", "refiner_tile_size": 768,
    "save_draft": False,
}


def upload_name(value):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("Expected a local uploaded filename")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or ".." in PurePosixPath(value).parts or value.endswith(("[input]", "[output]", "[temp]")):
        raise ValueError("Uploaded filenames must stay inside the current user's input folder")
    return value


def build_workflow(data):
    mode = data.get("mode", "t2v")
    if mode not in MODES:
        raise ValueError("Unknown mode: " + str(mode))
    unknown = set(data) - {"mode", "profile", "prompt", "seconds", "width", "height", "seed", "postprocess", "input_fps", "input_video", "first_frame", "last_frame", "drive_audio", "ref_images", "ref_videos", "ref_audios", "audio_mode", "audio_denoise_strength", "ref_image_size", "reference_video_audio", "fast_decode", "refiner", "draft_width", "draft_height", "fused_modulation", "refiner_decoder", "refiner_tile_size", "save_draft"}
    if unknown:
        raise ValueError("Unknown request fields: " + ", ".join(sorted(unknown)))
    defaults = dict(DEFAULT_VIDEO_SETTINGS)
    for dimension in ("width", "height"):
        if dimension in data:
            defaults.pop("draft_" + dimension)
    if mode in ("postprocess", "refine"):
        defaults["refiner"] = False
        defaults["fast_decode"] = False
    elif data.get("refiner") is False:
        defaults["fast_decode"] = False
    data = {**defaults, **data}
    graph = {}

    def node(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    fps = 24.0
    seconds = float(data["seconds"])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be a positive finite number")
    frames = math.ceil(seconds * fps)
    post = data.get("postprocess", {})
    if not isinstance(post, dict):
        raise ValueError("postprocess must be an object")
    for key in ("ref_images", "ref_videos", "ref_audios"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(key + " must be a list of uploaded filenames")
    if mode in ("postprocess", "refine"):
        fps = float(data.get("input_fps", 24.0))
        video = data.get("input_video")
        if not video:
            raise ValueError(mode + " requires input_video from /upload/image")
        node("source", "VHS_LoadVideo", video=upload_name(video), force_rate=fps, custom_width=0, custom_height=0,
             frame_load_cap=math.ceil(seconds * fps), skip_first_frames=0, select_every_nth=1, format="None")
        images, audio = ["source", 0], ["source", 2]
    else:
        profile_name = data["profile"]
        if profile_name not in PROFILES:
            raise ValueError("Unknown profile: " + str(profile_name))
        profile = PROFILES[profile_name]
        references = any(data.get(k) for k in ("ref_images", "ref_videos", "ref_audios")) or mode == "ref2v" or (data.get("drive_audio") and data.get("audio_mode") == "reference_only")
        family = "ref2va" if references else "fl2va"
        full = profile["attention"] == "vdn"
        model = node("model", "UNETLoader", unet_name=f"minimax_h3_{family}{'' if full else '_pruned'}_int8_convrot.safetensors", weight_dtype="default")
        node("clip", "CLIPLoader", clip_name="qwen3vl_32b_minimax_h3_int8_convrot.safetensors", type="minimax", device="default")
        node("vae", "VAELoader", vae_name="minimax_h3_video_vae_fp16.safetensors")
        node("audio_vae", "VAELoader", vae_name="minimax_h3_audio_vae_fp32.safetensors")
        steps = profile["steps"]
        if profile["lora"]:
            if references and steps == 8:
                raise ValueError("8-step profiles use an FL2V LoRA; use fast4, sol4 or base20 for references")
            lora = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" if references else profile["lora"]
            model = node("lora", "LoraLoaderModelOnly", model=model, lora_name=lora, strength_model=1.0)
        if profile["attention"] == "cute":
            model = node("attention", "SolAttnH3", model=model, enabled=True, tau=1.0, thresh_type="diag",
                         first_dense_steps=0.2, first_dense_layers=2, sink_mode="prefix", correctness_gate=True, strict=True, kv_splits=1)
        elif profile["attention"] == "triton":
            model = node("attention", "MiniMaxH3MemoryEfficientSolAttentionPatch", model=model, enabled=True,
                         tau=1.0, min_tokens=4096, strict=True, thresh_type="diag", int8_qk=False,
                         int8_pv=False, sink_conditioning="exact_kv", dense_blocks="0-2,-1")
        elif full:
            model = node("attention", "VDNH3Accelerator", model=model, stage="stage-dmd-step-250", strength=1.0, head_chunk=8)
        if data.get("fused_modulation"):
            model = node("fusion", "MiniMaxH3FusedModulation", model=model, enabled=True)
        condition = dict(clip=["clip", 0], video_vae=["vae", 0], audio_vae=["audio_vae", 0],
                         prompt=data.get("prompt", ""), width=(int(data.get("draft_width", math.ceil(int(data["width"]) / 64) * 32)) if data.get("refiner") else int(data["width"])),
                         height=(int(data.get("draft_height", math.ceil(int(data["height"]) / 64) * 32)) if data.get("refiner") else int(data["height"])),
                         length=frames + (5 - frames) % 17, task_type="auto", audio_mode=data.get("audio_mode", "lock_source" if data.get("drive_audio") else "native"),
                         audio_denoise_strength=float(data.get("audio_denoise_strength", 0.35)),
                         add_source_as_reference=bool(data.get("drive_audio")) and not (data.get("first_frame") or data.get("last_frame")), prompt_primary_audio_ordinal=0,
                         strict_prompt_tags=False, ref_image_size=data.get("ref_image_size", "match"), reference_video_policy="model_minimum")
        if mode in ("i2v", "fl2v") and not data.get("first_frame"):
            raise ValueError(mode + " requires first_frame from /upload/image")
        if mode == "fl2v" and not data.get("last_frame"):
            raise ValueError("fl2v requires last_frame from /upload/image")
        if mode == "audio2v" and not data.get("drive_audio"):
            raise ValueError("audio2v requires drive_audio from /upload/image")
        for key in ("first_frame", "last_frame"):
            if data.get(key):
                condition[key] = node(key, "LoadImage", image=upload_name(data[key]))
        if data.get("drive_audio"):
            condition["drive_audio"] = node("drive_audio", "LoadAudio", audio=upload_name(data["drive_audio"]))
        for i, image in enumerate(data.get("ref_images", [])):
            condition[f"ref_images.ref_image_{i}"] = node(f"ref_image_{i}", "LoadImage", image=upload_name(image))
        for i, audio_file in enumerate(data.get("ref_audios", [])):
            condition[f"ref_audios.ref_audio_{i}"] = node(f"ref_audio_{i}", "LoadAudio", audio=upload_name(audio_file))
        for i, video in enumerate(data.get("ref_videos", [])):
            node(f"ref_video_{i}", "VHS_LoadVideo", video=upload_name(video), force_rate=24.0, custom_width=0,
                 custom_height=0, frame_load_cap=frames, skip_first_frames=0, select_every_nth=1, format="None")
            condition[f"ref_videos.ref_video_{i}"] = [f"ref_video_{i}", 0]
            if data.get("reference_video_audio", False):
                condition[f"ref_video_audios.ref_video_audio_{i}"] = [f"ref_video_{i}", 2]
        native_guides = references and (data.get("first_frame") or data.get("last_frame"))
        latent, positive = ["condition", 1], ["condition", 0]
        if native_guides:
            ref_condition = {key: condition[key] for key in ("clip", "audio_vae", "prompt", "width", "height", "length", "ref_image_size")}
            ref_condition["vae"] = ["vae", 0]
            ref_condition.update({key: value for key, value in condition.items() if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios."))})
            if data.get("drive_audio") and condition["audio_mode"] == "reference_only":
                ref_condition[f"ref_audios.ref_audio_{len(data.get('ref_audios', []))}"] = ["drive_audio", 0]
            node("condition", "MiniMaxH3ReferenceToVideo", **ref_condition)
            for key, index in (("first_frame", 0), ("last_frame", -1)):
                if key in condition:
                    positive = node("guide_" + key, "MiniMaxH3AddGuide", positive=positive, latent=latent, vae=["vae", 0], image=condition[key], frame_idx=index)
            if data.get("drive_audio") and condition["audio_mode"] in ("lock_source", "remix_source"):
                latent = node("audio_control", "MiniMaxH3AudioLatentControlT8", av_latent=latent, source_audio=["drive_audio", 0],
                              audio_vae=["audio_vae", 0], mode="lock" if condition["audio_mode"] == "lock_source" else "remix", strength=condition["audio_denoise_strength"])
        else:
            node("condition", "MiniMaxH3AudioConditioningT8", **condition)
        node("clock", "MiniMaxH3DualClockSamplerT8", model=model, av_latent=latent, steps=steps, shift_video=12.0, shift_audio=3.0)
        node("guider", "BasicGuider", model=["clock", 0], conditioning=positive)
        node("noise", "RandomNoise", noise_seed=int(data.get("seed", secrets.randbits(53))))
        node("sample", "SamplerCustomAdvanced", noise=["noise", 0], guider=["guider", 0], sampler=["clock", 1], sigmas=["clock", 2], latent_image=latent)
        decode_vae = node("decode_vae", "VAELoader", vae_name="taeh3.safetensors") if data.get("fast_decode") else ["vae", 0]
        node("decode", "MiniMaxH3AVDecodeT8", av_latent=["sample", 0], video_vae=decode_vae, audio_vae=["audio_vae", 0])
        audio = (["drive_audio", 0] if native_guides else ["condition", 2]) if data.get("drive_audio") and condition["audio_mode"] == "lock_source" else ["decode", 1]
        node("trim", "MiniMaxH3OutputTrimT8", frames=["decode", 0], audio=audio, start_seconds=0.0, duration_seconds=seconds, fps=fps)
        images, audio = ["trim", 0], ["trim", 1]
    if data.get("refiner") and data["save_draft"] and mode not in ("postprocess", "refine"):
        node("draft_video", "CreateVideo", images=images, audio=audio, fps=fps, color_space="sRGB", bit_depth=8)
        node("draft_save", "SaveVideo", video=["draft_video", 0], filename_prefix="local_video/h3_draft_" + secrets.token_hex(8),
             format="mp4", **{"format.codec": "h264", "format.codec.encoding": "re-encode", "format.codec.encoding.crf": 18.0})
    if data.get("refiner") or mode == "refine":
        width, height = int(data["width"]), int(data["height"])
        images = node("refine_resize", "ImageScale", image=images, upscale_method="bicubic",
                      width=math.ceil(width / 64) * 32, height=math.ceil(height / 64) * 32, crop="disabled")
        padding = (1 - frames) % 8
        if padding:
            last = node("refine_last", "ImageFromBatch", image=images, batch_index=-1, length=1)
            tail = node("refine_padding", "RepeatImageBatch", image=last, amount=padding)
            images = node("refine_frames", "ImageBatch", image1=images, image2=tail)
        node("refine_vae", "VAELoader", vae_name="sol-refiner-ltx-2.5-h3-video-vae-bf16.safetensors")
        node("refine_encode", "VAEEncodeTiled", pixels=images, vae=["refine_vae", 0], tile_size=512, overlap=64, temporal_size=128, temporal_overlap=8)
        node("refine_upscaler", "LatentUpscaleModelLoader", model_name="ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors")
        node("refine_upscale", "LTXVLatentUpsampler", samples=["refine_encode", 0], upscale_model=["refine_upscaler", 0], vae=["refine_vae", 0])
        node("refine_model", "UNETLoader", unet_name="sol-refiner-ltx-2.5-h3-transformer-comfy-int8-convrot.safetensors", weight_dtype="default")
        node("refine_clip", "CLIPLoader", clip_name="gemma4-12b-with-proj-sol-refiner-ltx-2.5-h3-comfy-int8-convrot.safetensors", type="ltxv", device="default")
        node("refine_text", "CLIPTextEncode", text=data.get("prompt", ""), clip=["refine_clip", 0])
        node("refine_condition", "LTXVConditioning", positive=["refine_text", 0], negative=["refine_text", 0], frame_rate=fps)
        node("refine_guider", "BasicGuider", model=["refine_model", 0], conditioning=["refine_condition", 0])
        node("refine_noise", "RandomNoise", noise_seed=int(data.get("seed", secrets.randbits(53))))
        node("refine_sampler", "KSamplerSelect", sampler_name="euler")
        node("refine_sigmas", "ManualSigmas", sigmas="0.909375, 0")
        node("refine_sample", "SamplerCustomAdvanced", noise=["refine_noise", 0], guider=["refine_guider", 0], sampler=["refine_sampler", 0], sigmas=["refine_sigmas", 0], latent_image=["refine_upscale", 0])
        decoder = data["refiner_decoder"]
        if decoder == "tae":
            node("refine_decode_vae", "VAELoader", vae_name="taeltx2_3.safetensors")
            node("refine_decode", "VAEDecode", samples=["refine_sample", 0], vae=["refine_decode_vae", 0])
        elif decoder == "native":
            node("refine_decode", "VAEDecodeTiled", samples=["refine_sample", 0], vae=["refine_vae", 0], tile_size=int(data["refiner_tile_size"]), overlap=64, temporal_size=64, temporal_overlap=8)
        else:
            raise ValueError("Unknown refiner_decoder: " + str(decoder))
        images = node("refine_trim", "ImageFromBatch", image=["refine_decode", 0], batch_index=0, length=frames)
        if width % 64 or height % 64:
            images = node("refine_output_size", "ImageScale", image=images, upscale_method="bicubic", width=width, height=height, crop="disabled")
    if post.get("upscale"):
        images = node("upscale", "RTXVideoSuperResolution", images=images, resize_type="scale by multiplier",
                      **{"resize_type.scale": float(post.get("scale", 2.0))}, quality=post.get("quality", "HIGHBITRATE_HIGH"), strength=1.0, image_encoding="8-bit RGB")
    if post.get("denoise"):
        images = node("denoise", "RTXVideoSuperResolution", images=images, resize_type="scale by multiplier",
                      **{"resize_type.scale": 1.0}, quality="DENOISE_HIGH", strength=1.0, image_encoding="8-bit RGB")
    if post.get("deblur"):
        images = node("deblur", "RTXVideoSuperResolution", images=images, resize_type="scale by multiplier",
                      **{"resize_type.scale": 1.0}, quality="DEBLUR_HIGH", strength=1.0, image_encoding="8-bit RGB")
    multiplier = int(post.get("fps_multiplier", 1))
    if multiplier > 1:
        last = node("last_image", "ImageFromBatch", image=images, batch_index=-1, length=1)
        images = node("interpolate", "RTXVideoFrameGeneration", images=images, generation_type="frame rate multiplier",
                      **{"generation_type.multiplier": multiplier}, mode="MEDIUM", automatic_shot_change_detection=True,
                      shot_change=False, image_encoding="8-bit RGB")
        tail = node("hold_last", "RepeatImageBatch", image=last, amount=multiplier - 1)
        images = node("complete_frames", "ImageBatch", image1=images, image2=tail)
        fps *= multiplier
    color_space = "sRGB"
    if post.get("hdr"):
        images = node("hdr", "RTXTrueHDR", images=images, contrast=100, saturation=100, middle_gray=50,
                      luminance=650, debanding=True, output_colorspace="HDR PQ")
        color_space = "HDR PQ"
    node("video", "CreateVideo", images=images, audio=audio, fps=fps, color_space=color_space, bit_depth=10 if post.get("hdr") else 8)
    node("save", "SaveVideo", video=["video", 0], filename_prefix="local_video/" + secrets.token_hex(8),
         format="mp4", **{"format.codec": "h264", "format.codec.encoding": "re-encode", "format.codec.encoding.crf": 18.0})
    return graph
