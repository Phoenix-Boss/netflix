import os
import re
import asyncio
from urllib.parse import quote_plus
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

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


async def get_fallback_stream(title: str, requested_quality: str = None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    try:
        async with AsyncSession(impersonate="chrome", verify=False, proxy=_get_proxy()) as session:
            # STEP 1: Search
            search_url = f"{BASE_URL}/csearch.php?searchname={quote_plus(title)}"
            print(f"[fzmovies] Searching: {search_url}")
            resp = await session.get(search_url, headers=headers, timeout=15)
            print(f"[fzmovies] Search status: {resp.status_code} | Length: {len(resp.text)}")
            
            if resp.status_code != 200:
                print(f"[fzmovies] Search failed with status {resp.status_code}")
                return None

            html = resp.text
            search_results = []
            
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.search(r'movie-.*\.htm', href, re.IGNORECASE):
                    text = a.get_text(" ", strip=True)
                    if len(text) > 5 and "fzmovies" not in text.lower():
                        ym = re.search(r'\((\d{4})\)', text)
                        search_results.append({
                            "url": href,
                            "title": text,
                            "year": int(ym.group(1)) if ym else 0
                        })

            print(f"[fzmovies] Found {len(search_results)} results")
            for r in search_results[:5]:
                sim = get_similarity(title, r["title"])
                print(f"[fzmovies]   '{r['title']}' (year={r['year']}, sim={sim:.1f}%)")

            if not search_results:
                first_word = title.split()[0]
                idx = html.lower().find(first_word.lower())
                if idx != -1:
                    snippet = html[max(0, idx - 200):idx + 500]
                    snippet = re.sub(r'\s+', ' ', snippet).strip()
                    print(f"[fzmovies] HTML SNIPPET AROUND '{first_word}': {snippet[:800]}")
                else:
                    print(f"[fzmovies] Could not find '{first_word}' in HTML. First 800 chars: {html[:800]}")
                return None

            search_results.sort(key=lambda x: x["year"], reverse=True)

            selected_movie = None
            for res in search_results:
                sim = get_similarity(title, res["title"])
                if sim >= 90:
                    selected_movie = res
                    break

            if not selected_movie:
                print(f"[fzmovies] No match above 90% threshold")
                return None

            print(f"[fzmovies] Selected: {selected_movie['title']}")

            # STEP 2: Get Quality Links (increased timeout for heavy pages)
            movie_url = f"{BASE_URL}/{selected_movie['url'].lstrip('/')}"
            print(f"[fzmovies] Fetching movie page: {movie_url}")
            resp = await session.get(movie_url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"[fzmovies] Movie page failed: {resp.status_code}")
                return None

            qualities = []
            seen_urls = set()
            
            movie_soup = BeautifulSoup(resp.text, "html.parser")
            for a in movie_soup.find_all("a", href=True):
                href = a["href"]
                if "download1.php" in href and href not in seen_urls:
                    seen_urls.add(href)
                    if not href.startswith("http"):
                        href = f"{BASE_URL}/{href.lstrip('/')}"

                    parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
                    text_match = re.search(r'([\w\.\s\-]+\.(?:mp4|mkv))', parent_text, re.IGNORECASE)
                    size_match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s*(MB|GB)\s*\)', parent_text, re.IGNORECASE)

                    qualities.append({
                        "url": href,
                        "text": text_match.group(1).strip() if text_match else "Unknown.mp4",
                        "size": f"{size_match.group(1)} {size_match.group(2).upper()}" if size_match else "Unknown"
                    })

            print(f"[fzmovies] Found {len(qualities)} quality links")

            # STEP 3: Get Final dlink.php URLs
            final_results = []
            for quality in qualities:
                try:
                    resp = await session.get(quality["url"], headers=headers, allow_redirects=True, timeout=30)
                    if resp.status_code != 200: continue

                    # Use BeautifulSoup to find dlink
                    dl_soup = BeautifulSoup(resp.text, "html.parser")
                    dlinks = []
                    for a in dl_soup.find_all("a", href=True):
                        if "dlink" in a["href"]:
                            dlinks.append(a["href"])
                    
                    # Fallback regex just in case
                    if not dlinks:
                        dlinks = re.findall(r'href=[\"\'](dlink\.php\?[^\"\']+)[\"\']', resp.text, re.IGNORECASE)

                    if dlinks:
                        final_results.append({
                            "file": quality["text"],
                            "size": quality["size"],
                            "downloads": [f"{BASE_URL}/{dl.lstrip('/')}" if not dl.startswith('http') else dl for dl in dlinks]
                        })
                    else:
                        # DEBUG: What is on this page now?
                        snippet = re.sub(r'\s+', ' ', resp.text[:600]).strip()
                        print(f"[fzmovies] No dlink found. Page snippet: {snippet}")
                except Exception as e:
                    print(f"[fzmovies] Error getting dlink: {e}")
                    continue

            if not final_results:
                print(f"[fzmovies] No dlink URLs found")
                return None

            # STEP 4: Quality Selection
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

            if not selected or not selected.get('downloads'):
                return None

            print(f"[fzmovies] SUCCESS: {selected['file']} ({selected['size']})")
            return {
                "stream": selected['downloads'][0],
                "quality": actual_quality,
                "needs_transcode": needs_transcode,
                "title": selected['file'],
                "size": selected['size']
            }

    except Exception as e:
        print(f"[fzmovies] Error: {e}")
        return None