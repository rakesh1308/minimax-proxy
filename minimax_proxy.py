# minimax_proxy.py - Minimax Token Plan Proxy using mmx-cli for reliable streaming
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import json
import os
import asyncio
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
PROXY_TIMEOUT = 120.0

print(f"[{datetime.now()}] MiniMax Proxy starting (using mmx-cli)...")
print(f"[{datetime.now()}] API Key configured: {MINIMAX_API_KEY != 'sk-cp-xxxxxxxxxxxx'}")


def log(msg: str):
    print(f"[{datetime.now()}] {msg}")


def clean_content(content: str) -> str:
    """Clean content by removing <think>... thinking tags"""
    if not content:
        return content
    result = content
    # Remove <think>... tags
    result = content.replace('<think>', '').replace('', '')
    return result.strip()


async def read_all_lines(stream):
    """Read all lines from a stream"""
    lines = []
    while True:
        line = await stream.readline()
        if not line:
            break
        lines.append(line.decode('utf-8', errors='replace').strip())
    return lines


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def proxy_minimax(request: Request):
    """Proxy requests to MiniMax via mmx-cli"""
    log("=" * 50)
    log("RECEIVED REQUEST to /v1/chat/completions")
    
    data = await request.json()
    is_streaming = data.get("stream", False)
    model = data.get('model', 'MiniMax-M2.7')
    
    log(f"Streaming: {is_streaming}, Model: {model}")
    
    # Extract messages
    messages = data.get('messages', [])
    
    if not messages:
        return JSONResponse(status_code=400, content={"error": "No messages provided"})
    
    # Get last user message
    user_message = ""
    system_message = ""
    
    for msg in messages:
        role = msg.get('role', '')
        content = msg.get('content', '')
        if isinstance(content, list):
            content = ' '.join([c.get('text', '') for c in content if isinstance(c, dict)])
        
        if role == 'system':
            system_message = content
        elif role == 'user':
            user_message = content
    
    if not user_message:
        return JSONResponse(status_code=400, content={"error": "No user message found"})
    
    log(f"User message: {user_message[:100]}...")
    
    # Build mmx command
    # Map model names: MiniMax-M2.7 -> M2.7, MiniMax-M2.5 -> M2.5, etc.
    model_suffix = model.replace("MiniMax-", "")
    cmd = ["mmx", "text", "chat", "--message", user_message, "--model", model_suffix, "--stream"]
    
    if system_message:
        cmd.extend(["--system", system_message])
    
    log(f"Running command: {' '.join(cmd)}")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "MINIMAX_API_KEY": MINIMAX_API_KEY}
        )
        
        if is_streaming:
            async def stream_output():
                try:
                    # Read stdout in a separate task
                    stdout_task = asyncio.create_task(read_all_lines(process.stdout))
                    
                    # Read stderr for thinking content
                    stderr_task = asyncio.create_task(read_all_lines(process.stderr))
                    
                    # Wait for both
                    stdout_lines, stderr_lines = await asyncio.gather(stdout_task, stderr_task)
                    
                    await process.wait()
                    
                    # Log stderr (reasoning/thinking)
                    for line in stderr_lines:
                        if line:
                            log(f"mmx reasoning: {line[:200]}...")
                    
                    # Process stdout lines and yield streaming chunks
                    for line in stdout_lines:
                        if not line:
                            continue
                        
                        log(f"mmx output: {line[:200]}...")
                        
                        if line.startswith('{'):
                            try:
                                chunk_data = json.loads(line)
                                
                                # Handle different response formats from mmx
                                content = chunk_data.get('content', '') or chunk_data.get('text', '')
                                reasoning = chunk_data.get('reasoning_content', '') or chunk_data.get('thinking', '')
                                
                                if content:
                                    cleaned = clean_content(content)
                                    if cleaned:
                                        delta = {"content": cleaned}
                                        chunk = {
                                            "id": f"chatcmpl-{os.urandom(12).hex()}",
                                            "object": "chat.completion.chunk",
                                            "created": int(datetime.now().timestamp()),
                                            "model": model,
                                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]
                                        }
                                        yield f"data: {json.dumps(chunk)}\n\n".encode()
                                
                                if reasoning:
                                    delta = {"content": f"[Thinking: {reasoning[:500]}...]" if len(reasoning) > 500 else f"[Thinking: {reasoning}]"}
                                    chunk = {
                                        "id": f"chatcmpl-{os.urandom(12).hex()}",
                                        "object": "chat.completion.chunk",
                                        "created": int(datetime.now().timestamp()),
                                        "model": model,
                                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n".encode()
                                    
                            except json.JSONDecodeError:
                                pass
                        elif line and not line.startswith('data:'):
                            # Plain text output
                            delta = {"content": clean_content(line)}
                            chunk = {
                                "id": f"chatcmpl-{os.urandom(12).hex()}",
                                "object": "chat.completion.chunk",
                                "created": int(datetime.now().timestamp()),
                                "model": model,
                                "choices": [{"index": 0, "delta": delta, "finish_reason": None}]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n".encode()
                    
                    # Send final chunk
                    final_chunk = {
                        "id": f"chatcmpl-{os.urandom(12).hex()}",
                        "object": "chat.completion.chunk",
                        "created": int(datetime.now().timestamp()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(final_chunk)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                except Exception as e:
                    log(f"Stream error: {e}")
                    yield b"data: [DONE]\n\n"
            
            return StreamingResponse(stream_output(), media_type="text/event-stream")
        else:
            # Non-streaming: collect all output
            stdout_lines = await read_all_lines(process.stdout)
            stderr_lines = await read_all_lines(process.stderr)
            
            await process.wait()
            
            # Log stderr
            for line in stderr_lines:
                if line:
                    log(f"mmx reasoning: {line[:200]}...")
            
            # Parse response
            full_content = ""
            for line in stdout_lines:
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        content = data.get('content', '') or data.get('text', '')
                        if content:
                            full_content += content
                    except:
                        pass
                else:
                    full_content += line + "\n"
            
            result = {
                "id": f"chatcmpl-{os.urandom(12).hex()}",
                "object": "chat.completion",
                "created": int(datetime.now().timestamp()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": clean_content(full_content)
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            }
            
            return result
            
    except Exception as e:
        log(f"ERROR: {str(e)}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/health")
async def health_check():
    log("Health check called")
    return {"status": "healthy", "service": "minimax_proxy", "using": "mmx-cli"}


@app.get("/")
async def root():
    log("Root endpoint called")
    return {
        "message": "MiniMax Token Plan Proxy (using mmx-cli)",
        "endpoints": {
            "POST /v1/chat/completions": "Proxy to MiniMax via mmx-cli",
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
        "using": "mmx-cli"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)