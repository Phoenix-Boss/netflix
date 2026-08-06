import os
from typing import Optional, List, Dict, Any
from .vidapi import extract, format_sources, extract_quality
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