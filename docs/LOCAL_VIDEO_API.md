# OneAPI 本地视频

视频能力统一由本插件提供，复用 OneAPI 的 `_queue_prompt` 和 ComfyUI 原生执行队列。独立 LocalVideoAPI 插件已移除。原有 OneAPI 图片、OpenAI/Gemini 接口保留；所有 OneAPI 接口使用现有 ComfyUI-Login 登录认证及用户隔离。

- 同步对比：<http://127.0.0.1:8788/>
- Swagger：<http://127.0.0.1:8788/oneapi/docs>
- OpenAPI：`/oneapi/openapi.json`。Swagger 静态资源本地提供，无需 CDN。

## 默认快速预览与正式产出

只传prompt时使用896×512、24fps、5秒、H3/Turbo4步与原生H3解码，refiner=false，五项后处理全部关闭。新默认工作流为`h3_preview_512p.json`。不传seed、null或-1时每次随机，明确种子则可复现。

正式产出请显式传入分辨率和二阶段设置：

```json
{"prompt":"A woman waves in a sunlit kitchen.","width":1344,"height":768,
 "draft_width":896,"draft_height":512,"refiner":true,"fast_decode":true,"seconds":5}
```

该配置是此前验证的H3→NVIDIA SoL Refiner方案：4+1步、INT8 ConvRot、Triton SoL，TAEH3解码草稿，原生LTX精修解码。旧`h3_sol_default.json`、quality/preview768p模板保留原配置，不随新的快速API默认值改变。

超分、降噪、去模糊、插帧与HDR均由本次请求的postprocess对象控制，省略时全部关闭。插帧新增interpolate布尔开关；明确false覆盖残留fps_multiplier，旧的仅倍率请求仍兼容。

横竖屏、全部字段、Mac mini后端TypeScript示例、异步轮询和认证下载见 **[调用方接入指南](VIDEO_CLIENT_GUIDE.md)**。额外超分会乘以width/height；不能把测试低分辨率和正式高分辨率的耗时作为同画质加速对照。

## 接口

前缀 `/oneapi/v1/video`；可使用登录 Cookie 或 `Authorization: Bearer <token>`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/capabilities` | 实际默认值、节点、模型、加速档、后处理可用性 |
| GET | `/nodes` | 所有本地节点完整输入输出 Schema |
| GET | `/workflows` | 默认设置、模式和档位 |
| POST | `/workflows/build` | 返回原生API节点图，不排队 |
| POST | `/jobs` | 提交设置或 `{"workflow":{...}}` 任意本地节点图；返回202及任务ID |
| GET | `/jobs/{id}` | 状态、执行秒数、错误、history、输出下载URL |
| DELETE | `/jobs/{id}` | 取消本用户待执行任务；运行中返回409 |
| POST | `/upload` | multipart字段 `image`，上传图像、视频或音频 |
| GET | `/comparison` | 当前用户的所有测试视频、配置、视觉评估和默认选择依据 |
| GET | `/comparison/{kind}/{id}` | `media` / `poster` / `workflow` 文件；视频支持Range |

所有已安装本地节点可组合成API图经 `/jobs` 执行；节点存在不表示任意组合均已实测。入队前验证所有输出节点，防止只有草稿有效时被误报为成片成功。任务历史沿用原生内存存储，重启后过期；已保存视频、工作流、比较目录保留。

### 最小调用

```python
import json, time
from pathlib import Path
from urllib.request import Request, urlopen

base = "http://127.0.0.1:8788"
headers = {"Authorization": "Bearer " + Path("/run/comfyui-local-video.token").read_text().strip(),
           "Content-Type": "application/json"}
def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    return json.load(urlopen(Request(base + path, data, headers)))
job = request("/oneapi/v1/video/jobs", {"prompt":"A cat walks through a garden."})
while True:
    result = request(job["status_url"])
    if result["status"] in ("completed", "failed"):
        break
    time.sleep(1)
