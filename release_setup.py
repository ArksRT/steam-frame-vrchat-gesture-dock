"""Install the redistributable GUI and the Steam Frame Dashboard component.

install() takes host, username, password and two UI callbacks. confirm_host_key
receives (host, algorithm, SHA256 fingerprint); progress receives a status line.
The installed executable's path is returned after SteamVR startup registration.
"""

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from dock_i18n import language_context, saved_language, tr


def _directory():
    return Path(os.environ["LOCALAPPDATA"]) / "VRChatGestureDock"


def _config(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(tr("Could not read the connection settings on this PC."))
    return data


def defaults():
    try:
        data = _config(_directory() / "connection.json")
        return data.get("host", "frame"), data.get("username", "steamos")
    except (OSError, ValueError):
        return "frame", "steamos"


def _address(host, username):
    host, username = host.strip(), username.strip()
    try:
        host = str(ipaddress.ip_address(host.removeprefix("[").removesuffix("]")))
        if "%" in host and not re.fullmatch(r"[A-Za-z0-9_.-]+", host.split("%", 1)[1]):
            raise ValueError("IPv6 interface")
    except ValueError:
        if (len(host) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                      for label in host.rstrip(".").split("."))):
            raise ValueError(tr("Enter the Steam Frame hostname or IP address.")) from None
        host = host.rstrip(".").lower()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", username):
        raise ValueError(tr("Enter a valid SteamOS username, such as steamos."))
    return host, username


def _run(command, *, env=None, check=True, timeout=25):
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                          encoding="utf-8", errors="replace", env=env, check=check,
                          timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)


def _keygen():
    folder = Path(os.environ["SYSTEMROOT"]) / "System32" / "OpenSSH"
    if not all((folder / name).is_file() for name in ("ssh.exe", "ssh-keygen.exe")):
        raise RuntimeError(tr("Install OpenSSH Client in Windows Optional Features, then run the installer again."))
    return folder / "ssh-keygen.exe"


def _atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


@contextmanager
def _setup_lock(directory):
    import msvcrt
    with (directory / "setup.lock").open("a+b") as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError(tr("Another installer is configuring Gesture Dock. Wait for it to finish.")) from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _known_keys(keygen, path, host):
    if not path.is_file():
        return [], []
    lookup = _run([keygen, "-F", host, "-f", path], check=False)
    if lookup.returncode not in (0, 1) or lookup.stderr.strip():
        raise RuntimeError(tr("Could not check the SSH trust record in {path}.", path=path))
    acceptable, revoked = [], []
    for line in lookup.stdout.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        target = acceptable
        if parts[0].startswith("@"):
            marker = parts.pop(0)
            if marker == "@cert-authority":
                raise RuntimeError(tr("This device uses SSH host certificates. The installer supports pinned host keys."))
            if marker != "@revoked":
                raise RuntimeError(tr("The SSH trust record contains an unrecognized marker."))
            target = revoked
        if len(parts) < 3:
            raise RuntimeError(tr("The SSH host key record has an invalid format."))
        target.append((parts[1], parts[2]))
    return acceptable, revoked


@contextmanager
def _connect(host, username, password, keygen, pins, confirm_host_key):
    import paramiko

    pinned, revoked = _known_keys(keygen, pins, host)
    known, system_revoked = _known_keys(keygen, Path.home() / ".ssh" / "known_hosts", host)
    trusted = pinned or known
    try:
        connection = socket.create_connection((host, 22), timeout=8)
    except OSError as exc:
        raise RuntimeError(tr("Could not connect to {host} over SSH. Check the address, SSH setting and local network.", host=host)) from exc
    with connection as sock, paramiko.Transport(sock) as remote:
        if trusted:
            algs = {algorithm for algorithm, _ in trusted}
            if "ssh-rsa" in algs:
                algs.update(("rsa-sha2-512", "rsa-sha2-256"))
            options = remote.get_security_options()
            matches = tuple(a for a in options.key_types if a in algs)
            if not matches:
                raise RuntimeError(tr("The saved SSH host key type is unsupported."))
            options.key_types = matches
        remote.banner_timeout = remote.auth_timeout = 12
        remote.start_client(timeout=12)
        key = remote.get_remote_server_key()
        identity = (key.get_name(), key.get_base64())
        if identity in revoked + system_revoked or (trusted and identity not in trusted):
            raise RuntimeError(tr("The Steam Frame SSH host key has changed or was revoked. Verify the headset before reconnecting."))
        digest = hashlib.sha256(key.asbytes()).digest()
        fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        if not trusted and not confirm_host_key(host, identity[0], fingerprint):
            raise RuntimeError(tr("Connection canceled."))
        try:
            remote.auth_password(username, password)
        except paramiko.AuthenticationException as exc:
            raise RuntimeError(tr("SSH sign-in failed. Check the SteamOS username and password.")) from exc
        if identity not in pinned:
            old = pins.read_bytes() if pins.is_file() else b""
            entry = f"{host} {identity[0]} {identity[1]}\n".encode("ascii")
            _atomic(pins, (old.rstrip(b"\r\n") + b"\n" if old else b"") + entry)
        yield remote


