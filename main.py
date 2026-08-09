import os, sys, asyncio, gzip, logging
from io import BytesIO
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import httpx

# ==========================================
# CRITICAL FIX: PROXY PRESERVATION
# ==========================================
# WARNING: Do NOT strip proxy variables here! 
# Your internal vidsrc.pm scraper (_get_proxy in __init__.py) relies on 
# these exact environment variables to bypass Cloudflare anti-bot.
# If you delete them on startup, vidsrc.pm WILL fail and fallback to FZMovies.
#
# for var in [
#     "PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY",
#     "https_proxy", "http_proxy", "ALL_PROXY",
# ]:
#     os.environ.pop(var, None)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info(f"Python version: {sys.version}")

try:
    from models import (
        info, extract, format_sources, extract_quality,
        get_subtitles, fetch, VERSION,
    )
    from models.cache import (
        stats as cache_stats,
        clear as cache_clear,
        clear_category as cache_clear_category,
    )
    logger.info("All models imported successfully")
except Exception as e:
    logger.error(f"Error importing models: {e}")
    raise

app = FastAPI(title="Streaming API", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ExtractItem(BaseModel):
    id: str
    type: str = "movie"
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None  # Added year to request model


# ---------------------------------------------------------------------------
# TMDB helper
# ---------------------------------------------------------------------------

async def _fetch_tmdb_meta(dbid: str, mtype: str) -> dict:
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="TMDB_API_KEY not configured on server.",
        )
    url = (
        f"https://api.themoviedb.org/3/{mtype}/{dbid}"
        f"?api_key={api_key}&append_to_response=credits"
    )
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=404, detail="TMDB metadata not found."
                )
            d = resp.json()

            # Certification
            certification = ""
            if mtype == "movie":
                try:
                    dates = d.get("release_dates", {}).get("results", [])
                    if dates:
                        certification = (
                            dates[0]
                            .get("release_dates", [{}])[0]
                            .get("certification", "")
                        )
                except Exception:
                    pass
            else:
                try:
                    ratings = d.get("content_ratings", {}).get("results", [])
                    if ratings:
                        certification = ratings[0].get("rating", "")
                except Exception:
                    pass

            return {
                "id": d.get("id"),
                "title": d.get("title") or d.get("name"),
                "overview": d.get("overview", ""),
                "poster_path": d.get("poster_path", ""),
                "backdrop": d.get("backdrop_path", ""),
                "rating": d.get("vote_average", 0),
                "vote_count": d.get("vote_count", 0),
                "runtime": d.get("runtime")
                or (d.get("episode_run_time", [None])[0]),
                "release_date": d.get("release_date")
                or d.get("first_air_date", ""),
                "genres": [g["name"] for g in d.get("genres", [])],
                "number_of_seasons": d.get("number_of_seasons", 0),
                "status": d.get("status", ""),
                "tagline": d.get("tagline", ""),
                "popularity": d.get("popularity", 0),
                "budget": d.get("budget", 0),
                "revenue": d.get("revenue", 0),
                "certification": certification,
                "cast": [
                    {
                        "name": c["name"],
                        "character": c.get("character", ""),
                        "profile_path": c.get("profile_path"),
                    }
                    for c in d.get("credits", {}).get("cast", [])[:15]
                ],
            }
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="TMDB API timeout")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"TMDB API error: {str(e)}"
            )


# ---------------------------------------------------------------------------
# Root / health
# ---------------------------------------------------------------------------

@app.api_route("/", methods=["GET", "HEAD"])
async def index():
    try:
        return await info()
    except Exception:
        return {
            "status": "ok",
            "message": "Streaming API is running",
            "version": VERSION,
            "python_version": sys.version.split()[0],
        }


@app.get("/health")
@app.head("/health")
async def health():
    return {
        "status": "healthy",
        "version": VERSION,
        "python_version": sys.version.split()[0],
        "providers": [
            "vidapi", "fzmovies", "o2tv",
            "kissasian", "dramacool", "shortdrama", "torrents",
        ],
    }


# ---------------------------------------------------------------------------
# TMDB metadata endpoints
# ---------------------------------------------------------------------------

