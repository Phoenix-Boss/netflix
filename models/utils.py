import os
from curl_cffi.requests import AsyncSession

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _get_proxy():
    """
    Get proxy URL from environment variables.
    Render injects HTTPS_PROXY / HTTP_PROXY automatically.
    Falls back to PROXY_URL for local dev if you set it manually.
    Returns None when no proxy is set (e.g. local dev without proxy).
    """
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )


async def fetch(url, headers=None, timeout=15):
    """Fetch a URL using a new async session with proxy support."""
    async with AsyncSession(impersonate="chrome", proxy=_get_proxy()) as session:
        h = {"User-Agent": UA, "Accept": "text/html,*/*", **(headers or {})}
        return await session.get(url, headers=h, timeout=timeout, allow_redirects=True)


async def fetch_session(url, session, headers=None, timeout=15):
    """Fetch a URL using an existing async session."""
    h = {"User-Agent": UA, "Accept": "text/html,*/*", **(headers or {})}
    return await session.get(url, headers=h, timeout=timeout, allow_redirects=True)


async def error(msg):
    return [{"name": "Error", "data": {"stream": "", "subtitle": [], "quality": "", "title": "", "imdb_id": "", "thumbnails": "", "error": msg}}]


async def is_stream_alive(url, timeout=5):
    """Check if a stream URL is reachable (returns True/False)."""
    try:
        async with AsyncSession(impersonate="chrome", proxy=_get_proxy()) as session:
            resp = await session.head(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
            return resp.status_code in [200, 206, 301, 302, 303, 307, 308]
    except Exception:
        return False