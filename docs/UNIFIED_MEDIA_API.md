# OneAPI 统一图像、视频与语音接口

入口：<http://127.0.0.1:8788/oneapi/docs>。使用现有ComfyUI登录Cookie或Bearer token。图片、视频、TTS、ASR复用同一原生队列、用户隔离和模型管理；没有新建独立推理服务。原有接口继续保留。

## 通用接口

| 方法 | 路径 | 内容 |
| --- | --- | --- |
| GET | `/oneapi/v1/capabilities` | 本机真实模型/节点清单、缺失项、已实测状态、本地和云端区别 |
| GET | `/oneapi/v1/nodes` | 全部已注册节点输入输出Schema |
| POST | `/oneapi/v1/upload` | multipart `image`或`file`字段上传图片、视频、音频 |
| POST | `/oneapi/v1/workflows/build` | 按task构建原生API图，不执行 |
| POST | `/oneapi/v1/jobs` | 异步提交，202返回ID、状态地址及完整图 |
| GET | `/oneapi/v1/jobs/{id}` | queued/running/completed/failed、秒数、文件URL、文本及history |
| DELETE | `/oneapi/v1/jobs/{id}` | 取消本用户待执行任务；运行中返回409 |

`task`为`image`、`video`、`tts`或`asr`。也可直接提交`{"workflow":{...}}`组合全部已注册节点，包含本机未设简化预设的模型、音频处理和图像操作。模型文件存在和节点注册不等于实测通过；capabilities分别报告available和tested。云端节点需相应Comfy服务凭据/额度，未自动发起付费调用。通用jobs接受与原有execute一致的prompt_ext_params，可在extra_data中提供服务所需凭据；不会回显到workflow。local_node_categories按分类列出全部本地节点。

### 文生图

```json
{"task":"image","model":"qwen-image-2.1-turbo","prompt":"一只橘猫坐在窗边，温暖的自然光，照片风格", "width":1024,"height":1024,"n":1,"seed":42}
```

图像预设为 `qwen-image-2.1-turbo`（默认，官方完整INT8 ConvRot checkpoint，8步/CFG1）与 `qwen-image-2.1`（普通25步/CFG1对照）。共用Qwen3-VL8B INT8文本编码器与Qwen2.1 VAE，复用GPU池与原生队列。追加input_image走原生参考图编辑，denoise默认1；n控制批量。

Turbo使用官方8步sigma轨迹，4/6步通过对该轨迹线性重采样覆盖完整去噪过程，是可选实验配置，并非官方独立4/6步模型。例如：

```json
{"task":"image","model":"qwen-image-2.1-turbo","prompt":"一张茶馆海报，标题写着春日茶会","steps":6,"width":1024,"height":1024,"seed":42}
```

生图对比：<http://127.0.0.1:8788/oneapi/v1/images/compare>。本轮统一1024×1024、同中文提示词和seed20261028，各模型先预热再实测，文字条件缓存保留，采样和解码真实执行。25步均值5.113秒，Turbo8步1.886秒、6步1.474秒、4步1.101秒。8步主体中文正确、细节较好，海报另有未要求的装饰印章；6步纹理略软，4步细节明显模糊，因此默认选官方8步。重启后的默认同步接口首条3.998秒，连续调用1.879秒；首条加载与预热后数据分开展示，系统文件与CUDA内核缓存保留。

显存每100ms采样，为上下文、权重及GPU池缓存的总占用。主对照25步峰值16.40GiB，Turbo主对照9.28GiB；默认接口冷/热调用峰值也可达16.40GiB，不能把该差异解释为Turbo本身节省固定显存。完整参数、sigma和缓存节点保留在逐条证据。

来源：[官方Comfy权重](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)、[官方Turbo说明](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo)。公开权重采用Qwen Research非商业研究/评估许可，商用需另行授权。完整Turbo checkpoint通过HF SHA256核对，未使用LoRA叠加作为默认路径。

