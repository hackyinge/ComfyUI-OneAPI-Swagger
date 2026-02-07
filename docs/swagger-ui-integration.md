# ComfyUI-OneAPI-Swagger 集成

## 📋 功能概览

本次更新为 ComfyUI-OneAPI-Swagger 添加了完整的 Swagger UI 支持，提供交互式的 API 文档和测试界面。

## 🎯 新增功能

### 1. Swagger UI 界面
- **访问地址**: `http://localhost:8188/oneapi/docs`
- **功能特性**:
  - 🎨 精美的渐变紫色主题
  - 📖 完整的 API 文档展示
  - 🧪 在线测试所有 API 接口
  - 📝 丰富的请求/响应示例
  - 🔍 详细的 Schema 定义

### 2. OpenAPI 规范
- **访问地址**: `http://localhost:8188/oneapi/openapi.json`
- **规范版本**: OpenAPI 3.0.3
- **内容包含**:
  - 完整的 API 端点定义
  - 详细的参数说明
  - 响应格式定义
  - 错误处理说明
  - 实用示例

## 🚀 使用方法

### 访问 Swagger UI

1. 确保 ComfyUI 正在运行
2. 在浏览器中打开: `http://localhost:8188/oneapi/docs`
3. 浏览 API 文档并进行测试

### 测试 API

在 Swagger UI 中：

1. **选择接口** - 点击任意 API 端点展开
2. **Try it out** - 点击 "Try it out" 按钮
3. **填写参数** - 根据提示填写请求参数
4. **执行请求** - 点击 "Execute" 按钮
5. **查看结果** - 在下方查看完整的响应数据

## 📚 API 端点说明

### POST /oneapi/v1/execute
执行 ComfyUI 工作流

**参数**:
- `workflow`: 工作流定义（JSON对象/本地文件名/URL）
- `params`: 参数映射字典（可选）
- `wait_for_result`: 是否等待结果（默认 true）
- `timeout`: 超时时间（秒）
- `prompt_ext_params`: 额外参数（可选）

**响应**:
- `status`: 执行状态
- `prompt_id`: Prompt ID
- `images`: 图片 URL 列表
- `images_by_var`: 按变量名映射的图片
- `videos`: 视频 URL 列表（如有）
- `audios`: 音频 URL 列表（如有）
- `texts`: 文本输出列表（如有）

### POST /oneapi/v1/save-api-workflow
保存 API 工作流

**参数**:
- `name`: 工作流名称
- `workflow`: 工作流 JSON 对象
- `overwrite`: 是否覆盖已存在文件（默认 false）

**响应**:
- `message`: 保存成功消息
- `filename`: 保存的文件名

## 🎨 设计特性

### 视觉设计
- **主题色**: 渐变紫色（#667eea → #764ba2）
- **布局**: 清晰的分组和标签
- **交互**: 流畅的展开/折叠动画
- **响应式**: 支持移动端访问

### 用户体验
- **语法高亮**: JSON 请求/响应自动高亮
- **实时验证**: 参数格式自动验证
- **错误提示**: 清晰的错误信息展示
- **一键复制**: 快速复制请求示例

## 💡 示例场景

### 场景 1: 快速测试文生图工作流

1. 在 Swagger UI 中打开 `/oneapi/v1/execute`
2. 点击 "Try it out"
3. 在 workflow 字段输入工作流文件名: `my_workflow.json`
4. 在 params 字段输入:
   ```json
   {
     "prompt": "a cute cat wearing a hat"
   }
   ```
5. 点击 "Execute"
6. 查看返回的图片 URL

### 场景 2: 测试图生图工作流

1. 使用相同步骤打开接口
2. 输入参数:
   ```json
   {
     "prompt": "beautiful landscape",
     "input_image": "https://example.com/input.jpg"
   }
   ```
3. 执行并查看结果

### 场景 3: 保存工作流

1. 打开 `/oneapi/v1/save-api-workflow`
2. 输入工作流名称和 JSON
3. 选择是否覆盖
4. 保存成功

## 🔧 技术实现

### 文件结构
```
ComfyUI-OneAPI/
├── oneapi.py              # 主路由文件（已更新）
├── openapi_spec.py        # OpenAPI 规范定义（新增）
├── swagger_ui.py          # Swagger UI HTML 模板（新增）
├── README.md              # 英文文档（已更新）
└── docs/
    └── swagger-ui-integration.md  # Swagger UI 功能说明
```

### 新增路由
- `GET /oneapi/docs` → Swagger UI 页面
- `GET /oneapi/openapi.json` → OpenAPI 规范 JSON

### 依赖说明
- 使用 CDN 加载 Swagger UI (v5.11.0)
- 无需额外安装 Python 依赖
- 完全基于现有 aiohttp 框架

## 📊 优势对比

| 特性 | 传统方式 | Swagger UI |
|------|---------|-----------|
| 文档查看 | 手动查看 README | 可视化界面 |
| API 测试 | 命令行 curl | 在线交互测试 |
| 参数理解 | 文本说明 | Schema 定义 |
| 示例展示 | 静态代码 | 动态示例 |
| 学习曲线 | 较陡 | 平缓 |
| 调试效率 | 低 | 高 |

## ✅ 完成清单

- [x] 创建 OpenAPI 3.0.3 规范
- [x] 实现 Swagger UI 界面
- [x] 添加所有 API 端点文档
- [x] 提供丰富的请求示例
- [x] 更新 README 文档
- [x] 美化 UI 主题
- [x] 支持响应式设计
- [x] 添加中英文文档

## 🎉 总结

通过集成 Swagger UI，ComfyUI-OneAPI 现在提供了：

1. **更友好的开发体验** - 无需记忆 API 格式
2. **更快的调试速度** - 在线测试，实时反馈
3. **更完整的文档** - Schema 定义，示例丰富
4. **更低的学习成本** - 可视化界面，直观易懂

这使得 ComfyUI-OneAPI 成为一个更加专业、易用的工具！

## 🙏 致谢

本项目基于 [puke3615/ComfyUI-OneAPI](https://github.com/puke3615/ComfyUI-OneAPI) 进行二次开发。感谢原作者的优秀工作和开源贡献！
