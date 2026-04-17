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
        # Check if it's a complete response object with content field
        if 'content' in obj and 'usage' in obj:
            return True
        return False
    except:
        return False


async def stream_from_process(process, model):
    """Stream output from mmx-cli process in real-time"""
    log("Starting real-time stream from mmx-cli")
    sent_chunks = set()
    
    try:
        # Read stdout line by line as they come
        while True:
            line = await process.stdout.readline()
            if not line:
                # Check if process has exited
                if process.returncode is not None:
                    break
                await asyncio.sleep(0.01)
                continue
            
            line_str = line.decode('utf-8', errors='replace').strip()
            if not line_str:
                continue
            
            # Skip complete JSON response objects (they're summaries, not streaming deltas)
            if is_complete_json_object(line_str):
                log(f"Skipping complete JSON object (summary)")
                continue
            
            log(f"mmx output: {line_str[:100]}...")
            
            # Parse JSON line for streaming chunks
            if line_str.startswith('{') and not line_str.endswith('}'):
                # Partial JSON - might be streaming delta
                try:
                    data = json.loads(line_str)
                    
                    # Check for content in various fields
                    content = (data.get('content') or data.get('text') or 
                              data.get('message', {}).get('content', ''))
                    reasoning = (data.get('reasoning_content') or data.get('thinking') or
                               data.get('reasoning') or data.get('message', {}).get('reasoning_content', ''))
                    
                    chunk_id = data.get('id', f"chatcmpl-{os.urandom(8).hex()}")
                    created = data.get('created', int(datetime.now().timestamp()))
                    
                    # Yield reasoning as thinking
                    if reasoning and reasoning not in sent_chunks:
                        sent_chunks.add(reasoning)
                        chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": f"[Thinking] {reasoning}"},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode()
                    
                    # Yield content
                    if content and content not in sent_chunks:
                        sent_chunks.add(content)
                        cleaned = clean_content(content)
                        if cleaned:
                            chunk = {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": cleaned},
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n".encode()
                            
                except json.JSONDecodeError:
                    pass
            
            # Handle plain text lines (streaming content)
            elif line_str and not line_str.startswith('{') and not line_str.startswith('data:'):
                # Plain text streaming output
                cleaned = clean_content(line_str)
                if cleaned and len(cleaned) > 2:  # Skip very short lines
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
        
        # Process finished, wait for any remaining output
        await process.wait()
        
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
        reasoning_buffer = []
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            line_str = line.decode('utf-8', errors='replace').strip()
            if line_str:
                # Log thinking process
                if 'Thinking:' in line_str or 'Response:' in line_str:
                    log(f"mmx thinking: {line_str[:100]}...")
                else:
                    reasoning_buffer.append(line_str)
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
    
    # Extract messages
    messages = data.get('messages', [])
    
    if not messages:
        return JSONResponse(status_code=400, content={"error": "No messages provided"})
    
    # Get messages
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
    model_suffix = model.replace("MiniMax-", "")
    
    # Build command - use --stream for streaming output
    cmd = ["mmx", "text", "chat", 
           "--message", user_message, 
           "--model", model_suffix]
    
    # Add --stream flag for streaming
    if is_streaming:
        cmd.append("--stream")
    
    if system_message:
        cmd.extend(["--system", system_message])
    
    log(f"Running: {' '.join(cmd)}")
    
    try:
        # Set environment with API key
        env = os.environ.copy()
        env["MINIMAX_API_KEY"] = MINIMAX_API_KEY
        
        # Start process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        
        log(f"Process started with PID: {process.pid}")
        
        if is_streaming:
            # Start stderr reader task
            stderr_task = asyncio.create_task(read_stderr(process))
            
            async def generate():
                async for chunk in stream_from_process(process, model):
                    yield chunk
                await stderr_task
            
            return StreamingResponse(generate(), media_type="text/event-stream")
        else:
            # Non-streaming: collect all output
            stdout_lines = []
            stderr_lines = []
            
            # Read stdout
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                stdout_lines.append(line.decode('utf-8', errors='replace').strip())
            
            # Read stderr
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode('utf-8', errors='replace').strip())
            
            await process.wait()
            
            # Log stderr
            for line in stderr_lines:
                if line:
                    log(f"mmx stderr: {line[:200]}...")
            
            # Parse response - skip complete JSON objects
            full_content = ""
            for line in stdout_lines:
                if is_complete_json_object(line):
                    continue
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        content = (data.get('content') or data.get('text') or
                                 data.get('message', {}).get('content', ''))
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