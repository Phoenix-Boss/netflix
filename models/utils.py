import os
import logging
from typing import Optional, Dict, Any, Union
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _get_proxy() -> Optional[str]:
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


async def fetch(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> Any:
    """
    Fetch a URL using a new async session with proxy support.
    Returns the response object from curl_cffi.
    """
    try:
        proxy = _get_proxy()
        async with AsyncSession(impersonate="chrome", proxy=proxy) as session:
            h = {"User-Agent": UA, "Accept": "text/html,*/*", **(headers or {})}
            response = await session.get(url, headers=h, timeout=timeout, allow_redirects=True)
            return response
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        raise


async def fetch_session(url: str, session: AsyncSession, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> Any:
    """
    Fetch a URL using an existing async session.
    Returns the response object from curl_cffi.
    """
    try:
        h = {"User-Agent": UA, "Accept": "text/html,*/*", **(headers or {})}
        response = await session.get(url, headers=h, timeout=timeout, allow_redirects=True)
        return response
    except Exception as e:
        logger.error(f"Error fetching {url} with session: {e}")
        raise


async def error(msg: str) -> list:
    """
    Return a standardized error response.
    """
    return [{
        "name": "Error",
        "data": {
            "stream": "",
            "subtitle": [],
            "quality": "",
            "title": "",
            "imdb_id": "",
            "thumbnails": "",
            "error": msg
        }
    }]


async def is_stream_alive(url: str, timeout: int = 5) -> bool:
    """
    Check if a stream URL is reachable (returns True/False).
    """
    try:
        proxy = _get_proxy()
        async with AsyncSession(impersonate="chrome", proxy=proxy) as session:
            resp = await session.head(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
            return resp.status_code in [200, 206, 301, 302, 303, 307, 308]
    except Exception as e:
        logger.debug(f"Stream alive check failed for {url}: {e}")
        return False


def format_error_response(error_msg: str, status_code: int = 500) -> Dict[str, Any]:
    """
    Format a standardized error response dictionary.
    """
    return {
        "success": False,
        "error": error_msg,
        "code": status_code
    }


__all__ = [
    'fetch',
    'fetch_session',
    'error',
    'is_stream_alive',
    'format_error_response',
    'UA',
    '_get_proxy'
]