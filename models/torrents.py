# models/torrents.py - Production Torrent Scraper
# Sources: 1337x, TorrentGalaxy, YTS (API), Nyaa, SolidTorrents
# Pattern: Matches vidapi.py style exactly

import os
import re
import json
import asyncio
import logging
import base64
import hashlib
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from urllib.parse import quote, unquote

from .utils import fetch_session
from .cache import get as cache_get, set as cache_set

# ==========================================
# LOGGING
# ==========================================
logger = logging.getLogger("torrents")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("[Torrents] %(message)s"))
logger.addHandler(_handler)

def _dbg():
    return os.environ.get("VIDAPI_DEBUG", "0") == "1"

# ==========================================
# CONFIG
# ==========================================
TIMEOUT = 15

SIZE_RE = re.compile(r"([\d.]+)\s*(GB|MB|TB|KB)", re.I)
SE_RE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,2})")
SE_RE2 = re.compile(r"(\d{1,2})[xX](\d{1,2})")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

Q_RANK = {
    "2160p": 5, "4k": 5, "uhd": 5,
    "1080p": 4, "1080": 4,
    "720p": 3, "720": 3,
    "480p": 2, "480": 2,
    "hdtv": 1, "webrip": 1, "web-dl": 1, "webdl": 1, "web": 1,
    "dvdrip": 1, "bdrip": 1, "brrip": 1, "bluray": 1, "blu-ray": 1,
}

BAD_KW = ["sample", "trailer", "promo", "extras", "bonus", "proof", "nuke"]
GOOD_KW = ["bluray", "blu-ray", "brrip", "bdrip", "web-dl", "webrip", "remux", "proper", "repack"]

PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.pomf.se:80/announce",
]

def _proxy():
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )


# ==========================================
# HELPERS
# ==========================================

def _size_mb(text: str) -> Optional[float]:
    if not text:
        return None
    m = SIZE_RE.search(text)
    if not m:
        return None
    v = float(m.group(1))
    u = m.group(2).upper()
    if u == "TB": return v * 1024 * 1024
    if u == "GB": return v * 1024
    if u == "KB": return v / 1024
    return v

def _quality(name: str) -> tuple:
    if not name:
        return (0, "SD")
    n = name.lower()
    for q, s in Q_RANK.items():
        if q in n:
            label = q.upper() if len(q) <= 4 else q.replace("-", "").upper()
            return (s, label)
    return (0, "SD")

def _parse_se(name: str) -> Optional[tuple]:
    if not name:
        return None
    m = SE_RE.search(name) or SE_RE2.search(name)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None

def _clean_name(name: str) -> str:
    if not name:
        return ""
    n = re.sub(r"\[.*?\]", "", name)
    n = re.sub(r"\(.*?\)", "", n)
    n = re.sub(r"\.\w{3,4}$", "", n)
    n = re.sub(r"[-_](YTS|YIFY|RARBG|ETTG|TGx|PROPER|REPACK|FASTSUB|NOGROUP|PSA|NTb|FGT|CMRG)[-_]", "", n, flags=re.I)
    n = re.sub(r"[-_](720p|1080p|2160p|4K|BluRay|WEB-DL|WEBRip|HDTV|DVDRip|BRRip|x264|x265|HEVC|AAC|AC3|DTS|HDRip|REMUX|10bit|HDR|DolbyVision|Atmos)[-_.].*$", "", n, flags=re.I)
    n = n.replace(".", " ").replace("_", " ").replace("-", " ")
    n = re.sub(r"\s+", " ", n).strip()
    return n

def _parse_magnet(magnet: str) -> Dict:
    r = {"hash": "", "trackers": []}
    if not magnet or not magnet.startswith("magnet:"):
        return r
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.I)
    if m:
        r["hash"] = m.group(1).lower()
    else:
        m = re.search(r"btih:([A-Z2-7]{32})", magnet)
        if m:
            try:
                r["hash"] = base64.b32decode(m.group(1)).hex()
            except Exception:
                r["hash"] = m.group(1).lower()
    for m in re.finditer(r"tr=([^&]+)", magnet):
        r["trackers"].append(unquote(m.group(1)))
    return r

