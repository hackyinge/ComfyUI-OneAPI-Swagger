# ComfyUI-OneAPI-Swagger 接口文档 ✨

ComfyUI-OneAPI-Swagger 旨在为 ComfyUI 提供一层极简的、标准化且高度兼容的 API 封装。它允许你通过简单的 HTTP 请求调用复杂的 ComfyUI 工作流，并支持与 OpenAI 及 Google Gemini 开发工具链的无缝对接。

---

## 🚀 核心特性

- **一键调用**：将复杂的 JSON 工作流简化为一个 API 路径调用。
- **动态参数**：通过节点标题占位符（如 `$param.text`）动态注入业务数据。
- **多端兼容**：原生支持 OpenAI 和 Gemini 的请求格式。
- **智能输出**：自动分类并收集工作流产生的所有多媒体结果（视频、图片、音频、文本）。
- **内置文档**：提供交互式的 Swagger UI (`/oneapi/docs`)。

---

## 🛠️ 节点标记规则

为了让 API 能够正确识别并填充工作流中的参数，你需要在 ComfyUI 工作流中对节点标题（Title）进行简单的重命名：

### 1. 输入标记 (Input)

- **通用节点**：在标题中使用 `$param.字段名`。例如，将 CLIP 文本编码器的标题改为 `$param.text`。
- **LoadImage 节点**：可以直接使用 `$image_param` 格式，或者为了支持多图，使用 `$param.image1`, `$param.image2` ... `$param.image5`。
- **Seed 字段**：在含有 seed 的节点标题中使用 `$seed.seed`，API 会自动生成并填充随机种子。

### 2. 输出标记 (Output)

- **多媒体输出**：在 `SaveImage`, `VHS_VideoCombine` 等节点的标题中添加 `$output.变量名`。
- **默认规则**：如果没有标记，API 会默认抓取所有产生文件的节点，并以节点 ID 作为 key 返回。

---

## 📂 接口定义

### 1. 万能执行接口 (Native OneAPI)

这是最强大的接口，支持直接发送工作流 JSON 或调用本地已保存的工作流。

- **URL**: `/oneapi/v1/execute`
- **Method**: `POST`
- **关键参数**:
  - `workflow`: 工作流文件名（如 `LTX-more-image-3-3`）或完整的 JSON。
  - `params`: 键值对，对应工作流中的 `$param.xxx`。
  - `wait_for_result`: 是否同步等待结果（默认 `true`）。

**请求示例**:

```json
{
  "workflow": "LTX-more-image-2-3",
  "params": {
    "text": "a cute cat running",
    "image1": "https://example.com/start.jpg",
    "image2": "https://example.com/end.jpg"
  }
}
```

---

### 2. OpenAI 兼容接口 (Chat Completions)

支持图生视频、视频生成等，可直接适配 OpenAI SDK。

- **URL**: `/oneapi/v1/chat/completions` 或 `/v1/chat/completions`
- **Method**: `POST`
- **特性**:
  - 模型名（`model`）直接对应工作流文件名。
  - 支持消息数组中的多图提取。
  - 图片会自动映射到 `$param.image1` 到 `$param.image5`。

**请求示例**:

```json
{
  "model": "LTX-more-image-3-3",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "Transition between these three photos" },
        { "type": "image_url", "image_url": { "url": "URL_OR_BASE64_1" } },
        { "type": "image_url", "image_url": { "url": "URL_OR_BASE64_2" } },
        { "type": "image_url", "image_url": { "url": "URL_OR_BASE64_3" } }
      ]
    }
  ]
}
```

---

### 3. Google Gemini 兼容接口 (Generate Content)

专为适配 Google AI SDK 设计，主要用于简单的内容生成。

- **URL**: `/v1/models/{workflow_name}:generateContent`
- **Method**: `POST`

---

### 4. 工作流管理

- **保存工作流**: `POST /oneapi/v1/save-api-workflow`
  - 用于将前端生成的工作流持久化到服务器，供后续调用。
- **OpenAPI 规范**: `GET /oneapi/openapi.json`
- **交互式文档**: `GET /oneapi/docs` (Swagger UI)

---

## 📦 内置工作流说明

本插件内置了一系列针对 LTX-Video 优化的多图输入工作流：

| 模型名 (Model/Workflow) | 支持图片数 | 说明               |
| :---------------------- | :--------- | :----------------- |
| `LTX-more-image-2-3`    | 2 张       | 首尾帧控制视频生成 |
| `LTX-more-image-3-3`    | 3 张       | 三图融合转换       |
| `LTX-more-image-4-3`    | 4 张       | 四图序列转换       |
| `LTX-more-image-5-3`    | 5 张       | 五图长序列视频生成 |

---

## 💡 开发者建议

1. **测试连接**：先访问 `/oneapi/docs` 确认插件运行正常。
2. **多图提示**：在使用 OpenAI 接口调用多图工作流时，图片的顺序决定了它们填充到 `image1`~`image5` 的位置。
3. **超时处理**：视频生成耗时较长，请确保客户端（或 Nginx 反向代理）的超时设置大于 300 秒。
