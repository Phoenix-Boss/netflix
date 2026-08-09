# ==========================================
# CONFIGURATION
# ==========================================
 $ApiUrl = "https://netflix-tf79.onrender.com/stream/1930?title=The%20Amazing%20Spider-Man&year=2012"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " INVOKING YOUR API" -ForegroundColor Cyan
Write-Host " URL: $ApiUrl" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan

try {
    # Invoke the API (Timeout after 30 seconds)
    $response = Invoke-RestMethod -Uri $ApiUrl -Method Get -TimeoutSec 30
    
    # Pretty-print the raw JSON
    Write-Host "`n[RAW JSON RESPONSE]" -ForegroundColor Yellow
    Write-Host ($response | ConvertTo-Json -Depth 10)
    
    # ==========================================
    # QUICK ANALYSIS
    # ==========================================
    Write-Host "------------------------------------------------------------" -ForegroundColor Cyan
    
    if ($response -and $response.Count -gt 0) {
        $sourceName = $response[0].name
        $streamUrl  = $response[0].data.stream
        
        Write-Host "Source Returned : $sourceName"
        
        if ($streamUrl -match "fzmovies") {
            Write-Host "Status          : " -NoNewline; Write-Host "⚠️  FALLBACK (FZMovies was used)" -ForegroundColor Yellow
            Write-Host "Why?            : vidsrc.pm likely failed, timed out, or returned no streams."
        }
        elseif ($streamUrl -match "magnet:") {
            Write-Host "Status          : " -NoNewline; Write-Host "🧲 LAST RESORT (Torrent was used)" -ForegroundColor Red
            Write-Host "Why?            : vidsrc.pm AND all streaming fallbacks failed."
        }
        elseif ($streamUrl) {
            Write-Host "Status          : " -NoNewline; Write-Host "✅ PRIMARY (vidsrc.pm working)" -ForegroundColor Green
            Write-Host "Stream Preview  : $($streamUrl.Substring(0, [math]::Min(100, $streamUrl.Length)))..."
        }
        else {
            Write-Host "Status          : " -NoNewline; Write-Host "❌ EMPTY (No stream URL found in data)" -ForegroundColor Red
        }
    }
    else {
        Write-Host "Result: " -NoNewline; Write-Host "Empty array or unexpected format." -ForegroundColor Red
    }
}
catch {
    Write-Host "`n[REQUEST FAILED]" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor DarkRed
}