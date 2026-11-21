"""Install the redistributable GUI and the Steam Frame Dashboard component.

install() takes host, username, password and two UI callbacks. confirm_host_key
receives (host, algorithm, SHA256 fingerprint); progress receives a status line.
The installed executable's path is returned after the remote tunnel is verified.
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

import paramiko


def _directory():
    return Path(os.environ["LOCALAPPDATA"]) / "VRChatGestureDock"


def _config(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("无法读取电脑上的连接配置。")
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
            raise ValueError("请输入 Steam Frame 的主机名或 IP 地址。") from None
        host = host.rstrip(".").lower()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", username):
        raise ValueError("请输入有效的 SteamOS 用户名，例如 steamos。")
    return host, username


def _run(command, *, env=None, check=True, timeout=25):
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                          encoding="utf-8", errors="replace", env=env, check=check,
                          timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)


def _keygen():
    folder = Path(os.environ["SYSTEMROOT"]) / "System32" / "OpenSSH"
    if not all((folder / name).is_file() for name in ("ssh.exe", "ssh-keygen.exe")):
        raise RuntimeError("请在 Windows 的「可选功能」中安装 OpenSSH 客户端，再运行安装程序。")
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
            raise RuntimeError("另一个安装窗口正在配置快捷按钮。请等待它完成。") from exc
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
        raise RuntimeError(f"无法核对 SSH 信任记录：{path}")
    acceptable, revoked = [], []
    for line in lookup.stdout.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        target = acceptable
        if parts[0].startswith("@"):
            marker = parts.pop(0)
            if marker == "@cert-authority":
                raise RuntimeError("此设备配置了 SSH 主机证书验证。安装器目前支持固定主机公钥。")
            if marker != "@revoked":
                raise RuntimeError("SSH 信任记录含有无法识别的标记。")
            target = revoked
        if len(parts) < 3:
            raise RuntimeError("SSH 主机公钥记录格式异常。")
        target.append((parts[1], parts[2]))
    return acceptable, revoked


@contextmanager
def _connect(host, username, password, keygen, pins, confirm_host_key):
    pinned, revoked = _known_keys(keygen, pins, host)
    known, system_revoked = _known_keys(keygen, Path.home() / ".ssh" / "known_hosts", host)
    trusted = pinned or known
    with socket.create_connection((host, 22), timeout=8) as sock, paramiko.Transport(sock) as remote:
        if trusted:
            algs = {algorithm for algorithm, _ in trusted}
            if "ssh-rsa" in algs:
                algs.update(("rsa-sha2-512", "rsa-sha2-256"))
            options = remote.get_security_options()
            matches = tuple(a for a in options.key_types if a in algs)
            if not matches:
                raise RuntimeError("保存的主机公钥类型不受支持。")
            options.key_types = matches
        remote.banner_timeout = remote.auth_timeout = 12
        remote.start_client(timeout=12)
        key = remote.get_remote_server_key()
        identity = (key.get_name(), key.get_base64())
        if identity in revoked + system_revoked or (trusted and identity not in trusted):
            raise RuntimeError("Steam Frame 的 SSH 主机公钥与信任记录不符或已被撤销。请先核对设备。")
        digest = hashlib.sha256(key.asbytes()).digest()
        fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        if not trusted and not confirm_host_key(host, identity[0], fingerprint):
            raise RuntimeError("已取消连接。")
        try:
            remote.auth_password(username, password)
        except paramiko.AuthenticationException as exc:
            raise RuntimeError("SSH 登录失败。请检查 SteamOS 用户名和密码。") from exc
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
        raise RuntimeError("现有转发密钥不是有效的 Ed25519 密钥。")
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
            raise RuntimeError(message.strip()[-2000:] or "Steam Frame 未完成操作。")
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
        "wrong_os": "目标设备不是运行 ARM64 SteamOS 的 Steam Frame。请核对设备地址。",
        "python_missing": "头显缺少 Python 3 或 aiohttp。请先更新 SteamOS，或为系统 Python 安装 aiohttp。",
        "systemd_unavailable": "无法访问 SteamOS 的用户服务。请用头显当前登录的 SteamOS 用户连接。",
    }
    if status != "ready":
        raise RuntimeError(messages.get(status, "无法确认 Steam Frame 的运行环境。"))


def _deploy(remote, resources, public):
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
    _run([ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=env)


_STOP = r"""
$dir = $env:VRCHAT_DOCK_INSTALL
$older = Join-Path $dir 'pc_bridge.py'
$program = (Join-Path $dir 'program') + '\'
$workers = @(Get-CimInstance Win32_Process -Filter "Name = 'VRChatGestureDock.exe' OR Name LIKE 'pythonw%.exe'" |
  Where-Object {
    ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($program,[StringComparison]::OrdinalIgnoreCase) -and
     $_.CommandLine -match '(?:^|\s)--bridge(?:\s|$)') -or
    ($_.Name -like 'pythonw*.exe' -and $_.CommandLine -and $_.CommandLine.Contains('"' + $older + '"'))
  })
