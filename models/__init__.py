import os
from typing import Optional, List, Dict, Any

# ==========================================
# CRITICAL FIX: KILL RENDER'S INTERNAL PROXY
# ==========================================
# Render injects HTTPS_PROXY which causes 407 Auth Required errors.
# By placing this at the top of the models package, we guarantee that
# curl_cffi NEVER sees these variables, no matter what file imports this.
for var in [
    "PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY",
    "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy",
]:
    os.environ.pop(var, None)

# Now it is safe to import the scrapers
from .vidapi import extract, format_sources, extract_quality
from .torrents import extract as torrent_extract, format_torrent_sources, get_best_magnet, get_all_magnets
from .subtitles import get_subtitles
from .utils import fetch, error

_base_version = "13.0.0"
_commit = os.environ.get("RENDER_GIT_COMMIT_SHA", "")[:7]
VERSION = f"{_base_version}+{_commit}" if _commit else _base_version

async def info():
    return {
        "project": "Streaming API",
        "version": VERSION,
        "provider": "VidAPI (vidsrc.pm)",
        "covers": ["movie", "tv", "anime", "asian", "short"],
        "subtitle_source": "SubDL + OpenSubtitles",
        "python_version": os.sys.version.split()[0] if hasattr(os, 'sys') else "unknown"
    }

__all__ = [
    'extract',
    'format_sources',
    'extract_quality',
    'get_subtitles',
    'fetch',
    'error',
    'info',
    'VERSION'
]