def _build_magnet(info_hash: str, name: str, trackers: List[str] = None) -> str:
    if not info_hash:
        return ""
    if not trackers:
        trackers = PUBLIC_TRACKERS
    mag = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"
    for t in trackers:
        mag += f"&tr={quote(t)}"
    return mag

def _score(t: Dict, s: int = None, e: int = None, prefer_q: str = "1080p") -> int:
    score = 0
    name = t.get("name", "").lower()
    seeders = t.get("seeders", 0)
    score += min(int(seeders), 2000)

    qs, qn = _quality(name)
    target_s = Q_RANK.get(prefer_q.lower(), 4)
    if qs == target_s:
        score += 1000
    elif qs > 0:
        score += qs * 150

    if s is not None and e is not None:
        se = _parse_se(name)
        if se:
            if se[0] == s and se[1] == e:
                score += 50000
            elif se[0] == s:
                score += 10000
        else:
            score -= 20000
    else:
        if _parse_se(name):
            score -= 20000

    sz = t.get("size_mb")
    if sz:
        if prefer_q.lower() == "1080p" and 1500 < sz < 6000:
            score += 300
        elif prefer_q.lower() == "720p" and 500 < sz < 3000:
            score += 300
        elif prefer_q.lower() in ["2160p", "4k"] and 5000 < sz < 20000:
            score += 300

    for kw in BAD_KW:
        if kw in name:
            score -= 50000
    for kw in GOOD_KW:
        if kw in name:
            score += 100

    return score


# ==========================================
# SCRAPERS
# ==========================================

async def _scrape_1337x(query: str, session: AsyncSession, is_tv: bool = False, s: int = None, e: int = None) -> List[Dict]:
    results = []
    bases = [
        "https://www.1337x.to",
        "https://1337x.st",
        "https://x1337x.ws",
        "https://x1337x.se",
    ]
    
    for base in bases:
        try:
            search_url = f"{base}/search/{quote(query)}/1/"
            if _dbg(): logger.debug(f"(1337x) Trying {base}...")
            
            resp = await asyncio.wait_for(
                fetch_session(search_url, session, headers={"Referer": base + "/"}),
                timeout=TIMEOUT
            )
            if not resp or resp.status_code != 200:
                continue
            
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.table-list tbody tr")
            
            if not rows:
                continue
                
            for row in rows:
                try:
                    cells = row.find_all("td")
                    if len(cells) < 5:
                        continue
                    
                    name_link = cells[0].find("a", href=re.compile(r"/torrent/"))
                    if not name_link:
                        continue
                    
                    name = name_link.get_text(strip=True)
                    torrent_path = name_link.get("href", "")
                    torrent_url = base + torrent_path
                    
                    seeders_text = cells[1].get_text(strip=True).replace(",", "").strip()
                    leechers_text = cells[2].get_text(strip=True).replace(",", "").strip()
                    seeders = int(seeders_text) if seeders_text.isdigit() else 0
                    leechers = int(leechers_text) if leechers_text.isdigit() else 0
                    size_text = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    size_mb = _size_mb(size_text)
                    
                    if seeders == 0:
                        continue
                    
                    results.append({
                        "name": name,
                        "detail_url": torrent_url,
                        "magnet": "",
                        "seeders": seeders,
                        "leechers": leechers,
                        "size": size_text,
                        "size_mb": size_mb,
                        "source": "1337x",
                        "_need_magnet": True
                    })
                except Exception:
                    continue
            
            if results:
                break
                
        except asyncio.TimeoutError:
            continue
        except Exception as ex:
            if _dbg(): logger.debug(f"(1337x) {base} err: {ex}")
            continue
    
    if _dbg(): logger.debug(f"(1337x) Found {len(results)} results")
    return results[:25]


