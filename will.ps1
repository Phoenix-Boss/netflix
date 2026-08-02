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
}

function getSimilarity(a, b) {
    a = a.toLowerCase().replace(/[^a-z0-9]/g, '');
    b = b.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (a === b) return 100;
    if (a.length > 0 && b.length > 0 && (a.indexOf(b) !== -1 || b.indexOf(a) !== -1)) return 100;
    if (a.length === 0 || b.length === 0) return 0;
    var matrix = [];
    for (var i = 0; i <= b.length; i++) matrix[i] = [i];
    for (var j = 0; j <= a.length; j++) matrix[0][j] = j;
    for (var i = 1; i <= b.length; i++) {
        for (var j = 1; j <= a.length; j++) {
            if (b.charAt(i - 1) === a.charAt(j - 1)) matrix[i][j] = matrix[i - 1][j - 1];
            else matrix[i][j] = Math.min(matrix[i - 1][j - 1] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j] + 1);
        }
    }
    var maxLen = Math.max(a.length, b.length);
    return ((maxLen - matrix[b.length][a.length]) / maxLen) * 100;
}

(async () => {
    var movieName = process.argv[2];
    if (!movieName) { process.exit(1); }

    var baseUrl = "https://fzmovies.live";
    var browser = await chromium.launch({ headless: true, args: ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"] });
    var context = await browser.newContext({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", viewport: { width: 1920, height: 1080 }, locale: 'en-US' });
    await context.addInitScript("Object.defineProperty(navigator, 'webdriver', { get: function() { return undefined; } }); window.open = function() { return null; };");
    var page = await context.newPage();

    try {
        await robustGoto(page, baseUrl + "/csearch.php");
    } catch(e) { await browser.close(); process.exit(1); }
    
    await page.fill("#searchname", movieName);
    await page.click("input[type='submit']");
    await page.waitForLoadState("domcontentloaded");
    
    var searchResults = await page.evaluate(function() {
        var results = []; var html = document.body.innerHTML;
        var regex = /href=\"(movie-[^"]+\.htm)\"[^>]*>([\s\S]*?)<\/a>/gi; var match;
        while ((match = regex.exec(html)) !== null) {
            var rawText = match[2].replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();
            if (rawText.length > 3) { var ym = rawText.match(/\((\d{4})\)/); results.push({ url: match[1], title: rawText, year: ym ? parseInt(ym[1]) : 0 }); }
        }
        results.sort(function(a, b) { return b.year - a.year; });
        return results;
    });
    
    var selectedMovie = null;
    for (var i = 0; i < searchResults.length; i++) {
        if (getSimilarity(movieName, searchResults[i].title) >= 90) { selectedMovie = searchResults[i]; break; }
    }
    if (!selectedMovie) { await browser.close(); process.exit(1); }

    await robustGoto(page, baseUrl + "/" + selectedMovie.url.replace(/^\//, ""));
    try { await page.waitForSelector("a[href*='download1.php'], a[onclick*='download1.php']", { timeout: 15000 }); } catch(e) { await browser.close(); process.exit(1); }
    
    var qualities = await page.evaluate(function(baseUrl) {
        var results = [], links = document.querySelectorAll("a"), seen = {};
        for (var i = 0; i < links.length; i++) {
            var el = links[i], text = (el.innerText || "").trim();
            if (text.indexOf(".mp4") === -1 && text.indexOf(".mkv") === -1) continue;
            var url = "", href = el.getAttribute("href") || "", onclick = el.getAttribute("onclick") || "";
            if (href.indexOf("download1.php") !== -1) url = href.indexOf("http") === 0 ? href : baseUrl + "/" + href.replace(/^\//, "");
            else if (onclick.indexOf("download1.php") !== -1) { var m = onclick.match(/download1\.php\?downloadoptionskey=([^&\"']+)&pt=([^\"']+)/); if(m) url = baseUrl + "/download1.php?downloadoptionskey=" + m[1] + "&pt=" + m[2]; }
            else { var p = el.closest("li") || el.parentElement; if(p) { var pl = p.querySelectorAll("a[href*='download1.php'], a[onclick*='download1.php']"); for(var j=0;j<pl.length;j++) { var ph=pl[j].getAttribute("href")||"", po=pl[j].getAttribute("onclick")||""; if(ph.indexOf("download1.php")!==-1){url=ph.indexOf("http")===0?ph:baseUrl+"/"+ph.replace(/^\//,"");break;} else if(po.indexOf("download1.php")!==-1){var pm=po.match(/download1\.php\?downloadoptionskey=([^&\"']+)&pt=([^\"']+)/);if(pm){url=baseUrl+"/download1.php?downloadoptionskey="+pm[1]+"&pt="+pm[2];break;}}}}}
            if (!url || url.indexOf("download1.php") === -1 || seen[url]) continue; seen[url] = true;
            var pt = (el.closest("li") || el.parentElement).innerText || ""; var size = "Unknown", sm = pt.match(/\(\s*(\d+(?:\.\d+)?)\s*(MB|GB)\s*\)/i); if(sm) size = sm[1] + " " + sm[2].toUpperCase();
            results.push({ url: url, text: text.replace(/\s+/g, " ").trim(), size: size });
        }
        return results;
    }, baseUrl);

    var finalResults = [];
    for (var i = 0; i < qualities.length; i++) {
        var quality = qualities[i];
        if (quality.url.indexOf("http") !== 0) continue;
        try {
            await robustGoto(page, quality.url);
            var clicked = false;
            var btns = ["text=DOWNLOAD THIS MOVIE ON YOUR DEVICE", "a:has-text('DOWNLOAD')", "input[type='submit']"];
            for (var s = 0; s < btns.length; s++) { try { var b = await page.waitForSelector(btns[s], { timeout: 5000 }); if(b){await b.click(); clicked=true;break;}}catch(e){continue;} }
            if (!clicked) continue;
            await page.waitForURL("**/download.php**", { timeout: 20000 }); await page.waitForTimeout(2000);
            var mp4Links = await page.evaluate(function(baseUrl) {
                var links = [], regex = /href=\"(dlink\.php\?[^"]+)\"/gi, match;
                while ((match = regex.exec(document.body.innerHTML)) !== null) { var link = match[1].replace(/&amp;/g, "&"); links.push(link.indexOf("http") === 0 ? link : baseUrl + "/" + link.replace(/^\//, "")); }
                return links;
            }, baseUrl);
            if (mp4Links.length > 0) finalResults.push({ file: quality.text, size: quality.size, downloads: mp4Links });
        } catch (e) { continue; }
    }
    await browser.close();
    console.log(JSON.stringify(finalResults));
})();
"@
Set-Content "test.js" -Value $jsCode -Encoding UTF8

# =====================================================================
# 7. WRITE models/fzmovies.py
# =====================================================================
 $fzCode = @"
import json
import asyncio
import os

async def get_fallback_stream(title: str, requested_quality: str = None):
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'test.js')
    
    if not os.path.exists(script_path):
        print("[fzmovies] Error: test.js not found in root directory.")
        return None
        
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", script_path, title,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        
        if proc.returncode != 0 or not stdout:
            return None
            
        results = json.loads(stdout.decode('utf-8'))
        if not results:
            return None
            
        def get_q_score(file_name):
            fn = file_name.lower()
            if "2160p" in fn or "4k" in fn: return 0
            if "1080p" in fn: return 1
            if "720p" in fn: return 2
            if "webrip" in fn or "bluray" in fn: return 2.5
            if "480p" in fn: return 3
            if "camrip" in fn or "hdcam" in fn: return 4
            return 99
            
        results.sort(key=lambda x: get_q_score(x['file']))
        
        selected = None
        needs_transcode = False
        actual_quality = "auto"
        
        if requested_quality:
            req_q = requested_quality.lower().replace("p", "")
            for res in results:
                if req_q in res['file'].lower():
                    selected = res
                    actual_quality = requested_quality
                    break
            
            if not selected and results:
                selected = results[0]
                needs_transcode = True
                for q in ["2160p", "4k", "1080p", "720p", "480p"]:
                    if q in selected['file'].lower():
                        actual_quality = q
                        break
        else:
            selected = results[0]
            
        if not selected or not selected.get('downloads'):
            return None
            
        return {
            "stream": selected['downloads'][0],
            "quality": actual_quality,
            "needs_transcode": needs_transcode,
            "title": selected['file'],
            "size": selected['size']
        }
        
    except asyncio.TimeoutError:
        print("[fzmovies] Timeout reached (90s)")
        return None
    except Exception as e:
        print(f"[fzmovies] Parsing error: {e}")
        return None
"@
Set-Content "models\fzmovies.py" -Value $fzCode -Encoding UTF8

# =====================================================================
# 8. WRITE models/o2tv.py (RENDER/LINUX COMPATIBLE)
# =====================================================================
 $o2tvCode = @"
import asyncio
import re
import io
import shutil
import sys
from pathlib import Path
import pytesseract
from PIL import Image
from curl_cffi.requests import AsyncSession

# Dynamic Linux/Render path resolution for Tesseract
tesseract_path = shutil.which("tesseract")
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

BASE_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXECUTABLE = sys.executable

def preprocess_captcha(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    img = img.convert('L')
    img = img.point(lambda p: 255 if p > 120 else 0, 'L')
    return img

async def extract(title: str, season: int, episode: int):
    slug = title.replace(" ", "-")
    base_url = "https://o2tvseries4u.com"
    async with AsyncSession(impersonate="chrome", verify=False) as session:
        resp = await session.get(f"{base_url}/{slug}/index.html")
        if resp.status_code != 200 or slug not in resp.url: return None
        resp = await session.get(f"{base_url}/{slug}/Season-{season:02d}/index.html")
        if resp.status_code != 200: return None
        ep_regex = rf'href="({base_url}/{slug}/Season-{season:02d}/Episode-{episode:02d}/index\.html)"'
        ep_match = re.search(ep_regex, resp.text, re.IGNORECASE)
        if not ep_match: return None
        resp = await session.get(ep_match.group(1))
        if resp.status_code != 200: return None
        dl_match = re.search(r'href="([^"]*?/download/\d+)"', resp.text)
        if not dl_match: return None
        download_id_url = dl_match.group(1)
        if download_id_url.startswith("/"): download_id_url = base_url + download_id_url
        resp = await session.get(download_id_url, allow_redirects=True)
        captcha_url = resp.url 
        if "areyouhuman.php" not in captcha_url: return None
        for attempt in range(1, 5):
            resp = await session.get(captcha_url)
            html = resp.text
            img_match = re.search(r'src="(/simplecaptcha1/[^"]+)"', html)
            if not img_match: continue
            img_full_url = f"{base_url}{img_match.group(1).replace('&amp;', '&')}"
            img_resp = await session.get(img_full_url)
            clean_img = preprocess_captcha(img_resp.content)
            captcha_text = re.sub(r'[^a-zA-Z0-9]', '', pytesseract.image_to_string(clean_img, config='--psm 8 --oem 3').strip())
            post_data = {"captchainput": captcha_text, "submit": "Continue Download"}
            post_resp = await session.post(captcha_url, data=post_data, headers={"Referer": captcha_url}, allow_redirects=False)
            if post_resp.status_code in [301, 302]:
                video_url = post_resp.headers.get("location")
                if video_url and (".mp4" in video_url or ".mkv" in video_url): return video_url
            await asyncio.sleep(0.5)
        return None

def format_source(result: dict, subs=None, needs_transcode: bool = False):
    if not result or not result.get("download_url"): return []
    return [{
        "name": "O2TV Direct",
        "data": {
            "stream": result["download_url"],
            "subtitle": subs or [],
            "quality": "auto",
            "title": result.get("show", ""),
            "episode": f'S{str(result.get("season", "")).zfill(2)}E{str(result.get("episode", "")).zfill(2)}',
            "needs_transcode": needs_transcode
        }
    }]
"@
Set-Content "models\o2tv.py" -Value $o2tvCode -Encoding UTF8

# =====================================================================
# 9. COMMIT AND PUSH TO GITHUB
# =====================================================================
Write-Host "[6/6] Pushing to GitHub for Render..." -ForegroundColor Yellow
git add .
git commit -m "feat: Migrate to Render (Added Dockerfile, Playwright, Tesseract)"
git push

Write-Host "
============================================================
              SUCCESS! READY FOR RENDER
============================================================
1. Go to https://dashboard.render.com
2. Click 'New +' -> 'Web Service'
3. Connect your 'Phoenix-Boss/netflix' repo
4. Render will AUTO-DETECT the Dockerfile!
5. Click 'Create Web Service'
============================================================
" -ForegroundColor Green