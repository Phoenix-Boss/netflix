 $BaseUrl = "http://localhost:8000"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       LOCAL API PIPELINE TEST (Direct Bypass Active)       " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

 $tests = @(
    @{ dbid="tt1375666"; title="Inception"; year="2010"; type="Standard Movie" },
    @{ dbid="1930"; title="The Amazing Spider-Man"; year="2012"; type="Year-Strict Movie" }
)

foreach ($test in $tests) {
    $dbid = $test.dbid
    $title = $test.title
    $year = $test.year
    $type = $test.type
    
    $url = "$BaseUrl/stream/$($dbid)?title=$([uri]::EscapeDataString($title))&year=$year"
    
    Write-Host "`n------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "TEST: $($type)" -ForegroundColor Yellow
    Write-Host "URL:  $url" -ForegroundColor DarkGray
    Write-Host "------------------------------------------------------------" -ForegroundColor DarkGray

    try {
        $response = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 30
        
        if ($response.status -eq 200 -and $response.sources.Count -gt 0) {
            $streamUrl = $response.sources[0].data.stream
            $streamTitle = $response.sources[0].data.title

            # Determine the winner based on the URL
            if ($streamUrl -match "innovationdrivenstudio|vaplayer|m3u8") {
                Write-Host "  Status   : " -NoNewline; Write-Host "200 OK" -ForegroundColor Green
                Write-Host "  Provider : " -NoNewline; Write-Host "PRIMARY (Direct Vaplayer Bypass)" -ForegroundColor Green
                Write-Host "  Title    : $streamTitle"
                Write-Host "  Stream   : $($streamUrl.Substring(0, [math]::Min(80, $streamUrl.Length)))..." -ForegroundColor DarkCyan
            }
            elseif ($streamUrl -match "fzmovies") {
                Write-Host "  Status   : " -NoNewline; Write-Host "200 OK" -ForegroundColor Yellow
                Write-Host "  Provider : " -NoNewline; Write-Host "FALLBACK (FZMovies)" -ForegroundColor Yellow
                Write-Host "  Title    : $streamTitle"
                Write-Host "  Note     : Direct API failed, fell back to FZMovies."
            }
            elseif ($streamUrl -match "magnet:") {
                Write-Host "  Status   : " -NoNewline; Write-Host "200 OK" -ForegroundColor Red
                Write-Host "  Provider : " -NoNewline; Write-Host "LAST RESORT (Torrent)" -ForegroundColor Red
            }
            else {
                Write-Host "  Status   : " -NoNewline; Write-Host "200 OK" -ForegroundColor White
                Write-Host "  Provider : " -NoNewline; Write-Host "Unknown" -ForegroundColor White
                Write-Host "  Stream   : $streamUrl"
            }
        } else {
            Write-Host "  Status   : " -NoNewline; Write-Host "404 No Streams Found" -ForegroundColor Red
        }
    }
    catch {
        if ($_.Exception.Message -match "404") {
            Write-Host "  Status   : " -NoNewline; Write-Host "404 Not Found" -ForegroundColor Red
        } else {
            Write-Host "  Error    : " -NoNewline; Write-Host $_.Exception.Message -ForegroundColor Red
        }
    }
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "                    TESTS COMPLETE                          " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan