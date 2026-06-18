$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
    $PythonPrefixArgs = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
    $PythonPrefixArgs = @("-3")
} else {
    throw "Python was not found."
}

function Invoke-Python {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $Arguments
    )

    & $PythonExe @PythonPrefixArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Arguments -join ' ')"
    }
}

$BuildPackages = Join-Path $Root ".build-packages"
$CachedPackages = Join-Path $Root ".build-venv\Lib\site-packages"

function Copy-CachedPackage {
    param([string] $Pattern)

    Get-ChildItem -Path $CachedPackages -Filter $Pattern -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName -Destination $BuildPackages -Recurse -Force
    }
}

if (Test-Path $BuildPackages) {
    Remove-Item $BuildPackages -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildPackages | Out-Null

if (Test-Path (Join-Path $CachedPackages "PyInstaller")) {
    @(
        "PyInstaller",
        "pyinstaller-*.dist-info",
        "_pyinstaller_hooks_contrib",
        "pyinstaller_hooks_contrib-*.dist-info",
        "altgraph*",
        "pefile*",
        "ordlookup",
        "win32ctypes",
        "pywin32_ctypes*",
        "requests",
        "requests-*.dist-info",
        "urllib3",
        "urllib3-*.dist-info",
        "certifi",
        "certifi-*.dist-info",
        "charset_normalizer",
        "charset_normalizer-*.dist-info",
        "idna",
        "idna-*.dist-info",
        "dotenv",
        "python_dotenv-*.dist-info",
        "packaging",
        "packaging-*.dist-info"
    ) | ForEach-Object {
        Copy-CachedPackage $_
    }
} else {
    Invoke-Python -m pip install --upgrade --target $BuildPackages -r "requirements.txt"
    Invoke-Python -m pip install --upgrade --target $BuildPackages -r "requirements-build.txt"
}

$PythonBase = (& $PythonExe @PythonPrefixArgs -c "import sys; print(sys.base_prefix)")
if ($LASTEXITCODE -ne 0) {
    throw "Could not detect Python base path."
}

$TclDir = Join-Path $PythonBase "tcl"
if (-not (Test-Path $TclDir)) {
    throw "Tcl/Tk folder was not found: $TclDir"
}

$env:PYTHONPATH = $BuildPackages

Invoke-Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "WoW_Raid_Candidate_Analyzer" `
    --specpath "build" `
    --distpath "dist" `
    --workpath "build\pyinstaller" `
    --paths $BuildPackages `
    --add-data "$TclDir;tcl" `
    --hidden-import tkinter `
    --exclude-module cryptography `
    --exclude-module OpenSSL `
    --exclude-module numpy `
    --exclude-module IPython `
    --exclude-module h2 `
    --exclude-module socks `
    "wow_raid_candidate_gui.py"

$DistDir = Join-Path $Root "dist"
$EnvExample = Join-Path $Root ".env.example"
if (Test-Path $EnvExample) {
    Copy-Item $EnvExample (Join-Path $DistDir ".env.example") -Force
}

Write-Host ""
Write-Host "EXE is ready:"
Write-Host (Join-Path $DistDir "WoW_Raid_Candidate_Analyzer.exe")
