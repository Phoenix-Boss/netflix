from curl_cffi.requests import AsyncSession
from urllib.parse import quote

_SBDL_KEY = "subdl_qFxEpBh6BuFCLpPB0YxC1YS0s0VBstg1Te7obtj4jmY"
_OS_KEY = "9xkBmnpMy7D3wP9HoxSifWGwJidqY7eO"

async def get_subtitles(imdb_id, title, media_type, s, e):
    if not imdb_id:
        return []
    results = []
    try:
        r = await _subdl(imdb_id, media_type, s, e)
        if r:
            print(f"[subs] SubDL: {len(r)}")
            results.extend(r)
    except Exception as ex:
        print(f"[subs] SubDL: {ex}")
    if not results:
        try:
            r = await _os_download(imdb_id, media_type, s, e)
            if r:
                print(f"[subs] OS: {len(r)}")
                results.extend(r)
        except Exception:
            pass
    if not results:
        try:
            r = await _os_pages(imdb_id, media_type, s, e)
            if r:
                print(f"[subs] OS pages: {len(r)}")
                results.extend(r)
        except Exception:
            pass
    seen = set()
    deduped = []
    for item in results:
        f = item.get("file", "")
        if f and f not in seen:
            seen.add(f)
            deduped.append(item)
    return deduped

async def _subdl(imdb_id, media_type, s, e):
    params = {"api_key": _SBDL_KEY, "imdb_id": imdb_id, "language": "eng"}
    if media_type == "tv" and s is not None and e is not None:
        params["season_number"] = s
        params["episode_number"] = e
    else:
        params["type"] = media_type
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    url = f"https://api.subdl.com/api/v1/subtitles?{qs}"
    async with AsyncSession(impersonate="chrome") as session:
        resp = await session.get(url, timeout=15)
        if resp.status_code != 200:
            print(f"[subs] SubDL status: {resp.status_code}")
            return []
        items = resp.json().get("subtitles", [])
        results = []
        for item in items:
            if item.get("lang", "").lower() != "english":
                continue
            sub_url = item.get("url", "")
            release = item.get("release_name", "")
            if not sub_url:
                continue
            results.append({"lang": "en", "file": f"https://api.subdl.com/download?link={sub_url}", "label": release or "English"})
            if len(results) >= 8:
                break
        if not results:
            params2 = {"api_key": _SBDL_KEY, "imdb_id": imdb_id}
            if media_type == "tv" and s is not None and e is not None:
                params2["season_number"] = s
                params2["episode_number"] = e
            else:
                params2["type"] = media_type
            qs2 = "&".join(f"{k}={quote(str(v))}" for k, v in params2.items())
            resp2 = await session.get(f"https://api.subdl.com/api/v1/subtitles?{qs2}", timeout=15)
            if resp2.status_code == 200:
                for item in resp2.json().get("subtitles", []):
                    if item.get("lang", "").lower() != "english":
                        continue
                    sub_url = item.get("url", "")
                    release = item.get("release_name", "")
                    if sub_url:
                        results.append({"lang": "en", "file": f"https://api.subdl.com/download?link={sub_url}", "label": release or "English"})
                        if len(results) >= 8:
                            break
        return results

async def _os_download(imdb_id, media_type, s, e):
    url = f"https://api.opensubtitles.com/api/v1/subtitles?imdb_id={imdb_id}&languages=en"
    if media_type == "tv" and s is not None and e is not None:
        url += f"&season_number={s}&episode_number={e}"
    async with AsyncSession(impersonate="chrome") as session:
        resp = await session.get(url, headers={"Api-Key": _OS_KEY, "User-Agent": "VidSrc-API/1.0"}, timeout=15)
        if resp.status_code != 200:
            return []
        items = resp.json().get("data", [])
        results = []
        seen = set()
        for item in items[:8]:
            attrs = item.get("attributes", {})
            files = attrs.get("files", [])
            if not files:
                continue
            fid = files[0].get("file_id", "")
            release = attrs.get("release", "")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            try:
                dl = await session.post("https://api.opensubtitles.com/api/v1/download", json={"file_id": fid}, headers={"Api-Key": _OS_KEY, "User-Agent": "VidSrc-API/1.0", "Content-Type": "application/json"}, timeout=8)
                if dl.status_code == 200:
                    link = dl.json().get("link", "")
                    if link:
                        results.append({"lang": "en", "file": link, "label": release or "English"})
            except Exception:
                pass
            if len(results) >= 5:
                break
        return results

async def _os_pages(imdb_id, media_type, s, e):
    url = f"https://api.opensubtitles.com/api/v1/subtitles?imdb_id={imdb_id}&languages=en"
    if media_type == "tv" and s is not None and e is not None:
        url += f"&season_number={s}&episode_number={e}"
    async with AsyncSession(impersonate="chrome") as session:
        resp = await session.get(url, headers={"Api-Key": _OS_KEY, "User-Agent": "VidSrc-API/1.0"}, timeout=15)
        if resp.status_code != 200:
            return []
        items = resp.json().get("data", [])
        results = []
        seen = set()
        for item in items[:10]:
            attrs = item.get("attributes", {})
            page_url = attrs.get("url", "")
            release = attrs.get("release", "")
            fid = attrs.get("files", [{}])[0].get("file_id", "")
            if not page_url or fid in seen:
                continue
            seen.add(fid)
            if not page_url.startswith("http"):
                page_url = "https://www.opensubtitles.com" + page_url
            results.append({"lang": "en", "file": page_url, "label": release or "English"})
        return results