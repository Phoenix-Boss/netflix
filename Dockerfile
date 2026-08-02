FROM python:3.9-slim

# Install system-level dependencies required by curl_cffi
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcurl4-openssl-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Run the FastAPI app on Vercel's required port
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
