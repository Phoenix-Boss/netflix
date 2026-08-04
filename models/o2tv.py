import os
import asyncio
import re
import io
import shutil
import sys
import logging
from pathlib import Path
import pytesseract
from PIL import Image
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

# ==========================================
# LOGGING SETUP (Enable with O2TV_DEBUG=1)
# ==========================================
logger = logging.getLogger("o2tv")
logger.setLevel(logging.INFO) # Default to silent
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[O2TV] %(message)s'))
logger.addHandler(handler)

def _is_debug():
    return os.environ.get("O2TV_DEBUG", "0") == "1"

# ==========================================
# DEPENDENCY RESOLUTION
# ==========================================
tesseract_path = shutil.which("tesseract")
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
else:
    for _p in [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]:
        if os.path.exists(_p):
            pytesseract.pytesseract.tesseract_cmd = _p
            break

BASE_URL = "https://o2tvseries4u.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def _get_proxy():
    """Get proxy URL from environment. Render uses HTTPS_PROXY/HTTP_PROXY."""
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )

def _fix_url(url):
    """Ensure URL is absolute."""
    if not url: return url
    if url.startswith("//"): return "https:" + url
    if url.startswith("/"): return BASE_URL + url
    return url

def preprocess_captcha(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert('L')
    return img.point(lambda p: 255 if p > 120 else 0, 'L')

# ==========================================
# DYNAMIC SLUG DISCOVERY
# ==========================================
async def _find_slug(session, title: str) -> str | None:
    """Discover the real slug with ID suffix (e.g. 'Breaking-Bad-9')."""
    slug_base = title.replace(" ", "-")
    
    # Method 1: Search page (BS4 + Regex fallback)
    try:
        logger.debug(f"Searching for slug via search page: {slug_base}")
        resp = await session.get(f"{BASE_URL}/search?searchname={slug_base}", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # BS4: Look for <a> tags containing the show name followed by a dash and numbers
        for a in soup.find_all('a', href=True):
            match = re.search(rf'({re.escape(slug_base)}-[0-9]+)', a['href'], re.IGNORECASE)
            if match:
                logger.debug(f"Found slug via BS4: {match.group(1)}")
                return match.group(1)
        
        # Regex Fallback: If BS4 misses it due to weird DOM nesting
        pattern = rf'href="([^"]*?{re.escape(slug_base)}-[0-9]+/?)"'
        m = re.search(pattern, resp.text, re.IGNORECASE)
        if m:
            slug = m.group(1).strip("/")
            logger.debug(f"Found slug via Regex fallback: {slug}")
            return slug
            
    except Exception as e:
        logger.debug(f"Search method failed: {e}")

    # Method 2: Brute force IDs 1-20
    logger.debug("Falling back to brute-force ID discovery (1-20)...")
    for sid in range(1, 21):
        try:
            test_slug = f"{slug_base}-{sid}"
            resp = await session.get(f"{BASE_URL}/{test_slug}/index.html", headers=HEADERS, timeout=8, allow_redirects=False)
            # If it doesn't redirect to the homepage, we found the right ID
            if resp.status_code in [200, 301, 302]:
                final = resp.headers.get("location", "")
                if not final or test_slug.lower() in final.lower():
                    logger.debug(f"Found slug via Brute Force: {test_slug}")
                    return test_slug
        except Exception:
            pass
        await asyncio.sleep(0.15)

    # Method 3: Alphabetical listing (BS4)
    try:
        logger.debug("Falling back to alphabetical listing...")
        first_char = slug_base[0].upper()
        resp = await session.get(f"{BASE_URL}/alpha/{first_char}.html", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for a in soup.find_all('a', href=True):
            match = re.search(rf'({re.escape(slug_base)}-[0-9]+)', a['href'], re.IGNORECASE)
            if match:
                logger.debug(f"Found slug via Alpha BS4: {match.group(1)}")
                return match.group(1)
                
    except Exception as e:
        logger.debug(f"Alpha method failed: {e}")

    return None

# ==========================================
# MAIN EXTRACTION FLOW
# ==========================================
async def extract(title: str, season: int, episode: int):
    if _is_debug(): logger.setLevel(logging.DEBUG)
    
    async with AsyncSession(impersonate="chrome", verify=False, proxy=_get_proxy()) as session:
        
        # 1. Find real slug (handles DOM changes / ID suffixes)
        logger.debug(f"Step 1: Finding slug for '{title}'")
        slug = await _find_slug(session, title)
        if not slug:
            logger.error(f"Slug not found for {title}")
            return None
            
        # 2. Season page
        logger.debug(f"Step 2: Fetching Season-{season:02d} page")
        season_url = f"{BASE_URL}/{slug}/Season-{season:02d}/index.html"
        resp = await session.get(season_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200 or slug.lower() not in resp.url.lower():
            logger.error(f"Season page failed. Status: {resp.status_code}, URL: {resp.url}")
            return None
            
        # 3. Episode link (BS4 primary, Regex fallback)
        logger.debug(f"Step 3: Finding Episode-{episode:02d} link")
        ep_url = None
        soup = BeautifulSoup(resp.text, 'html.parser')
        ep_pattern = rf'Episode-{episode:02d}'
        
        for a in soup.find_all('a', href=True):
            if ep_pattern in a['href']:
                ep_url = _fix_url(a['href'])
                break
                
        if not ep_url:
            logger.debug("BS4 failed for episode, falling back to regex")
            ep_regex = rf'href="([^"]*?Episode-{episode:02d}[^"]*\.html)"'
            m = re.search(ep_regex, resp.text, re.IGNORECASE)
            if m: ep_url = _fix_url(m.group(1))
            
        if not ep_url:
            logger.error(f"Episode {episode} link not found.")
            return None
            
        # 4. Episode page -> download button
        logger.debug(f"Step 4: Fetching episode page for download button")
        resp = await session.get(ep_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200: return None
        
        download_url = None
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # BS4: Check for standard /download/ID links or areyouhuman redirects
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if '/download/' in href or 'areyouhuman' in href:
                download_url = _fix_url(href)
                logger.debug(f"Found download button via BS4: {download_url}")
                break
                
        # Regex Fallback: If download link is hidden in JS or weird formatting
        if not download_url:
            logger.debug("BS4 failed for download btn, falling back to regex")
            dl_match = re.search(r'href="([^"]*?/download/\d+)"', resp.text)
            if not dl_match:
                dl_match = re.search(r'href="([^"]*areyouhuman[^"]*)"', resp.text, re.IGNORECASE)
            if dl_match:
                download_url = _fix_url(dl_match.group(1))
                
        if not download_url:
            logger.error("No download button found.")
            return None
            
        # 5. Follow to CAPTCHA page
        logger.debug(f"Step 5: Following to CAPTCHA page")
        resp = await session.get(download_url, headers=HEADERS, allow_redirects=True, timeout=15)
        captcha_url = str(resp.url)
        
        # Handle rare case where it's a direct link
        if "areyouhuman" not in captcha_url:
            if ".mp4" in captcha_url or ".mkv" in captcha_url:
                return {"download_url": captcha_url, "show": title, "season": season, "episode": episode}
            logger.error("Did not land on CAPTCHA page.")
            return None
            
        # 6. Solve CAPTCHA
        logger.debug(f"Step 6: Solving CAPTCHA (5 attempts)")
        for attempt in range(1, 6):
            try:
                resp = await session.get(captcha_url, headers=HEADERS, timeout=15)
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # BS4 to find captcha image
                img_tag = soup.find('img', src=re.compile(r'simplecaptcha1'))
                if not img_tag:
                    logger.debug(f"Attempt {attempt}: BS4 no img, trying regex")
                    img_match = re.search(r'src="(/simplecaptcha1/[^"]+)"', resp.text)
                    if img_match:
                        img_full_url = BASE_URL + img_match.group(1).replace('&amp;', '&')
                    else:
                        await asyncio.sleep(0.5)
                        continue
                else:
                    img_full_url = _fix_url(img_tag.get('src', '').replace('&amp;', '&'))
                    
                img_resp = await session.get(img_full_url, headers=HEADERS, timeout=10)
                clean_img = preprocess_captcha(img_resp.content)
                captcha_text = re.sub(r'[^a-zA-Z0-9]', '', pytesseract.image_to_string(clean_img, config='--psm 8 --oem 3').strip())
                
                if not captcha_text:
                    await asyncio.sleep(0.5)
                    continue
                    
                logger.debug(f"Attempt {attempt}: Submitting OCR '{captcha_text}'")
                post_data = {"captchainput": captcha_text, "submit": "Continue Download"}
                post_resp = await session.post(
                    captcha_url, 
                    data=post_data, 
                    headers={**HEADERS, "Referer": captcha_url, "Origin": BASE_URL}, 
                    allow_redirects=False, 
                    timeout=15
                )
                
                if post_resp.status_code in [301, 302, 303, 307]:
                    video_url = post_resp.headers.get("location", "")
                    if video_url and (".mp4" in video_url or ".mkv" in video_url):
                        logger.debug(f"SUCCESS on attempt {attempt}")
                        return {"download_url": video_url, "show": title, "season": season, "episode": episode}
                        
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.debug(f"Attempt {attempt} error: {e}")
                await asyncio.sleep(1.5)
                
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