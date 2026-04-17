# Dockerfile - Minimax Token Plan Proxy using mmx-cli
FROM node:18-slim

# Install Python and pip
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install mmx-cli globally
RUN npm install -g mmx-cli

# Copy Python requirements and install (use --break-system-packages for externally-managed env)
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application
COPY minimax_proxy.py .

EXPOSE 8000

# Run the application
CMD ["python3", "-m", "uvicorn", "minimax_proxy:app", "--host", "0.0.0.0", "--port", "8000"]