Fibo2.0因中文文字效果不佳，已按用户要求移除专用权重、专用文本编码器、工作流与原生支持，释放12.356GiB；共用Flux2 VAE及历史测试样片保留。旧六个简化生图预设先前已退休。其他旧权重若被ControlNet、FaceID或编辑工作流共用则保留，避免破坏现有能力。清单见output/oneapi_qwen_turbo_20261024/fibo_removed.json。

OpenAI形式的同步入口：

```http
POST /oneapi/v1/images/generations
Content-Type: application/json
```

```json
{"model":"qwen-image-2.1-turbo","prompt":"一只橘猫坐在窗边","size":"1024x1024","n":1,"response_format":"url"}
```

返回`created`、`data:[{"url":"..."}]`、job_id、execution_seconds。`response_format:b64_json`返回base64。URL下载仍需要认证。原有`/v1/images/generations`与OpenAI/Gemini兼容接口保留。

### 多图参考与OpenAI图文混排

两个Qwen模型共用原生`TextEncodeQwenImage21`，当前安装节点提供16个参考输入槽；两版均已实测1、2、3、9张。16槽是当前节点接口容量，不是已验证的模型理论上限或显存保证。图越多、像素越多，编码和采样开销越高。

通用jobs、workflows/build、images/generations支持`input_images:["图1.png","图2.png"]`，也支持`[{"id":"person","image":"图1.png"},...]`。原有input_image排第一，随后为input_images，再按content出现顺序追加图片；n仍控制输出张数。

OpenAI客户端使用`base_url=http://127.0.0.1:8788/v1`，`GET /v1/models`列出两版模型；同样支持`/oneapi/v1/models`和`/oneapi/v1/chat/completions`。示例：

```json
{
  "model":"qwen-image-2.1-turbo",
  "size":"1024x1024",
  "stream":true,
  "messages":[{"role":"user","content":[
    {"type":"text","text":"这张作为基础海报，保留标题："},
    {"type":"image_url","id":"layout","image_url":{"url":"data:image/png;base64,..."}},
    {"type":"text","text":"使用这张的红色茶杯："},
    {"type":"image_url","id":"cup","image_url":{"url":"data:image/png;base64,..."}},
    {"type":"text","text":"以<ref:layout>为基础，用<ref:cup>替换杯子。"}
  ]}]
}
```

id是可选扩展，省略时按出现顺序生成image1/image2；普通OpenAI text+image_url无需扩展也可用。适配层保留文字顺序，生成明确参考编号，并将`<ref:cup>`转换为原生`<image2>`。`{"type":"image_ref","id":"cup"}`可再次指向已有参考，不重复上传或占槽。重复id、未知id、越界文件名在入队前返回400，绝不静默丢图。仅最后一条user消息作为本次输入，历史轮次需要的图片应重新附上。接口不猜测“这张图”在UI中的隐藏选择，应由客户端按实际图层顺序附图或使用id。

也可直接向images/generations、jobs和workflows/build传content或messages。响应的`reference_map`列出index/id/placeholder/实际上传文件名；同步图片另返回normalized_prompt。工作流构建接口保持返回原生图，绑定可从condition输入与提示词核对。命名原生工作流`local_qwen-image-2.1_multiref.json`、`local_qwen-image-2.1-turbo_multiref.json`提供image1/image2/image3/prompt/seed参数，其余数量经build动态生成。

chat响应为chat.completion，assistant.content含Markdown data:image图片，同时提供images扩展数组；oneapi包含job_id、execution_seconds、reference_map及normalized_prompt。stream=true使用SSE，等待期间每10秒发送注释心跳，整图生成后发图片chunk、stop、[DONE]，不是逐像素渐进输出。未用OpenAI SDK实测，已使用相同HTTP协议验证非流式与SSE。外部http(s)图片不自动抓取；应上传文件或传data:image，避免服务端代请求任意地址。

本轮全部成片1024²、配对seed20261028；25步版1/2/3/9参考为9.891/12.980/17.179/149.782秒，Turbo8步为2.867/4.691/6.176/43.347秒。缓存不同：Turbo复用前序图像条件，具体节点和GPU总显存见网页，不能把差值完全归因于步数。初始3图样片没有遵循木框要求，9图样片串入书店内容；明确按图文分段绑定角色后，3图与9图样片保留正确中文、红杯及木框。多图接口支持不等于模型保证遵循每一条要求。

