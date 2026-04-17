# Dockerfile - Minimax Token Plan Proxy using mmx-cli
FROM node:18-slim

WORKDIR /app

# Install mmx-cli globally
RUN npm install -g mmx-cli

# Copy Python app files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY minimax_proxy.py .

EXPOSE 8000

# Run the application
CMD ["python", "-c", "import uvicorn; uvicorn.run('minimax_proxy:app', host='0.0.0.0', port=8000)"]