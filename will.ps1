cd "C:\Users\Boss\Desktop\EDGES\vidsrc-api"

 $files = @(
    "models/utils.py",
    "models/vidapi.py",
    "models/fzmovies.py",
    "models/o2tv.py",
    "models/subtitles.py",
    "models/dramacool.py",
    "models/kissasian.py",
    "models/shortdrama.py"
)

Write-Host "Injecting proxy routing into existing models..." -ForegroundColor Yellow

foreach ($f in $files) {
    # Read file preserving exactly what you have
    $content = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
    $modified = $false
    
    # 1. Add 'import os' if not already there (handles BOM perfectly)
    if ($content -notmatch 'import os') {
        if ($content.StartsWith([char]0xFEFF)) {
            $content = [char]0xFEFF + "import os`r`n" + $content.Substring(1)
        } else {
            $content = "import os`r`n" + $content
        }
        $modified = $true
    }

    # 2. Target AsyncSession (fzmovies, o2tv, utils, subs, vidapi)
    if ($content.Contains('AsyncSession(impersonate="chrome", verify=False)') -and !$content.Contains('proxy=os.getenv')) {
        $content = $content.Replace(
            'AsyncSession(impersonate="chrome", verify=False)',
            'AsyncSession(impersonate="chrome", verify=False, proxy=os.getenv("PROXY_URL"))'
        )
        $modified = $true
    }
    if ($content.Contains('AsyncSession(impersonate="chrome")') -and !$content.Contains('proxy=os.getenv')) {
        $content = $content.Replace(
            'AsyncSession(impersonate="chrome")',
            'AsyncSession(impersonate="chrome", proxy=os.getenv("PROXY_URL"))'
        )
        $modified = $true
    }

    # 3. Target Synchronous Session (dramacool, kissasian, shortdrama)
    if ($content.Contains('Session(impersonate="chrome131")') -and !$content.Contains('proxy=os.getenv')) {
        $content = $content.Replace(
            'Session(impersonate="chrome131")',
            'Session(impersonate="chrome131", proxy=os.getenv("PROXY_URL"))'
        )
        $modified = $true
    }

    # Save only if changes were made
    if ($modified) {
        [System.IO.File]::WriteAllText($f, $content, [System.Text.Encoding]::UTF8)
        Write-Host "[OK] $f patched successfully." -ForegroundColor Green
    } else {
        Write-Host "[SKIP] $f already has proxy or no session found." -ForegroundColor DarkGray
    }
}

# Update Dockerfile just in case standard python libraries are used under the hood
 $dockerfile = @"
FROM python:3.9-slim

RUN apt-get update && apt-get install -y build-essential libcurl4-openssl-dev libssl-dev tesseract-ocr && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV HTTPS_PROXY=${PROXY_URL}
ENV HTTP_PROXY=${PROXY_URL}

COPY requirements.txt .
RUN pip install --no-cache-dir --quiet -r requirements.txt
COPY . .
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"
"@
[System.IO.File]::WriteAllText("Dockerfile", $dockerfile, [System.Text.Encoding]::UTF8)
Write-Host "[OK] Dockerfile updated with ENV proxy fallback." -ForegroundColor Green

Write-Host "`nCommitting and pushing to Render..." -ForegroundColor Cyan
git add .
git commit -m "feat: inject global proxy into all curl_cffi sessions"
git push