### 大图上传与局域网代理

推荐先逐文件`POST /oneapi/v1/upload`，multipart字段file，再用返回文件名提交小JSON；客户端最多并发3个上传是本轮已测配置。chat image_url也接受已上传文件名（本地扩展），或通过顶层input_images附上传引用。原图保存不改字节，不强制JPEG或有损压缩。参考1按输出尺寸适配；其余默认reference_megapixels=1，保持比例适配约1MP供推理，reference_megapixels=0保留原像素、仅做原生32倍数适配；上传保存的文件不变。

8788代理client_max_body_size=1g、client_body_timeout=300s、proxy_request_buffering=off、proxy_send/read_timeout=900s；ComfyUI --max-upload-size=512MiB，实际有效上限以较小值为准。登录插件大JSON清洗与OneAPI JSON解析已移至工作线程；认证规则保持。同步任务等待上限仍600秒，长队列建议用jobs轮询，不要无限增大同步超时。

局域网地址8788实测：9张各10MiB文件，共90MiB，分文件并发3上传约0.435秒；同样图片编码成120.001MiB JSON，经workflows/build上传、保存并生成9图原生工作流用8.521秒，46次健康检查零超时、最慢0.345秒，进程未重启。首尾文件两端SHA256抽检一致。此组用有效PNG尾部填充凑足字节，测试传输体积，不代表大像素解码；测试在服务器通过自身局域网IP进行，不代表另一台电脑的网线/Wi-Fi实际带宽。

同样120MiB JSON经OpenAI SSE入口真实9图生成也通过：执行59.914秒、接口总等待81.989秒。上传时长、排队、模型执行与结果返回分开展示。未压力测试大量并发大JSON；生产调用优先用文件上传加小JSON，避免Base64额外约33%体积和多份内存拷贝。另用9张真实3072×3072 PNG（加入低幅度噪声，共148.90MiB）逐文件上传并真实生成，执行62.241秒、GPU总峰值21.03GiB；上传原图保留，入模适配约1MP。该次提示词输出了九宫格而非单海报，质量失败已标记。测试图片、原始绑定、失败样片及完整证据均在生图对比页。

### 本地TTS

```json
{"task":"tts","model":"qwen3-tts-1.7b","text":"你好，欢迎使用统一的本地语音服务。","voice":"Vivian","language":"Chinese","speed":1,"format":"flac"}
```

默认 Qwen3-TTS 1.7B / Vivian，支持中文及其他语言、9个预设音色和 instruct 语气指令；0.6B CustomVoice 提供低显存选项。Piper 保留 CPU 快速模式：huayan 中文、lessac 英文；旧请求只传这些音色名仍选择 Piper。speed 对 Qwen 音频做保音高变速，不减少生成 token 的计算量。异步任务保存 flac/mp3/opus，speech_details 保存加载/推理耗时、音频时长和峰值显存。

OpenAI形式的同步音频入口：

```http
POST /oneapi/v1/audio/speech
Content-Type: application/json
```

```json
{"model":"qwen3-tts-1.7b","input":"你好，欢迎使用本地语音服务。","voice":"Vivian","instruct":"用温暖自然的普通话讲述。","response_format":"wav"}
```

直接返回音频字节，支持wav/flac/mp3/opus。默认wav由原生保存的FLAC无损转封装/PCM编码得到；X-OneAPI-Job-ID和X-Execution-Seconds提供任务和耗时。

声音克隆先通过 `/oneapi/v1/upload` 上传参考音频，然后调用相同 speech 接口：

```json
{"model":"qwen3-tts-clone-0.6b","input":"这是使用参考音色说出的新句子。","reference_audio":"上传返回的文件名.wav","reference_text":"参考录音的准确原文。","language":"Chinese","response_format":"wav"}
```

仅音色向量模式设置 `x_vector_only:true`，可省略 reference_text；两种模式都已通过实测。参考必须是当前用户上传的本地音频名，不接受 URL、任意绝对路径或越界路径。本次使用合成 Vivian 音色作参考，未验证用户个人声音。

