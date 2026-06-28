# Khôi phục file gốc từ backup mới nhất (.bk3 -> .bk2 -> .bk1).
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
$source = Resolve-Path -LiteralPath $Path
$candidates = @(
    "$source.bk3",
    "$source.bk2",
    "$source.bk1"
)

$backup = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
        $backup = $candidate
        break
    }
}

if (-not $backup) {
    Write-Error "Không tìm thấy backup (.bk3 / .bk2 / .bk1) cho: $source"
}

Copy-Item -LiteralPath $backup -Destination $source -Force
Write-Host "Restored $source <= $backup"
