# 更新日志

## [v1.1.1] - 2026-02-07

### 🐛 重要修复

#### 动态服务器地址支持
- 🔧 修复硬编码 `127.0.0.1:8188` 导致远程访问失败的问题
- ✨ 添加 `_get_internal_api_url()` 函数，自动从请求中获取服务器地址
- ✅ 支持局域网 IP 访问（如 `http://192.168.100.170:4020`）
- ✅ 支持自定义端口
- ✅ 支持反向代理场景

#### 修改的内部 API 调用
- `/upload/image` - 媒体上传接口
- `/prompt` - 工作流提交接口
- `/history/{prompt_id}` - 历史查询接口

#### 详细说明
查看 `docs/DYNAMIC_SERVER_ADDRESS.md` 了解完整的修复细节和工作原理。

---

## [v1.1.0] - 2026-02-07

### 🎉 新增功能

#### Swagger UI 集成
- ✨ 添加交互式 API 文档界面 (`/oneapi/docs`)
- 📄 提供完整的 OpenAPI 3.0.3 规范 (`/oneapi/openapi.json`)
- 🎨 精美的渐变紫色 UI 主题
- 🧪 支持在线测试所有 API 接口
- 📝 丰富的请求/响应示例
- 🔍 详细的 Schema 定义

#### 文档改进
- 📚 更新 README，添加 Swagger UI 使用说明
- 🙏 添加原项目致谢说明
- 📖 创建详细的功能概览文档 (`docs/swagger-ui-integration.md`)

### 📦 新增文件

```
ComfyUI-OneAPI-Swagger/
├── openapi_spec.py              # OpenAPI 3.0.3 规范定义
├── swagger_ui.py                # Swagger UI HTML 模板
├── CHANGELOG.md                 # 更新日志（本文件）
└── docs/
    └── swagger-ui-integration.md  # Swagger UI 功能说明
```

### 🔧 修改文件

- `oneapi.py`: 添加 Swagger UI 路由
  - `GET /oneapi/docs` → Swagger UI 页面
  - `GET /oneapi/openapi.json` → OpenAPI 规范 JSON
- `README.md`: 更新说明文档，添加 Swagger UI 章节和致谢信息

### ✨ 功能亮点

1. **零配置使用**
   - 无需安装额外依赖
   - 使用 CDN 加载 Swagger UI
   - 基于现有 aiohttp 框架

2. **完整的 API 文档**
   - `/oneapi/v1/execute` - 执行工作流
   - `/oneapi/v1/save-api-workflow` - 保存工作流
   - 详细的参数说明和示例

3. **优秀的用户体验**
   - 清晰的分组和标签
   - 语法高亮显示
   - 实时请求验证
   - 响应式设计

### 📖 使用方法

启动 ComfyUI 后，访问：
- **Swagger UI**: http://localhost:8188/oneapi/docs
- **OpenAPI JSON**: http://localhost:8188/oneapi/openapi.json

### 🙏 致谢

本项目基于 [puke3615/ComfyUI-OneAPI](https://github.com/puke3615/ComfyUI-OneAPI) 进行二次开发。感谢原作者的优秀工作和开源贡献！

---

## [v1.0.0] - 原始版本

### 核心功能
- ✅ 简化的 REST API 执行 ComfyUI 工作流
- ✅ 动态参数替换（`$param.field` 标记）
- ✅ 多源 workflow 支持（JSON/文件/URL）
- ✅ 智能输出管理（按类型和变量名分类）
- ✅ UI 集成（右键菜单保存工作流）
