# minimax_proxy.py - Minimal working proxy for Minimax Token Plan
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import re
import json
import os

app = FastAPI()

# Configuration - Set these in Zeabur environment variables
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "sk-cp-xxxxxxxxxxxx")
PROXY_TIMEOUT = 60.0


def clean_thinking_blocks(content: str) -> str:
    """Remove thinking blocks and fix parameter format"""
    if not content:
        return content
    
    # Remove thinking blocks - be careful with nested tags
    result = content
    
    # Use a simpler approach that works better with streaming
    # Match <thinking>...</thinking> tags (case-insensitive)
    thinking_pattern = re.compile(r'<thinking>[\s\S]*?</thinking>', re.IGNORECASE)
    result = thinking_pattern.sub('', result)
    
    # Fix parameter format: <parameter name="x"> to <parameter=x>
    result = re.sub(r'<parameter name="([^"]+)">', r'<parameter=\1>', result)
    
    return result


async def clean_minimax_stream(response: httpx.Response):
    """Clean thinking blocks from Minimax streaming response"""
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
                
                # Try to parse and clean the JSON
                try:
                    data = json.loads(data_str)
                    
                    if 'choices' in data and len(data['choices']) > 0:
                        choice = data['choices'][0]
                        
                        # Handle delta content in streaming
                        if 'delta' in choice:
                            delta = choice.get('delta', {})
                            content = delta.get('content', '')
                            
                            if content:
                                cleaned_content = clean_thinking_blocks(content)
                                if cleaned_content != content:
                                    delta['content'] = cleaned_content
                                    choice['delta'] = delta
                                    yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'.encode()
                                    continue
                        
                        # Handle message content in non-streaming format
                        elif 'message' in choice:
                            message = choice.get('message', {})
                            content = message.get('content', '')
                            
                            if content:
                                cleaned_content = clean_thinking_blocks(content)
                                if cleaned_content != content:
                                    message['content'] = cleaned_content
                                    choice['message'] = message
                                    yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'.encode()
                                    continue
                    
                    # Pass through unchanged
                    yield f'data: {data_str}\n\n'.encode()
                    
                except json.JSONDecodeError:
                    # Pass through non-JSON data as-is
                    yield f'data: {data_str}\n\n'.encode()
    
    # Yield any remaining buffer content
    if buffer.strip():
        if buffer.strip().startswith('data: '):
            yield f'{buffer.strip()}\n\n'.encode()


@app.post("/v1/chat/completions")
async def proxy_minimax(request: Request):
    """Proxy requests to Minimax Token Plan API"""
    data = await request.json()
    
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    
    # Set accept header based on streaming
    if data.get("stream", False):
        headers["Accept"] = "text/event-stream"
    else:
        headers["Accept"] = "application/json"
    
    # Remove problematic parameters that might cause issues
    if "thinking" in data:
        del data["thinking"]
    if "reasoning" in data:
        del data["reasoning"]
    if "reasoning_level" in data:
        del data["reasoning_level"]
    if "extra_params" in data:
        del data["extra_params"]
    
    async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
        response = await client.post(
            "https://api.minimax.io/v1/chat/completions",
            json=data,
            headers=headers,
            follow_redirects=True
        )
        
        if data.get("stream", False):
            return StreamingResponse(
                clean_minimax_stream(response),
                media_type="text/event-stream"
            )
        else:
            # Handle non-streaming responses
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                message = result['choices'][0].get('message', {})
                content = message.get('content', '')
                
                if content:
                    # Clean thinking blocks from non-streaming response
                    cleaned_content = clean_thinking_blocks(content)
                    
                    if cleaned_content != content:
                        message['content'] = cleaned_content
                        result['choices'][0]['message'] = message
            
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