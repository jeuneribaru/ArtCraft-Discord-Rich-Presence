# Build a standalone ArtCraftRPC.exe (single file, no console window)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "py" }

Write-Host "Checking PyInstaller..." -ForegroundColor Cyan
& $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install PyInstaller."
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path ".\icon.ico")) {
    Write-Host "Generating default icon.ico..." -ForegroundColor Cyan
    & $python -c "from artcraft_rpc import create_default_tray_icon; img = create_default_tray_icon(); img.save('icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64)])"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to generate icon.ico."
        exit $LASTEXITCODE
    }
}

Write-Host "Building dist\ArtCraftRPC.exe..." -ForegroundColor Cyan
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name ArtCraftRPC `
    --icon icon.ico `
    --add-data "config.json;." `
    --add-data "apps.json;." `
    --add-data "icon.ico;." `
    main.pyw

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit $LASTEXITCODE
}

Remove-Item -Path ".\build", ".\ArtCraftRPC.spec" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`nDone! Executable ready at: $PSScriptRoot\dist\ArtCraftRPC.exe" -ForegroundColor Green
