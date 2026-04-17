# Dockerfile - Minimax Token Plan Proxy using mmx-cli
FROM python:3.11-slim

# Install Node.js for mmx-cli
RUN apt-get update && apt-get install -y curl \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && npm install -g mmx-cli

WORKDIR /app

# Copy Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY minimax_proxy.py .

# Run the application
EXPOSE 8000
CMD ["uvicorn", "minimax_proxy:app", "--host", "0.0.0.0", "--port", "8000"]