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

class SubtitleItem(BaseModel):
    id: str
    type: str = "movie"
    language: str = "en"
    season: Optional[int] = None
    episode: Optional[int] = None

@app.get("/")
async def index():
    return await info()

@app.get("/stream/{dbid}")
async def get_stream(dbid: str, s: int = None, e: int = None):
    if not dbid:
        raise HTTPException(status_code=404, detail="Invalid id")
    result = await extract(dbid, s, e)
    if not result:
        raise HTTPException(status_code=404, detail="No streams found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    return {"status": 200, "info": "success", "sources": format_sources(result, subs)}

@app.get("/extract")
async def extract_endpoint(id: str, type: str = "movie", s: int = None, e: int = None, provider: str = None):
    if not id:
        raise HTTPException(status_code=404, detail="id required")
    result = await extract(id, s, e)
    if not result:
        raise HTTPException(status_code=404, detail="No streams found")
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), type, s, e)
    return {"status": 200, "info": "success", "sources": format_sources(result, subs)}

@app.post("/batch/extract")
async def batch_extract(items: List[ExtractItem]):
    results = []
    for item in items:
        try:
            result = await extract(item.id, item.season, item.episode)
            if result:
                mt = item.type
                subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, item.season, item.episode)
                results.append({"id": item.id, "status": "success", "sources": format_sources(result, subs)})
            else:
                results.append({"id": item.id, "status": "not_found"})
        except Exception as ex:
            results.append({"id": item.id, "status": "error", "error": str(ex)})
    return {"status": 200, "total": len(results), "success": sum(1 for r in results if r["status"] == "success"), "results": results}

# ==========================================
# SMART ROUTING & FALLBACKS
# ==========================================

@app.get("/fallback/{title}")
async def movie_fallback(title: str, q: str = None):
    from models.fzmovies import get_fallback_stream
    result = await get_fallback_stream(title, q)
    if not result:
        raise HTTPException(status_code=404, detail="Movie not found on fallback provider.")
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
                if sources:
                    sources[0]["data"]["needs_transcode"] = True
                return {"status": 200, "provider": "vidapi (Transcode to " + q + ")", "sources": sources}

    result = await extract(dbid, s, e)
    if not result:
        raise HTTPException(status_code=404, detail="No streams found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    sources = format_sources(result, subs)
    if sources:
        sources[0]["data"]["needs_transcode"] = False
    return {"status": 200, "provider": "vidapi", "sources": sources}

# ==========================================
# ASIAN & SHORT DRAMAS (Direct Routes)
# ==========================================

@app.get("/asian/search")
async def search_asian(q: str):
    from models.kissasian import search as ka_search
    results = await ka_search(q)
    return {"status": 200, "query": q, "total": len(results), "results": results}

@app.get("/asian/direct")
async def asian_direct(url: str, s: int = None, e: int = None):
    from models.kissasian import extract, format_as_source
    result = await extract(url, s, e)
    if not result: raise HTTPException(status_code=404, detail="No stream found")
    return {"status": 200, "sources": format_as_source(result)}

@app.get("/asian/{dbid}")
async def get_asian_drama(dbid: str, s: int = None, e: int = None, title: str = None):
    from models.kissasian import extract, format_as_source
    result = await extract(dbid, s, e, title=title)
    if not result: raise HTTPException(status_code=404, detail="No stream found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    return {"status": 200, "provider": "kissasian", "sources": format_as_source(result, subs)}

@app.get("/dramacool/search")
async def search_dramacool(q: str):
    from models.dramacool import search as dc_search
    results = await dc_search(q)
    return {"status": 200, "query": q, "total": len(results), "results": results}

@app.get("/dramacool/direct")
async def dramacool_direct(url: str, s: int = None, e: int = None):
    from models.dramacool import extract, format_as_source
    result = await extract(url, s, e)
    if not result: raise HTTPException(status_code=404, detail="No stream found")
    return {"status": 200, "sources": format_as_source(result)}

@app.get("/dramacool/{dbid}")
async def get_dramacool(dbid: str, s: int = None, e: int = None, title: str = None):
    from models.dramacool import extract, format_as_source
    result = await extract(dbid, s, e, title=title)
    if not result: raise HTTPException(status_code=404, detail="No stream found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    return {"status": 200, "provider": "dramacool", "sources": format_as_source(result, subs)}

@app.get("/short/search")
async def search_short(q: str, site: str = "reelshort"):
    from models.shortdrama import search as sd_search
    results = await sd_search(q, site)
    return {"status": 200, "query": q, "site": site, "total": len(results), "results": results}

@app.get("/short/direct")
async def short_direct(url: str, e: int = None, site: str = "reelshort"):
    from models.shortdrama import extract, format_as_source
    result = await extract(url, episode=e, site=site)
    if not result: raise HTTPException(status_code=404, detail="No stream found")
    return {"status": 200, "sources": format_as_source(result)}

@app.get("/short/{dbid}")
async def get_short(dbid: str, e: int = None, title: str = None, site: str = "reelshort"):
    from models.shortdrama import extract, format_as_source
    result = await extract(dbid, episode=e, title=title, site=site)
    if not result: raise HTTPException(status_code=404, detail="No stream found")
    return {"status": 200, "provider": result.get("provider", "shortdrama"), "sources": format_as_source(result)}

@app.get("/reelshort/{dbid}")
async def get_reelshort(dbid: str, e: int = None, title: str = None):
    return await get_short(dbid, e, title, "reelshort")

# ==========================================
# SUBTITLES & SYSTEM
# ==========================================

@app.get("/subs")
async def subs(url: str):
    try:
        response = await fetch(url)
        content = response.content
        try:
            with gzip.open(BytesIO(content), "rt", encoding="utf-8") as f:
                text = f.read()
            if "-->" in text:
                async def gen(): yield text.encode("utf-8")
                return StreamingResponse(gen(), media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename=subtitle.srt"})
        except Exception: pass
        try:
            text = content.decode("utf-8", errors="ignore")
            if "-->" in text:
                async def gen2(): yield text.encode("utf-8")
                return StreamingResponse(gen2(), media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename=subtitle.srt"})
        except Exception: pass
        raise HTTPException(status_code=500, detail="Could not parse subtitle")
    except HTTPException: raise
    except Exception: raise HTTPException(status_code=500, detail="Error fetching subtitle")

@app.get("/quality")
async def quality_endpoint(id: str, type: str = "movie", s: int = None, e: int = None):
    result = await extract(id, s, e)
    if not result:
        return {"status": 404, "error": "No streams found"}
    qualities = extract_quality(result.get("file_name", ""))
    return {"status": 200, "id": id, "quality": qualities, "count": len(qualities)}

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "13.0.0", "providers": ["vidapi", "fzmovies", "o2tv", "kissasian", "dramacool", "reelshort"]}

@app.get("/cache/stats")
async def cache_stats_endpoint(): return cache_stats()

@app.delete("/cache/clear")
async def cache_clear_endpoint(category: str = None):
    if category: cache_clear_category(category)
    else: cache_clear()
    return {"status": 200, "message": f"Cleared{f' {category}' if category else ' all'} cache"}