# Dockerfile - Minimal setup for Zeabur
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY minimax_proxy.py .

# Run the application
EXPOSE 8000
CMD ["uvicorn", "minimax_proxy:app", "--host", "0.0.0.0", "--port", "8000"]