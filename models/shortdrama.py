import os
import re,json
from typing import Optional,Dict,List
from curl_cffi import requests as curl_requests

class ShortDramaExtractor:
    SITES = {
        "reelshort": "https://reelshort.com",
        "shortmax": "https://shortmax.com",
    }
    
    def __init__(self):
        self.session = curl_requests.Session(impersonate="chrome131", proxy=os.getenv("PROXY_URL"))
        self.session.timeout = 20
    
    def search(self, title: str, site: str = "reelshort") -> List[Dict]:
        base = self.SITES.get(site, self.SITES["reelshort"])
        try:
            resp = self.session.get(f"{base}/search?q={title}", timeout=20)
            if resp.status_code != 200: return []
            results = []
            seen = set()
            
            next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
            if next_data:
                try:
                    data = json.loads(next_data.group(1))
                    props = data.get("props", {}).get("pageProps", {})
                    def find_videos(obj):
                        if isinstance(obj, list):
                            for item in obj:
                                if isinstance(item, dict) and ("videoId" in item or "id" in item or "title" in item):
                                    yield item
                                else:
                                    yield from find_videos(item)
                        elif isinstance(obj, dict):
                            for v in obj.values():
                                yield from find_videos(v)
                    for item in find_videos(props):
                        name = item.get("title") or item.get("videoTitle", "")
                        vid = item.get("videoId") or item.get("id", "")
                        if name and vid:
                            slug = item.get("slug", vid)
                            url = f"{base}/video/{slug}"
                            if url not in seen:
                                seen.add(url)
                                results.append({"slug": slug, "name": name.strip(), "url": url, "site": site})
                except: pass

            if not results:
                for m in re.findall(r'href="(/video/[^"]*)"[^>]*>.*?>([^<]{4,})<', resp.text, re.S | re.I):
                    url, name = base + m[0], m[1].strip()
                    if url not in seen and not any(x in url for x in ["/search", "/privacy", "/terms"]):
                        seen.add(url)
                        results.append({"slug": url.strip("/").split("/")[-1], "name": name, "url": url, "site": site})
            return results[:10]
        except Exception as e:
            print(f"[SD] Search error: {e}")
            return []
    
    def extract_from_url(self, url: str, episode: int = None) -> Optional[Dict]:
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200: return None
            next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
            if next_data:
                try:
                    data = json.loads(next_data.group(1))
                    props = data.get("props", {}).get("pageProps", {})
                    def find_m3u8(obj):
                        if isinstance(obj, str) and ".m3u8" in obj: return obj
                        if isinstance(obj, dict):
                            for v in obj.values():
                                r = find_m3u8(v)
                                if r: return r
                        if isinstance(obj, list):
                            for v in obj:
                                r = find_m3u8(v)
                                if r: return r
                        return None
                    m3u8 = find_m3u8(props)
                    if m3u8:
                        if not m3u8.startswith("http"): m3u8 = "https:" + m3u8
                        return {"url": m3u8, "quality": "720p", "type": "hls", "server": "shortdrama", "referer": url, "origin": "", "qualities": []}
                except: pass
            url_m = re.search(r'"(?:url|file|m3u8|source)"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', resp.text, re.I)
            if url_m:
                return {"url": url_m.group(1), "quality": "720p", "type": "hls", "server": "shortdrama", "referer": url, "origin": "", "qualities": []}
            embed_m = re.search(r'<iframe[^>]*src=["\']([^"\']+)["\']', resp.text)
            if embed_m:
                return self._extract_from_embed(embed_m.group(1), url)
            return None
        except Exception as e:
            print(f"[SD] Extract error: {e}")
            return None
    
    def _extract_from_embed(self, embed_url: str, referer: str) -> Optional[Dict]:
        try:
            resp = self.session.get(embed_url, timeout=20, headers={"Referer": referer})
            if resp.status_code != 200: return None
            for pattern in [r'"(?:url|file|m3u8|source)"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', r'src:\s*"(https?://[^"]+\.m3u8[^"]*)"']:
                url_m = re.search(pattern, resp.text, re.I)
                if url_m:
                    return {"url": url_m.group(1), "quality": "720p", "type": "hls", "server": "shortdrama", "referer": embed_url, "origin": "", "qualities": []}
            return None
        except Exception as e:
            print(f"[SD] Embed error: {e}")
            return None
    
    def extract(self, id: str, season: int = None, episode: int = None, title: str = None, site: str = "reelshort") -> Optional[Dict]:
        if id.startswith('http'): url = id
        elif '/' in id and '.' not in id.split('/')[0]:
            base = self.SITES.get(site, self.SITES["reelshort"])
            url = f"{base}/{id}"
        else:
            results = self.search(title or id, site)
            if not results: return None
            url = results[0]['url']
        result = self.extract_from_url(url, episode)
        if result:
            result.update({"imdb_id": "", "title": title or "", "file_name": f"shortdrama_{result['quality']}", "provider": f"shortdrama-{site}", "is_hls": True, "site": site})
        return result

_ext = None
def get_extractor():
    global _ext
    if _ext is None: _ext = ShortDramaExtractor()
    return _ext

async def extract(id: str, season: int = None, episode: int = None, **kw) -> Optional[Dict]:
    return get_extractor().extract(id, season, episode, title=kw.get("title"), site=kw.get("site", "reelshort"))

async def search(q: str, site: str = "reelshort") -> List[Dict]: return get_extractor().search(q, site)

def format_as_source(result: Dict, subs: List = None) -> Dict:
    if not result: return {}
    return {"sources": [{"url": result["url"], "quality": result["quality"], "type": "hls", "server": "shortdrama", "referer": result["referer"], "origin": result["origin"], "is_hls": True}], "subtitles": subs or [], "provider": result.get("provider", "shortdrama")}