### 本地ASR

```json
{"task":"asr","input_audio":"uploaded.wav","model":"qwen3-asr-0.6b","language":"zh","word_timestamps":true,"prompt":"词汇：上海，小王。"}
```

默认 Qwen3-ASR 0.6B；1.7B 可显式选择。支持中文/方言、多语言和 prompt 上下文；word_timestamps 启用本地 Qwen3 ForcedAligner 0.6B。SenseVoice INT8 为 CPU 快速选项，支持中/粤/英/日/韩、标点和数字格式化、语言/情绪/事件标签。faster-whisper-small 保留 CPU INT8 模式。SenseVoice / Whisper 使用4线程；Qwen 的实际线程数写入每条识别元数据。

原生 LoadAudio 支持的音频/带音轨视频均可作为输入。返回文本、语言、分段和可选时间戳，保存 txt/json。Qwen/SenseVoice 采用独立30秒分窗，切点可能影响连续语句；窗口时间不是精确说话起止。SenseVoice 返回 CTC token 起点，end 用下一个起点估算；需要强制对齐请选择 Qwen。ASR 不提供说话人分离。Qwen/Whisper 可能输出繁体或中文数字，保留模型原文。

同步识别：`POST /oneapi/v1/audio/transcriptions`，multipart字段file、model、language、word_timestamps、response_format。model 默认为 qwen3-asr-0.6b，可选 qwen3-asr-1.7b、sensevoice-small-int8、faster-whisper-small，兼容 whisper-1 别名；prompt 仅适用于 Qwen 模型。response_format支持json、verbose_json、text、srt、vtt。

本轮中译英试验出现混合语言，未发布专用翻译接口；Whisper小模型识别仍可能有错字，需按实际语料复核。

### 视频

```json
{"task":"video","prompt":"A woman waves in a sunlit kitchen.","seconds":5}
```

使用已选择的H3→SoL Refiner默认方案，1344×768、24fps、5秒。原有`/oneapi/v1/video/...`接口保留。默认只保存成片，省去中间草稿MP4编码，成片像素与音轨不变；`save_draft:true`恢复草稿输出。

## 调用与工作流

`/jobs`适合异步任务，返回的status_url可轮询。同步接口等待同一队列，最长600秒；超时返回504及job_id，任务不自动取消，可继续查询。执行失败返回422及原生错误，禁止把部分输出当成成功交付。

图像、CPU语音及新增 local_tts_qwen.json、local_tts_clone.json、local_asr_qwen.json、local_asr_sensevoice.json 命名工作流在插件workflows目录，另导出到`user/default/workflows/OneAPI/`。可经原有`/oneapi/v1/execute`调用，例如：

```json
{"workflow":"local_qwen-image-2.1-turbo.json","params":{"prompt":"一只橘猫","seed":42},"wait_for_result":true}
```

`local_tts.json`的prompt参数映射为语音文本；`local_asr.json`的input_audio参数映射上传音频名。新的 Qwen TTS 工作流提供 prompt/voice/language/instruct/seed；克隆另提供 reference_audio/reference_text；新 ASR 工作流提供 input_audio/language。其他图像工作流提供prompt/seed，编辑工作流另提供input_image。媒体必须先上传，不接受任意绝对路径或跨用户文件。

## 本地模型与依赖

`requirements-local-audio.txt` 为语音扩展依赖。主环境保留 Transformers 5.14.1；Qwen3-TTS 使用独立 `venv-qwen-tts` 的 Transformers 4.57.3 / qwen-tts 0.1.1，复用主环境已有 torch，不安装 Gradio UI。创建环境后，在其 site-packages 的 comfy-runtime.pth 写入主环境 site-packages 绝对路径；使用 `pip install --no-deps -r requirements-qwen-tts.txt` 安装列出的 SDK 依赖，其他依赖从主环境提供。

模型位于 `models/oneapi_audio/`，加载始终设置 local_files_only。TTS 独立环境设置 HF_HUB_OFFLINE、TRANSFORMERS_OFFLINE 和 HF_HUB_DISABLE_TELEMETRY，不会运行时下载。TTS 通过私有 IPC 子进程隔离 SDK；ASR 在 ComfyUI 进程执行，没有新 HTTP 服务或端口。

