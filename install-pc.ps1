$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:LOCALAPPDATA 'VRChatGestureDock'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw "Missing pythonw.exe" }
& $python -c "import sys; assert sys.version_info >= (3, 9), 'Python 3.9+ required'"
if ($LASTEXITCODE -ne 0) { throw "Python validation failed" }
$key = Join-Path $installDir 'frame-tunnel-key'
if (-not (Test-Path -LiteralPath $key)) {
    & $python -c "import subprocess,sys; subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','vrchat-gesture-dock','-f',sys.argv[1]],check=True)" $key
    if ($LASTEXITCODE -ne 0) { throw "Could not create tunnel key" }
}
& $python (Join-Path $PSScriptRoot 'release_setup.py') --script-startup $installDir $pythonw
if ($LASTEXITCODE -ne 0) { throw "SteamVR startup registration failed" }
Write-Output "SteamVR Gesture Dock registered at $installDir"
