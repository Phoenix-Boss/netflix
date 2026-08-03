# fix_corruption.ps1
# Repairs the "missing leading character" corruption found across the repo.
# Only touches lines that match a known broken keyword pattern - safe/targeted.

$patterns = @(
    @{ Broken = '^mport\b';  Fix = 'import' }
    @{ Broken = '^rom\b';    Fix = 'from' }
    @{ Broken = '^lass\b';   Fix = 'class' }
    @{ Broken = '^ef\b';     Fix = 'def' }
    @{ Broken = '^sync def'; Fix = 'async def' }
    @{ Broken = '^elf';      Fix = 'self' }
)

$files = Get-ChildItem -Recurse -Include *.py -Exclude "fix_corruption.ps1" |
         Where-Object { $_.FullName -notmatch '\\__pycache__\\' }

$totalFixed = 0

foreach ($file in $files) {
    $lines = Get-Content $file.FullName
    $changed = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        foreach ($p in $patterns) {
            if ($lines[$i] -match $p.Broken) {
                $old = $lines[$i]
                $lines[$i] = $lines[$i] -replace $p.Broken, $p.Fix
                Write-Host "FIXED $($file.FullName):$($i+1)  '$old'  ->  '$($lines[$i])'"
                $changed = $true
                $totalFixed++
            }
        }
    }

    if ($changed) {
        # Write back as UTF-8 without BOM to avoid reintroducing encoding issues
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($file.FullName, $lines, $utf8NoBom)
    }
}

Write-Host ""
Write-Host "Done. Total lines fixed: $totalFixed"
Write-Host "Review with: git diff"