Qwen TTS / ASR 默认 `device:"cuda", keep_loaded:true`：首次按需加载，后续复用同一模型权重，仍执行真实推理。TTS 和 ASR 各保留一个活动模型；ASR 仅在请求字级时间戳后额外保留对齐模型。切换该服务的模型或设备会替换旧权重，重启服务会释放全部。`model_cache_hit` 区分本次是否复用权重；`load_seconds`、`inference_seconds`、`device`、`dtype` 提供实际执行证据。

设置 `keep_loaded:false` 可逐次加载；`device:"cpu"` 使用同一 Qwen 模型的 FP32 推理，TTS 使用4线程。两个字段适用于通用 jobs、同步 speech JSON、transcriptions multipart，且仅对 Qwen 模型开放。Piper / Whisper / SenseVoice 保留各自 CPU 路径。Qwen 节点每次执行，不复用音频或识别结果缓存；输入音频解码等上游节点仍可缓存。

需要主动释放时，可调用 `POST /oneapi/v1/audio/unload`（相同认证，无必需参数）。它在同一原生队列执行，完成后返回 released=true；不会在语音推理中途释放权重。原生工作流也可执行 OneAPIReleaseSpeechModels 输出节点。显存不足时GPU池会自动回收，无需每次手动卸载；模型于下一条语音请求按需重新加载。复用权重会占用显存，详见对比页实际空闲 / 峰值总占用；CPU 对照只保留 ComfyUI 既有 CUDA 上下文，不额外放置模型到 GPU。

来源：[Piper Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md)、[Piper voices](https://huggingface.co/rhasspy/piper-voices)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[small转换权重](https://huggingface.co/Systran/faster-whisper-small)。保留各模型来源和许可证信息，具体语音样本及实测见本次验证记录。

语音试听/识别对比：<http://127.0.0.1:8788/oneapi/v1/audio/compare>，视频页侧栏已有入口。记录包含原始文本、模型、参数、时长、执行/加载/推理耗时和内容 CER；CER 不是主观音质评分。基础验证见 `output/oneapi_speech_20261024/REPORT.md`；本轮加载优化见 `output/oneapi_speech_speed_20261024/REPORT.md`。常驻 / CPU 对照见 `output/oneapi_speech_resident_20261024/`。对比页默认显示本轮GPU池试听与识别，保留上轮常驻、CPU、逐次加载及全部历史筛选。冷调用与连续调用均值单列；GPU BF16 与 CPU FP32 分别标注，试听和原始识别结果保留。

Qwen 来源：[TTS 官方仓库](https://github.com/QwenLM/Qwen3-TTS)、[ASR 原生 Transformers 模型](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf)；SenseVoice 来源：[sherpa 官方 Python API](https://k2-fsa.github.io/sherpa/onnx/sense-voice/python-api.html)。



## GPU池调度

默认 keep_loaded=true 允许同模型复用。任务在原生执行队列内检查实际空闲显存、待加载权重大小与工作区预算；容量充足保留其他模型，不足时按最近使用时间回收闲置 TTS、ASR 或原生图像/视频模型。预算是估计，分阶段加载仍由 ComfyUI 原生显存管理负责。CPU 请求不会触发 GPU 回收。

队列空闲120秒后释放GPU语音模型并将原生模型卸载到CPU。TTS的CPU库预加载进程保留，降低下一次加载成本。原生RAM缓存使用 --cache-ram 6 12，避免默认inactive阈值导致所有模型在任务切换时失效。显式 keep_loaded=false 仍逐次卸载；POST /oneapi/v1/audio/unload 是可选的主动释放接口。

GET /oneapi/v1/gpu-pool 返回实际空闲显存、驻留资源和当前用户调度记录；jobs结果中的gpu_pool包含本次预算、复用量与回收原因。语音对比页新增同文案/同音频的GPU池记录，视频对比页新增GPU池分类，全部5秒视频可同步播放。输出缓存命中不算推理加速。
