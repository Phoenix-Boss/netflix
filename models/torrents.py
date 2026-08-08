import os, re, json, asyncio, logging, html
from typing import Optional, List, Dict
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from urllib.parse import quote, unquote
from .utils import fetch_session
from .cache import get as cache_get, set as cache_set

logger = logging.getLogger("torrents")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

def _dbg(): return os.environ.get("VIDAPI_DEBUG", "0") == "1"
TIMEOUT = 20
SIZE_RE = re.compile(r"([\d.]+)\s*(GB|MB|TB|KB)", re.I)
SE_RE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,2})")
Q_RANK = {"2160p": 5, "4k": 5, "uhd": 5, "1080p": 4, "1080": 4, "720p": 3, "720": 3, "480p": 2, "480": 2, "hdtv": 1, "webrip": 1, "web-dl": 1, "webdl": 1, "web": 1, "dvdrip": 1, "bdrip": 1, "brrip": 1, "bluray": 1}
BAD_KW = ["sample", "trailer", "promo", "extras", "bonus", "proof", "nuke"]
PUBLIC_TRACKERS = ["udp://tracker.opentrackr.org:1337/announce", "udp://open.tracker.cl:1337/announce", "udp://tracker.openbittorrent.com:6969/announce", "udp://open.stealth.si:80/announce", "udp://tracker.torrent.eu.org:451/announce", "udp://exodus.desync.com:6969/announce"]

def _proxy(): return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy") or os.getenv("PROXY_URL") or None
def _size_mb(text):
    if not text: return None
    m = SIZE_RE.search(text)
    if not m: return None
    v, u = float(m.group(1)), m.group(2).upper()
    if u == "TB": return v * 1024 * 1024
    if u == "GB": return v * 1024
    if u == "KB": return v / 1024
    return v
def _quality(name):
    if not name: return (0, "SD")
    n = name.lower()
    for q, s in Q_RANK.items():
        if q in n: return (s, q.upper() if len(q) <= 4 else q)
    return (0, "SD")
def _parse_se(name):
    if not name: return None
    m = SE_RE.search(name)
    return (int(m.group(1)), int(m.group(2))) if m else None
def _clean_name(name):
    if not name: return ""
    n = re.sub(r"\[.*?\]|\(.*?\)", "", name)
    n = re.sub(r"\.\w{3,4}$", "", n)
    n = re.sub(r"[-_](YTS|YIFY|RARBG|ETTG|TGx|PROPER|REPACK|FASTSUB|NOGROUP|PSA|NTb|FGT|CMRG)[-_]", "", n, flags=re.I)
    n = re.sub(r"[-_](720p|1080p|2160p|4K|BluRay|WEB-DL|WEBRip|HDTV|DVDRip|BRRip|x264|x265|HEVC|AAC|AC3|DTS|HDRip|REMUX|10bit|HDR|DolbyVision|Atmos)[-_.].*$", "", n, flags=re.I)
    n = n.replace(".", " ").replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", n).strip()
def _parse_magnet(magnet):
    r = {"hash": "", "trackers": []}
    if not magnet or not magnet.startswith("magnet:"): return r
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.I)
    if m: r["hash"] = m.group(1).lower()
    else:
        m = re.search(r"btih:([A-Z2-7]{32})", magnet)
        if m:
            try: r["hash"] = __import__("base64").b32decode(m.group(1)).hex()
            except: r["hash"] = m.group(1).lower()
    for m in re.finditer(r"tr=([^&]+)", magnet): r["trackers"].append(unquote(m.group(1)))
    return r
def _build_magnet(info_hash, name, trackers=None):
    if not info_hash: return ""
    if not trackers: trackers = PUBLIC_TRACKERS
    mag = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"
    for t in trackers: mag += f"&tr={quote(t)}"
    return mag
def _score(t, s=None, e=None, pq="1080p"):
    score = 0
    name, seeders = t.get("name", "").lower(), t.get("seeders", 0)
    score += min(int(seeders), 2000)
    qs, qn = _quality(name)
    ts = Q_RANK.get(pq.lower(), 4)
    if qs == ts: score += 1000
    elif qs > 0: score += qs * 150
    if s is not None and e is not None:
        se = _parse_se(name)
        if se:
            if se[0] == s and se[1] == e: score += 50000
            elif se[0] == s: score += 10000
        else: score -= 20000
    else:
        if _parse_se(name): score -= 20000
    for kw in BAD_KW:
        if kw in name: score -= 50000
    return score

