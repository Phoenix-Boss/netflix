import os, re, json, asyncio, logging, base64
from typing import Optional, List, Dict
from curl_cffi.requests import AsyncSession
from urllib.parse import quote, unquote
from .utils import fetch_session
from .cache import get as cache_get, set as cache_set

logger = logging.getLogger("torrents")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

def _dbg():
    return os.environ.get("VIDAPI_DEBUG", "0") == "1"

TIMEOUT = 15
SIZE_RE = re.compile(r"([\d.]+)\s*(GB|MB|TB|KB)", re.I)
SE_RE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,2})")
Q_RANK = {
    "2160p": 5, "4k": 5, "uhd": 5,
    "1080p": 4, "1080": 4,
    "720p": 3, "720": 3,
    "480p": 2, "480": 2,
    "hdtv": 1, "webrip": 1, "web-dl": 1, "webdl": 1, "web": 1,
    "dvdrip": 1, "bdrip": 1, "brrip": 1, "bluray": 1,
}
BAD_KW = ["sample", "trailer", "promo", "extras", "bonus", "proof", "nuke"]
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
]


def _proxy():
    return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.getenv("PROXY_URL") or None


def _size_mb(text):
    if not text:
        return None
    m = SIZE_RE.search(text)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).upper()
    if u == "TB":
        return v * 1024 * 1024
    if u == "GB":
        return v * 1024
    if u == "KB":
        return v / 1024
    return v


def _quality(name):
    if not name:
        return (0, "SD")
    n = name.lower()
    for q, s in Q_RANK.items():
        if q in n:
            return (s, q.upper() if len(q) <= 4 else q)
    return (0, "SD")