$pids = @($workers | ForEach-Object { $_.ProcessId })
$key = Join-Path $dir 'frame-tunnel-key'
$tunnels = @(Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" | Where-Object {
  $pids -contains $_.ParentProcessId -and $_.CommandLine -and $_.CommandLine.Contains($key)
})
foreach ($p in @($workers) + @($tunnels)) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
"""

_REGISTER = r"""
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut(
  (Join-Path ([Environment]::GetFolderPath('Startup')) 'VRChat Gesture Dock.lnk'))
$shortcut.TargetPath = $env:VRCHAT_DOCK_EXE
$shortcut.Arguments = '--bridge'
$shortcut.WorkingDirectory = $env:VRCHAT_DOCK_INSTALL
$shortcut.Save()
"""


def _start(directory, exe):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return subprocess.Popen([exe, "--bridge"], cwd=directory, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)


def _wait_for_bridge(remote, process, instance):
    probe = ("import http.client,json; h=http.client.HTTPConnection('127.0.0.1',27656,timeout=2);"
             "h.request('GET','/health'); print(json.load(h.getresponse())['instance_id'])")
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Windows 后台服务已退出。请检查本机 27656 端口及安装目录的 bridge.log。")
        try:
            if _remote_command(remote, "python3 -c " + shlex.quote(probe), timeout=5).strip() == instance:
                return
        except (OSError, RuntimeError, paramiko.SSHException):
            pass
        time.sleep(1)
    raise RuntimeError("头显尚未连通这台电脑的后台服务。请检查 SSH 转发权限，并确认其他电脑没有占用头显的 27656 端口。")


def install(host, username, password, confirm_host_key, progress):
    host, username = _address(host, username)
    if not password:
        raise ValueError("请输入 SteamOS 账户密码。")
    keygen = _keygen()
    if not getattr(sys, "frozen", False):
        raise RuntimeError("请从已打包的 Windows 程序安装。")
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    settings = directory / "connection.json"
    with _setup_lock(directory):
        progress("正在核对设备身份并登录 Steam Frame…")
        with _connect(host, username, password, keygen, directory / "frame-known-hosts", confirm_host_key) as remote:
            progress("正在安装头显底栏按钮…")
            _preflight(remote)
            old = _config(settings)
            prior_data = settings.read_bytes() if settings.is_file() else None
            exe = _stage_executable(directory, Path(sys.executable))
            _deploy(remote, Path(sys._MEIPASS), _public_key(directory, keygen))
            instance = old.get("instance_id") or secrets.token_hex(16)
            new = dict(host=host, username=username, instance_id=instance)
            progress("正在启动电脑服务，检查头显的连接…")
            _powershell(_STOP, directory, exe)
            try:
                _atomic(settings, json.dumps(new, ensure_ascii=False, indent=2).encode("utf-8"))
                process = _start(directory, exe)
                _wait_for_bridge(remote, process, instance)
                _powershell(_REGISTER, directory, exe)
            except Exception:
                # The old shortcut is still present until verification succeeds.
                _powershell(_STOP, directory, exe)
                if prior_data is None:
                    settings.unlink(missing_ok=True)
                else:
                    _atomic(settings, prior_data)
                startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/VRChat Gesture Dock.lnk"
                if startup.is_file():
                    os.startfile(startup)
                raise
            try:
                status = _remote_command(remote, 'python3 "$HOME/.local/share/vrchat-gesture-dock/frame_dock.py" --status',
                                         timeout=6).strip()
            except (OSError, RuntimeError, paramiko.SSHException, EOFError):
                status = "waiting"
            if status == "mounted":
                progress("安装完成。头显底栏已显示「手势」，串流 VRChat 时点击即可切换。")
            else:
                progress("安装完成，电脑与头显已连通。请开启 CEF 远程调试并打开 Dashboard，等待「手势」按钮出现。")
            return exe
