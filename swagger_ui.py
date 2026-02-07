"""
Swagger UI HTML template for ComfyUI-OneAPI-Swagger
"""

SWAGGER_UI_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ComfyUI-OneAPI-Swagger - Swagger UI</title>
    <link rel="stylesheet" type="text/css" href="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.11.0/swagger-ui.min.css">
    <style>
        body {
            margin: 0;
            padding: 0;
        }
        
        .topbar {
            display: none;
        }
        
        .swagger-ui .info {
            margin: 30px 0;
        }
        
        .swagger-ui .info .title {
            font-size: 36px;
            color: #3b4151;
        }
        
        .swagger-ui .scheme-container {
            background: #f7f7f7;
            padding: 20px;
            margin: 20px 0;
            border-radius: 4px;
        }
        
        /* 自定义配色 */
        .swagger-ui .opblock.opblock-post {
            background: rgba(73, 204, 144, 0.1);
            border-color: #49cc90;
        }
        
        .swagger-ui .opblock.opblock-post .opblock-summary-method {
            background: #49cc90;
        }
        
        .swagger-ui .opblock.opblock-get {
            background: rgba(97, 175, 254, 0.1);
            border-color: #61affe;
        }
        
        .swagger-ui .opblock.opblock-get .opblock-summary-method {
            background: #61affe;
        }
        
        /* 优化响应式 */
        @media (max-width: 768px) {
            .swagger-ui .info .title {
                font-size: 28px;
            }
        }
        
        /* 自定义头部横幅 */
        .custom-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        .custom-header h1 {
            margin: 0;
            font-size: 32px;
            font-weight: 600;
        }
        
        .custom-header p {
            margin: 10px 0 0 0;
            font-size: 16px;
            opacity: 0.9;
        }
        
        .custom-header .links {
            margin-top: 15px;
        }
        
        .custom-header a {
            color: white;
            text-decoration: none;
            margin: 0 10px;
            padding: 8px 16px;
            background: rgba(255,255,255,0.2);
            border-radius: 4px;
            transition: background 0.3s;
        }
        
        .custom-header a:hover {
            background: rgba(255,255,255,0.3);
        }
    </style>
</head>
<body>
    <!-- 自定义头部 -->
    <div class="custom-header">
        <h1>✨ ComfyUI-OneAPI-Swagger</h1>
        <p>简洁的 REST API 接口执行 ComfyUI 工作流</p>
        <div class="links">
            <a href="https://github.com/hackyinge/ComfyUI-OneAPI" target="_blank">📦 GitHub</a>
            <a href="/oneapi/openapi.json" target="_blank">📄 OpenAPI JSON</a>
        </div>
    </div>
    
    <!-- Swagger UI 容器 -->
    <div id="swagger-ui"></div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.11.0/swagger-ui-bundle.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.11.0/swagger-ui-standalone-preset.min.js"></script>
    <script>
        window.onload = function() {
            // 初始化 Swagger UI
            const ui = SwaggerUIBundle({
                url: '/oneapi/openapi.json',
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "BaseLayout",
                defaultModelsExpandDepth: 1,
                defaultModelExpandDepth: 1,
                docExpansion: 'list',
                filter: true,
                tryItOutEnabled: true,
                // 自定义请求拦截器
                requestInterceptor: (request) => {
                    // 可以在这里添加自定义请求头
                    return request;
                },
                // 自定义响应拦截器
                responseInterceptor: (response) => {
                    return response;
                }
            });
            
            window.ui = ui;
        };
    </script>
</body>
</html>
"""
