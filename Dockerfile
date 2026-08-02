FROM python:3.9-slim

RUN apt-get update && apt-get install -y build-essential libcurl4-openssl-dev libssl-dev tesseract-ocr && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV HTTPS_PROXY=
ENV HTTP_PROXY=

COPY requirements.txt .
RUN pip install --no-cache-dir --quiet -r requirements.txt
COPY . .
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port "