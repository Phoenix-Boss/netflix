mport time

CACHE_TTL = 3600
_cache = {}

def get(key):
    entry = _cache.get(key)
    if entry and time.time() - entry["time"] < CACHE_TTL:
        return entry["data"]
    if entry:
        del _cache[key]
    return None

def set(key, data):
    _cache[key] = {"data": data, "time": time.time()}

def clear():
    _cache.clear()

def stats():
    now = time.time()
    valid = sum(1 for e in _cache.values() if now - e["time"] < CACHE_TTL)
    return {"entries": valid, "ttl_seconds": CACHE_TTL, "total_keys": len(_cache)}

def clear_category(cat):
    keys_to_del = [k for k in _cache if k.startswith(f"{cat}:")]
    for k in keys_to_del:
        del _cache[k]