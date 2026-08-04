 $script = @'
import asyncio
import re
import os
import sys
import io
import shutil
import time
from pathlib import Path
from curl_cffi.requests import AsyncSession
from PIL import Image
import pytesseract

# ============ TESSERACT SETUP ============
print("=" * 60)
print("O2TV SERIES EXTRACTOR - FULL TEST (v2)")
print("=" * 60)

tesseract_path = shutil.which("tesseract")
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    print(f"[SETUP] Tesseract: {tesseract_path}")
else:
    for p in [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            print(f"[SETUP] Tesseract: {p}")
            break

try:
    pytesseract.image_to_string(Image.new('L', (100, 30), color=255), config='--psm 8').strip()
    print("[SETUP] Tesseract test: OK")
except Exception as e:
    print(f"[SETUP] Tesseract FAILED: {e}")
    sys.exit(1)

print(f"[SETUP] Python: {sys.executable}")
print(f"[SETUP] CWD: {os.getcwd()}")
print()

BASE_URL = "https://o2tvseries4u.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def preprocess_captcha(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert('L')
    return img.point(lambda p: 255 if p > 120 else 0, 'L')


def save_debug(img_bytes, attempt):
    d = Path("captcha_debug"); d.mkdir(exist_ok=True)
    p = d / f"cap_{attempt}_{int(time.time())}.png"
    p.write_bytes(img_bytes)
    print(f"    [DEBUG] Saved: {p}")


async def find_show_slug(session, title: str, debug: bool = True):
    """
    Discover the real slug (with ID suffix) for a show.
    Tries multiple methods: search, alphabetical browse, homepage scrape.
    """
    slug_base = title.replace(" ", "-")

    # --- METHOD 1: Search page ---
    print(f"    [Method 1] Search page...")
    search_url = f"{BASE_URL}/search?searchname={slug_base}"
    if debug:
        print(f"      GET {search_url}")

    resp = await session.get(search_url, headers=HEADERS, timeout=20)
    if debug:
        print(f"      Status: {resp.status_code} | Final URL: {resp.url} | Length: {len(resp.text)}")

    # Look for show link with ID suffix: /Breaking-Bad-9/
    # Pattern: href="/Breaking-Bad-9/" or href="/breaking-bad-9/"
    slug_pattern = rf'href="(/[a-zA-Z0-9-]*{re.escape(slug_base)}-[0-9]+/)"'
    m = re.search(slug_pattern, resp.text, re.IGNORECASE)
    if m:
        found = m.group(1).strip("/")
        if debug:
            print(f"      FOUND slug: {found}")
        return found

    # Broader: any link containing the show name followed by -NUMBER
    broad_pattern = rf'href="(/[a-zA-Z0-9-]*-[0-9]+/)"'
    all_id_links = re.findall(broad_pattern, resp.text, re.IGNORECASE)
    if debug:
        print(f"      Found {len(all_id_links)} ID-style links on search page")
        for link in all_id_links[:10]:
            print(f"        - {link}")

    for link in all_id_links:
        link_clean = link.strip("/").lower()
        # Check if the show name words are all present
        words = slug_base.lower().split("-")
        if all(w in link_clean for w in words):
            if debug:
                print(f"      MATCHED by words: {link_clean}")
            return link_clean

    # --- METHOD 2: Try the homepage for a list of shows ---
    print(f"    [Method 2] Homepage scrape...")
    resp = await session.get(BASE_URL, headers=HEADERS, timeout=20)
    if debug:
        print(f"      Status: {resp.status_code} | Length: {len(resp.text)}")

    # Look for any link with the show name + ID
    m = re.search(slug_pattern, resp.text, re.IGNORECASE)
    if m:
        found = m.group(1).strip("/")
        if debug:
            print(f"      FOUND slug: {found}")
        return found

    # --- METHOD 3: Try common ID suffixes (brute force a few) ---
    print(f"    [Method 3] Brute-force ID suffixes 1-15...")
    for sid in range(1, 16):
        test_slug = f"{slug_base}-{sid}"
        test_url = f"{BASE_URL}/{test_slug}/index.html"
        resp = await session.get(test_url, headers=HEADERS, timeout=10, allow_redirects=False)
        if resp.status_code in [200, 301, 302]:
            final = resp.headers.get("location", test_url)
            if test_slug.lower() in final.lower():
                if debug:
                    print(f"      FOUND: {test_slug} (status {resp.status_code})")
                return test_slug
        await asyncio.sleep(0.2)

    # --- METHOD 4: Alphabetical listing pages ---
    print(f"    [Method 4] Alphabetical listing...")
    first_letter = slug_base[0].upper()
    alpha_url = f"{BASE_URL}/alpha/{first_letter}.html"
    if debug:
        print(f"      GET {alpha_url}")

    resp = await session.get(alpha_url, headers=HEADERS, timeout=20)
    if debug:
        print(f"      Status: {resp.status_code} | Length: {len(resp.text)}")

    m = re.search(slug_pattern, resp.text, re.IGNORECASE)
    if m:
        found = m.group(1).strip("/")
        if debug:
            print(f"      FOUND slug: {found}")
        return found

    # Broader search on alpha page
    all_id_links = re.findall(broad_pattern, resp.text, re.IGNORECASE)
    if debug:
        print(f"      Found {len(all_id_links)} ID-style links")
    for link in all_id_links:
        link_clean = link.strip("/").lower()
        words = slug_base.lower().split("-")
        if all(w in link_clean for w in words):
            if debug:
                print(f"      MATCHED: {link_clean}")
            return link_clean

    return None


async def extract_o2tv(title: str, season: int, episode: int, debug: bool = True):
    async with AsyncSession(impersonate="chrome", verify=False) as session:

        # ========== STEP 1: Find real show slug ==========
        print(f"\n[STEP 1] Discovering show slug for '{title}'...")
        slug = await find_show_slug(session, title, debug)

        if not slug:
            print(f"    FAILED: Could not find show slug")
            return None

        print(f"    CONFIRMED slug: {slug}")

        # ========== STEP 2: Season page ==========
        print(f"\n[STEP 2] Fetching season {season} page...")
        season_url = f"{BASE_URL}/{slug}/Season-{season:02d}/index.html"
        if debug:
            print(f"    URL: {season_url}")

        resp = await session.get(season_url, headers=HEADERS, timeout=20)
        if debug:
            print(f"    Status: {resp.status_code} | URL: {resp.url} | Length: {len(resp.text)}")

        if resp.status_code != 200 or slug.lower() not in resp.url.lower():
            print(f"    FAILED: Season page not found")
            return None

        # ========== STEP 3: Episode link ==========
        print(f"\n[STEP 3] Finding episode {episode} link...")
        ep_pattern = rf'href="({re.escape(BASE_URL)}/{re.escape(slug)}/Season-{season:02d}/Episode-{episode:02d}/index\.html)"'
        ep_match = re.search(ep_pattern, resp.text, re.IGNORECASE)

        if not ep_match:
            ep_pattern2 = rf'href="([^"]*Episode-{episode:02d}[^"]*\.html)"'
            ep_match = re.search(ep_pattern2, resp.text, re.IGNORECASE)

        if not ep_match:
            print(f"    FAILED: Episode link not found")
            if debug:
                all_eps = re.findall(r'href="([^"]*Episode-\d+[^"]*\.html)"', resp.text, re.IGNORECASE)
                print(f"    Episodes found: {len(all_eps)}")
                for e in all_eps[:10]:
                    print(f"      - {e}")
            return None

        ep_url = ep_match.group(1)
        if not ep_url.startswith("http"):
            ep_url = BASE_URL + ep_url
        if debug:
            print(f"    Episode URL: {ep_url}")

        # ========== STEP 4: Episode page -> download button ==========
        print(f"\n[STEP 4] Fetching episode page...")
        resp = await session.get(ep_url, headers=HEADERS, timeout=20)
        if debug:
            print(f"    Status: {resp.status_code} | Length: {len(resp.text)}")

        if resp.status_code != 200:
            print(f"    FAILED: Status {resp.status_code}")
            return None

        # Find download button
        dl_match = re.search(r'href="([^"]*?/download/\d+)"', resp.text)
        if not dl_match:
            patterns = [
                r'href="([^"]*areyouhuman[^"]*)"',
                r'href="([^"]*download[^"]*\d+[^"]*)"',
                r'href="([^"]*dload[^"]*)"',
            ]
            for pat in patterns:
                dl_match = re.search(pat, resp.text, re.IGNORECASE)
                if dl_match:
                    if debug:
                        print(f"    Download button found via: {pat[:40]}")
                    break

        if not dl_match:
            print(f"    FAILED: No download button found")
            if debug:
                all_links = re.findall(r'href="([^"]+)"', resp.text)
                print(f"    All links ({len(all_links)}):")
                for l in all_links[:25]:
                    print(f"      - {l[:120]}")
            return None

        download_url = dl_match.group(1)
        if download_url.startswith("/"):
            download_url = BASE_URL + download_url
        if debug:
            print(f"    Download button: {download_url}")

        # ========== STEP 5: Follow to CAPTCHA ==========
        print(f"\n[STEP 5] Following to CAPTCHA page...")
        resp = await session.get(download_url, headers=HEADERS, allow_redirects=True, timeout=20)
        captcha_url = str(resp.url)
        if debug:
            print(f"    Status: {resp.status_code} | URL: {captcha_url}")

        if "areyouhuman" not in captcha_url:
            if ".mp4" in captcha_url or ".mkv" in captcha_url:
                print(f"    Direct download URL (no captcha)!")
                return {"download_url": captcha_url, "show": title, "season": season, "episode": episode}
            print(f"    FAILED: Not a CAPTCHA page")
            if debug:
                snippet = re.sub(r'\s+', ' ', resp.text[:500]).strip()
                print(f"    Snippet: {snippet}")
            return None

        # ========== STEP 6: Solve CAPTCHA ==========
        print(f"\n[STEP 6] Solving CAPTCHA (5 attempts)...")

        for attempt in range(1, 6):
            print(f"\n    --- Attempt {attempt}/5 ---")

            resp = await session.get(captcha_url, headers=HEADERS, timeout=20)
            html = resp.text

            img_match = re.search(r'src="(/simplecaptcha1/[^"]+)"', html)
            if not img_match:
                print(f"    No CAPTCHA image found")
                if debug:
                    snippet = re.sub(r'\s+', ' ', html[:600]).strip()
                    print(f"    Snippet: {snippet}")
                continue

            img_full_url = f"{BASE_URL}{img_match.group(1).replace('&amp;', '&')}"
            if debug:
                print(f"    Image: {img_full_url}")

            img_resp = await session.get(img_full_url, headers=HEADERS, timeout=10)
            if debug:
                print(f"    Image size: {len(img_resp.content)} bytes")
                save_debug(img_resp.content, attempt)

            clean_img = preprocess_captcha(img_resp.content)
            captcha_text = pytesseract.image_to_string(
                clean_img,
                config='--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            ).strip()
            captcha_text = re.sub(r'[^a-zA-Z0-9]', '', captcha_text)

            if debug:
                print(f"    OCR: '{captcha_text}'")

            if not captcha_text:
                await asyncio.sleep(1)
                continue

            post_data = {"captchainput": captcha_text, "submit": "Continue Download"}
            post_headers = {**HEADERS, "Referer": captcha_url, "Origin": BASE_URL}

            if debug:
                print(f"    Posting: captchainput='{captcha_text}'")

            post_resp = await session.post(
                captcha_url, data=post_data, headers=post_headers,
                allow_redirects=False, timeout=20
            )

            if debug:
                print(f"    Status: {post_resp.status_code}")
                for k, v in post_resp.headers.items():
                    if k.lower() in ["location", "set-cookie", "content-type"]:
                        print(f"    {k}: {v}")

            if post_resp.status_code in [301, 302, 303, 307]:
                video_url = post_resp.headers.get("location", "")
                if debug:
                    print(f"    Redirect: {video_url}")

                if video_url and (".mp4" in video_url or ".mkv" in video_url or "download" in video_url.lower()):
                    print(f"\n{'='*60}")
                    print(f"SUCCESS! Download URL found!")
                    print(f"{'='*60}")
                    print(f"URL: {video_url}")
                    return {"download_url": video_url, "show": title, "season": season, "episode": episode}
                else:
                    print(f"    Wrong captcha (redirect not to video)")
            else:
                print(f"    Wrong captcha (no redirect)")

            await asyncio.sleep(1.5)

        print(f"\n    FAILED: All CAPTCHA attempts exhausted")
        return None


def format_source(result: dict, subs=None, needs_transcode=False):
    if not result or not result.get("download_url"):
        return []
    return [{
        "name": "O2TV Direct",
        "data": {
            "stream": result["download_url"],
            "subtitle": subs or [],
            "quality": "auto",
            "title": result.get("show", ""),
            "episode": f'S{str(result.get("season","")).zfill(2)}E{str(result.get("episode","")).zfill(2)}',
            "needs_transcode": needs_transcode
        }
    }]


async def main():
    args = sys.argv[1:]
    if len(args) >= 3:
        title, season, episode = args[0], int(args[1]), int(args[2])
    else:
        title, season, episode = "Breaking Bad", 1, 1
        print("No args -> defaulting to: Breaking Bad S01E01")
        print("Usage: python test_o2tv.py \"Show Name\" SEASON EPISODE\n")

    print(f"Target: {title} - S{season:02d}E{episode:02d}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    start = time.time()
    result = await extract_o2tv(title, season, episode, debug=True)
    elapsed = time.time() - start

    print(f"\nTotal time: {elapsed:.2f}s")

    if result:
        import json
        print(f"\n{'='*60}")
        print("FINAL SOURCE:")
        print(f"{'='*60}")
        print(json.dumps(format_source(result), indent=2))
    else:
        print(f"\n{'='*60}")
        print("EXTRACTION FAILED")
        print(f"{'='*60}")
        print("\nCheck:")
        print("  - captcha_debug/ folder to see what CAPTCHAs look like")
        print("  - The slug discovery step above for which methods were tried")
        print("  - Whether your IP/region is blocked")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
'@

 $scriptPath = "test_o2tv.py"
 $script | Out-File -FilePath $scriptPath -Encoding UTF8

Write-Host "Created updated test script: $scriptPath" -ForegroundColor Green
Write-Host ""
Write-Host "Running test..." -ForegroundColor Cyan
Write-Host ("=" * 60)

& "C:\Program Files\Python313\python.exe" $scriptPath "Breaking Bad" 1 1