@app.get("/rcp/{dbid}")
async def get_rcp(dbid: str, type: str = "movie"):
    try:
        return {"status": 200, "meta": await _fetch_tmdb_meta(dbid, type)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/prorcp/{dbid}")
async def get_prorcp(
    dbid: str,
    type: str = "movie",
    s: int = None,
    e: int = None,
    title: str = None,
    year: int = None,  # ADDED: Year parameter
):
    if not dbid:
        raise HTTPException(status_code=404, detail="Invalid id")
    try:
        meta_task = _fetch_tmdb_meta(dbid, type)
        # ADDED: Pass year=year to extract
        stream_task = extract(dbid, s, e, title=title, year=year)
        mt = "tv" if s is not None and e is not None else "movie"

        meta_res, stream_res = await asyncio.gather(
            meta_task, stream_task, return_exceptions=True
        )
        if isinstance(meta_res, Exception):
            raise HTTPException(
                status_code=404, detail=f"Meta failed: {str(meta_res)}"
            )
        if isinstance(stream_res, Exception):
            stream_res = None

        subs = []
        if stream_res:
            try:
                subs = await get_subtitles(
                    stream_res.get("imdb_id", ""),
                    stream_res.get("title") or meta_res.get("title"),
                    mt, s, e,
                )
            except Exception:
                pass

        return {
            "status": 200,
            "meta": meta_res,
            "sources": format_sources(stream_res, subs) if stream_res else [],
            "stream_url": (
                stream_res.get("stream_urls", [None])[0]
                if stream_res and stream_res.get("stream_urls")
                else None
            ),
            "provider": stream_res.get("provider") if stream_res else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Primary streaming endpoint
# ---------------------------------------------------------------------------

@app.get("/stream/{dbid}")
async def get_stream(
    dbid: str,
    s: int = None,
    e: int = None,
    title: str = None,
    year: int = None,  # ADDED: Year parameter
):
    if not dbid:
        raise HTTPException(status_code=404, detail="Invalid id")
    try:
        # ADDED: Pass year=year to extract
        result = await extract(dbid, s, e, title=title, year=year)
        if not result:
            raise HTTPException(status_code=404, detail="No streams found")
        mt = "tv" if s is not None and e is not None else "movie"
        subs = await get_subtitles(
            result.get("imdb_id"), result.get("title"), mt, s, e
        )
        return {
            "status": 200,
            "info": "success",
            "sources": format_sources(result, subs),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Asian drama endpoints
# ---------------------------------------------------------------------------

@app.get("/asian")
async def asian_stream(
    title: str,
    s: int = None,
    e: int = None,
    provider: str = "dramacool",
):
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    try:
        if provider.lower() == "kissasian":
            from models.kissasian import (
                extract as ka_extract,
                format_as_source as ka_format,
            )
            result = await ka_extract(title, s, e, title=title)
            if not result or not result.get("url"):
                raise HTTPException(
                    status_code=404, detail="Not found on KissAsian."
                )
            return {"status": 200, "info": "success", **ka_format(result)}
        else:
            from models.dramacool import (
                extract as dc_extract,
                format_as_source as dc_format,
            )
            result = await dc_extract(title, s, e, title=title)
            if not result or not result.get("url"):
                raise HTTPException(
                    status_code=404, detail="Not found on DramaCool."
                )
            return {"status": 200, "info": "success", **dc_format(result)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Short drama endpoint
# ---------------------------------------------------------------------------

@app.get("/shortdrama")
async def shortdrama_stream(
    title: str,
    site: str = "reelshort",
    episode: int = None,
):
    try:
        from models.shortdrama import (
            extract as sd_extract,
            format_as_source as sd_format,
        )
        result = await sd_extract(title, episode=episode, site=site)
        if not result or not result.get("url"):
            raise HTTPException(
                status_code=404, detail="Short drama not found."
            )
        return {"status": 200, "info": "success", **sd_format(result)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Torrent endpoints
# ---------------------------------------------------------------------------

@app.get("/torrent/{dbid}")
async def torrent_stream(
    dbid: str,
    s: int = None,
    e: int = None,
    title: str = None,
    quality: str = "1080p",
):
    if not dbid and not title:
        raise HTTPException(
            status_code=400, detail="ID or title is required"
        )
    try:
        from models.torrents import (
            extract as torrent_extract,
            format_torrent_sources,
            get_all_magnets,
        )
        result = await torrent_extract(
            dbid, s=s, e=e, title=title, quality=quality
        )
        if not result or not result.get("_torrent_data", {}).get("magnet"):
            raise HTTPException(status_code=404, detail="No torrents found")

        mt = "tv" if s is not None and e is not None else "movie"
        subs = []
        try:
            subs = await get_subtitles(
                result.get("imdb_id", ""),
                result.get("title", ""),
                mt, s, e,
            )
        except Exception:
            pass

        return {
            "status": 200,
            "info": "success",
            "provider": result.get("provider", "Torrent"),
            "torrent": result["_torrent_data"],
            "magnets": get_all_magnets(result),
            "sources": format_torrent_sources(result, subs),
        }
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.get("/torrent/search")
async def torrent_search_endpoint(
    title: str,
    quality: str = "1080p",
):
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    try:
        from models.torrents import (
            extract as torrent_extract,
            format_torrent_sources,
            get_all_magnets,
        )
        result = await torrent_extract(
            "search", title=title, quality=quality
        )
        if not result or not result.get("_torrent_data", {}).get("magnet"):
            raise HTTPException(status_code=404, detail="No torrents found")

        return {
            "status": 200,
            "info": "success",
            "provider": result.get("provider", "Torrent"),
            "torrent": result["_torrent_data"],
            "alternatives": result.get("alternatives", []),
            "magnets": get_all_magnets(result),
            "sources": format_torrent_sources(result),
        }
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


# ---------------------------------------------------------------------------
# Fallback endpoint
# ---------------------------------------------------------------------------

@app.get("/fallback/{title}")
async def movie_fallback(title: str, q: str = None, year: int = None):
    try:
        from models.fzmovies import get_fallback_stream
        # Note: Ensure get_fallback_stream in fzmovies.py accepts year=year if you want strict year matching here too
        result = await get_fallback_stream(title, q, year=year)
        if not result:
            raise HTTPException(
                status_code=404,
                detail="Movie not found on fallback provider.",
            )
        return {
            "status": 200,
            "provider": "fzmovies",
            "sources": [{"name": "Fallback Stream", "data": result}],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Smart TV endpoint (o2tv for low-quality, vidapi otherwise)
# ---------------------------------------------------------------------------

@app.get("/smart/{dbid}")
async def tv_smart(dbid: str, s: int = None, e: int = None, q: str = "1080p"):
    try:
        from models.o2tv import (
            extract as o2tv_extract,
            format_source as o2tv_format,
        )

        # Low-quality TV → try o2tv direct MP4 first, fall back to vidapi + transcode
        if q in ["480p", "720p"] and s is not None and e is not None:
            primary_result = await extract(dbid, s, e)
            if primary_result and primary_result.get("title"):
                title = primary_result.get("title")
                o2tv_result = await o2tv_extract(title, s, e)
                if o2tv_result and o2tv_result.get("download_url"):
                    subs = await get_subtitles(
                        primary_result.get("imdb_id", ""),
                        title, "tv", s, e,
                    )
                    return {
                        "status": 200,
                        "provider": "o2tv (Direct MP4)",
                        "sources": o2tv_format(
                            o2tv_result, subs, needs_transcode=False
                        ),
                    }
                else:
                    subs = await get_subtitles(
                        primary_result.get("imdb_id", ""),
                        title, "tv", s, e,
                    )
                    sources = format_sources(primary_result, subs)
                    if sources:
                        sources[0]["data"]["needs_transcode"] = True
                    return {
                        "status": 200,
                        "provider": f"vidapi (Transcode to {q})",
                        "sources": sources,
                    }

        # Default path
        result = await extract(dbid, s, e)
        if not result:
            raise HTTPException(status_code=404, detail="No streams found")
        mt = "tv" if s is not None and e is not None else "movie"
        subs = await get_subtitles(
            result.get("imdb_id"), result.get("title"), mt, s, e
        )
        sources = format_sources(result, subs)
        if sources:
            sources[0]["data"]["needs_transcode"] = False
        return {
            "status": 200,
            "provider": "vidapi",
            "sources": sources,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Utility endpoints
# ---------------------------------------------------------------------------

@app.get("/test-proxy")
async def test_proxy():
    try:
        from curl_cffi.requests import AsyncSession
        results = {}
        try:
            async with AsyncSession() as session:
                r = await session.get("https://httpbin.org/ip", timeout=10)
                results["direct_https"] = {
                    "status": r.status_code,
                    "body": r.text[:200],
                }
        except Exception as e:
            results["direct_https"] = {"error": str(e)}
        return {
            "proxy_used": os.environ.get("PROXY_URL", "NONE"),
            "tests": results,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/subs")
async def subs(url: str):
    try:
        response = await fetch(url)
        content = response.content

        # Try gzip decompression first
        try:
            with gzip.open(BytesIO(content), "rt", encoding="utf-8") as f:
                text = f.read()
            if "-->" in text:

                async def gen_gz():
                    yield text.encode("utf-8")

                return StreamingResponse(
                    gen_gz(),
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": (
                            "attachment; filename=subtitle.srt"
                        )
                    },
                )
        except Exception:
            pass

        # Plain text fallback
        text = content.decode("utf-8", errors="ignore")
        if "-->" in text:

            async def gen_plain():
                yield text.encode("utf-8")

            return StreamingResponse(
                gen_plain(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": (
                        "attachment; filename=subtitle.srt"
                    )
                },
            )

        raise HTTPException(status_code=500, detail="Could not parse subtitle")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching subtitle: {str(e)}"
        )


@app.get("/cache/stats")
async def get_cache_stats():
    return cache_stats()


@app.post("/cache/clear")
async def clear_cache(category: str = None):
    if category:
        cache_clear_category(category)
    else:
        cache_clear()
    return {"status": "cleared", "category": category}


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": exc.status_code, "detail": exc.detail},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": 500, "detail": "Internal server error"},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)