# 成片是 files 中 node_id="save" 的项；draft_save 是中间草稿。
# 下载 result 中相应 url 时仍携带认证。
```

已有命名工作流调用：

```json
{"workflow":"h3_sol_default.json","params":{"prompt":"A cat walks through a garden.","seed":42},"wait_for_result":true,"timeout":180}
```

提交至 `/oneapi/v1/execute`。正常登录 helper：`venv/bin/python script_examples/login_local_comfyui.py`。凭据以机器加密文件保存，仓库与工作流不保存明文密码。

## 模式和加速

`t2v` 文生、`i2v` 首帧（`first_frame`）、`fl2v` 首尾帧（加`last_frame`）、`ref2v` 参考媒体、`audio2v` 驱动音频（`drive_audio`）、`refine` 仅精修上传视频、`postprocess` 仅处理上传视频。后两者要求 `input_video`，不会隐式生成H3或自动精修后处理输入。

`ref_images`、`ref_videos`、`ref_audios` 为已上传文件名数组；参考提示词标签为 `<Picture 1>`、`<Video 1>`、`<Audio 1>`。参考输入自动使用Ref2VA与匹配LoRA。音频支持 `native`、`lock_source`、`remix_source`、`reference_only`；驱动音频默认锁定原音轨。`reference_video_audio:true` 才读取参考视频音轨。只接受当前用户上传目录内文件，不接受URL、绝对路径或`..`。

档位：`base20`、`light4`、`fast4`、`triton4`（默认）、`sol4`（NVIDIA CuTe）、`quality8`、`balanced8`、`vdn4`。8步LoRA只支持FL2V，不用于Ref2V；VDN使用完整基座，不叠加Turbo。`fused_modulation:true` 可选，本轮未证明显著采样提速。

后处理示例：

```json
{"mode":"postprocess","input_video":"uploaded.mp4","seconds":5,"input_fps":24,
 "postprocess":{"deblur":true,"interpolate":true,"fps_multiplier":2}}
```

另支持 `upscale:true,scale:2`、`denoise:true`、`hdr:true`。插帧保持原时长；HDR为PQ/BT.2020/10bit，需HDR显示环境。后处理秒数不含输入生成，不能混入端到端生成排名。

## 对比与验证

网页按尺寸和帧率筛选，同屏只能比较一种规格；默认展示本轮固定768p正式测试，历史混规格记录保留在相应筛选项。播放、暂停或拖动任意已选视频，其余同步跟随；单一音轨避免叠音。每张卡显示配置、耗时、视觉评估、证据和下载。

本轮控制：1344×768 / 24fps / 120帧 / 5秒；草稿896×512；同提示词、seed20261040、4+1步；每轮清空节点缓存并卸载GPU模型，保留系统文件与内核缓存。首次编译计入耗时并另做复测。视觉检查全部视频0/2/4秒抽帧、候选原尺寸细节；这是本机和本轮内容的选择，不是通用质量评分。

```bash
venv/bin/python -m unittest discover -s custom_nodes/ComfyUI-OneAPI-Swagger/tests -v
venv/bin/python script_examples/build_video_comparison.py
```

证据和评估：`output/h3_acceleration_fixed_768p_20261024/`。没有更改核心模型数学或增加核心互联网请求。

公开一步精修来自 [NVIDIA SoL Refiner](https://nvlabs.github.io/Sana/Sol-Refiner/)，本机使用szwagros社区INT8 ConvRot转换权重；不是早期gated LTX三步方案。[NVIDIA H3 Super Acceleration](https://nvlabs.github.io/Sana/Sol-Engine/H3-Super-Acceleration/) 采用TAE解码思路；[TAE权重作者](https://github.com/madebyollin/taehv)为独立项目。GB200/Blackwell公布速度不能套用到4090。

全部图像、语音与通用任务接口见 [统一媒体API](UNIFIED_MEDIA_API.md)。
