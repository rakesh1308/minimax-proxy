# minimax_proxy.py - Minimax Token Plan Proxy using mmx-cli with proper streaming
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
    print(f"[{datetime.now()}] {msg}", flush=True)


def clean_content(content: str) -> str:
    """Clean content by removing <think>... thinking tags"""
    if not content:
        return content
    result = content
    result = content.replace('<think>', '').replace('', '')
    return result.strip()


def is_complete_json_object(line: str) -> bool:
    """Check if line is a complete JSON object (not streaming delta)"""
    line = line.strip()
    if not line.startswith('{'):
        return False
    if not line.endswith('}'):
        return False
    try:
        obj = json.loads(line)
        if 'content' in obj and 'usage' in obj:
            return True
        return False
    except:
        return False


async def stream_from_process(process, model):
    """Stream output from mmx-cli process in real-time with proper buffering"""
    log("Starting real-time stream from mmx-cli")
    
    content_buffer = ""
    last_yield_time = datetime.now()
    min_chunk_interval = 0.05  # 50ms minimum between chunks
    
    try:
        while True:
            line = await process.stdout.readline()
            if not line:
                if process.returncode is not None:
                    # Process finished, flush any remaining content
                    if content_buffer:
                        chunk = {
                            "id": f"chatcmpl-{os.urandom(8).hex()}",
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": content_buffer},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode()
                    break
                await asyncio.sleep(0.01)
                continue
            
            line_str = line.decode('utf-8', errors='replace')
            if not line_str:
                continue
            
            stripped = line_str.strip()
            
            # Skip complete JSON summary objects
            if is_complete_json_object(stripped):
                log(f"Skipping complete JSON object (summary)")
                continue
            
            # Skip lines that are JSON fragments without content
            if stripped.startswith('{') and not stripped.endswith('}'):
                try:
                    data = json.loads(stripped)
                    content = data.get('content') or data.get('text')
                    if not content:
                        continue
                except json.JSONDecodeError:
                    pass
            
            # Accumulate content for better formatting
            content_buffer += line_str
            content_buffer = content_buffer.strip()
            
            # Only yield when we have complete lines and enough time has passed
            if '\n' in content_buffer:
                lines = content_buffer.split('\n')
                content_buffer = lines[-1]  # Keep incomplete line
                
                for full_line in lines[:-1]:
                    full_line = full_line.strip()
                    if not full_line:
                        continue
                    
                    # Skip JSON summary lines
                    if is_complete_json_object(full_line):
                        continue
                    
                    cleaned = clean_content(full_line)
                    if cleaned:
                        chunk = {
                            "id": f"chatcmpl-{os.urandom(8).hex()}",
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": cleaned + "\n"},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode()
        
        # Wait for process to complete
        await process.wait()
        
        # Flush remaining content
        if content_buffer.strip():
            cleaned = clean_content(content_buffer)
            if cleaned:
                chunk = {
                    "id": f"chatcmpl-{os.urandom(8).hex()}",
                    "object": "chat.completion.chunk",
                    "created": int(datetime.now().timestamp()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": cleaned},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode()
        
        # Send final chunk
        final_chunk = {
            "id": f"chatcmpl-{os.urandom(8).hex()}",
            "object": "chat.completion.chunk",
            "created": int(datetime.now().timestamp()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(final_chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"
        
    except Exception as e:
        log(f"Stream error: {e}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")
        yield b"data: [DONE]\n\n"


async def read_stderr(process):
    """Read stderr (reasoning logs) from mmx-cli"""
    try:
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            line_str = line.decode('utf-8', errors='replace').strip()
            if line_str and ('Thinking:' in line_str or 'Response:' in line_str):
                log(f"mmx thinking: {line_str[:100]}...")
    except Exception as e:
        log(f"Stderr read error: {e}")


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def proxy_minimax(request: Request):
    """Proxy requests to MiniMax via mmx-cli"""
    log("=" * 50)
    log("RECEIVED REQUEST")
    
    data = await request.json()
    is_streaming = data.get("stream", False)
    model = data.get('model', 'MiniMax-M2.7')
    
    log(f"Stream: {is_streaming}, Model: {model}")
    
    messages = data.get('messages', [])
    
    if not messages:
        return JSONResponse(status_code=400, content={"error": "No messages provided"})
    
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
    
    model_suffix = model.replace("MiniMax-", "")
    
    cmd = ["mmx", "text", "chat", 
           "--message", user_message, 
           "--model", model_suffix]
    
    if is_streaming:
        cmd.append("--stream")
    
    if system_message:
        cmd.extend(["--system", system_message])
    
    log(f"Running: {' '.join(cmd)}")
    
    try:
        env = os.environ.copy()
        env["MINIMAX_API_KEY"] = MINIMAX_API_KEY
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        
        log(f"Process started with PID: {process.pid}")
        
        if is_streaming:
            stderr_task = asyncio.create_task(read_stderr(process))
            
            async def generate():
                async for chunk in stream_from_process(process, model):
                    yield chunk
                await stderr_task
            
            return StreamingResponse(generate(), media_type="text/event-stream")
        else:
            stdout_lines = []
            stderr_lines = []
            
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                stdout_lines.append(line.decode('utf-8', errors='replace').strip())
            
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode('utf-8', errors='replace').strip())
            
            await process.wait()
            
            full_content = ""
            for line in stdout_lines:
                if is_complete_json_object(line):
                    continue
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        content = data.get('content') or data.get('text')
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
        log(f"ERROR: {e}")
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
        "using": "mmx-cli",
        "api_key_configured": MINIMAX_API_KEY != "sk-cp-xxxxxxxxxxxx"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)