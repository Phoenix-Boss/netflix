import os
import re, base64, asyncio, json
from typing import Optional, Dict, List
from curl_cffi import requests as cr
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from urllib.parse import urlparse, urljoin, parse_qs

_CACHED_MIRROR = None

class DramacoolExtractor:
    KNOWN = ["https://asianc.online", "https://asianc.tv", "https://asianc.to"]
    FALLBACK = "https://asianc.online"

    def __init__(self):
        global _CACHED_MIRROR
        self.s = cr.Session(impersonate="chrome131", proxy=os.getenv("PROXY_URL"))
        self.s.timeout = 20
        if _CACHED_MIRROR:
            self.B = _CACHED_MIRROR
            return
        self.B = self._mirror()
        _CACHED_MIRROR = self.B

    def _ok(self, url):
        try:
            r = cr.Session(impersonate="chrome131", proxy=os.getenv("PROXY_URL")).get(url, timeout=8)
            return r.status_code == 200 and "Just a moment" not in r.text and "drama-detail" in r.text
        except:
            return False

    def _mirror(self):
        for u in self.KNOWN:
            if self._ok(u):
                return u
        return self.FALLBACK

    def _search_on(self, mirror, title):
        r = self.s.get(f"{mirror}/search?keyword={title.replace(' ', '+')}&type=movies", timeout=10)
        if r.status_code != 200 or len(r.text) < 1000:
            return None
        res = []
        seen = set()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='/drama-detail/']"):
            h = a.get("href", "")
            h3 = a.find("h3", class_="title")
            nm = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
            if not nm or not h:
                continue
            slug = h.replace("/drama-detail/", "").strip("/")
            if slug not in seen:
                seen.add(slug)
                res.append({"slug": slug, "name": nm, "url": f"{mirror}{h}"})
        if not res:
            for a in soup.select("a.img"):
                h = a.get("href", "")
                h3 = a.find("h3", class_="title")
                if not h3 or not h:
                    continue
                slug = h.replace("/drama-detail/", "").strip("/") if "/drama-detail/" in h else re.sub(r'-episode-\d+\.html$', '', h).strip("/")
                if slug and slug not in seen:
                    seen.add(slug)
                    res.append({"slug": slug, "name": h3.get_text(strip=True), "url": f"{mirror}{h}"})
        tl = title.lower().strip()
        res.sort(key=lambda x: (
            0 if x["name"].lower() == tl else 1 if x["name"].lower().startswith(tl) else 2 if tl in x["name"].lower() else 3,
            len(x["name"])
        ))
        return res if res else None

    def search(self, title) -> List[Dict]:
        for m in self.KNOWN:
            try:
                if m != self.B and not self._ok(m):
                    continue
                res = self._search_on(m, title)
                if res:
                    self.B = m
                    _CACHED_MIRROR = m
                    return res
            except:
                pass
        return []

    def _do_extract(self, slug, season, episode) -> Optional[Dict]:
        for m in self.KNOWN:
            try:
                if m != self.B and not self._ok(m):
                    continue
                result = self._try_extract(m, slug, episode)
                if result:
                    self.B = m
                    _CACHED_MIRROR = m
                    return result
            except:
                pass
        return None

    def _try_extract(self, base, slug, episode) -> Optional[Dict]:
        ep = f"{base}/{slug}-episode-{episode}.html"
        r = self.s.get(ep, timeout=15)
        if "Recently Drama" in r.text and not r.text.count("iframe"):
            dr = self.s.get(f"{base}/drama-detail/{slug}", timeout=15)
            soup = BeautifulSoup(dr.text, "html.parser")
            for a in soup.select("a[href*='episode-']"):
                h = a.get("href", "")
                if f"episode-{episode}" in h:
                    ep = f"{base}{h}" if h.startswith("/") else h
                    r = self.s.get(ep, timeout=15)
                    break
        if r.status_code != 200 or len(r.text) < 1000:
            return None
        if "Just a moment" in r.text:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        h1 = soup.find("h1")
        raw_title = h1.get_text(strip=True) if h1 else (soup.find("meta", property="og:title") or {}).get("content", "")
        title = self._clean_title(raw_title) if raw_title else ""
        ifr = soup.find("iframe", src=True)
        iu = ifr["src"] if ifr else None
        if not iu:
            for sc in soup.find_all("script"):
                m = re.search(r'(https?://[^"\'\s]+/iframe/[^"\'\s]+)', sc.string or "")
                if m:
                    iu = m.group(1)
                    break
        if not iu:
            return None
        if iu.startswith("//"):
            iu = "https:" + iu
        result = self._player(iu)
        if result:
            result["title"] = title
        return result

    @staticmethod
    def _clean_title(raw):
        if not raw:
            return ""
        t = raw.strip()
        for sep in [" | ", " - "]:
            if sep in t:
                t = t.split(sep)[0].strip()
                break
        t = re.sub(r'\s+Episode\s+\d+\s*$', '', t, flags=re.IGNORECASE)
        t = re.sub(r'\s*\(\d{4}\)\s*', ' ', t)
        return re.sub(r'\s{2,}', ' ', t).strip(' .-')

    def _player(self, iframe_url) -> Optional[Dict]:
        iframe_base = re.match(r'(https?://[^/]+)', iframe_url)
        iframe_base = iframe_base.group(1) if iframe_base else ""
        r = self.s.get(iframe_url, timeout=12)
        txt = r.text
        servers = []
        soup = BeautifulSoup(txt, "html.parser")
        for li in soup.select("li.linkserver"):
            dv = li.get("data-video", "")
            if not dv:
                continue
            if dv.startswith("/"):
                dv = urljoin(iframe_base, dv)
            servers.append(dv)
        for url in servers:
            if self._is_3rdplayer(url):
                result = self._probe_3rdplayer(url)
                if result:
                    return result
        m3 = self._find_m3u8(txt)
        if m3:
            return {"url": m3, "quality": "auto", "subtitles": []}
        return None

    @staticmethod
    def _is_3rdplayer(url):
        if "3rdplayer" in url.lower():
            return True
        try:
            return (urlparse(url).hostname or "") == "vidb.top"
        except:
            return False

    def _probe_3rdplayer(self, url) -> Optional[Dict]:
        r = self.s.get(url, timeout=12)
        txt = r.text
        soup = BeautifulSoup(txt, "html.parser")
        crypto_tag = soup.find("script", attrs={"data-name": "crypto"})
        if not crypto_tag:
            return None
        crypto_data = crypto_tag.get("data-value", "")
        if not crypto_data:
            return None
        all_js = "\n".join(sc.string or "" for sc in soup.find_all("script") if sc.string)
        key, iv = self._extract_keys(all_js)
        if not key or not iv:
            return None
        decrypted = self._aes_decrypt(crypto_data, key, iv)
        if not decrypted:
            return None
        video_url = None
        if decrypted.startswith("http"):
            video_url = decrypted
        else:
            try:
                j = json.loads(decrypted)
                if isinstance(j, dict):
                    for k in ["file", "src", "url", "source", "sources"]:
                        if k in j:
                            v = j[k]
                            if isinstance(v, str) and v.startswith("http"):
                                video_url = v
                                break
                            if isinstance(v, list):
                                for item in v:
                                    if isinstance(item, dict) and "file" in item and item["file"].startswith("http"):
                                        video_url = item["file"]
                                        break
            except:
                pass
        if not video_url:
            return None
        subs = []
        params = parse_qs(urlparse(url).query)
        if "sub" in params:
            sub_dec = self._aes_decrypt(params["sub"][0], key, iv)
            if sub_dec and sub_dec.startswith("http"):
                subs.append({"url": sub_dec, "lang": "en", "label": "English"})
        return {"url": video_url, "quality": "auto", "subtitles": subs}

    @staticmethod
    def _extract_keys(js_text):
        key = None
        iv = None
        for parts in re.findall(r"['\"](\d+)['\"]\s*\+\s*['\"](\d+)['\"]\s*\+\s*['\"](\d+)['\"]\s*\+\s*['\"](\d+)['\"]", js_text):
            combined = "".join(parts)
            if len(combined) == 32 and combined.isdigit():
                key = combined
                break
        for parts in re.findall(r"['\"](\d+)['\"]\s*\+\s*['\"](\d+)['\"]", js_text):
            combined = "".join(parts)
            if len(combined) == 16 and combined.isdigit() and combined != key:
                iv = combined
                break
        return key, iv

    @staticmethod
    def _aes_decrypt(data_b64, key_str, iv_str):
        try:
            key = key_str.encode("utf-8")
            iv = iv_str.encode("utf-8")
            encrypted = base64.b64decode(data_b64)
            if len(encrypted) % 16 != 0:
                return None
            cipher = AES.new(key, AES.MODE_CBC, iv)
            return unpad(cipher.decrypt(encrypted), 16).decode("utf-8")
        except:
            return None

    @staticmethod
    def _find_m3u8(txt):
        m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', txt)
        if m:
            return m.group(1)
        for b64 in re.findall(r'["\']([A-Za-z0-9+/=]{30,})["\']', txt):
            try:
                d = base64.b64decode(b64).decode("utf-8", errors="ignore")
                m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', d)
                if m:
                    return m.group(1)
            except:
                pass
        return None


