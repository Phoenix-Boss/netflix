import json
import asyncio
import os

async def get_fallback_stream(title: str, requested_quality: str = None):
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'test.js')
    
    if not os.path.exists(script_path):
        print("[fzmovies] Error: test.js not found in root directory.")
        return None
        
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", script_path, title,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        
        if proc.returncode != 0 or not stdout:
            return None
            
        results = json.loads(stdout.decode('utf-8'))
        if not results:
            return None
            
        def get_q_score(file_name):
            fn = file_name.lower()
            if "2160p" in fn or "4k" in fn: return 0
            if "1080p" in fn: return 1
            if "720p" in fn: return 2
            if "webrip" in fn or "bluray" in fn: return 2.5
            if "480p" in fn: return 3
            if "camrip" in fn or "hdcam" in fn: return 4
            return 99
            
        results.sort(key=lambda x: get_q_score(x['file']))
        
        selected = None
        needs_transcode = False
        actual_quality = "auto"
        
        if requested_quality:
            req_q = requested_quality.lower().replace("p", "")
            for res in results:
                if req_q in res['file'].lower():
                    selected = res
                    actual_quality = requested_quality
                    break
            
            if not selected and results:
                selected = results[0]
                needs_transcode = True
                for q in ["2160p", "4k", "1080p", "720p", "480p"]:
                    if q in selected['file'].lower():
                        actual_quality = q
                        break
        else:
            selected = results[0]
            
        if not selected or not selected.get('downloads'):
            return None
            
        return {
            "stream": selected['downloads'][0],
            "quality": actual_quality,
            "needs_transcode": needs_transcode,
            "title": selected['file'],
            "size": selected['size']
        }
        
    except asyncio.TimeoutError:
        print("[fzmovies] Timeout reached (90s)")
        return None
    except Exception as e:
        print(f"[fzmovies] Parsing error: {e}")
        return None