async def _fetch_1337x_magnet(url: str, session: AsyncSession) -> str:
    try:
        resp = await asyncio.wait_for(fetch_session(url, session, timeout=TIMEOUT), timeout=TIMEOUT)
        if not resp or resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        a = soup.find("a", href=re.compile(r"^magnet:\?"))
        return a.get("href", "") if a else ""
    except Exception:
        return ""


async def _scrape_torrentgalaxy(query: str, session: AsyncSession, is_tv: bool = False, s: int = None, e: int = None) -> List[Dict]:
    results = []
    bases = [
        "https://torrentgalaxy.to",
        "https://torrentgalaxy.mx",
    ]
    
    search_q = query
    if is_tv and s and e:
        search_q = f"{query} S{s:02d}E{e:02d}"
    
    for base in bases:
        try:
            url = f"{base}/torrents.php?search={quote(search_q)}&sort=id&order=desc"
            if _dbg(): logger.debug(f"(TG) Trying {base}...")
            
            resp = await asyncio.wait_for(
                fetch_session(url, session, headers={"Referer": base + "/"}),
                timeout=TIMEOUT
            )
            if not resp or resp.status_code != 200:
                continue
            
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("div.tgxtablerow")
            
            if not rows:
                continue
            
            for row in rows:
                try:
                    name_el = row.select_one("a.txlight") or row.select_one("div.tgxtablecellfull a")
                    if not name_el:
                        continue
                    name = name_el.get_text(strip=True)
                    
                    mag_el = row.find("a", href=re.compile(r"^magnet:\?"))
                    magnet = mag_el.get("href", "") if mag_el else ""
                    
                    if not magnet:
                        continue
                    
                    se_el = row.select_one("span.font-orange")
                    le_el = row.select_one("span.font-red")
                    sz_el = row.select_one("span.badge-secondary")
                    
                    seeders = int(se_el.get_text(strip=True).replace(",", "")) if se_el and se_el.get_text(strip=True).replace(",", "").isdigit() else 0
                    leechers = int(le_el.get_text(strip=True).replace(",", "")) if le_el and le_el.get_text(strip=True).replace(",", "").isdigit() else 0
                    size_text = sz_el.get_text(strip=True) if sz_el else ""
                    size_mb = _size_mb(size_text)
                    
                    if seeders == 0:
                        continue
                    
                    results.append({
                        "name": name,
                        "detail_url": "",
                        "magnet": magnet,
                        "seeders": seeders,
                        "leechers": leechers,
                        "size": size_text,
                        "size_mb": size_mb,
                        "source": "TorrentGalaxy",
                        "_need_magnet": False
                    })
                except Exception:
                    continue
            
            if results:
                break
                
        except asyncio.TimeoutError:
            continue
        except Exception as ex:
            if _dbg(): logger.debug(f"(TG) {base} err: {ex}")
            continue
    
    if _dbg(): logger.debug(f"(TG) Found {len(results)} results")
    return results[:25]


async def _scrape_yts(query: str, session: AsyncSession) -> List[Dict]:
    results = []
    
    try:
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={quote(query)}&sort_by=seeds&order_by=desc&limit=10"
        if _dbg(): logger.debug(f"(YTS) Searching...")
        
        resp = await asyncio.wait_for(
            fetch_session(url, session, headers={"Accept": "application/json"}),
            timeout=TIMEOUT
        )
        if not resp or resp.status_code != 200:
            return []
        
        data = resp.json()
        if data.get("data", {}).get("movie_count", 0) == 0:
            return []
        
        for movie in data["data"].get("movies", []):
            title = movie.get("title_long", movie.get("title", ""))
            for t in movie.get("torrents", []):
                q = t.get("quality", "")
                magnet = t.get("url", "")
                seeders = t.get("seeds", 0)
                peers = t.get("peers", 0)
                size_text = t.get("size", "")
                size_mb = _size_mb(size_text)
                
                if seeders == 0 and peers == 0:
                    continue
                
                results.append({
                    "name": f"{title} [{q}] [{size_text}]",
                    "detail_url": "",
                    "magnet": magnet,
                    "seeders": seeders,
                    "leechers": peers,
                    "size": size_text,
                    "size_mb": size_mb,
                    "source": "YTS",
                    "_need_magnet": False
                })
    except Exception as ex:
        if _dbg(): logger.debug(f"(YTS) err: {ex}")
    
    if _dbg(): logger.debug(f"(YTS) Found {len(results)} results")
    return results


