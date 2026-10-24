# Mac mini 调用方：视频生成与可选后处理接入

本页可以直接交给调用方项目的开发者或代码助手实现。所有能力经 ComfyUI-OneAPI-Swagger 提供，使用同一原生队列。2026-10-25起视频默认改为低分辨率预览；旧固定768p二阶段模板保留。调用方负责收集用户选择，服务端按本次 JSON 构建工作流；不传的增强项不会自动开启。

## 1. 服务与认证

当前局域网服务：`http://192.168.100.213:8788`。在 Mac mini **后端**配置：

```sh
COMFYUI_BASE_URL=http://192.168.100.213:8788
COMFYUI_TOKEN=你的有效Bearer令牌
```

请求使用 `Authorization: Bearer <COMFYUI_TOKEN>`。不要把令牌写进源码或前端公开环境变量。Mac mini 网页前端调用自己的后端，由后端转发到 ComfyUI，避免跨域、Cookie 和媒体下载认证问题。不要从 Mac mini 使用 `127.0.0.1` 连接这台 ComfyUI 服务器。

在线 Swagger：`http://192.168.100.213:8788/oneapi/docs`；Schema：`/oneapi/openapi.json`；实际配置和可用模型：`GET /oneapi/v1/video/capabilities`。

## 2. 默认行为：快速预览

```http
POST /oneapi/v1/video/jobs
Content-Type: application/json
Authorization: Bearer <token>
```

```json
{"prompt":"A woman waves in a sunlit kitchen."}
```

省略参数时：**896×512、5 秒、24fps、H3/Turbo 4 步、原生 H3 解码**。`refiner:false`，不执行 SoL 二阶段精修；超分、降噪、去模糊、插帧和 HDR 全部关闭。用于快速验证内容，不把低分辨率预览等同于正式产出画质。

`POST /oneapi/v1/jobs` 也支持同样参数，但必须加 `"task":"video"`；不传 task 会走默认图片任务。

## 3. 调用方传哪些字段

| 字段 | 不传时 | 用途 |
| --- | --- | --- |
| `prompt` | 空文本 | 本次视频提示词 |
| `seconds` | `5` | 成片秒数 |
| `width` / `height` | `896` / `512` | 生成/精修后、额外后处理前的尺寸；直接交换宽高即可切换横竖屏 |
| `seed` | 每次随机 | 也支持 `null` / `-1` 随机；显式非负整数保持可复现。202 响应回传实际 seed，二阶段共用同一 seed |
| `refiner` | `false` | 是否执行 H3→NVIDIA SoL Refiner 二阶段，正式产出可明确开启 |
| `draft_width` / `draft_height` | 开精修时按目标尺寸约一半、32像素网格计算 | 控制 H3 草稿尺寸；已验证的1344×768方案显式传896×512 |
| `fast_decode` | `false` | H3 草稿是否用 TAEH3 解码；此前二阶段方案使用 true，最终 LTX 解码仍为原生 |
| `profile` | `triton4` | 加速配置；一般调用方不需要更改 |
| `save_draft` | `false` | 开精修时是否另外保存草稿 MP4；前端播放 node_id=save 的成片 |
| `postprocess` | 全部关闭 | 以下五个独立开关及倍率，均为本次请求参数 |

### 后处理字段

全部放在 `postprocess` 对象内，不放到 JSON 顶层。布尔值必须是 JSON 的 `true/false`，不要发送字符串 `"false"`。

| 字段 | 默认行为 | 作用 |
| --- | --- | --- |
| `upscale` | `false` | NVIDIA RTX 超分 |
| `scale` | 超分开启时 `2` | 同时放大宽和高；2 倍意味着像素数约4倍。本机节点公布倍率范围1至4 |
| `quality` | `HIGHBITRATE_HIGH` | 超分 SDK 档位；通常保留默认，完整选项见 `/oneapi/v1/video/nodes` 的 RTXVideoSuperResolution |
| `denoise` | `false` | NVIDIA 降噪，保持本阶段宽高 |
| `deblur` | `false` | NVIDIA 去模糊，保持本阶段宽高 |
| `interpolate` | `false` | 是否插帧；开启但不传倍率时为2倍 |
| `fps_multiplier` | 插帧开启时 `2` | 默认24fps变48fps，保持原时长；节点公布倍率范围2至16 |
| `hdr` | `false` | NVIDIA TrueHDR，输出10bit HDR PQ；普通SDR播放场景保持关闭 |