async def _scrape_tpb(query, session, is_tv=False, s=None, e=None):
    results = []
    bases = ["https://thepiratebay.org", "https://tpb.party", "https://piratebay.party", "https://thehiddenbay.com"]
    search_q = f"{query} S{s:02d}E{e:02d}" if is_tv and s and e else query
    for base in bases:
        try:
            url = f"{base}/search.php?q={quote(search_q)}&search=Pirate+Search&page=0&orderby="
            if _dbg(): logger.debug(f"(TPB) Trying {base}...")
            resp = await asyncio.wait_for(fetch_session(url, session, headers={"Referer": base+"/"}), timeout=TIMEOUT)
            if not resp or resp.status_code != 200: continue
            soup = BeautifulSoup(resp.text, "html.parser")
            entries = soup.find_all("li", class_="list-entry")
            if not entries: continue
            for entry in entries:
                try:
                    name_tag = entry.find("span", class_="item-title")
                    name = name_tag.text.strip() if name_tag else ""
                    icons_span = entry.find("span", class_="item-icons")
                    magnet = ""
                    if icons_span:
                        m_tag = icons_span.find("a", href=lambda h: h and h.startswith("magnet:"))
                        if m_tag: magnet = html.unescape(m_tag["href"])
                    if not magnet: continue
                    se_tag = entry.find("span", class_="item-seed")
                    le_tag = entry.find("span", class_="item-leech")
                    sz_tag = entry.find("span", class_="item-size")
                    seeders = int(se_tag.text.strip().replace(",","")) if se_tag and se_tag.text.strip().replace(",","").isdigit() else 0
                    leechers = int(le_tag.text.strip().replace(",","")) if le_tag and le_tag.text.strip().replace(",","").isdigit() else 0
                    size_text = sz_tag.get_text(strip=True) if sz_tag else ""
                    if seeders == 0: continue
                    results.append({"name": name, "detail_url": "", "magnet": magnet, "seeders": seeders, "leechers": leechers, "size": size_text, "size_mb": _size_mb(size_text) or 0, "source": "PirateBay", "_need_magnet": False})
                except Exception: continue
            if results: break
        except asyncio.TimeoutError: continue
        except Exception as ex:
            if _dbg(): logger.debug(f"(TPB) {base} err: {ex}")
    if _dbg(): logger.debug(f"(TPB) Found {len(results)}")
    return results[:25]

async def _scrape_1337x(query, session, is_tv=False, s=None, e=None):
    results = []
    bases = ["https://www.1337x.to", "https://1337x.st", "https://x1337x.ws"]
    for base in bases:
        try:
            url = f"{base}/search/{quote(query)}/1/"
            if _dbg(): logger.debug(f"(1337x) Trying {base}...")
            resp = await asyncio.wait_for(fetch_session(url, session, headers={"Referer": base+"/"}), timeout=TIMEOUT)
            if not resp or resp.status_code != 200: continue
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", class_="table-list")
            if not table: continue
            rows = table.find_all("tr")[1:]
            if not rows: continue
            for row in rows[:20]:
                try:
                    cols = row.find_all("td")
                    if len(cols) < 5: continue
                    links = cols[0].find_all("a")
                    if len(links) < 2: continue
                    name = cols[0].text.strip()
                    href = links[1].get("href", "")
                    detail_url = base + href
                    seeders = int(cols[1].text.strip().replace(",","")) if cols[1].text.strip().replace(",","").isdigit() else 0
                    leechers = int(cols[2].text.strip().replace(",","")) if cols[2].text.strip().replace(",","").isdigit() else 0
                    size_text = cols[4].text.strip()
                    if seeders == 0: continue
                    results.append({"name": name, "detail_url": detail_url, "magnet": "", "seeders": seeders, "leechers": leechers, "size": size_text, "size_mb": _size_mb(size_text) or 0, "source": "1337x", "_need_magnet": True})
                except Exception: continue
            if results: break
        except asyncio.TimeoutError: continue
        except Exception as ex:
            if _dbg(): logger.debug(f"(1337x) {base} err: {ex}")
    if _dbg(): logger.debug(f"(1337x) Found {len(results)}")
    return results[:20]

async def _fetch_1337x_magnet(url, session):
    try:
        resp = await asyncio.wait_for(fetch_session(url, session, timeout=TIMEOUT), timeout=TIMEOUT)
        if not resp or resp.status_code != 200: return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        a = soup.find("a", href=re.compile(r"^magnet:\?"))
        return html.unescape(a["href"]) if a else ""
    except: return ""

