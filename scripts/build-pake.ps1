param(
    [string]$Url = "http://127.0.0.1:5050",
    [string]$Name = "Model Hub",
    [string]$Icon = "",
    [int]$Width = 1200,
    [int]$Height = 800,
    [switch]$Fullscreen,
    [switch]$Install,
    [string]$AppVersion = "2.0.0"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Model Hub Pake Build ===" -ForegroundColor Cyan
Write-Host "Target : $Url" -ForegroundColor Gray
Write-Host "Name   : $Name" -ForegroundColor Gray
Write-Host "Version: $AppVersion" -ForegroundColor Gray
Write-Host ""

# Check Node.js
$nodeVer = node --version 2>$null
if (-not $nodeVer) {
    Write-Error "Node.js >=18 required. Install from https://nodejs.org/"
    exit 1
}
Write-Host "Node.js: $nodeVer" -ForegroundColor Green

# Check Rust (needed by Pake/Tauri)
$rustc = rustc --version 2>$null
if (-not $rustc) {
    Write-Host "Rust not detected. Pake will auto-install it." -ForegroundColor Yellow
} else {
    Write-Host "Rust   : $rustc" -ForegroundColor Green
}

# Check MSVC link.exe (required for Windows MSVC target)
$linkExe = Get-Command link.exe -ErrorAction SilentlyContinue
if (-not $linkExe) {
    Write-Host ""
    Write-Host "WARNING: MSVC linker (link.exe) not found." -ForegroundColor Red
    Write-Host "Pake requires Visual Studio Build Tools with C++ workload on Windows." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Options:" -ForegroundColor Cyan
    Write-Host "  1. Install via winget (run as Admin):" -ForegroundColor White
    Write-Host "     winget install Microsoft.VisualStudio.2022.BuildTools" -ForegroundColor Gray
    Write-Host '     & "C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe" --passive --wait --add Microsoft.VisualStudio.Workload.VCTools;includeRecommended' -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Use GitHub Actions CI (recommended):" -ForegroundColor White
    Write-Host "     Push a tag, and the workflow in .github/workflows/pake.yml" -ForegroundColor Gray
    Write-Host "     will build on GitHub's Windows runners (MSVC pre-installed)." -ForegroundColor Gray
    Write-Host ""
    $answer = Read-Host "Continue anyway? (y/N)"
    if ($answer -ne "y") {
        exit 1
    }
}

# Set Rust default to MSVC target if Rust is installed and MSVC is available
if ($linkExe) {
    $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
    $currentTarget = rustup default 2>$null
    if ($currentTarget -and -not $currentTarget.Contains("msvc")) {
        Write-Host "Switching Rust to MSVC toolchain..." -ForegroundColor Yellow
        rustup default stable-x86_64-pc-windows-msvc 2>&1
    }
}

# Install pake-cli if not present
$pakeInstalled = Get-Command pake -ErrorAction SilentlyContinue
if (-not $pakeInstalled) {
    Write-Host "Installing pake-cli..." -ForegroundColor Yellow
    npm install -g pake-cli 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install pake-cli"
        exit 1
    }
    Write-Host "pake-cli installed." -ForegroundColor Green
}

# Build the command
$argsList = @($Url, "--name", $Name, "--width", $Width, "--height", $Height, "--app-version", $AppVersion)
if ($Icon) { $argsList += @("--icon", $Icon) }
if ($Fullscreen) { $argsList += "--fullscreen" }
if ($Install) { $argsList += "--install" }

Write-Host ""
Write-Host "Running: pake $($argsList -join ' ')" -ForegroundColor Yellow
Write-Host ""

& pake $argsList 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=== Build Complete ===" -ForegroundColor Green
    Write-Host "Output is in the current directory:" -ForegroundColor Green
    Get-ChildItem -Path "$Name*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  $($_.FullName)" -ForegroundColor Cyan
    }
} else {
    Write-Error "Build failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
