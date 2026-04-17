# minimax_proxy.py - Minimax Token Plan Proxy with proper SSE streaming
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import re
import json
import os
from datetime import datetime

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "sk-cp-xxxxxxxxxxxx")
MINIMAX_API_BASE = os.getenv("MINIMAX_API_BASE", "https://api.minimax.io")
PROXY_TIMEOUT = 120.0

print(f"[{datetime.now()}] MiniMax Proxy starting...")
print(f"[{datetime.now()}] API Key configured: {MINIMAX_API_KEY != 'sk-cp-xxxxxxxxxxxx'}")
print(f"[{datetime.now()}] API Base: {MINIMAX_API_BASE}")


def log(msg: str):
    print(f"[{datetime.now()}] {msg}")


def clean_content(content: str) -> str:
    """Clean content by removing <think>... thinking tags"""
    if not content:
        return content
    result = content
    # Remove <think>... tags
    result = re.sub(r'<think>[\s\S]*?', '', result)
    return result.strip()


async def stream_minimax_response(response: httpx.Response, model: str):
    """Stream MiniMax response with proper SSE handling"""
    log("Starting SSE stream handler")
    buffer = ""
    chunk_count = 0
    
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        
        try:
            chunk_str = chunk.decode('utf-8', errors='replace')
        except Exception as e:
            log(f"Decode error: {e}")
            continue
        
        buffer += chunk_str
        chunk_count += 1
        
        # Process complete SSE lines
        while '\n' in buffer:
            line_end = buffer.find('\n')
            line = buffer[:line_end].strip()
            buffer = buffer[line_end + 1:]
            
            if not line:
                continue
            
            if line.startswith('data: '):
                data_str = line[6:].strip()
                
                if data_str == '[DONE]':
                    log(f"Stream done. Total chunks: {chunk_count}")
                    yield b'data: [DONE]\n\n'
                    continue
                
                try:
                    data = json.loads(data_str)
                    
                    if 'choices' in data and len(data['choices']) > 0:
                        choice = data['choices'][0]
                        delta = choice.get('delta', {})
                        
                        # Handle reasoning_content (MiniMax thinking in OpenAI format)
                        reasoning_content = delta.get('reasoning_content', '')
                        if reasoning_content:
                            reasoning_data = {
                                "id": data.get("id", f"chatcmpl-{os.urandom(8).hex()}"),
                                "object": "chat.completion.chunk",
                                "created": data.get("created", int(datetime.now().timestamp())),
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": f"[Thinking] {reasoning_content}"},
                                    "finish_reason": None
                                }]
                            }
                            yield f'data: {json.dumps(reasoning_data, ensure_ascii=False)}\n\n'.encode()
                        
                        # Handle regular content
                        content = delta.get('content', '')
                        if content:
                            cleaned = clean_content(content)
                            if cleaned:
                                delta['content'] = cleaned
                                yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'.encode()
                                continue
                        
                        # If no content after cleaning, pass through
                        if not reasoning_content:
                            yield f'data: {data_str}\n\n'.encode()
                    else:
                        # Pass through usage/stats data
                        yield f'data: {data_str}\n\n'.encode()
                        
                except json.JSONDecodeError:
                    yield f'data: {data_str}\n\n'.encode()
    
    # Handle remaining buffer
    if buffer.strip():
        if buffer.strip().startswith('data: '):
            yield f'{buffer.strip()}\n\n'.encode()


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def proxy_minimax(request: Request):
    """Proxy requests to MiniMax API"""
    log("=" * 50)
    log("RECEIVED REQUEST")
    
    data = await request.json()
    is_streaming = data.get("stream", False)
    model = data.get('model', 'MiniMax-M2.7')
    
    log(f"Stream: {is_streaming}, Model: {model}")
    
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if is_streaming else "application/json"
    }
    
    # Remove problematic params
    for param in ["thinking", "reasoning", "reasoning_level", "extra_params"]:
        if param in data:
            del data[param]
    
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            response = await client.post(
                f"{MINIMAX_API_BASE}/v1/chat/completions",
                json=data,
                headers=headers,
                follow_redirects=True
            )
            
            log(f"Minimax response status: {response.status_code}")
            
            if is_streaming:
                return StreamingResponse(
                    stream_minimax_response(response, model),
                    media_type="text/event-stream"
                )
            else:
                result = response.json()
                
                if 'choices' in result and len(result['choices']) > 0:
                    message = result['choices'][0].get('message', {})
                    content = message.get('content', '')
                    
                    if content:
                        cleaned = clean_content(content)
                        if cleaned != content:
                            message['content'] = cleaned
                            result['choices'][0]['message'] = message
                
                return result
                
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/health")
async def health_check():
    log("Health check called")
    return {"status": "healthy", "service": "minimax_proxy"}


@app.get("/")
async def root():
    log("Root endpoint called")
    return {
        "message": "MiniMax Token Plan Proxy",
        "endpoints": {
            "POST /v1/chat/completions": "Proxy to MiniMax API",
            "POST /chat/completions": "Alias for /v1/chat/completions",
            "GET /health": "Health check"
        }
    }


@app.get("/test")
async def test_endpoint():
    log("Test endpoint called")
    return {
        "status": "ok",
        "message": "Proxy is accessible",
        "api_configured": MINIMAX_API_KEY != "sk-cp-xxxxxxxxxxxx"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)