async def _scrape_tg(query, session, is_tv=False, s=None, e=None):
    results = []
    bases = ["https://torrentgalaxy.to", "https://torrentgalaxy.mx"]
    search_q = f"{query} S{s:02d}E{e:02d}" if is_tv and s and e else query
    for base in bases:
        try:
            url = f"{base}/torrents.php?search={quote(search_q)}&sort=id&order=desc"
            if _dbg(): logger.debug(f"(TG) Trying {base}...")
            resp = await asyncio.wait_for(fetch_session(url, session, headers={"Referer": base+"/"}), timeout=TIMEOUT)
            if not resp or resp.status_code != 200: continue
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("div.tgxtablerow")
            if not rows: continue
            for row in rows:
                try:
                    name_el = row.select_one("a.txlight") or row.select_one("div.tgxtablecellfull a")
                    if not name_el: continue
                    name = name_el.text.strip()
                    mag_el = row.find("a", href=re.compile(r"^magnet:\?"))
                    if not mag_el: continue
                    magnet = mag_el["href"]
                    se_el = row.select_one("span.font-orange")
                    sz_el = row.select_one("span.badge-secondary")
                    seeders = int(se_el.text.strip().replace(",","")) if se_el and se_el.text.strip().replace(",","").isdigit() else 0
                    size_text = sz_el.text.strip() if sz_el else ""
                    if seeders == 0: continue
                    results.append({"name": name, "detail_url": "", "magnet": magnet, "seeders": seeders, "leechers": 0, "size": size_text, "size_mb": _size_mb(size_text) or 0, "source": "TorrentGalaxy", "_need_magnet": False})
                except: continue
            if results: break
        except asyncio.TimeoutError: continue
        except Exception as ex:
            if _dbg(): logger.debug(f"(TG) {base} err: {ex}")
    if _dbg(): logger.debug(f"(TG) Found {len(results)}")
    return results[:25]

async def _scrape_yts(query, session):
    try:
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={quote(query)}&sort_by=seeds&order_by=desc&limit=10"
        if _dbg(): logger.debug(f"(YTS) Searching...")
        resp = await asyncio.wait_for(fetch_session(url, session, headers={"Accept": "application/json"}), timeout=TIMEOUT)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        if data.get("data", {}).get("movie_count", 0) == 0: return []
        results = []
        for movie in data["data"].get("movies", []):
            title = movie.get("title_long", "")
            for t in movie.get("torrents", []):
                q = t.get("quality", "")
                magnet = t.get("url", "")
                seeders = t.get("seeds", 0)
                size_text = t.get("size", "")
                if seeders == 0: continue
                results.append({"name": f"{title} [{q}] [{size_text}]", "detail_url": "", "magnet": magnet, "seeders": seeders, "leechers": t.get("peers", 0), "size": size_text, "size_mb": _size_mb(size_text) or 0, "source": "YTS", "_need_magnet": False})
        if _dbg(): logger.debug(f"(YTS) Found {len(results)}")
        return results
    except Exception as ex:
        if _dbg(): logger.debug(f"(YTS) err: {ex}")
        return []

