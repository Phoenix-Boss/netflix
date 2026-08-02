FROM python:3.9-slim

ENV NODE_VERSION=20.x
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    libcurl4-openssl-dev \
    libssl-dev \
    tesseract-ocr \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION} | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY package.json .
RUN npm install

RUN npx playwright install chromium

