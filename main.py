from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import gzip
from models import info, extract, format_sources, extract_quality, get_subtitles, fetch
from models.cache import stats as cache_stats, clear as cache_clear, clear_category as cache_clear_category
from io import BytesIO
from fastapi.responses import StreamingResponse

app = FastAPI(title="Streaming API", version="13.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ExtractItem(BaseModel):
    id: str
    type: str = "movie"
    season: Optional[int] = None
    episode: Optional[int] = None

@app.get("/")
async def index(): return await info()

@app.get("/stream/{dbid}")
async def get_stream(dbid: str, s: int = None, e: int = None):
    if not dbid: raise HTTPException(status_code=404, detail="Invalid id")
    result = await extract(dbid, s, e)
    if not result: raise HTTPException(status_code=404, detail="No streams found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    return {"status": 200, "info": "success", "sources": format_sources(result, subs)}

@app.get("/fallback/{title}")
async def movie_fallback(title: str, q: str = None):
    from models.fzmovies import get_fallback_stream
    result = await get_fallback_stream(title, q)
    if not result: raise HTTPException(status_code=404, detail="Movie not found on fallback provider.")
    return {"status": 200, "provider": "fzmovies", "sources": [{"name": "Fallback Stream", "data": result}]}

@app.get("/smart/{dbid}")
async def tv_smart(dbid: str, s: int = None, e: int = None, q: str = "1080p"):
    from models.o2tv import extract as o2tv_extract, format_source as o2tv_format
    if q in ["480p", "720p"] and s is not None and e is not None:
        primary_result = await extract(dbid, s, e)
        if primary_result and primary_result.get("title"):
            title = primary_result.get("title")
            o2tv_result = await o2tv_extract(title, s, e)
            if o2tv_result and o2tv_result.get("download_url"):
                subs = await get_subtitles(primary_result.get("imdb_id", ""), title, "tv", s, e)
                sources = o2tv_format(o2tv_result, subs, needs_transcode=False)
                return {"status": 200, "provider": "o2tv (Direct MP4)", "sources": sources}
            else:
                subs = await get_subtitles(primary_result.get("imdb_id", ""), title, "tv", s, e)
                sources = format_sources(primary_result, subs)
                if sources: sources[0]["data"]["needs_transcode"] = True
                return {"status": 200, "provider": "vidapi (Transcode to " + q + ")", "sources": sources}
    result = await extract(dbid, s, e)
    if not result: raise HTTPException(status_code=404, detail="No streams found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    sources = format_sources(result, subs)
    if sources: sources[0]["data"]["needs_transcode"] = False
    return {"status": 200, "provider": "vidapi", "sources": sources}

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "13.0.0", "providers": ["vidapi", "fzmovies (Pure Python)", "o2tv"]}

@app.get("/subs")
async def subs(url: str):
    try:
        response = await fetch(url)
        content = response.content
        try:
            with gzip.open(BytesIO(content), "rt", encoding="utf-8") as f: text = f.read()
            if "-->" in text:
                async def gen(): yield text.encode("utf-8")
                return StreamingResponse(gen(), media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename=subtitle.srt"})
        except Exception: pass
        text = content.decode("utf-8", errors="ignore")
        if "-->" in text:
            async def gen2(): yield text.encode("utf-8")
            return StreamingResponse(gen2(), media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename=subtitle.srt"})
        raise HTTPException(status_code=500, detail="Could not parse subtitle")
    except HTTPException: raise
    except Exception: raise HTTPException(status_code=500, detail="Error fetching subtitle")
