FROM python:3.9-slim

# Install system dependencies for curl_cffi and Tesseract (for O2TV)
RUN apt-get update && apt-get install -y \
    build-essential \
    libcurl4-openssl-dev \
    libssl-dev \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects the PORT env variable automatically
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"
