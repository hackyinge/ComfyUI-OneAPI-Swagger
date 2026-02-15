"""
OpenAPI specification for ComfyUI-OneAPI-Swagger
"""

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "ComfyUI-OneAPI-Swagger",
        "description": """
# ComfyUI-OneAPI-Swagger ✨

ComfyUI-OneAPI-Swagger 提供简洁的 REST API 接口来执行 ComfyUI 工作流。

## 核心特性

- **极致简洁** - 一个 API 请求执行复杂的 ComfyUI 工作流
- **动态参数** - 通过节点标题标记动态替换工作流参数
- **多源支持** - 支持 JSON 对象、本地文件和 URL 作为工作流输入
- **智能输出** - 自动分类和组织多个输出节点
- **灵活标记** - 支持输入标记 (`$param.field`) 和输出标记 (`$output.name`)

## 节点标题标记规则

### 输入参数标记

1. **LoadImage 节点**: 使用 `$image_param` 格式
2. **其他节点**: 使用 `$param.field_name` 格式

示例:
- `$input_image` - LoadImage 节点使用 params.input_image 作为图片
- `$prompt.text` - 使用 params.prompt 替换 text 字段

### 输出标记

为 SaveImage 节点标题添加标记:
- 格式: `$output.name` (例如: `$output.background`)
- 没有标记时，使用节点 ID 作为变量名

## Workflow 参数支持三种形式

1. **JSON 对象**: 直接传递工作流 JSON
2. **本地文件**: 传递文件名 (如 `1.json`)，从 `user/default/api_workflows/` 加载
3. **URL**: 传递 URL (如 `http://xxx/1.json`)，自动下载并解析

        """,
        "version": "1.0.0",
        "contact": {
            "name": "ComfyUI-OneAPI",
            "url": "https://github.com/hackyinge/ComfyUI-OneAPI-Swagger"
        },
        "license": {
            "name": "MIT",
            "url": "https://opensource.org/licenses/MIT"
        }
    },
    "servers": [
        {
            "url": "/",
            "description": "当前服务器"
        }
    ],
    "tags": [
        {
            "name": "workflow",
            "description": "工作流执行相关接口"
        },
        {
            "name": "management",
            "description": "工作流管理相关接口"
        },
        {
            "name": "gemini",
            "description": "Gemini 兼容接口 (Google AI SDK 适配)"
        },
        {
            "name": "openai",
            "description": "OpenAI 兼容接口 (支持图生视频)"
        }
    ],
    "paths": {
        "/oneapi/v1/execute": {
            "post": {
                "tags": ["workflow"],
                "summary": "执行工作流",
                "description": "执行 ComfyUI 工作流，支持参数化和多种输出格式",
                "operationId": "executeWorkflow",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/ExecuteWorkflowRequest"
                            },
                            "examples": {
                                "simple": {
                                    "summary": "简单的文本转图片",
                                    "value": {
                                        "workflow": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}}},
                                        "params": {
                                            "prompt": "a cute cat"
                                        }
                                    }
                                },
                                "with_url": {
                                    "summary": "使用 URL 图片的图生图",
                                    "value": {
                                        "workflow": "my_workflow.json",
                                        "params": {
                                            "prompt": "beautiful landscape",
                                            "input_image": "https://example.com/image.jpg"
                                        }
                                    }
                                },
                                "async_execution": {
                                    "summary": "异步执行（不等待结果）",
                                    "value": {
                                        "workflow": {"1": {"class_type": "CheckpointLoaderSimple"}},
                                        "wait_for_result": False
                                    }
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "执行成功",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ExecuteWorkflowResponse"
                                },
                                "examples": {
                                    "completed": {
                                        "summary": "执行完成",
                                        "value": {
                                            "status": "completed",
                                            "prompt_id": "12345678-1234-1234-1234-123456789abc",
                                            "images": [
                                                "http://localhost:8118/view?filename=image1.png&type=output",
                                                "http://localhost:8118/view?filename=image2.png&type=output"
                                            ],
                                            "images_by_var": {
                                                "background": ["http://localhost:8118/view?filename=image1.png&type=output"],
                                                "character": ["http://localhost:8118/view?filename=image2.png&type=output"]
                                            }
                                        }
                                    },
                                    "queued": {
                                        "summary": "已入队（异步模式）",
                                        "value": {
                                            "status": "queued",
                                            "prompt_id": "12345678-1234-1234-1234-123456789abc",
                                            "message": "Workflow submitted"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "400": {
                        "description": "请求参数错误",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ErrorResponse"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "服务器内部错误",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ErrorResponse"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/oneapi/v1/save-api-workflow": {
            "post": {
                "tags": ["management"],
                "summary": "保存 API 工作流",
                "description": "将工作流保存到用户的工作流目录",
                "operationId": "saveApiWorkflow",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/SaveWorkflowRequest"
                            },
                            "examples": {
                                "save_new": {
                                    "summary": "保存新工作流",
                                    "value": {
                                        "name": "my_workflow",
                                        "workflow": {"1": {"class_type": "CheckpointLoaderSimple"}},
                                        "overwrite": False
                                    }
                                },
                                "overwrite_existing": {
                                    "summary": "覆盖已存在的工作流",
                                    "value": {
                                        "name": "my_workflow.json",
                                        "workflow": {"1": {"class_type": "CheckpointLoaderSimple"}},
                                        "overwrite": True
                                    }
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "保存成功",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/SaveWorkflowResponse"
                                }
                            }
                        }
                    },
                    "400": {
                        "description": "请求参数错误",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ErrorResponse"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "服务器内部错误",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ErrorResponse"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/v1beta/models/{model}:generateContent": {
            "$ref": "#/paths/~1oneapi~1v1~1models~1{model}:generateContent"
        },
        "/v1/models/{model}:generateContent": {
            "$ref": "#/paths/~1oneapi~1v1~1models~1{model}:generateContent"
        },
        "/oneapi/v1/models/{model}:generateContent": {
            "post": {
                "tags": ["gemini"],
                "summary": "Gemini 兼容内容生成",
                "description": "提供与 Google Gemini API 兼容的接口。支持多种模型路径如 /v1beta/models/{model}:generateContent。",
                "operationId": "geminiGenerateContent",
                "parameters": [
                    {
                        "name": "model",
                        "in": "path",
                        "required": True,
                        "description": "模型标识符，对应本地保存的工作流文件名",
                        "schema": {
                            "type": "string"
                        }
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/GeminiGenerateContentRequest"
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "成功生成内容",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/GeminiGenerateContentResponse"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/oneapi/v1/chat/completions": {
            "$ref": "#/paths/~1v1~1chat~1completions"
        },
        "/v1/chat/completions": {
            "post": {
                "tags": ["openai"],
                "summary": "OpenAI 兼容对话接口",
                "description": "提供与 OpenAI Chat Completions API 兼容的接口。支持多模态输入（文本+单张或多张图像），支持图生视频及多图融合工作流。图片会自动映射到工作流中的 $param.image1, $param.image2... 插槽。",
                "operationId": "chatCompletions",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/ChatCompletionRequest"
                            },
                            "examples": {
                                "i2v": {
                                    "summary": "单图生视频 (首尾帧相同)",
                                    "value": {
                                        "model": "LTX2-SWZ",
                                        "messages": [
                                            {
                                                "role": "user",
                                                "content": [
                                                    {"type": "text", "text": "a cinematic video of a sunset"},
                                                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                                                ]
                                            }
                                        ]
                                    }
                                },
                                "multi_image": {
                                    "summary": "多图工作流输入",
                                    "value": {
                                        "model": "LTX-more-image-3-3",
                                        "messages": [
                                            {
                                                "role": "user",
                                                "content": [
                                                    {"type": "text", "text": "three images transition"},
                                                    {"type": "image_url", "image_url": {"url": "https://example.com/img1.jpg"}},
                                                    {"type": "image_url", "image_url": {"url": "https://example.com/img2.jpg"}},
                                                    {"type": "image_url", "image_url": {"url": "https://example.com/img3.jpg"}}
                                                ]
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                },
                "responses": {
                  "200": {
                        "description": "成功返回聊天回复 (包含结果链接/Base64)",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ChatCompletionResponse"
                                }
                            }
                        }
                    }
                }
            }
        }

    },
    "components": {
        "schemas": {
            "ExecuteWorkflowRequest": {
                "type": "object",
                "required": ["workflow"],
                "properties": {
                    "workflow": {
                        "oneOf": [
                            {"type": "object", "description": "工作流 JSON 对象"},
                            {"type": "string", "description": "工作流文件名或 URL"}
                        ],
                        "description": "工作流定义，支持 JSON 对象、本地文件名（从 user/default/api_workflows/ 加载）或 URL"
                    },
                    "params": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "参数映射字典，用于替换工作流中的标记参数",
                        "example": {
                            "prompt": "a cute cat",
                            "input_image": "https://example.com/image.jpg"
                        }
                    },
                    "wait_for_result": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否等待执行结果"
                    },
                    "timeout": {
                        "type": "integer",
                        "nullable": True,
                        "description": "超时时间（秒），None 表示无超时",
                        "example": 300
                    },
                    "prompt_ext_params": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "额外的 prompt 请求参数（可选）",
                        "example": {
                            "extra_data": {"extra_pnginfo": {"workflow": {}}}
                        }
                    }
                }
            },
            "ExecuteWorkflowResponse": {
                "type": "object",
                "required": ["status", "prompt_id"],
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["queued", "processing", "completed", "timeout", "error"],
                        "description": "执行状态"
                    },
                    "prompt_id": {
                        "type": "string",
                        "format": "uuid",
                        "description": "Prompt ID"
                    },
                    "message": {
                        "type": "string",
                        "description": "状态消息（仅在 queued 状态时）"
                    },
                    "error": {
                        "type": "string",
                        "description": "错误信息（仅在 error 状态时）"
                    },
                    "duration": {
                        "type": "number",
                        "description": "执行时长（秒）"
                    },
                    "images": {
                        "type": "array",
                        "items": {"type": "string", "format": "uri"},
                        "description": "图片 URL 列表（仅在有图片结果时存在）"
                    },
                    "images_by_var": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string", "format": "uri"}
                        },
                        "description": "按变量名映射的图片 URL（仅在有图片结果时存在）"
                    },
                    "videos": {
                        "type": "array",
                        "items": {"type": "string", "format": "uri"},
                        "description": "视频 URL 列表（仅在有视频结果时存在）"
                    },
                    "videos_by_var": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string", "format": "uri"}
                        },
                        "description": "按变量名映射的视频 URL（仅在有视频结果时存在）"
                    },
                    "audios": {
                        "type": "array",
                        "items": {"type": "string", "format": "uri"},
                        "description": "音频 URL 列表（仅在有音频结果时存在）"
                    },
                    "audios_by_var": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string", "format": "uri"}
                        },
                        "description": "按变量名映射的音频 URL（仅在有音频结果时存在）"
                    },
                    "texts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "文本输出列表（仅在有文本结果时存在）"
                    },
                    "texts_by_var": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "description": "按变量名映射的文本输出（仅在有文本结果时存在）"
                    },
                    "outputs": {
                        "type": "object",
                        "description": "原始输出数据"
                    }
                }
            },
            "SaveWorkflowRequest": {
                "type": "object",
                "required": ["name", "workflow"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工作流名称，自动添加 .json 后缀",
                        "example": "my_workflow"
                    },
                    "workflow": {
                        "type": "object",
                        "description": "工作流 JSON 对象（必须是 API 格式）"
                    },
                    "overwrite": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否覆盖已存在的文件"
                    }
                }
            },
            "SaveWorkflowResponse": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "example": "Workflow saved successfully"
                    },
                    "filename": {
                        "type": "string",
                        "example": "my_workflow.json"
                    }
                }
            },
            "ErrorResponse": {
                "type": "object",
                "required": ["error"],
                "properties": {
                    "error": {
                        "type": "string",
                        "description": "错误信息"
                    }
                }
            },
            "GeminiGenerateContentRequest": {
                "type": "object",
                "required": ["contents"],
                "properties": {
                    "contents": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "parts": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "generationConfig": {
                        "type": "object",
                        "properties": {
                            "imageConfig": {
                                "type": "object",
                                "properties": {
                                    "aspectRatio": {
                                        "type": "string",
                                        "enum": ["16:9", "9:16"],
                                        "default": "16:9",
                                        "description": "图像比例，默认 16:9 (1280x720)，支持 9:16 (720x1280)"
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "GeminiGenerateContentResponse": {
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"type": "string"},
                                        "parts": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "text": {"type": "string"},
                                                    "inlineData": {
                                                        "type": "object",
                                                        "properties": {
                                                            "mimeType": {"type": "string"},
                                                            "data": {"type": "string", "description": "Base64 encoded image data"}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                },
                                "finishReason": {"type": "string"}
                            }
                        }
                    }
                }
            },
            "ChatCompletionRequest": {
                "type": "object",
                "required": ["model", "messages"],
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "模型标识符，对应本地保存的工作流文件名 (如 ltx2_landscape)"
                    },
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                                "content": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "type": {"type": "string", "enum": ["text", "image_url"]},
                                                    "text": {"type": "string"},
                                                    "image_url": {
                                                        "type": "object",
                                                        "properties": {
                                                            "url": {"type": "string", "description": "Base64 data or image URL"}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            "ChatCompletionResponse": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "object": {"type": "string", "default": "chat.completion"},
                    "created": {"type": "integer"},
                    "model": {"type": "string"},
                    "choices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "message": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"type": "string", "default": "assistant"},
                                        "content": {"type": "string", "description": "生成的回复内容，通常包含媒体链接"}
                                    }
                                },
                                "finish_reason": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
}
