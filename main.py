import os
import asyncio
import httpx

# ============================================================
# NUCLEAR OPTION: Kill ALL proxy variables on startup.
# Render paid tier has direct internet access — no proxy needed.
# ============================================================
for var in ["PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"]:
    os.environ.pop(var, None)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import gzip
from models import info, extract, format_sources, extract_quality, get_subtitles, fetch, VERSION
from models.cache import stats as cache_stats, clear as cache_clear, clear_category as cache_clear_category
from io import BytesIO
from fastapi.responses import StreamingResponse

app = FastAPI(title="Streaming API", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ExtractItem(BaseModel):
    id: str
    type: str = "movie"
    season: Optional[int] = None
    episode: Optional[int] = None

# ==========================================
# HELPER: DYNAMIC TMDB METADATA FETCHER
# Used by RCP and ProRCP to keep code DRY
# ==========================================
async def _fetch_tmdb_meta(dbid: str, mtype: str) -> dict:
    """Fetches clean metadata directly from TMDB API."""
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="TMDB_API_KEY not configured on server.")
    
    url = f"https://api.themoviedb.org/3/{mtype}/{dbid}?api_key={api_key}&append_to_response=credits"
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="TMDB metadata not found.")
        
        d = resp.json()
        
        # Extract certification safely
        certification = ""
        if mtype == "movie":
            try:
                dates = d.get("release_dates", {}).get("results", [])
                if dates:
                    us_dates = dates[0].get("release_dates", [])
                    if us_dates: certification = us_dates[0].get("certification", "")
            except (IndexError, KeyError, TypeError):
                pass
        else:
            try:
                ratings = d.get("content_ratings", {}).get("results", [])
                if ratings: certification = ratings[0].get("rating", "")
            except (IndexError, KeyError, TypeError):
                pass

        return {
            "id": d.get("id"),
            "title": d.get("title") or d.get("name"),
            "overview": d.get("overview", ""),
            "poster_path": d.get("poster_path", ""),
            "backdrop": d.get("backdrop_path", ""),
            "rating": d.get("vote_average", 0),
            "vote_count": d.get("vote_count", 0),
            "runtime": d.get("runtime") or (d.get("episode_run_time", [None])[0]),
            "release_date": d.get("release_date") or d.get("first_air_date", ""),
            "genres": [g["name"] for g in d.get("genres", [])],
            "number_of_seasons": d.get("number_of_seasons", 0),
            "status": d.get("status", ""),
            "tagline": d.get("tagline", ""),
            "popularity": d.get("popularity", 0),
            "budget": d.get("budget", 0),
            "revenue": d.get("revenue", 0),
            "certification": certification,
            "cast": [
                {"name": c["name"], "character": c.get("character", ""), "profile_path": c.get("profile_path")}
                for c in d.get("credits", {}).get("cast", [])[:15]
            ]
        }

# ==========================================
# ENDPOINTS
# ==========================================
@app.api_route("/", methods=["GET", "HEAD"])
async def index(): return await info()

# ==========================================
# RCP: REMOTE PROCEDURE CALL (Metadata Only)
# Replaces passing 30 params via URL. Frontend just passes ID.
# ==========================================
@app.get("/rcp/{dbid}")
async def get_rcp(dbid: str, type: str = "movie"):
    """Returns pure, clean metadata for a movie or TV show."""
    try:
        meta = await _fetch_tmdb_meta(dbid, type)
        return {"status": 200, "meta": meta}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# PRORCP: ALL-IN-ONE PRO ENDPOINT
