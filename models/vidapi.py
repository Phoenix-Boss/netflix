import os
import re, json, time, asyncio
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from .utils import fetch_session, is_stream_alive
from urllib.parse import quote
from .cache import get as cache_get, set as cache_set

VIDSRC_DOMAINS = ["vidsrc.pm", "vidsrc.rip", "vidsrc.cc", "vidsrc.lol", "vidsrc.top", "vidsrc.dev"]
FALLBACK_DOMAINS = ["vidsrc.link", "vidsrc.in", "vidsrc.tw", "vidapi.xyz"]

TIMEOUT = 10

def _get_proxy():
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )

async def extract(dbid, s=None, e=None, retry=True):
    media_type = "tv" if s is not None and e is not None else "movie"
    cache_key = f"stream:{media_type}:{dbid}:{s}:{e}"
    cached = cache_get(cache_key)
    if cached:
        print(f"[vidapi] CACHE HIT: {dbid}")
        return cached
    result = await _do_extract(dbid, media_type, s, e)
    if result is None and retry:
        print(f"[vidapi] RETRY: {dbid}")
        await asyncio.sleep(1)
        result = await _do_extract(dbid, media_type, s, e)
    if result:
        cache_set(cache_key, result)
    return result

async def _try_domain(domain, dbid, media_type, s, e, session):
    """Try a single domain. Returns result or None."""
    try:
        if media_type == "tv":
            embed_url = f"https://{domain}/embed/tv/{dbid}/{s}/{e}"
        else:
            embed_url = f"https://{domain}/embed/movie/{dbid}"

        resp1 = await asyncio.wait_for(
            fetch_session(embed_url, session, headers={"Referer": f"https://{domain}/"}),
            timeout=TIMEOUT
        )
        if not resp1 or resp1.status_code != 200:
            return None

        soup = BeautifulSoup(resp1.text, "html.parser")
        iframe = soup.find("iframe")
        if not iframe:
            return None

        player_url = iframe.get("src", "")
        if player_url.startswith("//"):
            player_url = "https:" + player_url
        if not player_url.startswith("http"):
            return None

        resp2 = await asyncio.wait_for(
            fetch_session(player_url, session, headers={"Referer": embed_url}),
            timeout=TIMEOUT
        )
        if not resp2 or resp2.status_code != 200:
            return None

        match = re.search(r"const CONFIG = ({.*?});", resp2.text, re.S)
        if not match:
            return None

        config = json.loads(match.group(1))
        media_id = config.get("MediaId", config.get("mediaId", dbid))
        id_type = config.get("idType", "tmdb")
        stream_api = config.get("streamDataApiUrl", "https://streamdata.vaplayer.ru/api.php")
        api_url = f"{stream_api}?{id_type}={quote(str(media_id))}&type={media_type}"
        if media_type == "tv":
            api_url += f"&season={s}&episode={e}"

        resp3 = await asyncio.wait_for(
            fetch_session(api_url, session, headers={"Referer": player_url, "Origin": "https://nextgencloudfabric.com", "Accept": "application/json"}),
            timeout=TIMEOUT
        )
        if not resp3 or resp3.status_code != 200:
            return None

        data = resp3.json()
        if str(data.get("status_code")) != "200" or not data.get("data"):
            return None

        d = data["data"]
        stream_urls = d.get("stream_urls", [])
        if not stream_urls:
            return None

        valid_streams = []
        for url in stream_urls[:5]:
            if await is_stream_alive(url, timeout=3):
                valid_streams.append(url)

        if not valid_streams:
            return None

        print(f"[vidapi] SUCCESS ({domain}): {len(valid_streams)} streams")
        return {
            "stream_urls": valid_streams,
            "imdb_id": d.get("imdb_id", ""),
            "title": d.get("title", ""),
            "file_name": d.get("file_name", ""),
            "backdrop": data.get("thumbnails_url", "")
        }

    except asyncio.TimeoutError:
        return None
    except Exception as ex:
        print(f"[vidapi] {domain} err: {ex}")
        return None


async def _do_extract(dbid, media_type, s, e):
    all_domains = VIDSRC_DOMAINS + FALLBACK_DOMAINS

    async with AsyncSession(impersonate="chrome", verify=False, proxy=_get_proxy()) as session:
        # Hit ALL domains in parallel instead of one-by-one
        tasks = [
            _try_domain(domain, dbid, media_type, s, e, session)
            for domain in all_domains
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # First valid result wins
        for result in results:
            if isinstance(result, dict) and result:
                return result
        return None


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
        sources.append({
            "name": name,
            "data": {
                "stream": url,
                "subtitle": subs or [],
                "quality": _quality(file_name, i),
                "title": title,
                "imdb_id": imdb_id,
                "thumbnails": backdrop
            }
        })
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