async def _scrape_nyaa(query: str, session: AsyncSession, is_tv: bool = False, s: int = None, e: int = None) -> List[Dict]:
    results = []
    
    search_q = query
    if is_tv and s and e:
        search_q = f"{query} S{s:02d}E{e:02d}"
    
    try:
        url = f"https://nyaa.si/?f=0&c=0_0&q={quote(search_q)}&s=seeders&o=desc"
        if _dbg(): logger.debug(f"(Nyaa) Searching...")
        
        resp = await asyncio.wait_for(
            fetch_session(url, session, headers={"Referer": "https://nyaa.si/"}),
            timeout=TIMEOUT
        )
        if not resp or resp.status_code != 200:
            return []
        
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table.torrent-list tbody tr")
        
        for row in rows:
            try:
                cells = row.find_all("td")
                if len(cells) < 6:
                    continue
                
                name_link = cells[1].find("a", href=re.compile(r"/view/"), class_=False)
                if not name_link:
                    continue
                name = name_link.get_text(strip=True)
                
                mag_el = cells[2].find("a", href=re.compile(r"^magnet:\?"))
                if not mag_el:
                    continue
                magnet = mag_el.get("href", "")
                
                size_text = cells[3].get_text(strip=True)
                size_mb = _size_mb(size_text)
                
                s_text = cells[5].get_text(strip=True).replace(",", "")
                l_text = cells[6].get_text(strip=True).replace(",", "") if len(cells) > 6 else "0"
                seeders = int(s_text) if s_text.isdigit() else 0
                leechers = int(l_text) if l_text.isdigit() else 0
                
                if seeders == 0:
                    continue
                
                results.append({
                    "name": name,
                    "detail_url": "",
                    "magnet": magnet,
                    "seeders": seeders,
                    "leechers": leechers,
                    "size": size_text,
                    "size_mb": size_mb,
                    "source": "Nyaa",
                    "_need_magnet": False
                })
            except Exception:
                continue
    except Exception as ex:
        if _dbg(): logger.debug(f"(Nyaa) err: {ex}")
    
    if _dbg(): logger.debug(f"(Nyaa) Found {len(results)} results")
    return results[:20]


async def _scrape_solidtorrents(query: str, session: AsyncSession, is_tv: bool = False, s: int = None, e: int = None) -> List[Dict]:
    results = []
    
    search_q = query
    if is_tv and s and e:
        search_q = f"{query} S{s:02d}E{e:02d}"
    
    try:
        url = f"https://solidtorrents.to/search?q={quote(search_q)}&sort=seeders"
        if _dbg(): logger.debug(f"(Solid) Searching...")
        
        resp = await asyncio.wait_for(
            fetch_session(url, session, headers={"Referer": "https://solidtorrents.to/"}),
            timeout=TIMEOUT
        )
        if not resp or resp.status_code != 200:
            return []
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        for item in soup.select("[class*='torrent'], .search-result"):
            try:
                name_el = item.select_one("a.title, h5 a, h4 a")
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                
                mag_el = item.find("a", href=re.compile(r"^magnet:\?"))
                if not mag_el:
                    continue
                magnet = mag_el.get("href", "")
                
                s_el = item.select_one("[class*='seed']")
                seeders = int(s_el.get_text(strip=True).replace(",", "")) if s_el and s_el.get_text(strip=True).replace(",", "").isdigit() else 0
                
                sz_el = item.select_one("[class*='size']")
                size_text = sz_el.get_text(strip=True) if sz_el else ""
                size_mb = _size_mb(size_text)
                
                if seeders == 0:
                    continue
                
                results.append({
                    "name": name,
                    "detail_url": "",
                    "magnet": magnet,
                    "seeders": seeders,
                    "leechers": 0,
                    "size": size_text,
                    "size_mb": size_mb,
                    "source": "SolidTorrents",
                    "_need_magnet": False
                })
            except Exception:
                continue
    except Exception as ex:
        if _dbg(): logger.debug(f"(Solid) err: {ex}")
    
    if _dbg(): logger.debug(f"(Solid) Found {len(results)} results")
    return results[:20]