def _parse_se(name):
    if not name:
        return None
    m = SE_RE.search(name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _clean_name(name):
    if not name:
        return ""
    n = re.sub(r"\[.*?\]|\(.*?\)", "", name)
    n = re.sub(r"\.\w{3,4}$", "", n)
    n = re.sub(
        r"[-_](YTS|YIFY|RARBG|ETTG|TGx|PROPER|REPACK|FASTSUB|NOGROUP)[-_]",
        "", n, flags=re.I,
    )
    n = re.sub(
        r"[-_](720p|1080p|2160p|4K|BluRay|WEB-DL|WEBRip|HDTV|DVDRip|BRRip"
        r"|x264|x265|HEVC|AAC|AC3|DTS|HDRip|REMUX|10bit|HDR|DolbyVision|Atmos)[-_.].*$",
        "", n, flags=re.I,
    )
    n = n.replace(".", " ").replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", n).strip()


def _parse_magnet(magnet):
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


def _build_magnet(info_hash, name, trackers=None):
    if not info_hash:
        return ""
    if not trackers:
        trackers = PUBLIC_TRACKERS
    mag = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"
    for t in trackers:
        mag += f"&tr={quote(t)}"
    return mag


def _get_webtor_embed(magnet, title=""):
    """Generate the official Webtor.io SDK embed URL (Instant, 0s latency)."""
    if not magnet:
        return ""
    return f"https://webtor.io/embed?url={quote(magnet)}&title={quote(title)}"


def _score(t, s=None, e=None, pq="1080p"):
    score = 0
    name = t.get("name", "").lower()
    seeders = t.get("seeders", 0)
    score += min(int(seeders), 2000)
    qs, qn = _quality(name)
    ts = Q_RANK.get(pq.lower(), 4)
    if qs == ts:
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
    for kw in BAD_KW:
        if kw in name:
            score -= 50000
    return score


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

async def _scrape_apibay(query, session, is_tv=False, s=None, e=None):
    """Scrape The Pirate Bay via Apibay (Unblocked JSON API)"""
    results = []
    try:
        url = f"https://apibay.org/q.php?q={quote(query)}&cat=0"
        if _dbg():
            logger.debug("(Apibay) Searching...")
        resp = await asyncio.wait_for(
            fetch_session(url, session, headers={"Accept": "application/json"}),
            timeout=TIMEOUT,
        )
        if not resp or resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        for item in data:
            try:
                name = unquote(item.get("name", ""))
                info_hash = item.get("info_hash", "")
                seeders = int(item.get("seeders", "0"))
                leechers = int(item.get("leechers", "0"))
                size_bytes = int(item.get("size", "0"))
                size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
                size_text = (
                    f"{size_mb:.2f} MB"
                    if size_mb < 1024
                    else f"{size_mb / 1024:.2f} GB"
                )
                if seeders == 0 or not info_hash:
                    continue
                magnet = _build_magnet(info_hash, name)
                results.append(
                    {
                        "name": name,
                        "detail_url": "",
                        "magnet": magnet,
                        "seeders": seeders,
                        "leechers": leechers,
                        "size": size_text,
                        "size_mb": size_mb,
                        "source": "PirateBay",
                        "_need_magnet": False,
                    }
                )
            except Exception:
                continue
        if _dbg():
            logger.debug(f"(Apibay) Found {len(results)}")
        return results[:25]
    except Exception as ex:
        if _dbg():
            logger.debug(f"(Apibay) err: {ex}")
        return []


async def _scrape_solid_api(query, session, is_tv=False, s=None, e=None):
    """Scrape SolidTorrents via their JSON API (Unblocked)"""
    results = []
    try:
        url = f"https://solidtorrents.to/api/v1/search?q={quote(query)}&sort=seeders"
        if _dbg():
            logger.debug("(SolidAPI) Searching...")
        resp = await asyncio.wait_for(
            fetch_session(url, session, headers={"Accept": "application/json"}),
            timeout=TIMEOUT,
        )
        if not resp or resp.status_code != 200:
            return []
        data = resp.json()
        for item in data.get("results", []):
            try:
                name = item.get("title", "")
                magnet = item.get("magnet", "")
                seeders = item.get("swarm", {}).get("seeders", 0)
                leechers = item.get("swarm", {}).get("leechers", 0)
                size_bytes = item.get("size", 0)
                size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
                size_text = (
                    f"{size_mb:.2f} MB"
                    if size_mb < 1024
                    else f"{size_mb / 1024:.2f} GB"
                )
                if seeders == 0 or not magnet:
                    continue
                results.append(
                    {
                        "name": name,
                        "detail_url": "",
                        "magnet": magnet,
                        "seeders": seeders,
                        "leechers": leechers,
                        "size": size_text,
                        "size_mb": size_mb,
                        "source": "SolidTorrents",
                        "_need_magnet": False,
                    }
                )
            except Exception:
                continue
        if _dbg():
            logger.debug(f"(SolidAPI) Found {len(results)}")
        return results[:25]
    except Exception as ex:
        if _dbg():
            logger.debug(f"(SolidAPI) err: {ex}")
        return []


async def _scrape_yts(query, session):
    """Scrape YTS via API"""
    try:
        url = (
            f"https://yts.mx/api/v2/list_movies.json?query_term={quote(query)}"
            f"&sort_by=seeds&order_by=desc&limit=10"
        )
        if _dbg():
            logger.debug("(YTS) Searching...")
        resp = await asyncio.wait_for(
            fetch_session(url, session, headers={"Accept": "application/json"}),
            timeout=TIMEOUT,
        )
        if not resp or resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("data", {}).get("movie_count", 0) == 0:
            return []
        results = []
        for movie in data["data"].get("movies", []):
            title = movie.get("title_long", "")
            for t in movie.get("torrents", []):
                q = t.get("quality", "")
                magnet = t.get("url", "")
                seeders = t.get("seeds", 0)
                size_text = t.get("size", "")
                if seeders == 0:
                    continue
                results.append(
                    {
                        "name": f"{title} [{q}] [{size_text}]",
                        "detail_url": "",
                        "magnet": magnet,
                        "seeders": seeders,
                        "leechers": t.get("peers", 0),
                        "size": size_text,
                        "size_mb": _size_mb(size_text) or 0,
                        "source": "YTS",
                        "_need_magnet": False,
                    }
                )
        if _dbg():
            logger.debug(f"(YTS) Found {len(results)}")
        return results
    except Exception as ex:
        if _dbg():
            logger.debug(f"(YTS) err: {ex}")
        return []


# ---------------------------------------------------------------------------
# Main extract
# ---------------------------------------------------------------------------

async def extract(dbid, s=None, e=None, title=None, quality="1080p", retry=True):
    if not title:
        return None
    is_tv = s is not None and e is not None
    media_type = "tv" if is_tv else "movie"
    cache_key = f"torrent:{media_type}:{dbid}:{s}:{e}:{quality}"
    if _dbg():
        logger.setLevel(logging.DEBUG)
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        logger.info(f"Searching torrents: {title} ({media_type})")
        async with AsyncSession(
            impersonate="chrome", verify=False, proxy=_proxy()
        ) as session:
            tasks = [
                asyncio.create_task(
                    _scrape_apibay(title, session, is_tv, s, e), name="apibay"
                ),
                asyncio.create_task(
                    _scrape_solid_api(title, session, is_tv, s, e), name="solid"
                ),
            ]
            if not is_tv:
                tasks.append(
                    asyncio.create_task(_scrape_yts(title, session), name="yts")
                )
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=TIMEOUT * 3,
                )
            except asyncio.TimeoutError:
                results = [None] * len(tasks)

            all_t = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    if _dbg():
                        logger.debug(f"Task {tasks[i].get_name()} err: {r}")
                    continue
                if isinstance(r, list):
                    all_t.extend(r)

            valid = [t for t in all_t if t.get("magnet")]
            if not valid:
                logger.info(f"No valid torrents for: {title}")
                return None

            for t in valid:
                t["_score"] = _score(t, s, e, quality)
            valid.sort(key=lambda x: x.get("_score", 0), reverse=True)

            best = valid[0]
            parsed = _parse_magnet(best.get("magnet", ""))
            _, q_name = _quality(best.get("name", ""))
            clean_title = _clean_name(best.get("name", ""))

            # --- Generate Webtor SDK embed URLs instantly (0s latency) ---
            best_embed = _get_webtor_embed(best.get("magnet", ""), clean_title)

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
                    "clean_name": clean_title,
                    "magnet": best.get("magnet", ""),
                    "info_hash": parsed.get("hash", ""),
                    "seeders": best.get("seeders", 0),
                    "leechers": best.get("leechers", 0),
                    "size": best.get("size", ""),
                    "size_mb": best.get("size_mb", 0),
                    "source": best.get("source", ""),
                    "quality": q_name,
                    "embed_url": best_embed,
                },
                "alternatives": [
                    {
                        "name": t.get("name", ""),
                        "clean_name": _clean_name(t.get("name", "")),
                        "magnet": t.get("magnet", ""),
                        "info_hash": _parse_magnet(t.get("magnet", "")).get(
                            "hash", ""
                        ),
                        "seeders": t.get("seeders", 0),
                        "size": t.get("size", ""),
                        "quality": _quality(t.get("name", ""))[1],
                        "source": t.get("source", ""),
                        "score": t.get("_score", 0),
                        "embed_url": _get_webtor_embed(t.get("magnet", ""), _clean_name(t.get("name", ""))),
                    }
                    for t in valid[1:8]
                    if t.get("magnet")
                ],
            }
            cache_set(cache_key, result)
            logger.info(
                f"Found {len(valid)} torrents. Best: {best.get('source')} "
                f"S={best.get('seeders')} Q={q_name}"
            )
            return result
    except Exception as ex:
        logger.error(f"Torrent extract error: {ex}")
        if retry:
            await asyncio.sleep(1)
            return await extract(dbid, s, e, title, quality, retry=False)
        return None


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def format_torrent_sources(result, subs=None):
    if not result or not result.get("_torrent_data"):
        return []
    td = result["_torrent_data"]
    title = result.get("title", "")
    imdb_id = result.get("imdb_id", "")

    sources = [
        {
            "name": (
                f"Torrent [{td.get('quality', 'Auto')}] - "
                f"{td.get('source', '')} ({td.get('seeders', 0)} S)"
            ),
            "data": {
                "type": "torrent",
                "is_torrent": True,
                "magnet": td.get("magnet", ""),
                "info_hash": td.get("info_hash", ""),
                # Put the Webtor SDK iframe URL here
                "stream": td.get("embed_url", ""),
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
            },
        }
    ]

    for i, alt in enumerate(result.get("alternatives", [])[:5]):
        sources.append(
            {
                "name": (
                    f"Alt {i + 1} [{alt.get('quality', '?')}] - "
                    f"{alt.get('source', '')} ({alt.get('seeders', 0)} S)"
                ),
                "data": {
                    "type": "torrent",
                    "is_torrent": True,
                    "magnet": alt.get("magnet", ""),
                    "info_hash": alt.get("info_hash", ""),
                    "stream": alt.get("embed_url", ""),
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
                },
            }
        )

    return sources


def get_best_magnet(result):
    if result and result.get("_torrent_data"):
        return result["_torrent_data"].get("magnet", "")
    return ""


def get_all_magnets(result):
    if not result:
        return []
    mags = (
        [result["_torrent_data"]["magnet"]]
        if result.get("_torrent_data", {}).get("magnet")
        else []
    )
    mags.extend(
        a["magnet"] for a in result.get("alternatives", []) if a.get("magnet")
    )
    return mags


__all__ = [
    "extract",
    "format_torrent_sources",
    "get_best_magnet",
    "get_all_magnets",
]