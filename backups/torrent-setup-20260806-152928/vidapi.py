import os
import re
import json
import asyncio
import logging
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from urllib.parse import quote

from .utils import fetch_session, is_stream_alive
from .cache import get as cache_get, set as cache_set

# Import the fallback extractors
from .o2tv import extract as o2tv_extract
from .fzmovies import extract as fzmovies_extract
from .kissasian import extract as kissasian_extract
from .dramacool import extract as dramacool_extract

# ==========================================
# LOGGING SETUP (Enable with VIDAPI_DEBUG=1)
# ==========================================
logger = logging.getLogger("vidapi")
logger.setLevel(logging.INFO)  # Default to silent
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[VidAPI] %(message)s'))
logger.addHandler(handler)

def _is_debug():
    return os.environ.get("VIDAPI_DEBUG", "0") == "1"

# ==========================================
# CONFIGURATION
# ==========================================
VIDSRC_DOMAINS = ["vidsrc.pm", "vidsrc.rip", "vidsrc.cc", "vidsrc.lol", "vidsrc.top", "vidsrc.dev"]
TIMEOUT = 8

_pending = {}  # cache_key -> asyncio.Future

def _get_proxy():
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )


async def extract(dbid, s=None, e=None, title=None, retry=True):
    media_type = "tv" if s is not None and e is not None else "movie"
    cache_key = f"stream:{media_type}:{dbid}:{s}:{e}"

    if _is_debug(): logger.setLevel(logging.DEBUG)

    cached = cache_get(cache_key)
    if cached:
        logger.debug(f"CACHE HIT: {dbid}")
        return cached

    if cache_key in _pending:
        logger.debug(f"COALESCE: {dbid} (waiting for existing fetch)")
        try:
            return await _pending[cache_key]
        except Exception:
            return None

    loop = asyncio.get_event_loop()
    future = loop.create_future()
    _pending[cache_key] = future

    try:
        result = await _do_extract(dbid, media_type, s, e, title)
        if result is None and retry:
            logger.debug(f"RETRY: {dbid}")
            await asyncio.sleep(1)
            result = await _do_extract(dbid, media_type, s, e, title)

        if result:
            cache_set(cache_key, result)

        if not future.done():
            future.set_result(result)
        return result

    except Exception as ex:
        logger.error(f"FATAL: {dbid} - {ex}")
        if not future.done():
            future.set_result(None)
        return None

    finally:
        _pending.pop(cache_key, None)


# ==========================================
# FALLBACK WRAPPERS
# ==========================================
async def _try_o2tv(dbid, s, e, title):
    try:
        logger.debug(f"(O2TV) Searching for {title} S{s:02d}E{e:02d}...")
        result = await o2tv_extract(title, s, e)
        if result and result.get("download_url"):
            return {"stream_urls": [result["download_url"]], "imdb_id": dbid, "title": result.get("show", title), "file_name": f'S{str(s).zfill(2)}E{str(e).zfill(2)}', "backdrop": ""}
    except Exception as ex:
        logger.debug(f"(O2TV) err: {ex}")
    return None

async def _try_fzmovies(dbid, title):
    try:
        logger.debug(f"(FZMovies) Searching for {title}...")
        result = await fzmovies_extract(title)
        if result and result.get("download_url"):
            return {"stream_urls": [result["download_url"]], "imdb_id": dbid, "title": result.get("title", title), "file_name": result.get("file_name", ""), "backdrop": ""}
    except Exception as ex:
        logger.debug(f"(FZMovies) err: {ex}")
    return None

async def _try_kissian(dbid, s, e, title):
    try:
        logger.debug(f"(KissAsian) Searching for {title} S{s:02d}E{e:02d}...")
        result = await kissasian_extract(dbid, s=s, e=e, title=title)
        if result and result.get("url"):
            return {
                "stream_urls": [result["url"]], "imdb_id": dbid, "title": result.get("title", title),
                "file_name": result.get("file_name", ""), "backdrop": "",
                # Special flags to tell format_sources this is HLS
                "_is_hls": True, "_hls_referer": result.get("referer", ""),
                "_hls_origin": result.get("origin", ""), "_hls_subtitles": result.get("subtitles", [])
            }
    except Exception as ex:
        logger.debug(f"(KissAsian) err: {ex}")
    return None

async def _try_dramacool(dbid, s, e, title):
    try:
        logger.debug(f"(DramaCool) Searching for {title} S{s:02d}E{e:02d}...")
        result = await dramacool_extract(dbid, s, e, title=title)
        if result and result.get("url"):
            return {
                "stream_urls": [result["url"]], "imdb_id": dbid, "title": result.get("title", title),
                "file_name": "", "backdrop": "",
                "_is_hls": True, "_hls_referer": "", "_hls_origin": "",
                "_hls_subtitles": result.get("subtitles", [])
            }
    except Exception as ex:
        logger.debug(f"(DramaCool) err: {ex}")
    return None