async def extract(dbid, s=None, e=None, title=None, quality="1080p", retry=True):
    if not title: return None
    is_tv = s is not None and e is not None
    media_type = "tv" if is_tv else "movie"
    cache_key = f"torrent:{media_type}:{dbid}:{s}:{e}:{quality}"
    if _dbg(): logger.setLevel(logging.DEBUG)
    cached = cache_get(cache_key)
    if cached: return cached
    try:
        logger.info(f"Searching torrents: {title} ({media_type})")
        async with AsyncSession(impersonate="chrome", verify=False, proxy=_proxy()) as session:
            tasks = [
                asyncio.create_task(_scrape_tpb(title, session, is_tv, s, e), name="tpb"),
                asyncio.create_task(_scrape_1337x(title, session, is_tv, s, e), name="1337x"),
                asyncio.create_task(_scrape_tg(title, session, is_tv, s, e), name="tg"),
            ]
            if not is_tv: tasks.append(asyncio.create_task(_scrape_yts(title, session), name="yts"))
            try: results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=TIMEOUT * 4)
            except asyncio.TimeoutError: results = [None] * len(tasks)
            
            all_t = []
            for i, r in enumerate(results):
                if isinstance(r, Exception): continue
                if isinstance(r, list): all_t.extend(r)
            
            mag_tasks, mag_idx = [], []
            for idx, t in enumerate(all_t):
                if t.get("_need_magnet") and t.get("detail_url"):
                    mag_tasks.append(asyncio.create_task(_fetch_1337x_magnet(t["detail_url"], session)))
                    mag_idx.append(idx)
            if mag_tasks:
                try:
                    magnets = await asyncio.wait_for(asyncio.gather(*mag_tasks, return_exceptions=True), timeout=TIMEOUT * 2)
                    for i, idx in enumerate(mag_idx):
                        if i < len(magnets) and not isinstance(magnets[i], Exception):
                            all_t[idx]["magnet"] = magnets[i]; all_t[idx]["_need_magnet"] = False
                except: pass
            
            valid = [t for t in all_t if t.get("magnet")]
            if not valid: logger.info(f"No valid torrents for: {title}"); return None
            
            for t in valid: t["_score"] = _score(t, s, e, quality)
            valid.sort(key=lambda x: x.get("_score", 0), reverse=True)
            
            best = valid[0]
            parsed = _parse_magnet(best.get("magnet", ""))
            _, q_name = _quality(best.get("name", ""))
            
            result = {
                "stream_urls": [], "imdb_id": dbid, "title": title, "file_name": best.get("name", ""), "backdrop": "", "provider": f"Torrent ({best.get('source', '')})", "_is_torrent": True,
                "_torrent_data": {"name": best.get("name", ""), "clean_name": _clean_name(best.get("name", "")), "magnet": best.get("magnet", ""), "info_hash": parsed.get("hash", ""), "seeders": best.get("seeders", 0), "leechers": best.get("leechers", 0), "size": best.get("size", ""), "size_mb": best.get("size_mb", 0), "source": best.get("source", ""), "quality": q_name},
                "alternatives": [{"name": t.get("name", ""), "clean_name": _clean_name(t.get("name", "")), "magnet": t.get("magnet", ""), "info_hash": _parse_magnet(t.get("magnet", "")).get("hash", ""), "seeders": t.get("seeders", 0), "size": t.get("size", ""), "quality": _quality(t.get("name", ""))[1], "source": t.get("source", ""), "score": t.get("_score", 0)} for t in valid[1:8] if t.get("magnet")]
            }
            cache_set(cache_key, result)
            logger.info(f"Found {len(valid)} torrents. Best: {best.get('source')} S={best.get('seeders')} Q={q_name}")
            return result
    except Exception as ex:
        logger.error(f"Torrent extract error: {ex}")
        if retry: await asyncio.sleep(1); return await extract(dbid, s, e, title, quality, retry=False)
        return None

def format_torrent_sources(result, subs=None):
    if not result or not result.get("_torrent_data"): return []
    td = result["_torrent_data"]
    title, imdb_id = result.get("title", ""), result.get("imdb_id", "")
    sources = [{"name": f"Torrent [{td.get('quality', 'Auto')}] - {td.get('source', '')} ({td.get('seeders', 0)} S)", "data": {"type": "torrent", "is_torrent": True, "magnet": td.get("magnet", ""), "info_hash": td.get("info_hash", ""), "stream": "", "subtitle": subs or [], "quality": td.get("quality", "auto"), "title": title, "imdb_id": imdb_id, "thumbnails": result.get("backdrop", ""), "seeders": td.get("seeders", 0), "leechers": td.get("leechers", 0), "size": td.get("size", ""), "size_mb": td.get("size_mb", 0), "source": td.get("source", ""), "clean_name": td.get("clean_name", "")}}]
    for i, alt in enumerate(result.get("alternatives", [])[:5]):
        sources.append({"name": f"Alt {i+1} [{alt.get('quality', '?')}] - {alt.get('source', '')} ({alt.get('seeders', 0)} S)", "data": {"type": "torrent", "is_torrent": True, "magnet": alt.get("magnet", ""), "info_hash": alt.get("info_hash", ""), "stream": "", "subtitle": subs or [], "quality": alt.get("quality", "auto"), "title": title, "imdb_id": imdb_id, "thumbnails": "", "seeders": alt.get("seeders", 0), "leechers": 0, "size": alt.get("size", ""), "size_mb": 0, "source": alt.get("source", ""), "clean_name": alt.get("clean_name", "")}})
    return sources

def get_best_magnet(result):
    return result["_torrent_data"].get("magnet", "") if result and result.get("_torrent_data") else ""

def get_all_magnets(result):
    if not result: return []
    mags = [result["_torrent_data"]["magnet"]] if result.get("_torrent_data", {}).get("magnet") else []
    mags.extend([a["magnet"] for a in result.get("alternatives", []) if a.get("magnet")])
    return mags

__all__ = ["extract", "format_torrent_sources", "get_best_magnet", "get_all_magnets"]