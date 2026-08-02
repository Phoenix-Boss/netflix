﻿import os
mport asyncio
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
    async with AsyncSession(impersonate="chrome", verify=False, proxy=os.getenv("PROXY_URL")) as session:
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
