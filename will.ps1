cd C:\Users\Boss\Desktop\EDGES\vidsrc-api

if (Test-Path "streamer") {
    Remove-Item "streamer" -Recurse -Force
}

 $f = "models\torrents.py"
 $c = [System.IO.File]::ReadAllText($f, [System.Text.UTF8Encoding]::new($false))

 $find = '"stream": "", "subtitle": subs or [], "quality": td.get'
 $repl = '"stream": get_webtor_stream(td.get("magnet", "")), "subtitle": subs or [], "quality": td.get'

 $c = $c.Replace($find, $repl)

[System.IO.File]::WriteAllText($f, $c, [System.Text.UTF8Encoding]::new($false))

git add -A
git commit -m "Fix Webtor injection"
git push origin main