# Fetches Meta + Streams + Subs concurrently.
# 1 Request from frontend = Full Details Screen Hydrated.
# ==========================================
@app.get("/prorcp/{dbid}")
async def get_prorcp(dbid: str, type: str = "movie", s: int = None, e: int = None, title: str = None):
    if not dbid: raise HTTPException(status_code=404, detail="Invalid id")
    
    try:
        # Run all 3 heavy tasks concurrently using asyncio.gather
        # This cuts load time from ~6 seconds down to ~2 seconds
        meta_task = _fetch_tmdb_meta(dbid, type)
        stream_task = extract(dbid, s, e, title=title)
        
        mt = "tv" if s is not None and e is not None else "movie"
        # Subs need title and imdb_id which we get from the stream/meta results
        meta_res, stream_res = await asyncio.gather(meta_task, stream_task, return_exceptions=True)

        # Handle exceptions gracefully
        if isinstance(meta_res, Exception):
            raise HTTPException(status_code=404, detail=f"Meta failed: {str(meta_res)}")
        if isinstance(stream_res, Exception):
            stream_res = None # Allow UI to load even if stream fails

        # Fetch subs using the results we just got
        subs = []
        if stream_res:
            imdb_id = stream_res.get("imdb_id", "")
            show_title = stream_res.get("title") or meta_res.get("title")
            subs = await get_subtitles(imdb_id, show_title, mt, s, e)

        return {
            "status": 200,
            "meta": meta_res,
            "sources": format_sources(stream_res, subs) if stream_res else [],
            "stream_url": stream_res["stream_urls"][0] if stream_res and stream_res.get("stream_urls") else None,
            "provider": stream_res.get("provider") if stream_res else None
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# UNIVERSAL STREAM ENDPOINT
# ==========================================
@app.get("/stream/{dbid}")
async def get_stream(dbid: str, s: int = None, e: int = None, title: str = None):
    if not dbid: raise HTTPException(status_code=404, detail="Invalid id")
    result = await extract(dbid, s, e, title=title)
    if not result: raise HTTPException(status_code=404, detail="No streams found")
    mt = "tv" if s is not None and e is not None else "movie"
    subs = await get_subtitles(result.get("imdb_id"), result.get("title"), mt, s, e)
    return {"status": 200, "info": "success", "sources": format_sources(result, subs)}

# ==========================================
# DEDICATED ASIAN DRAMA ENDPOINT
# ==========================================
@app.get("/asian")
async def asian_stream(title: str, s: int = None, e: int = None, provider: str = "dramacool"):
    if not title: raise HTTPException(status_code=400, detail="Title is required")
    
    if provider.lower() == "kissasian":
        from models.kissasian import extract as ka_extract, format_as_source as ka_format
        result = await ka_extract(title, s, e, title=title)
        if not result or not result.get("url"):
            raise HTTPException(status_code=404, detail="Not found on KissAsian.")
        formatted = ka_format(result)
        return {"status": 200, "info": "success", **formatted}
    else:
        from models.dramacool import extract as dc_extract, format_as_source as dc_format
        result = await dc_extract(title, s, e, title=title)
        if not result or not result.get("url"):
            raise HTTPException(status_code=404, detail="Not found on DramaCool.")
        formatted = dc_format(result)
        return {"status": 200, "info": "success", **formatted}

# ==========================================
# DEDICATED SHORT DRAMA ENDPOINT
# ==========================================
@app.get("/shortdrama")
async def shortdrama_stream(title: str, site: str = "reelshort", episode: int = None):
    from models.shortdrama import extract as sd_extract, format_as_source as sd_format
    result = await sd_extract(title, episode=episode, site=site)
    if not result or not result.get("url"):
        raise HTTPException(status_code=404, detail="Short drama not found.")
    formatted = sd_format(result)
    return {"status": 200, "info": "success", **formatted}

# ==========================================
# LEGACY ENDPOINTS
# ==========================================
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

# ==========================================
# UTILITIES
# ==========================================
@app.get("/health")
async def health():
    return {"status": "healthy", "version": VERSION, "providers": ["vidapi", "fzmovies", "o2tv", "kissasian", "dramacool", "shortdrama"]}

@app.get("/test-proxy")
async def test_proxy():
    from curl_cffi.requests import AsyncSession
    results = {}
    try:
        async with AsyncSession() as session:
            r = await session.get("https://httpbin.org/ip", timeout=10)
            results["direct_https"] = {"status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        results["direct_https"] = {"error": str(e)}
    return {"proxy_used": os.environ.get("PROXY_URL", "NONE"), "tests": results}

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