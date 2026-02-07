# ComfyUI-OneAPI-Swagger UI 快速开始

## 🚀 快速访问

启动 ComfyUI 后，在浏览器中打开：

**http://localhost:8188/oneapi/docs**

## 📚 功能说明

### 1. 查看 API 文档

在 Swagger UI 界面中，你可以看到：
- **所有 API 端点** - 展开查看详细信息
- **请求参数** - 每个参数的类型、说明和要求
- **响应格式** - 成功和失败情况的响应示例
- **Schema 定义** - 完整的数据结构定义

### 2. 测试 API

#### 测试工作流执行

1. 展开 `POST /oneapi/v1/execute` 接口
2. 点击 **"Try it out"** 按钮
3. 在 **Request body** 中输入：

```json
{
  "workflow": "my_workflow.json",
  "params": {
    "prompt": "a beautiful sunset over mountains"
  }
}
```

4. 点击 **"Execute"** 按钮
5. 查看下方的 **Response** 区域

#### 测试保存工作流

1. 展开 `POST /oneapi/v1/save-api-workflow` 接口
2. 点击 **"Try it out"**
3. 输入工作流名称和 JSON：

```json
{
  "name": "test_workflow",
  "workflow": {
    "1": {
      "class_type": "CheckpointLoaderSimple",
      "inputs": {
        "ckpt_name": "model.safetensors"
      }
    }
  },
  "overwrite": false
}
```

4. 执行并查看结果

## 💡 使用技巧

### 查看示例

每个接口都提供了多个示例：
- **simple** - 简单的文本转图片
- **with_url** - 使用 URL 图片的图生图
- **async_execution** - 异步执行（不等待结果）

点击示例名称即可自动填充参数。

### 理解响应格式

成功响应包含：
- `status`: 执行状态
- `prompt_id`: 任务 ID
- `images`: 图片 URL 列表
- `images_by_var`: 按变量名分组的图片
- `videos/audios/texts`: 其他类型输出（如有）

### 复制 cURL 命令

在响应区域可以找到等效的 cURL 命令，方便在命令行中使用。

## 🎨 界面说明

### 顶部横幅
- 显示项目标题和描述
- 提供 GitHub 链接和 OpenAPI JSON 下载

### API 列表
- **绿色 POST** - 创建/执行操作
- **蓝色 GET** - 查询操作

### 请求测试
- **Parameters** - 输入参数
- **Request body** - JSON 请求体
- **Responses** - 查看可能的响应

## 🔍 常见场景

### 场景 1: 执行简单文生图

```json
{
  "workflow": {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "cat", "clip": ["1", 1]}, "_meta": {"title": "$prompt.text"}},
    "3": {"class_type": "KSampler", "inputs": {"positive": ["2", 0]}},
    "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}, "_meta": {"title": "$output.result"}}
  },
  "params": {
    "prompt": "a cute cat"
  }
}
```

### 场景 2: 使用已保存的工作流

```json
{
  "workflow": "my_saved_workflow.json",
  "params": {
    "prompt": "beautiful landscape"
  }
}
```

### 场景 3: 从 URL 加载工作流

```json
{
  "workflow": "https://example.com/workflows/example.json",
  "params": {
    "prompt": "amazing artwork"
  }
}
```

## ⚠️ 注意事项

1. **工作流格式** - 必须使用 API 格式，不支持 UI 格式
2. **节点标记** - 参数替换需要在节点标题中添加 `$param.field` 标记
3. **超时设置** - 长时间运行的工作流建议设置合适的 `timeout` 值
4. **输出标记** - 使用 `$output.name` 标记可以方便地区分多个输出

## 🎯 下一步

- 查看完整文档：`docs/swagger-ui-integration.md`
- 查看更新日志：`CHANGELOG.md`
- 访问项目：https://github.com/hackyinge/ComfyUI-OneAPI

## 🙏 致谢

感谢 [puke3615](https://github.com/puke3615) 创建了优秀的 ComfyUI-OneAPI 项目！