async def _scrape_piratebay_proxy(query: str, session: AsyncSession, is_tv: bool = False, s: int = None, e: int = None) -> List[Dict]:
    """Scrape The Pirate Bay via proxy mirrors"""
    results = []
    
    proxies = [
        "https://thepiratebay.org",
        "https://tpb.party",
        "https://piratebay.party",
        "https://thehiddenbay.com",
        "https://thepiratebay10.org",
        "https://piratebay.org",
    ]
    
    search_q = query
    if is_tv and s and e:
        search_q = f"{query} S{s:02d}E{e:02d}"
    
    for proxy in proxies:
        try:
            url = f"{proxy}/search.php?q={quote(search_q)}&orderby=99"
            if _dbg(): logger.debug(f"(TPB) Trying {proxy}...")
            
            resp = await asyncio.wait_for(
                fetch_session(url, session, headers={"Referer": proxy + "/"}),
                timeout=TIMEOUT
            )
            if not resp or resp.status_code != 200:
                continue
            
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # TPB structure: table#searchResult tr
            table = soup.find("table", id="searchResult")
            if not table:
                # Try alternate structure
                rows = soup.select("table tr")
            else:
                rows = table.find_all("tr")[1:]  # Skip header
            
            if not rows:
                continue
            
            for row in rows:
                try:
                    # Name cell - TPB has different structures across proxies
                    name_link = row.find("a", class_="detLink")
                    if not name_link:
                        # Try alternate selector
                        links = row.find_all("a")
                        for link in links:
                            href = link.get("href", "")
                            if "/torrent/" in href and link.get_text(strip=True):
                                name_link = link
                                break
                    
                    if not name_link:
                        continue
                    
                    name = name_link.get_text(strip=True)
                    
                    # Magnet link
                    mag_el = row.find("a", href=re.compile(r"^magnet:"))
                    if not mag_el:
                        # Look in all links
                        for a in row.find_all("a"):
                            href = a.get("href", "")
                            if href.startswith("magnet:"):
                                mag_el = a
                                break
                    
                    if not mag_el:
                        continue
                    
                    magnet = mag_el.get("href", "")
                    
                    # Seeders and leechers - TPB uses td elements
                    tds = row.find_all("td")
                    seeders = 0
                    leechers = 0
                    size_text = ""
                    
                    for td in tds:
                        text = td.get_text(strip=True)
                        if "Seeders" in text or "Leechers" in text:
                            # Skip header-like cells
                            continue
                        # Try to parse as number
                        clean = text.replace(",", "").replace(".", "").strip()
                        if clean.isdigit():
                            if seeders == 0:
                                seeders = int(clean)
                            elif leechers == 0:
                                leechers = int(clean)
                        elif SIZE_RE.search(text):
                            size_text = text
                    
                    if seeders == 0:
                        continue
                    
                    results.append({
                        "name": name,
                        "detail_url": "",
                        "magnet": magnet,
                        "seeders": seeders,
                        "leechers": leechers,
                        "size": size_text,
                        "size_mb": _size_mb(size_text),
                        "source": "PirateBay",
                        "_need_magnet": False
                    })
                except Exception:
                    continue
            
            if results:
                break
                
        except asyncio.TimeoutError:
            continue
        except Exception as ex:
            if _dbg(): logger.debug(f"(TPB) {proxy} err: {ex}")
            continue
    
    if _dbg(): logger.debug(f"(TPB) Found {len(results)} results")
    return results[:25]


