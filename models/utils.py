import os
rom curl_cffi.requests import AsyncSession

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

async def fetch(url, headers=None, timeout=15):
    async with AsyncSession(impersonate="chrome", proxy=os.getenv("PROXY_URL")) as session:
        h = {"User-Agent": UA, "Accept": "text/html,*/*", **(headers or {})}
        return await session.get(url, headers=h, timeout=timeout, connect_timeout=8, allow_redirects=True)

async def fetch_session(url, session, headers=None, timeout=15, connect_timeout=8):
    h = {"User-Agent": UA, "Accept": "text/html,*/*", **(headers or {})}
    return await session.get(url, headers=h, timeout=timeout, connect_timeout=connect_timeout, allow_redirects=True)

async def error(msg):
    return [{"name": "Error", "data": {"stream": "", "subtitle": [], "quality": "", "title": "", "imdb_id": "", "thumbnails": "", "error": msg}}]

async def is_stream_alive(url, timeout=5):
    try:
        async with AsyncSession(impersonate="chrome", proxy=os.getenv("PROXY_URL")) as session:
            resp = await session.head(url, headers={"User-Agent": UA}, timeout=timeout, connect_timeout=3, allow_redirects=True)
            return resp.status_code in [200, 206, 301, 302, 303, 307, 308]
    except Exception:
        return False