async def search(q: str) -> List[Dict]:
    def _run():
        return DramacoolExtractor().search(q)
    return await asyncio.to_thread(_run)


async def extract(dbid, s, e, title=None):
    def _run():
        try:
            ex = DramacoolExtractor()
            raw = str(dbid).replace("+", " ")
            slug = raw
            matched_title = None
            need_search = title or " " in raw or "/" not in raw or (not raw.startswith("tt") and "." not in raw.split("/")[0])
            if need_search:
                t = title or raw
                res = ex.search(t)
                if res:
                    slug = res[0]["slug"]
                    matched_title = DramacoolExtractor._clean_title(res[0]["name"])
                else:
                    return None
            result = ex._do_extract(slug, s, e)
            if result and not result.get("title") and matched_title:
                result["title"] = matched_title
            return result
        except Exception as err:
            print(f"[DC] Extract error: {err}")
            return None
    return await asyncio.to_thread(_run)


def format_as_source(data, subs=None):
    if not data or "url" not in data:
        return None
    source = {
        "url": data["url"],
        "quality": data.get("quality", "auto"),
        "type": "hls",
        "server": "dramacool",
        "is_hls": True
    }
    merged = []
    seen = set()
    if data.get("subtitles"):
        for sub in data["subtitles"]:
            u = sub.get("url", "")
            if u and u not in seen:
                merged.append(sub)
                seen.add(u)
    if subs:
        for sub in subs:
            u = sub.get("url") or sub.get("src", "")
            if u and u not in seen:
                merged.append(sub)
                seen.add(u)
    return {"sources": [source], "subtitles": merged, "provider": "dramacool"}
