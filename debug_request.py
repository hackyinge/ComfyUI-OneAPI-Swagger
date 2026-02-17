import requests
import json
import base64

# 读取用户提供的 curl 数据 (从 curl.txt 中提取)
def test_request():
    url = "http://127.0.0.1:4020/oneapi/v1/chat/completions"
    
    # 模拟 curl.txt 中的数据
    data = {
        "model": "ltx-more-image-5-3",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": "1980年代东北农村，漫天大雪中，一个男孩的命运在神秘大仙的预言下发生转折。"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" # 1x1 transparent png
                        }
                    }
                ]
            }
        ]
    }
    
    print(f"Sending request to {url}...")
    try:
        response = requests.post(url, json=data, timeout=10)
        print(f"Status Code: {response.status_code}")
        print("Response Body:")
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    # 我们不在远程，但我们可以尝试在当前环境(如果后端在运行)触发
    # 用户说 http://confyui_api.hackyin.com:4020
    # 我尝试发给 localhost 的 4020 (假设后端在这里运行)
    test_request()
