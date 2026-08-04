import os
import re
import asyncio
import logging
from urllib.parse import quote_plus, quote, urljoin
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

# ==========================================
# LOGGING SETUP (Enable with FZ_DEBUG=1)
# ==========================================
logger = logging.getLogger("fzmovies")
logger.setLevel(logging.INFO) # Default to silent
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[FZMovies] %(message)s'))
logger.addHandler(handler)

def _is_debug():
    return os.environ.get("FZ_DEBUG", "0") == "1"

BASE_URL = "https://fzmovies.live"

def _get_proxy():
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )

def get_similarity(a: str, b: str) -> float:
    """Levenshtein distance similarity check."""
    a = re.sub(r'[^a-z0-9]', '', a.lower())
    b = re.sub(r'[^a-z0-9]', '', b.lower())
    if a == b: return 100.0
    if a and b and (a in b or b in a): return 100.0
    if not a or not b: return 0.0

    matrix = [[j for j in range(len(a) + 1)] for i in range(len(b) + 1)]
    for i in range(1, len(b) + 1):
        for j in range(1, len(a) + 1):
            if b[i-1] == a[j-1]:
                matrix[i][j] = matrix[i-1][j-1]
            else:
                matrix[i][j] = min(matrix[i-1][j-1] + 1, matrix[i][j-1] + 1, matrix[i-1][j] + 1)
    max_len = max(len(a), len(b))
    return ((max_len - matrix[len(b)][len(a)]) / max_len) * 100

def _is_antibot_redirect(url: str) -> bool:
    """Check if fzmovies redirected us to home/search (anti-bot)."""
    return "csearch.php" in url or url.rstrip("/") == BASE_URL.rstrip("/")

