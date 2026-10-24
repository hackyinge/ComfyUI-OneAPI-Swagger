# 本地媒体部署

插件复用现有 ComfyUI 端口和队列，不创建新的 HTTP 推理服务。文档中的 `127.0.0.1:8788` 是可选反向代理示例；直连时请使用自己的 ComfyUI 地址。默认模型与尺寸见 `/oneapi/v1/capabilities`，模型下载由部署者显式完成，插件不自动下载权重。

## 模型与依赖

需要包含 `TextEncodeQwenImage21`、Qwen3 ASR 和 H3/SoL 支持的 ComfyUI。视频加速及后处理还依赖工作流引用的扩展节点；capabilities 分别报告节点/权重是否存在及是否有本机实测记录。缺失权重不会自动替换为其他模型。

Qwen 图像模型放在标准 `models/diffusion_models`、`models/text_encoders`、`models/vae` 目录，具体文件名见 `media_workflows.py`。音频模型放在 `models/oneapi_audio/`，目录名见 `local_audio.py`。

在 ComfyUI Python 环境按需安装 `requirements-local-audio.txt`。Qwen TTS SDK 需要独立的 `ComfyUI/venv-qwen-tts`：创建环境后，在其 site-packages 的 `comfy-runtime.pth` 中写入主环境 site-packages 的绝对路径以复用 torch，再用该环境的 `pip install --no-deps -r requirements-qwen-tts.txt` 安装固定版本。依赖其余部分由主环境提供；不要把 TTS 的 Transformers 4.57.3 覆盖到主环境。该隔离方式已在 Linux/Python 3.12 验证，私有 IPC/forkserver 路径未验证 Windows。

所有本地权重均离线加载。开源代码许可不替代模型权重自身的许可。

## 上传与显存

本机测试使用的启动参数：

```sh
python main.py --listen 0.0.0.0 --reserve-vram 1.5 --cache-ram 6 12 --max-upload-size 512
```

这些是部署配置，不由插件自动修改。GPU 池默认空闲 120 秒后回收模型；同模型连续任务可复用权重。显存预算是估计，实际分阶段加载仍由 ComfyUI 管理。

9 张大图建议逐个 multipart 上传，再提交文件名组成的小 JSON。附加参考默认适配约 1MP 供推理；保存的上传原文件不变，`reference_megapixels:0` 可保留原像素。Base64 比原始字节增加约三分之一，512MiB 后端限制指整个 JSON 请求，不是单张图片。

已有 Nginx 反向代理可参考以下配置，保留原有认证、转发头及 WebSocket 配置：

```nginx
client_max_body_size 1g;
client_body_timeout 300s;
send_timeout 300s;
# 在已有 ComfyUI 代理 location 内设置：
proxy_request_buffering off;
proxy_buffering off;
proxy_send_timeout 900s;
proxy_read_timeout 900s;
```

最终上传上限是链路中最小值；其他代理也要检查。同步接口等待任务最多 600 秒，长队列使用 `/oneapi/v1/jobs` 轮询。SSE 等待时有心跳，整图完成后才返回图片。

## ComfyUI-Login 集成

对比页按登录插件提供的 `user_id` 隔离目录；目录为 `user/{image,audio,video}_comparison/{user_id}/catalog.json`。没有对应实测目录时返回 404，不随代码发布用户媒体、凭据或运行日志。未安装登录插件时，比较页和依赖用户标识的操作无法提供同等用户隔离；本插件自身不是认证服务。

部分 ComfyUI-Login 版本在事件循环里解析并清洗所有 JSON，120MiB 内联图片会造成健康检查超时。本机已在登录插件 `utils/sanitizer.py` 应用以下调整，保持原有认证及清洗规则。**该文件不属于本仓库，安装 OneAPI 不会自动应用此改动。**

```diff
+import asyncio
+import json
-                        data = await request.json()
+                        data = await asyncio.to_thread(json.loads, await request.read())
-                                sanitized_data[key] = self.sanitize_input(value)
+                                sanitized_data[key] = await asyncio.to_thread(self.sanitize_input, value)
```

## 验证

在安装了插件的 ComfyUI 环境，从 ComfyUI 根目录执行：

```sh
venv/bin/python -m unittest discover -s custom_nodes/ComfyUI-OneAPI-Swagger/tests
```

当前 57 项单元测试覆盖工作流、多图顺序和绑定、Turbo sigma、GPU 容量调度。真实模型测试条件与局限见统一 API 文档；模型权重、对比目录和测试图片需单独准备，不随 Git 提交。