def _public_key(directory, keygen):
    key = directory / "frame-tunnel-key"
    if not key.exists():
        _run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "vrchat-gesture-dock", "-f", key])
    public = _run([keygen, "-y", "-P", "", "-f", key]).stdout.split()
    if len(public) < 2 or public[0] != "ssh-ed25519":
        raise RuntimeError(tr("The existing tunnel key is not a valid Ed25519 key."))
    text = f"ssh-ed25519 {public[1]} vrchat-gesture-dock\n".encode("ascii")
    _atomic(key.with_suffix(".pub"), text)
    return text


def _remote_command(remote, command, timeout=30):
    with remote.open_session(timeout=8) as channel:
        channel.settimeout(timeout)
        channel.set_combine_stderr(True)
        channel.exec_command(command)
        channel.shutdown_write()
        with channel.makefile("rb") as output:
            message = output.read(65536).decode("utf-8", "replace")
        if channel.recv_exit_status():
            raise RuntimeError(message.strip()[-2000:] or tr("The Steam Frame could not finish the operation."))
        return message


def _preflight(remote):
    script = """
case "$(uname -m)" in aarch64|arm64) ;; *) echo wrong_os; exit ;; esac
if [ -r /etc/os-release ]; then . /etc/os-release; fi
case " ${ID:-} ${ID_LIKE:-} " in
  *' steamos '*) ;;
  *) echo wrong_os; exit ;;
esac
if ! command -v python3 >/dev/null || ! python3 -c 'import aiohttp' >/dev/null 2>&1; then
  echo python_missing
elif ! systemctl --user show-environment >/dev/null 2>&1; then
  echo systemd_unavailable
else
  echo ready
fi
"""
    status = _remote_command(remote, "sh -c " + shlex.quote(script)).strip()
    messages = {
        "wrong_os": tr("Check the address. The target must be a Steam Frame running ARM64 SteamOS."),
        "python_missing": tr("The headset needs Python 3 with aiohttp. Update SteamOS or install aiohttp for its system Python."),
        "systemd_unavailable": tr("Could not reach SteamOS user services. Connect as the SteamOS user signed in on the headset."),
    }
    if status != "ready":
        raise RuntimeError(messages.get(status, tr("Could not verify the Steam Frame environment.")))


def _deploy(remote, resources, public):
    import paramiko

    stage = "/tmp/vrchat-gesture-dock." + secrets.token_hex(12)
    names = ("dashboard.js", "frame_dock.py", "install-frame.sh")
    with paramiko.SFTPClient.from_transport(remote) as sftp:
        sftp.get_channel().settimeout(18)
        sftp.mkdir(stage, mode=0o700)
        try:
            for name in names:
                data = (resources / name).read_bytes().replace(b"\r\n", b"\n")
                with sftp.open(f"{stage}/{name}", "wb") as file:
                    file.write(data)
            with sftp.open(stage + "/frame-tunnel-key.pub", "wb") as file:
                file.write(public)
            _remote_command(remote, "sh " + shlex.quote(stage + "/install-frame.sh"))
        finally:
            for name in (*names, "frame-tunnel-key.pub"):
                with suppress(OSError, EOFError, paramiko.SSHException):
                    sftp.remove(stage + "/" + name)
            with suppress(OSError, EOFError, paramiko.SSHException):
                sftp.rmdir(stage)