async def extract(title: str, requested_quality: str = None):
    if _is_debug(): logger.setLevel(logging.DEBUG)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async with AsyncSession(impersonate="chrome", verify=False, proxy=_get_proxy()) as session:
        
        # ==========================================
        # STEP 1: Search
        # ==========================================
        search_url = f"{BASE_URL}/csearch.php?searchname={quote_plus(title)}"
        logger.debug(f"Searching: {search_url}")
        
        try:
            resp = await session.get(search_url, headers=headers, timeout=15)
        except Exception as e:
            logger.error(f"Search request failed: {e}")
            return None

        if resp.status_code != 200:
            logger.error(f"Search failed with status {resp.status_code}")
            return None

        search_results = []
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # BS4 Primary extraction
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r'movie-.*\.htm', href, re.IGNORECASE):
                text = a.get_text(" ", strip=True)
                if len(text) > 5 and "fzmovies" not in text.lower():
                    ym = re.search(r'\((\d{4})\)', text)
                    search_results.append({
                        "url": urljoin(BASE_URL, href),
                        "title": text,
                        "year": int(ym.group(1)) if ym else 0
                    })

        # Regex Fallback if DOM changes
        if not search_results:
            logger.debug("BS4 search failed, trying regex fallback")
            for match in re.finditer(r'href="(movie-[^"]+\.htm)"[^>]*>\s*([^<]+)', resp.text, re.IGNORECASE):
                href, text = match.group(1), match.group(2).strip()
                if len(text) > 5 and "fzmovies" not in text.lower():
                    ym = re.search(r'\((\d{4})\)', text)
                    search_results.append({
                        "url": urljoin(BASE_URL, href),
                        "title": text,
                        "year": int(ym.group(1)) if ym else 0
                    })

        if not search_results:
            logger.error(f"No search results found for '{title}'")
            return None

        # Sort by year descending, pick best match >= 90% similarity
        search_results.sort(key=lambda x: x["year"], reverse=True)
        selected_movie = None
        
        for res in search_results:
            sim = get_similarity(title, res["title"])
            logger.debug(f"Result: '{res['title']}' (Year: {res['year']}, Sim: {sim:.1f}%)")
            if sim >= 90:
                selected_movie = res
                break

        if not selected_movie:
            logger.error(f"No match above 90% threshold")
            return None

        logger.debug(f"Selected Movie: {selected_movie['title']}")

        # ==========================================
        # STEP 2: Movie Page (Quality Links)
        # ==========================================
        # fzmovies requires strict URL encoding for their movie pages
        raw_path = selected_movie['url'].replace(BASE_URL, "").lstrip('/')
        encoded_path = quote(raw_path)
        movie_url = f"{BASE_URL}/{encoded_path}"
        
        logger.debug(f"Fetching movie page: {movie_url}")
        resp = await session.get(movie_url, headers={**headers, "Referer": BASE_URL}, timeout=30)
        
        if resp.status_code != 200 or _is_antibot_redirect(resp.url):
            logger.error(f"Movie page failed or triggered anti-bot. URL: {resp.url}")
            return None

        qualities = []
        seen_urls = set()
        movie_soup = BeautifulSoup(resp.text, "html.parser")
        
        for a in movie_soup.find_all("a", href=True):
            href = a["href"]
            if "download1.php" in href and href not in seen_urls:
                seen_urls.add(href)
                full_url = urljoin(movie_url, href)
                
                parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
                text_match = re.search(r'([\w\.\s\-]+\.(?:mp4|mkv))', parent_text, re.IGNORECASE)
                size_match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s*(MB|GB)\s*\)', parent_text, re.IGNORECASE)

                qualities.append({
                    "url": full_url,
                    "text": text_match.group(1).strip() if text_match else "Unknown.mp4",
                    "size": f"{size_match.group(1)} {size_match.group(2).upper()}" if size_match else "Unknown"
                })

        if not qualities:
            logger.error("No quality download links found on movie page")
            return None

        logger.debug(f"Found {len(qualities)} quality options")

        # ==========================================
        # STEP 3 & 4: download1.php -> download.php -> Direct Links
        # ==========================================
        final_results = []
        
        for quality in qualities:
            try:
                logger.debug(f"Processing: {quality['text']} ({quality['size']})")
                resp = await session.get(quality["url"], headers={**headers, "Referer": movie_url}, allow_redirects=True, timeout=30)
                
                if resp.status_code != 200 or _is_antibot_redirect(resp.url):
                    logger.debug("Anti-bot triggered on download1 page")
                    continue

                dl1_soup = BeautifulSoup(resp.text, "html.parser")
                download_btn = None

                # Look for links pointing to download.php
                for a in dl1_soup.find_all("a", href=True):
                    if "download.php" in a["href"]:
                        download_btn = urljoin(quality["url"], a["href"])
                        break

                # Fallback: Look for generic DOWNLOAD text buttons
                if not download_btn:
                    for a in dl1_soup.find_all("a", href=True):
                        if "DOWNLOAD" in a.get_text(strip=True).upper() and "http" not in a["href"].lower():
                            download_btn = urljoin(quality["url"], a["href"])
                            break

                if not download_btn:
                    logger.debug("No download.php button found")
                    continue

                # Hit the final download page
                resp = await session.get(download_btn, headers={**headers, "Referer": quality["url"]}, allow_redirects=True, timeout=30)
                
                if resp.status_code != 200 or _is_antibot_redirect(resp.url):
                    logger.debug("Anti-bot triggered on final download page")
                    continue

                dl_soup = BeautifulSoup(resp.text, "html.parser")
                direct_links = []
                
                # Extract direct links (patterns usually involve dl, download, mirror, etc.)
                for a in dl_soup.find_all("a", href=True):
                    href = a["href"]
                    if any(x in href for x in ["dl", "download.", "mirror", "file=", "key="]):
                        direct_links.append(urljoin(resp.url, href))

                # Regex fallback for direct links
                if not direct_links:
                    direct_links = re.findall(r'href=["\'](https?://[^"\']*(?:dl|download|mirror)[^"\']*)["\']', resp.text, re.IGNORECASE)

                if direct_links:
                    final_results.append({
                        "file": quality["text"],
                        "size": quality["size"],
                        "downloads": direct_links
                    })
                    logger.debug(f"Found {len(direct_links)} direct links!")
            except Exception as e:
                logger.debug(f"Error processing quality link: {e}")
                continue

        if not final_results:
            logger.error("No direct download URLs resolved")
            return None

        # ==========================================
        # STEP 5: Quality Selection Logic
        # ==========================================
        def get_q_score(file_name):
            fn = file_name.lower()
            if "2160p" in fn or "4k" in fn: return 0
            if "1080p" in fn: return 1
            if "720p" in fn: return 2
            if "webrip" in fn or "bluray" in fn: return 2.5
            if "480p" in fn: return 3
            if "camrip" in fn or "hdcam" in fn: return 4
            return 99

        final_results.sort(key=lambda x: get_q_score(x['file']))
        selected = None
        needs_transcode = False
        actual_quality = "auto"

        if requested_quality:
            req_q = requested_quality.lower().replace("p", "")
            for res in final_results:
                if req_q in res['file'].lower():
                    selected = res
                    actual_quality = requested_quality
                    break
            if not selected and final_results:
                selected = final_results[0]
                needs_transcode = True
                for q in ["2160p", "4k", "1080p", "720p", "480p"]:
                    if q in selected['file'].lower():
                        actual_quality = q
                        break
        else:
            selected = final_results[0]
            # Auto-detect actual quality for metadata
            for q in ["2160p", "4k", "1080p", "720p", "480p"]:
                if q in selected['file'].lower():
                    actual_quality = q
                    break

        if not selected or not selected.get('downloads'):
            return None

        logger.debug(f"SUCCESS: {selected['file']} ({selected['size']})")
        
        return {
            "download_url": selected['downloads'][0],
            "quality": actual_quality,
            "needs_transcode": needs_transcode,
            "title": selected['file'],
            "size": selected['size'],
            "show": title # Kept for format_source compatibility
        }


def format_source(result: dict, subs=None, needs_transcode: bool = False):
    """Formats the fzmovies result to match the vidsrc-api standard."""
    if not result or not result.get("download_url"):
        return []
        
    return [{
        "name": "FZMovies Direct",
        "data": {
            "stream": result["download_url"],
            "subtitle": subs or [],
            "quality": result.get("quality", "auto"),
            "title": result.get("title", result.get("show", "")),
            "size": result.get("size", ""),
            "needs_transcode": result.get("needs_transcode", needs_transcode)
        }
    }]