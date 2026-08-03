VERSION = "13.0.0"
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