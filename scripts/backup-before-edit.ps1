# Xoay vòng backup local: .bk1 (cũ nhất) .. .bk3 (mới nhất) — chạy TRƯỚC khi sửa file.
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
$source = Resolve-Path -LiteralPath $Path
$bk1 = "$source.bk1"
$bk2 = "$source.bk2"
$bk3 = "$source.bk3"

if (Test-Path -LiteralPath $bk3) {
    if (Test-Path -LiteralPath $bk1) {
        Remove-Item -LiteralPath $bk1 -Force
    }
    if (Test-Path -LiteralPath $bk2) {
        Move-Item -LiteralPath $bk2 -Destination $bk1 -Force
    }
    Move-Item -LiteralPath $bk3 -Destination $bk2 -Force
    Copy-Item -LiteralPath $source -Destination $bk3 -Force
    Write-Host "Rotated -> $bk3"
}
elseif (Test-Path -LiteralPath $bk2) {
    Copy-Item -LiteralPath $source -Destination $bk3 -Force
    Write-Host "Created $bk3"
}
elseif (Test-Path -LiteralPath $bk1) {
    Copy-Item -LiteralPath $source -Destination $bk2 -Force
    Write-Host "Created $bk2"
}
else {
    Copy-Item -LiteralPath $source -Destination $bk1 -Force
    Write-Host "Created $bk1"
}