def _stage_executable(directory, source):
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    folder = directory / "program" / digest.hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "VRChatGestureDock.exe"
    if dest.is_file():
        if source.samefile(dest):
            return dest
        with dest.open("rb") as stream:
            verify = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                verify.update(chunk)
        if verify.digest() == digest.digest():
            return dest
    temp = dest.with_suffix(".new")
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return dest


def _powershell(script, directory, exe):
    env = dict(os.environ, VRCHAT_DOCK_INSTALL=str(directory), VRCHAT_DOCK_EXE=str(exe))
    ps = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(("$ErrorActionPreference='Stop';\n" + script).encode("utf-16-le")).decode()
    return _run([ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=env).stdout.strip()


_STOP = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$dir = $env:VRCHAT_DOCK_INSTALL
$older = Join-Path $dir 'pc_bridge.py'
$scriptArg = '(?:^|\s)"?' + [Regex]::Escape($older) + '"?(?:\s|$)'
$program = (Join-Path $dir 'program') + '\'
$workers = @(Get-CimInstance Win32_Process -Filter "Name = 'VRChatGestureDock.exe' OR Name LIKE 'pythonw%.exe'" |
  Where-Object {
    ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($program,[StringComparison]::OrdinalIgnoreCase) -and
     $_.CommandLine -match '(?:^|\s)--bridge(?:\s|$)') -or
    ($_.Name -like 'pythonw*.exe' -and $_.ExecutablePath -and $_.CommandLine -match $scriptArg)
  })
$pids = @($workers | ForEach-Object { $_.ProcessId })
$key = Join-Path $dir 'frame-tunnel-key'
$tunnels = @(Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" | Where-Object {
  $pids -contains $_.ParentProcessId -and $_.CommandLine -and $_.CommandLine.Contains($key)
})
foreach ($p in @($workers) + @($tunnels)) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($workers.Count -gt 0) {
  $first = $workers[0]
  @{executable=$first.ExecutablePath; script=($first.Name -like 'pythonw*.exe')} | ConvertTo-Json -Compress
}
"""

_READ_LEGACY = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$path = Join-Path ([Environment]::GetFolderPath('Startup')) 'VRChat Gesture Dock.lnk'
if (Test-Path -LiteralPath $path -PathType Leaf) {
  $link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
  @{path=$path; target=$link.TargetPath; arguments=$link.Arguments} | ConvertTo-Json -Compress
}
"""


def _owns_shortcut(directory, target, arguments):
    target = Path(target)
    if re.fullmatch(r"pythonw[\d.]*\.exe", target.name, re.I):
        return arguments in (str(directory / "pc_bridge.py"), '"' + str(directory / "pc_bridge.py") + '"')
    return (target.name.lower() == "vrchatgesturedock.exe"
            and target.parent.parent == directory / "program"
            and re.fullmatch(r"[0-9a-f]{16}", target.parent.name, re.I) is not None
            and arguments == "--bridge")


def _remove_legacy(directory, exe):
    raw = _powershell(_READ_LEGACY, directory, exe)
    if raw:
        link = json.loads(raw)
        if _owns_shortcut(directory, link["target"], link["arguments"]):
            Path(link["path"]).unlink(missing_ok=True)


def _start(directory, exe, arguments):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return subprocess.Popen([exe, *arguments], cwd=directory, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)


def _wait_for_bridge(remote, instance):
    import paramiko
    from pc_bridge import find_vrserver_pid

    probe = ("import http.client,json; h=http.client.HTTPConnection('127.0.0.1',27656,timeout=2);"
             "h.request('GET','/health'); print(json.load(h.getresponse())['instance_id'])")
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if not find_vrserver_pid():
            return False
        try:
            if _remote_command(remote, "python3 -c " + shlex.quote(probe), timeout=5).strip() == instance:
                return True
        except (OSError, RuntimeError, paramiko.SSHException):
            pass
        time.sleep(1)
    raise RuntimeError(tr("The headset could not reach the service on this PC. Check SSH port forwarding and make sure port 27656 on the headset is free."))


