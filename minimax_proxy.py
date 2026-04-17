# minimax_proxy.py - Minimax Token Plan Proxy with proper OpenAI streaming
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import re
import json
import os

app = FastAPI()

# Configuration - Set these in Zeabur environment variables
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "sk-cp-xxxxxxxxxxxx")
PROXY_TIMEOUT = 120.0


def clean_content(content: str) -> str:
    """Clean content by removing <think>... thinking tags"""
    if not content:
        return content
    
    result = content
    
    # Remove <think>... tags (MiniMax uses these in OpenAI format)
    result = re.sub(r'<think>[\s\S]*?', '', result)
    
    # Also handle any <thinking>...</thinking> tags (for compatibility)
    thinking_pattern = re.compile(r'<thinking>[\s\S]*?</thinking>', re.IGNORECASE)
    result = thinking_pattern.sub('', result)
    
    # Fix parameter format: <parameter name="x"> to <parameter=x>
    result = re.sub(r'<parameter name="([^"]+)">', r'<parameter=\1>', result)
    
    return result.strip()


async def stream_minimax_response(response: httpx.Response):
    """Stream MiniMax response, properly handling reasoning_content and content"""
    buffer = ""
    
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
            
        try:
            chunk_str = chunk.decode('utf-8', errors='replace')
        except Exception:
            continue
            
        buffer += chunk_str
        
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
                    yield b'data: [DONE]\n\n'
                    continue
                
                try:
                    data = json.loads(data_str)
                    
                    if 'choices' in data and len(data['choices']) > 0:
                        choice = data['choices'][0]
                        delta = choice.get('delta', {})
                        
                        # Handle reasoning_content (MiniMax's thinking in OpenAI format)
                        reasoning_content = delta.get('reasoning_content', '')
                        if reasoning_content:
                            # Create a reasoning chunk
                            reasoning_delta = {
                                "role": "assistant",
                                "reasoning_content": reasoning_content
                            }
                            reasoning_data = {
                                "id": data.get("id", ""),
                                "object": "chat.completion.chunk",
                                "created": data.get("created", 0),
                                "model": data.get("model", ""),
                                "choices": [{
                                    "index": 0,
                                    "delta": reasoning_delta,
                                    "finish_reason": None
                                }]
                            }
                            yield f'data: {json.dumps(reasoning_data, ensure_ascii=False)}\n\n'.encode()
                        
                        # Handle regular content
                        content = delta.get('content', '')
                        if content:
                            # Clean content (remove <think> tags)
                            cleaned_content = clean_content(content)
                            
                            if cleaned_content:
                                delta['content'] = cleaned_content
                                choice['delta'] = delta
                                yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'.encode()
                                continue
                        
                        # If no content after cleaning, just pass through
                        if not reasoning_content:
                            yield f'data: {data_str}\n\n'.encode()
                    else:
                        # Pass through non-choice data (like usage stats at end)
                        yield f'data: {data_str}\n\n'.encode()
                        
                except json.JSONDecodeError:
                    yield f'data: {data_str}\n\n'.encode()
    
    # Yield any remaining buffer content
    if buffer.strip():
        if buffer.strip().startswith('data: '):
            yield f'{buffer.strip()}\n\n'.encode()


@app.post("/v1/chat/completions")
async def proxy_minimax(request: Request):
    """Proxy requests to Minimax Token Plan API"""
    data = await request.json()
    
    # Check if streaming is requested
    is_streaming = data.get("stream", False)
    
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    
    if is_streaming:
        headers["Accept"] = "text/event-stream"
    else:
        headers["Accept"] = "application/json"
    
    # Remove problematic parameters that might cause issues
    problem_params = ["thinking", "reasoning", "reasoning_level", "extra_params"]
    for param in problem_params:
        if param in data:
            del data[param]
    
    async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
        response = await client.post(
            "https://api.minimax.io/v1/chat/completions",
            json=data,
            headers=headers,
            follow_redirects=True
        )
        
        if is_streaming:
            return StreamingResponse(
                stream_minimax_response(response),
                media_type="text/event-stream"
            )
        else:
            # Handle non-streaming responses
            result = response.json()
            
            if 'choices' in result and len(result['choices']) > 0:
                message = result['choices'][0].get('message', {})
                content = message.get('content', '')
                
                if content:
                    cleaned_content = clean_content(content)
                    if cleaned_content != content:
                        message['content'] = cleaned_content
                        result['choices'][0]['message'] = message
                
                # Handle reasoning_details in non-streaming
                reasoning_details = message.get('reasoning_details', [])
                if reasoning_details and isinstance(reasoning_details, list):
                    # Keep reasoning separate, don't merge into content
                    pass
            
            return result


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "minimax_proxy"}


@app.get("/")
async def root():
    return {
        "message": "Minimax Token Plan Proxy",
        "endpoints": {
            "POST /v1/chat/completions": "Proxy to Minimax API",
            "GET /health": "Health check",
            "GET /": "This info page"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)