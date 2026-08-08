import os
import logging
from typing import Optional, Dict, Any, List

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Status codes that indicate a stream/resource is reachable
_ALIVE_CODES = {200, 206, 301, 302, 303, 307, 308}


def _get_proxy() -> Optional[str]:
    """
    Get proxy URL from environment variables.
    Render injects HTTPS_PROXY / HTTP_PROXY automatically.
    Falls back to PROXY_URL for local dev if you set it manually.
    Returns None when no proxy is set (e.g. local dev without proxy).
    """
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("http_proxy")
        or os.getenv("PROXY_URL")
        or None
    )


def _base_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Merge the default UA/Accept with any caller-supplied headers."""
    h = {"User-Agent": UA, "Accept": "text/html,*/*"}
    if extra:
        h.update(extra)
    return h


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def fetch(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> Any:
    """
    Fetch a URL using a new async session with proxy support.
    Returns the response object from curl_cffi.
    """
    try:
        proxy = _get_proxy()
        async with AsyncSession(
            impersonate="chrome", verify=False, proxy=proxy
        ) as session:
            resp = await session.get(
                url,
                headers=_base_headers(headers),
                timeout=timeout,
                allow_redirects=True,
            )
            return resp
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        raise


async def fetch_session(
    url: str,
    session: AsyncSession,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> Any:
    """
    Fetch a URL using an existing async session.
    Returns the response object from curl_cffi.
    """
    try:
        resp = await session.get(
            url,
            headers=_base_headers(headers),
            timeout=timeout,
            allow_redirects=True,
        )
        return resp
    except Exception as e:
        logger.error(f"Error fetching {url} with session: {e}")
        raise


async def fetch_post(
    url: str,
    data: Optional[bytes] = None,
    json_data: Optional[Dict] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
) -> Any:
    """
    POST to a URL using a new async session.
    Pass *data* for raw bytes or *json_data* for a JSON body.
    Returns the response object from curl_cffi.
    """
    try:
        proxy = _get_proxy()
        async with AsyncSession(
            impersonate="chrome", verify=False, proxy=proxy
        ) as session:
            h = _base_headers(headers)
            if json_data is not None:
                h["Content-Type"] = "application/json"
                h["Accept"] = "application/json, text/plain, */*"
            resp = await session.post(
                url,
                headers=h,
                data=data,
                json=json_data,
                timeout=timeout,
                allow_redirects=True,
            )
            return resp
    except Exception as e:
        logger.error(f"Error POSTing {url}: {e}")
        raise


# ---------------------------------------------------------------------------
# Stream health check
# ---------------------------------------------------------------------------

async def is_stream_alive(url: str, timeout: int = 5) -> bool:
    """
    Check if a stream URL is reachable.
    Tries HEAD first (fast); falls back to GET if the server
    rejects HEAD (some CDNs return 405/403 for HEAD but serve GET fine).
    """
    try:
        proxy = _get_proxy()
        async with AsyncSession(
            impersonate="chrome", verify=False, proxy=proxy
        ) as session:
            # Fast path: HEAD request
            try:
                resp = await session.head(
                    url,
                    headers={"User-Agent": UA},
                    timeout=timeout,
                    allow_redirects=True,
                )
                if resp.status_code in _ALIVE_CODES:
                    return True
                # 405 / 403 on HEAD → fall through to GET
                if resp.status_code not in {403, 405}:
                    return False
            except Exception:
                pass  # Connection error on HEAD → try GET

            # Slow path: GET with tiny range so we don't download the file
            try:
                resp = await session.get(
                    url,
                    headers={
                        "User-Agent": UA,
                        "Range": "bytes=0-0",
                    },
                    timeout=timeout,
                    allow_redirects=True,
                )
                return resp.status_code in _ALIVE_CODES
            except Exception:
                return False

    except Exception as e:
        logger.debug(f"Stream alive check failed for {url}: {e}")
        return False


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def error(msg: str) -> List[Dict[str, Any]]:
    """
    Return a standardized error source-list entry.
    (Sync because it does no I/O — kept as a list so callers can
    concatenate it with other source lists without wrapping.)
    """
    return [
        {
            "name": "Error",
            "data": {
                "stream": "",
                "subtitle": [],
                "quality": "",
                "title": "",
                "imdb_id": "",
                "thumbnails": "",
                "error": msg,
            },
        }
    ]


def format_error_response(
    error_msg: str, status_code: int = 500
) -> Dict[str, Any]:
    """
    Format a standardized error response dictionary.
    """
    return {
        "success": False,
        "error": error_msg,
        "code": status_code,
    }


__all__ = [
    "fetch",
    "fetch_session",
    "fetch_post",
    "error",
    "is_stream_alive",
    "format_error_response",
    "UA",
    "_get_proxy",
]