# ==========================================
# MAIN EXTRACT
# ==========================================

async def extract(dbid, s=None, e=None, title=None, quality="1080p", retry=True) -> Optional[Dict]:
    """
    Search torrents. Returns dict with torrent info or None.
    Compatible with vidapi.py extract() return format.
    """
    if not title:
        logger.warning("No title for torrent search")
        return None
    
    is_tv = s is not None and e is not None
    media_type = "tv" if is_tv else "movie"
    cache_key = f"torrent:{media_type}:{dbid}:{s}:{e}:{quality}"
    
    if _dbg():
        logger.setLevel(logging.DEBUG)
    
    cached = cache_get(cache_key)
    if cached:
        logger.debug(f"CACHE HIT: {title}")
        return cached
    
    try:
        logger.info(f"Searching torrents: {title} ({media_type})")
        
        async with AsyncSession(impersonate="chrome", verify=False, proxy=_proxy()) as session:
            tasks = [
                asyncio.create_task(_scrape_torrentgalaxy(title, session, is_tv, s, e), name="tg"),
                asyncio.create_task(_scrape_1337x(title, session, is_tv, s, e), name="1337x"),
                asyncio.create_task(_scrape_solidtorrents(title, session, is_tv, s, e), name="solid"),
                asyncio.create_task(_scrape_piratebay_proxy(title, session, is_tv, s, e), name="tpb"),
            ]
            
            if not is_tv:
                tasks.append(asyncio.create_task(_scrape_yts(title, session), name="yts"))
            
            tasks.append(asyncio.create_task(_scrape_nyaa(title, session, is_tv, s, e), name="nyaa"))
            
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=TIMEOUT * 4
                )
            except asyncio.TimeoutError:
                logger.debug("Overall timeout")
                results = [None] * len(tasks)
            
            all_torrents = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    if _dbg(): logger.debug(f"Task {tasks[i].get_name()} err: {r}")
                    continue
                if isinstance(r, list):
                    all_torrents.extend(r)
            
            # Fetch 1337x magnets
            mag_tasks = []
            mag_indices = []
            for idx, t in enumerate(all_torrents):
                if t.get("_need_magnet") and t.get("detail_url"):
                    mag_tasks.append(asyncio.create_task(_fetch_1337x_magnet(t["detail_url"], session)))
                    mag_indices.append(idx)
            
            if mag_tasks:
                try:
                    magnets = await asyncio.wait_for(
                        asyncio.gather(*mag_tasks, return_exceptions=True),
                        timeout=TIMEOUT * 2
                    )
                    for i, idx in enumerate(mag_indices):
                        if i < len(magnets) and not isinstance(magnets[i], Exception):
                            all_torrents[idx]["magnet"] = magnets[i]
                            all_torrents[idx]["_need_magnet"] = False
                except asyncio.TimeoutError:
                    logger.debug("Magnet fetch timeout")
            
            valid = [t for t in all_torrents if t.get("magnet")]
            
            if not valid:
                logger.info(f"No valid torrents for: {title}")
                return None
            
            # Score & sort
            for t in valid:
                t["_score"] = _score(t, s, e, quality)
            valid.sort(key=lambda x: x.get("_score", 0), reverse=True)
            
            best = valid[0]
            parsed = _parse_magnet(best.get("magnet", ""))
            _, q_name = _quality(best.get("name", ""))
            
            result = {
                "stream_urls": [],
                "imdb_id": dbid,
                "title": title,
                "file_name": best.get("name", ""),
                "backdrop": "",
                "provider": f"Torrent ({best.get('source', '')})",
                "_is_torrent": True,
                "_torrent_data": {
                    "name": best.get("name", ""),
                    "clean_name": _clean_name(best.get("name", "")),
                    "magnet": best.get("magnet", ""),
                    "info_hash": parsed.get("hash", ""),
                    "seeders": best.get("seeders", 0),
                    "leechers": best.get("leechers", 0),
                    "size": best.get("size", ""),
                    "size_mb": best.get("size_mb", 0) or 0,
                    "source": best.get("source", ""),
                    "quality": q_name,
                },
                "alternatives": [
                    {
                        "name": t.get("name", ""),
                        "clean_name": _clean_name(t.get("name", "")),
                        "magnet": t.get("magnet", ""),
                        "info_hash": _parse_magnet(t.get("magnet", "")).get("hash", ""),
                        "seeders": t.get("seeders", 0),
                        "size": t.get("size", ""),
                        "quality": _quality(t.get("name", ""))[1],
                        "source": t.get("source", ""),
                        "score": t.get("_score", 0),
                    }
                    for t in valid[1:8] if t.get("magnet")
                ]
            }
            
            cache_set(cache_key, result)
            logger.info(f"Found {len(valid)} torrents. Best: {best.get('source')} S={best.get('seeders')} Q={q_name}")
            return result
            
    except Exception as ex:
        logger.error(f"Torrent extract error: {ex}")
        if retry:
            logger.debug("Retrying...")
            await asyncio.sleep(1)
            return await extract(dbid, s, e, title, quality, retry=False)
        return None