明确传 `interpolate:false` 时，即使仍带着旧的 `fps_multiplier:2`，也不会插帧。兼容原有调用：只传 `fps_multiplier>1`、未传 interpolate 时，仍会插帧。新项目应明确使用 interpolate 开关。**关闭插帧时发送 interpolate:false，或同时删除 interpolate 与 fps_multiplier；不要只删除开关却残留倍率。**

`postprocess` 不传、传 `{}` 或五个开关全为 false，效果相同。倍率和质量字段本身不构成“超分开关”；只有 upscale=true 才超分。

`refiner`与额外超分开关相互独立：SoL内部潜空间×2用于达到请求的width/height，postprocess.upscale才会在此基础上再次放大。

处理顺序：生成 → 可选 SoL 精修 → 超分 → 降噪 → 去模糊 → 插帧 → HDR → 音视频封装。所有步骤都在提交时决定。增大分辨率、开精修/后处理通常增加耗时和显存；降噪与去模糊可能改变纹理，不应默认全开。超分不是原生高分辨率生成的替代。

## 4. 可复制请求

### 低成本竖屏预览

```json
{
  "prompt":"A woman waves in a sunlit kitchen.",
  "width":512,
  "height":896,
  "seconds":5
}
```

### 正式横屏：此前验证的二阶段配置

```json
{
  "prompt":"A woman waves in a sunlit kitchen.",
  "seconds":5,
  "width":1344,
  "height":768,
  "draft_width":896,
  "draft_height":512,
  "refiner":true,
  "fast_decode":true
}
```

输出1344×768 / 24fps，不执行额外后处理。正式产出的高分辨率和精修应由调用方明确传入，不会因为开了某个后处理就自动切换生成分辨率。

### 正式竖屏：独立选择超分和插帧

```json
{
  "prompt":"A woman waves in a sunlit kitchen.",
  "seconds":5,
  "width":768,
  "height":1344,
  "draft_width":512,
  "draft_height":896,
  "refiner":true,
  "fast_decode":true,
  "postprocess":{
    "upscale":true,
    "scale":2,
    "denoise":false,
    "deblur":false,
    "interpolate":true,
    "fps_multiplier":2,
    "hdr":false
  }
}
```

输出为 **1536×2688、48fps、5秒**，不是768×1344。width/height定义的是额外超分之前的尺寸。要1344×768成片且不额外放大，关闭 upscale；不要误填成品尺寸后再无意叠加倍率。

### 只处理已有视频

先通过 `/oneapi/v1/video/upload`（multipart字段image）或 `/oneapi/v1/upload`（字段file）上传原视频，使用返回的name；有subfolder时按 `subfolder/name` 拼接。

```json
{
  "mode":"postprocess",
  "input_video":"上传返回的文件名.mp4",
  "seconds":5,
  "input_fps":24,
  "postprocess":{"denoise":true,"deblur":true}
}
```

该模式不重新生成H3，不自动精修；默认读取输入视频自身尺寸。width/height不会在这个模式下自动缩放输入，只有超分开关会放大。input_fps应与原视频一致；源视频本身至少有请求的秒数。输入必须是当前用户上传文件名，不能传Mac上的路径、绝对路径或外部URL。

## 5. 提交、轮询和下载

提交返回 HTTP 202：

```json
{
  "id":"任务UUID",
  "status":"queued",
  "status_url":"/oneapi/v1/video/jobs/任务UUID",
  "seed":123456789,
  "workflow":{}
}
```

这里 workflow 仅示意，实际返回完整原生图。纯后处理没有生成噪声时不会返回seed。保存id后，每1至2秒请求status_url：queued/running继续等待；completed后从files中选`node_id="save"`且media_type以`video/`开头的成片；failed展示errors，不把可能出现的草稿当作成功成片。

下载url通常为相对路径`/view?...`，需拼接相同服务地址并携带认证。任务历史在ComfyUI重启后过期；调用方应在完成后将视频及本次请求/实际seed保存到自己的项目。

### Node/TypeScript后端示例（内置fetch，无新增依赖）

