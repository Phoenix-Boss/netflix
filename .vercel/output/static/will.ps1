cd "C:\Users\Boss\Desktop\EDGES\vidsrc-api"

# =====================================================================
# 1. DELETE VERCEL ARTIFACTS
# =====================================================================
Write-Host "[1/6] Removing Vercel files..." -ForegroundColor Yellow
Remove-Item -Force "vercel.json", "package.json", "package-lock.json" -ErrorAction SilentlyContinue

# =====================================================================
# 2. FIX .GITIGNORE (CRITICAL FOR test.js)
# =====================================================================
Write-Host "[2/6] Fixing .gitignore..." -ForegroundColor Yellow
 $gitignore = @"
node_modules/
__pycache__/
*.pyc
.pyo
.env
.env.local
*.png
*.html
test*.py
dump*.py
debug*.py
inspect*.py
analyze_api.py
trace_flex.js
"@
Set-Content ".gitignore" -Value $gitignore -Encoding UTF8

# =====================================================================
# 3. CREATE RENDER DOCKERFILE
# =====================================================================
Write-Host "[3/6] Creating Render Dockerfile..." -ForegroundColor Yellow
 $dockerfile = @'
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

COPY . .

CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"
'@
Set-Content "Dockerfile" -Value $dockerfile -Encoding UTF8

# =====================================================================
# 4. CREATE NODE PACKAGE.JSON (FOR PLAYWRIGHT ONLY)
# =====================================================================
Write-Host "[4/6] Creating Node package for Playwright..." -ForegroundColor Yellow
 $pkg = '{"dependencies": {"playwright": "^1.40.0"}}'
Set-Content "package.json" -Value $pkg -Encoding UTF8

# =====================================================================
# 5. UPDATE REQUIREMENTS.TXT
# =====================================================================
 $req = @"
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0.0
curl_cffi>=0.7.0
pycryptodome>=3.19.0
httpx>=0.25.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
pytesseract>=0.3.10
Pillow>=10.0.0
"@
Set-Content "requirements.txt" -Value $req -Encoding UTF8

# =====================================================================
# 6. WRITE test.js
# =====================================================================
Write-Host "[5/6] Writing test.js..." -ForegroundColor Yellow
 $jsCode = @"
const { chromium } = require('playwright');

async function robustGoto(page, url, retries) {
    retries = retries || 3;
    for (var i = 0; i < retries; i++) {
        try {
            await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
            return true;
        } catch (e) {
            if (i === retries - 1) throw e;
            await page.waitForTimeout(3000);
        }
    }
