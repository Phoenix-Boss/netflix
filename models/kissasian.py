import os
import re, base64
from typing import Optional, Dict, List
from curl_cffi import requests as curl_requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


def _get_proxy():
    """Get proxy URL from environment. Render uses HTTPS_PROXY/HTTP_PROXY."""
    return (
        os.environ.get("HTTPS_PROXY") or
        os.environ.get("HTTP_PROXY") or
        os.environ.get("https_proxy") or
        os.environ.get("http_proxy") or
        os.getenv("PROXY_URL") or
        None
    )


class KissAsianExtractor:
    BASE_URL = "https://kissasian.tf"
    PLAYER_URL = "https://player.dramavideo.se"

    def __init__(self):
        self.session = curl_requests.Session(impersonate="chrome131", proxy=_get_proxy())
        self.session.timeout = 20

    def search(self, title: str) -> List[Dict]:
        try:
            resp = self.session.get(f"{self.BASE_URL}/search?q={title}", timeout=20)
            if resp.status_code != 200:
                return []
            results = []
            seen = set()
            for m in re.findall(r'href="([^"]+)"[^>]*>\s*([^<]+?)\s*<', resp.text, re.I):
                url = m[0]
                name = m[1].strip()
                if not url.startswith("http"):
                    url = self.BASE_URL + url
                is_series = "/series/" in url
                skip = not is_series or any(x in url for x in ["/search", "/news", "/privacy", "/terms", "/contact", "/dmca", "javascript:", "/genres/", "order=", "status="])
                if name and len(name) > 5 and url not in seen and not skip:
                    seen.add(url)
                    results.append({"slug": url.strip("/").split("/")[-1], "name": name, "url": url})
            return results[:10]
        except Exception as e:
            print(f"[KA] Search error: {e}")
            return []

    def extract_from_url(self, url: str, season: int = None, episode: int = None) -> Optional[Dict]:
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                return None
            if season and episode:
                ep_m = re.search(rf'href="([^"]*episode-?{episode}[^"]*)"', resp.text, re.I)
                if ep_m:
                    ep_url = ep_m.group(1)
                    if not ep_url.startswith("http"):
                        ep_url = self.BASE_URL + ep_url
                    return self._extract_ep(ep_url)
            return self._extract_ep(url)
        except Exception as e:
            print(f"[KA] Extract error: {e}")
            return None

    def _extract_ep(self, ep_url: str) -> Optional[Dict]:
        try:
            resp = self.session.get(ep_url, timeout=20)
            if resp.status_code != 200:
                return None
            dv_m = re.search(r'src=["\']([^"\']*dramavideo\.[^"\']*watch\?v=(\d+))["\']', resp.text)
            if not dv_m:
                dv_m2 = re.search(r'dramavideo\.([^/]+)/watch\?v=(\d+)', resp.text)
                if not dv_m2:
                    return None
                dv_url = f"https://dramavideo.{dv_m2.group(1)}/watch?v={dv_m2.group(2)}"
            else:
                dv_url = dv_m.group(1)
                if not dv_url.startswith("http"):
                    dv_url = "https:" + dv_url
            resp2 = self.session.get(dv_url, timeout=20, headers={"Referer": ep_url})
            if resp2.status_code != 200:
                return None
            dv_m2 = re.search(r'data-video=["\']([^"\']+)["\']', resp2.text)
            pr_m = re.search(r'data-provider=["\']([^"\']+)["\']', resp2.text)
            if not dv_m2:
                return None
            player_url = f"{self.PLAYER_URL}/?id={dv_m2.group(1)}&sv={pr_m.group(1) if pr_m else 'v3'}"
            resp3 = self.session.get(player_url, timeout=20, headers={"Referer": dv_url})
            if resp3.status_code != 200:
                return None
            return self._decrypt(resp3.text)
        except Exception as e:
            print(f"[KA] Ep error: {e}")
            return None

    def _decrypt(self, html: str) -> Optional[Dict]:
        try:
            for script in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
                enc = re.search(r'encData\s*=\s*"([^"]+)"', script)
                key = re.search(r'keyHex\s*=\s*"([^"]+)"', script)
                iv = re.search(r'ivHex\s*=\s*"([^"]+)"', script)
                if all([enc, key, iv]):
                    cipher = AES.new(bytes.fromhex(key.group(1)), AES.MODE_CBC, bytes.fromhex(iv.group(1)))
                    dec = unpad(cipher.decrypt(base64.b64decode(enc.group(1))), AES.block_size).decode("utf-8")
                    url_m = re.search(r'"file"\s*:\s*"(https://[^"]+)"', dec)
                    if url_m:
                        q_m = re.search(r'"label"\s*:\s*"([^"]+)"', dec)
                        return {
                            "url": url_m.group(1),
                            "quality": q_m.group(1) if q_m else "720p",
                            "type": "hls",
                            "server": "dramavideo",
                            "referer": f"{self.PLAYER_URL}/",
                            "origin": self.PLAYER_URL,
                            "qualities": self._get_qualities(url_m.group(1)),
                        }
            return None
        except Exception as e:
            print(f"[KA] Decrypt error: {e}")
            return None

    def _get_qualities(self, master_url: str) -> List[Dict]:
        quals = []
        try:
            resp = self.session.get(master_url, timeout=10, headers={"Referer": f"{self.PLAYER_URL}/", "Origin": self.PLAYER_URL})
            if resp.status_code != 200 or "#EXTM3U" not in resp.text:
                return quals
            lines = resp.text.strip().split("\n")
            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    res = re.search(r"RESOLUTION=(\d+x\d+)", line)
                    bw = re.search(r"BANDWIDTH=(\d+)", line)
                    if res and i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                        qurl = lines[i + 1].strip()
                        if not qurl.startswith("http"):
                            qurl = f"https://hls.dramavideo.se{qurl}"
                        h = int(res.group(1).split("x")[1])
                        quals.append({
                            "url": qurl,
                            "quality": "1080p" if h >= 1080 else "720p" if h >= 720 else "480p" if h >= 480 else "360p",
                            "resolution": res.group(1),
                            "bandwidth": int(bw.group(1)) // 1000 if bw else 0,
                        })
        except Exception:
            pass
        return quals

    def extract(self, id: str, season: int = None, episode: int = None, title: str = None) -> Optional[Dict]:
        if id.startswith("http"):
            url = id
        elif "/" in id and "." not in id.split("/")[0]:
            url = f"{self.BASE_URL}/{id}"
        else:
            results = self.search(title or id)
            if not results:
                return None
            url = results[0]["url"]
        result = self.extract_from_url(url, season, episode)
        if result:
            result.update({
                "imdb_id": "",
                "title": title or "",
                "file_name": f"dramavideo_{result['quality']}",
                "provider": "kissasian",
                "is_hls": True,
            })
        return result


_ext = None
def get_extractor():
    global _ext
    if _ext is None:
        _ext = KissAsianExtractor()
    return _ext

async def extract(id: str, season: int = None, episode: int = None, **kw) -> Optional[Dict]:
    return get_extractor().extract(id, season, episode, title=kw.get("title"))

async def search(q: str) -> List[Dict]:
    return get_extractor().search(q)

def format_as_source(result: Dict, subs: List = None) -> Dict:
    if not result:
        return {}
    sources = [{
        "url": result["url"],
        "quality": result["quality"],
        "type": "hls",
        "server": "dramavideo",
        "referer": result["referer"],
        "origin": result["origin"],
        "is_hls": True,
    }]
    for q in result.get("qualities", []):
        if q["url"] != result["url"]:
            sources.append({
                "url": q["url"],
                "quality": q["quality"],
                "type": "hls",
                "server": "dramavideo",
                "referer": result["referer"],
                "origin": result["origin"],
                "is_hls": True,
            })
    return {"sources": sources, "subtitles": subs or [], "provider": "kissasian"}