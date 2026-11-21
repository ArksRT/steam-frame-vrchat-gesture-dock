$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:LOCALAPPDATA 'VRChatGestureDock'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw "Missing pythonw.exe" }
& $python -c "import sys; assert sys.version_info >= (3, 9), 'Python 3.9+ required'"
if ($LASTEXITCODE -ne 0) { throw "Python validation failed" }
foreach ($name in @('pc_bridge.py', 'pc_binding_api.py', 'gesture_bindings.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $installDir
}
$key = Join-Path $installDir 'frame-tunnel-key'
if (-not (Test-Path -LiteralPath $key)) {
    & $python -c "import subprocess,sys; subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','vrchat-gesture-dock','-f',sys.argv[1]],check=True)" $key
    if ($LASTEXITCODE -ne 0) { throw "Could not create tunnel key" }
}
$entry = Join-Path $installDir 'pc_bridge.py'
$startup = [Environment]::GetFolderPath('Startup')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup 'VRChat Gesture Dock.lnk'))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $entry + '"'
$shortcut.WorkingDirectory = $installDir
$shortcut.Save()
$old = Get-CimInstance Win32_Process -Filter "Name LIKE 'pythonw%.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"' + $entry + '"') }
foreach ($instance in $old) {
    $tunnels = Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe' AND ParentProcessId = $($instance.ProcessId)" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($key) }
    Stop-Process -Id $instance.ProcessId -Force
    foreach ($tunnel in $tunnels) { Stop-Process -Id $tunnel.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Process -FilePath $pythonw -ArgumentList ('"' + $entry + '"') -WorkingDirectory $installDir -WindowStyle Hidden
Write-Output "PC bridge installed at $installDir"