```ts
type Postprocess = {
  upscale?: boolean; scale?: number; quality?: string;
  denoise?: boolean; deblur?: boolean;
  interpolate?: boolean; fps_multiplier?: number; hdr?: boolean;
};
type VideoRequest = {
  prompt?: string; seconds?: number; width?: number; height?: number;
  mode?: "t2v" | "i2v" | "fl2v" | "ref2v" | "audio2v" | "postprocess" | "refine";
  input_video?: string; input_fps?: number; first_frame?: string; last_frame?: string;
  drive_audio?: string; ref_images?: string[]; ref_videos?: string[]; ref_audios?: string[];
  profile?: string; save_draft?: boolean;
  seed?: number | null; refiner?: boolean; fast_decode?: boolean;
  draft_width?: number; draft_height?: number; postprocess?: Postprocess;
};
const base = process.env.COMFYUI_BASE_URL!;
const auth = { Authorization: `Bearer ${process.env.COMFYUI_TOKEN!}` };
async function json(path: string, body?: unknown) {
  const response = await fetch(new URL(path, base), {
    method: body === undefined ? "GET" : "POST",
    headers: { ...auth, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) throw new Error(`ComfyUI ${response.status}: ${await response.text()}`);
  if (!response.headers.get("content-type")?.includes("application/json"))
    throw new Error("ComfyUI未返回JSON，请检查服务地址和认证是否有效");
  return response.json();
}
export async function createVideo(request: VideoRequest) {
  return json("/oneapi/v1/video/jobs", request); // 将id保存到调用方数据库
}
export async function waitVideo(statusUrl: string, timeoutMs = 15 * 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = await json(statusUrl);
    if (job.status === "failed") throw new Error(JSON.stringify(job.errors));
    if (job.status === "completed") {
      const file = job.files.find((f: any) => f.node_id === "save" && f.media_type.startsWith("video/"));
      if (!file) throw new Error("任务完成但没有找到成片");
      return { jobId: job.id, url: new URL(file.url, base).href, seconds: job.execution_seconds };
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
  throw new Error(`等待超时，任务未取消，可继续查询 ${statusUrl}`);
}
export async function downloadVideo(url: string) {
  // url应来自waitVideo，不接受浏览器指定的任意URL。
  const response = await fetch(url, { headers: auth });
  if (!response.ok) throw new Error(`下载失败 ${response.status}`);
  return Buffer.from(await response.arrayBuffer());
}
```

调用顺序为`createVideo(request)` → 保存id → `waitVideo(job.status_url)` → `downloadVideo(result.url)`，然后将视频保存到调用方项目。

前端通常不能给`<video src>`添加Bearer请求头。建议Mac mini后端将成片保存到自身媒体目录/对象存储，再返回自己域下的播放地址；或由后端受控代理下载。不要只把需要认证的ComfyUI `/view` URL交给未登录的浏览器。

## 6. 给调用方实现的任务说明

1. UI默认“快速预览896×512”，五个后处理复选框均不勾选；另提供横竖屏及宽高输入。
2. 正式产出选择明确设置width/height与refiner；不要默认自动全开增强。前端展示超分后的预计尺寸和插帧后的fps。
3. 将五个复选框的本次布尔状态放进postprocess，明确发送false最稳妥；仅在超分/插帧开启时附对应倍率。若选择省略关闭项，必须同时省略interpolate和fps_multiplier，避免兼容旧倍率行为导致误开插帧。不要发送字符串false。
4. 随机按钮不传seed或传-1；固定模式发送用户指定种子。响应实际seed只保存到本次记录，不自动回填到下一次随机请求。
5. 保存任务id，异步轮询并处理失败，完成后认证下载成片。重启、超时与断开网页不表示任务取消。
6. 不把调用方自己保存的旧原生workflow作为动态设置入口。`POST /oneapi/v1/video/workflows/build`可查看本次参数产生的图；直接调用jobs即可动态选择这些能力。已有`h3_sol_default.json`保留为固定768p二阶段模板，新快速模板为`h3_preview_512p.json`。

接口集成先验收：默认输出896×512/24fps；关闭所有增强不出现相应节点；只开超分输出1792×1024；只开插帧输出896×512/48fps；显式interpolate=false覆盖旧倍率；HDR输出10bit PQ。不同分辨率不能用耗时直接断言画质相同或加速倍率相同。