# ==========================================
# VIDAPI DOMAIN SCRAPER
# ==========================================
async def _try_domain(domain, dbid, media_type, s, e, session):
    try:
        embed_url = f"https://{domain}/embed/tv/{dbid}/{s}/{e}" if media_type == "tv" else f"https://{domain}/embed/movie/{dbid}"

        logger.debug(f"({domain}) Fetching embed...")
        resp1 = await asyncio.wait_for(fetch_session(embed_url, session, headers={"Referer": f"https://{domain}/"}), timeout=TIMEOUT)
        if not resp1 or resp1.status_code != 200: return None

        soup = BeautifulSoup(resp1.text, "html.parser")
        iframe = soup.find("iframe")
        if not iframe: return None

        player_url = iframe.get("src", "")
        if player_url.startswith("//"): player_url = "https:" + player_url
        if not player_url.startswith("http"): return None

        logger.debug(f"({domain}) Fetching player...")
        resp2 = await asyncio.wait_for(fetch_session(player_url, session, headers={"Referer": embed_url}), timeout=TIMEOUT)
        if not resp2 or resp2.status_code != 200: return None

        match = re.search(r"const CONFIG = ({.*?});", resp2.text, re.S)
        if not match:
            logger.debug(f"({domain}) Regex CONFIG failed, trying BS4 fallback...")
            soup2 = BeautifulSoup(resp2.text, "html.parser")
            for script in soup2.find_all("script"):
                if script.string and "CONFIG" in script.string:
                    match = re.search(r"CONFIG\s*=\s*({.*?});", script.string, re.S)
                    if match: break
        if not match: return None

        config = json.loads(match.group(1))
        media_id = config.get("MediaId", config.get("mediaId", dbid))
        id_type = config.get("idType", "tmdb")
        stream_api = config.get("streamDataApiUrl", "https://streamdata.vaplayer.ru/api.php")
        api_url = f"{stream_api}?{id_type}={quote(str(media_id))}&type={media_type}"
        if media_type == "tv": api_url += f"&season={s}&episode={e}"

        resp3 = await asyncio.wait_for(fetch_session(api_url, session, headers={"Referer": player_url, "Origin": "https://nextgencloudfabric.com", "Accept": "application/json"}), timeout=TIMEOUT)
        if not resp3 or resp3.status_code != 200: return None

        data = resp3.json()
        if str(data.get("status_code")) != "200" or not data.get("data"): return None

        d = data["data"]
        stream_urls = d.get("stream_urls", [])
        if not stream_urls: return None

        valid_streams = []
        for url in stream_urls[:2]:
            if await is_stream_alive(url, timeout=3):
                valid_streams.append(url)
                break
        if not valid_streams: valid_streams = stream_urls[:1]

        logger.debug(f"({domain}) SUCCESS: {len(valid_streams)} streams")
        return {"stream_urls": valid_streams, "imdb_id": d.get("imdb_id", ""), "title": d.get("title", ""), "file_name": d.get("file_name", ""), "backdrop": data.get("thumbnails_url", "")}

    except asyncio.TimeoutError: return None
    except Exception as ex:
        logger.debug(f"({domain}) err: {ex}")
        return None


# ==========================================
# RACE POOL
# ==========================================
async def _do_extract(dbid, media_type, s, e, title=None):
    async with AsyncSession(impersonate="chrome", verify=False, proxy=_get_proxy()) as session:
        pending = {asyncio.create_task(_try_domain(d, dbid, media_type, s, e, session)) for d in VIDSRC_DOMAINS}

        if media_type == "movie" and title:
            pending.add(asyncio.create_task(_try_fzmovies(dbid, title)))
            
        elif media_type == "tv" and title:
            pending.add(asyncio.create_task(_try_o2tv(dbid, s, e, title)))
            pending.add(asyncio.create_task(_try_kissian(dbid, s, e, title)))
            pending.add(asyncio.create_task(_try_dramacool(dbid, s, e, title)))

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception(): continue
                result = task.result()
                if isinstance(result, dict) and result.get("stream_urls"):
                    for p in pending: p.cancel()
                    return result
        return None


# ==========================================
# FORMATTING
# ==========================================
def _quality(file_name, index):
    if not file_name: return "auto"
    fn = file_name.lower()
    if "2160p" in fn or "4k" in fn: return "4K"
    if "1080p" in fn: return "1080p"
    if "720p" in fn: return "720p"
    if "480p" in fn: return "480p"
    return f"server{index + 1}"


def format_sources(result, subs=None):
    if not result: return []
    stream_urls = result.get("stream_urls", [])
    title = result.get("title", "")
    imdb_id = result.get("imdb_id", "")
    file_name = result.get("file_name", "")
    backdrop = result.get("backdrop", "")
    
    sources = []
    for i, url in enumerate(stream_urls):
        name = f"Server {i + 1}" if len(stream_urls) > 1 else "VidAPI"
        
        data = {
            "stream": url,
            "subtitle": subs or [],
            "quality": _quality(file_name, i),
            "title": title,
            "imdb_id": imdb_id,
            "thumbnails": backdrop
        }
        
        # INTELLIGENT HLS INJECTION:
        # If this result came from KissAsian or DramaCool, inject the required headers
        if result.get("_is_hls"):
            data["type"] = "hls"
            data["is_hls"] = True
            data["referer"] = result.get("_hls_referer", "")
            data["origin"] = result.get("_hls_origin", "")
            
            # Merge embedded HLS subtitles with fetched external subs
            hls_subs = result.get("_hls_subtitles", [])
            if hls_subs:
                if not data["subtitle"]: data["subtitle"] = []
                existing_urls = {s.get("url") for s in data["subtitle"] if s.get("url")}
                for hs in hls_subs:
                    if hs.get("url") and hs["url"] not in existing_urls:
                        data["subtitle"].append(hs)
                        
        sources.append({"name": name, "data": data})
    return sources


def extract_quality(file_name):
    if not file_name: return ["auto"]
    fn = file_name.lower()
    qualities = []
    if "2160p" in fn or "4k" in fn: qualities.append("4K")
    if "1080p" in fn: qualities.append("1080p")
    if "720p" in fn: qualities.append("720p")
    if "480p" in fn: qualities.append("480p")
    return qualities if qualities else ["auto"]