# ==========================================
# FORMAT FOR PLAYER
# ==========================================

def format_torrent_sources(result, subs=None) -> List[Dict]:
    """Format torrent result to match player source format."""
    if not result or not result.get("_torrent_data"):
        return []
    
    td = result["_torrent_data"]
    title = result.get("title", "")
    imdb_id = result.get("imdb_id", "")
    
    sources = []
    
    sources.append({
        "name": f"Torrent [{td.get('quality', 'Auto')}] - {td.get('source', '')} ({td.get('seeders', 0)} S)",
        "data": {
            "type": "torrent",
            "is_torrent": True,
            "magnet": td.get("magnet", ""),
            "info_hash": td.get("info_hash", ""),
            "stream": "",
            "subtitle": subs or [],
            "quality": td.get("quality", "auto"),
            "title": title,
            "imdb_id": imdb_id,
            "thumbnails": result.get("backdrop", ""),
            "seeders": td.get("seeders", 0),
            "leechers": td.get("leechers", 0),
            "size": td.get("size", ""),
            "size_mb": td.get("size_mb", 0),
            "source": td.get("source", ""),
            "clean_name": td.get("clean_name", ""),
        }
    })
    
    for i, alt in enumerate(result.get("alternatives", [])[:5]):
        sources.append({
            "name": f"Alt {i+1} [{alt.get('quality', '?')}] - {alt.get('source', '')} ({alt.get('seeders', 0)} S)",
            "data": {
                "type": "torrent",
                "is_torrent": True,
                "magnet": alt.get("magnet", ""),
                "info_hash": alt.get("info_hash", ""),
                "stream": "",
                "subtitle": subs or [],
                "quality": alt.get("quality", "auto"),
                "title": title,
                "imdb_id": imdb_id,
                "thumbnails": "",
                "seeders": alt.get("seeders", 0),
                "leechers": 0,
                "size": alt.get("size", ""),
                "size_mb": 0,
                "source": alt.get("source", ""),
                "clean_name": alt.get("clean_name", ""),
            }
        })
    
    return sources


def get_best_magnet(result: Optional[Dict]) -> str:
    if not result or not result.get("_torrent_data"):
        return ""
    return result["_torrent_data"].get("magnet", "")


def get_all_magnets(result: Optional[Dict]) -> List[str]:
    if not result:
        return []
    magnets = []
    if result.get("_torrent_data", {}).get("magnet"):
        magnets.append(result["_torrent_data"]["magnet"])
    for alt in result.get("alternatives", []):
        if alt.get("magnet"):
            magnets.append(alt["magnet"])
    return magnets


__all__ = [
    "extract",
    "format_torrent_sources",
    "get_best_magnet",
    "get_all_magnets",
]