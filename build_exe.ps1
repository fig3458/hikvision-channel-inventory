# Build slim onefile exe. Optional: place upx.exe next to this script or on PATH.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Cleaning old build..."
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
Remove-Item -Force HikChannelInventory.spec.bak -ErrorAction SilentlyContinue

$upxArgs = @()
$upx = Get-Command upx -ErrorAction SilentlyContinue
if (-not $upx -and (Test-Path "$PSScriptRoot\upx.exe")) {
    $env:PATH = "$PSScriptRoot;$env:PATH"
    $upx = Get-Command upx -ErrorAction SilentlyContinue
}
if ($upx) {
    Write-Host "==> UPX found: $($upx.Source)"
} else {
    Write-Host "==> UPX not found (optional). Continuing without it."
}

Write-Host "==> PyInstaller..."
python -m PyInstaller --noconfirm --clean hik_inventory.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exe = Join-Path $PSScriptRoot "dist\HikChannelInventory.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Build failed: $exe missing"
}
$sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 2)
Write-Host "==> OK: $exe ($sizeMB MB)"
