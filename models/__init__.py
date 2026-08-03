import os

_base_version = "13.0.0"
_commit = os.environ.get("RENDER_GIT_COMMIT_SHA", "")[:7]
VERSION = f"{_base_version}+{_commit}" if _commit else _base_version

from .vidapi import extract, format_sources, extract_quality
from .subtitles import get_subtitles
from .utils import fetch, error

async def info():
    return {
        "project": "Streaming API",
        "version": VERSION,
        "provider": "VidAPI (vidsrc.pm)",
        "covers": ["movie", "tv", "anime", "asian", "short"],
        "subtitle_source": "SubDL + OpenSubtitles",
    }