def configure_startup(directory, executable, arguments, *, updated_files, verify=None):
    """Migrate both the packaged app and the original Python installer."""
    import steamvr_lifecycle
    from pc_bridge import find_vrserver_pid

    originals = {path: path.read_bytes() if path.is_file() else None for path in updated_files}
    previous_worker = _powershell(_STOP, directory, executable)
    undo = None
    try:
        for path, content in updated_files.items():
            _atomic(path, content)
        undo = steamvr_lifecycle.register(directory, executable, arguments)
        if not steamvr_lifecycle.startup_enabled():
            mode = "disabled"
        elif not find_vrserver_pid():
            mode = "waiting"
        else:
            _start(directory, executable, arguments)
            mode = "running" if verify is None or verify() else "waiting"
        _remove_legacy(directory, executable)
        return mode
    except Exception:
        _powershell(_STOP, directory, executable)
        try:
            if undo is not None:
                undo()
        finally:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic(path, original)
            if previous_worker:
                worker = json.loads(previous_worker)
                args = [str(directory / "pc_bridge.py")] if worker["script"] else ["--bridge"]
                _start(directory, Path(worker["executable"]), args)
        raise


def install(host, username, password, confirm_host_key, progress, *, language=None):
    # Source callers from the Chinese-only releases do not pass a language.
    selected = language if language is not None else (saved_language() or "zh-CN")
    with language_context(selected):
        return _install(host, username, password, confirm_host_key, progress, selected)


def _install(host, username, password, confirm_host_key, progress, language):
    import paramiko

    host, username = _address(host, username)
    if not password:
        raise ValueError(tr("Enter the SteamOS account password."))
    keygen = _keygen()
    if not getattr(sys, "frozen", False):
        raise RuntimeError(tr("Run the packaged Windows installer."))
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    settings = directory / "connection.json"
    with _setup_lock(directory):
        progress(tr("Checking the headset identity and signing in to Steam Frame…"))
        with _connect(host, username, password, keygen, directory / "frame-known-hosts", confirm_host_key) as remote:
            progress(tr("Installing the Dashboard button on the headset…"))
            _preflight(remote)
            old = _config(settings)
            exe = _stage_executable(directory, Path(sys.executable))
            _deploy(remote, Path(sys._MEIPASS), _public_key(directory, keygen))
            instance = old.get("instance_id") or secrets.token_hex(16)
            new = dict(host=host, username=username, instance_id=instance, language=language)
            progress(tr("Registering the SteamVR startup app…"))
            mode = configure_startup(directory, exe, ["--bridge"],
                                     updated_files={settings: json.dumps(new, ensure_ascii=False, indent=2).encode("utf-8")},
                                     verify=lambda: _wait_for_bridge(remote, instance))
            if mode == "disabled":
                progress(tr("Installation complete. Startup remains off. To enable it, open SteamVR Settings → Startup / Shutdown → Choose Startup Overlay Apps and turn on VRChat Gesture Dock."))
                return exe
            if mode == "waiting":
                progress(tr("Installation complete. The gesture service will connect to your headset the next time SteamVR starts."))
                return exe
            try:
                status = _remote_command(remote, 'python3 "$HOME/.local/share/vrchat-gesture-dock/frame_dock.py" --status',
                                         timeout=6).strip()
            except (OSError, RuntimeError, paramiko.SSHException, EOFError):
                status = "waiting"
            if status == "mounted":
                progress(tr("Installation complete. Click Gestures in the headset Dashboard to switch modes while streaming VRChat."))
            else:
                progress(tr("Installation complete. The headset is connected to this PC. Enable CEF Remote Debugging and open the Dashboard to see the Gestures button."))
            return exe


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--script-startup":
    folder = Path(sys.argv[2]).resolve()
    scripts = ("pc_bridge.py", "pc_binding_api.py", "gesture_bindings.py", "steamvr_lifecycle.py", "dock_i18n.py")
    with _setup_lock(folder):
        updated = {folder / name: Path(__file__).with_name(name).read_bytes() for name in scripts}
        print(configure_startup(folder, Path(sys.argv[3]), [str(folder / "pc_bridge.py")], updated_files=updated))
