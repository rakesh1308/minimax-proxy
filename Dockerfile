# Dockerfile - Minimax Token Plan Proxy with proper SSE streaming
FROM python:3.11-slim

WORKDIR /app

# Copy Python requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY minimax_proxy.py .

EXPOSE 8000

# Run the application
CMD ["uvicorn", "minimax_proxy:app", "--host", "0.0.0.0", "--port", "8000"]