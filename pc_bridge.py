"""Serve the Frame's Dashboard on loopback through a restricted SSH reverse tunnel."""

import csv
import ctypes
import errno
import json
import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import closing
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pc_binding_api import BindingManager


PORT = 27656
ORIGIN = "https://steamloopback.host"


def data_directory():
    if getattr(sys, "frozen", False):
        return Path(os.environ["LOCALAPPDATA"]) / "VRChatGestureDock"
    return Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _permitted(self):
        origin = self.headers.get("Origin")
        return (self.headers.get("Host") == f"127.0.0.1:{PORT}"
                and origin in (None, ORIGIN))

    def _reply(self, status, body=None):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        if self.headers.get("Origin") == ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_OPTIONS(self):
        self._reply(204 if self._permitted() and self.path in ("/toggle", "/state") else 403)

    def do_GET(self):
        if not self._permitted():
            return self._reply(403)
        if self.path == "/health":
            return self._reply(200, {"app": "VRChatGestureDock", "protocol": 1,
                                     "instance_id": self.server.instance_id})
        if self.path != "/state":
            return self._reply(404)
        self._reply(200, self.server.manager.state())

    def do_POST(self):
        if not self._permitted():
            return self._reply(403)
        if self.path != "/toggle":
            return self._reply(404)
        if self.headers.get_content_type() != "application/json":
            return self._reply(415)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 256:
                return self._reply(400)
            body = json.loads(self.rfile.read(length))
            expected = body.get("expectedMode") if isinstance(body, dict) else None
            if expected not in ("frame", "quest"):
                return self._reply(400)
        except (ValueError, TypeError):
            return self._reply(400)
        result = self.server.manager.toggle(expected)
        logging.info("Mode request from %s: %s", expected, result)
        self._reply(200, result)


def tunnel_command(directory):
    ssh = Path(os.environ["SYSTEMROOT"]) / "System32/OpenSSH/ssh.exe"
    config_path = directory / "connection.json"
    if config_path.exists():
        settings = json.loads(config_path.read_text(encoding="utf-8"))
        user, host = settings["username"], settings["host"]
        # OpenSSH parses the -o argument a second time, including its quotes.
        known_hosts = (directory / "frame-known-hosts").as_posix().replace("%", "%%")
        trust = ["-F", "none", "-o", f'UserKnownHostsFile="{known_hosts}"',
                 "-o", "GlobalKnownHostsFile=none", "-o", "UpdateHostKeys=no"]
    elif getattr(sys, "frozen", False):
        raise ValueError("请先在安装窗口完成 Steam Frame 连接设置。")
    else:
        # The original script installer used the user's standard known_hosts.
        user, host, trust = "steamos", "frame", []
    command = [
        str(ssh), *trust, "-NT", "-i", str(directory / "frame-tunnel-key"),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2", "-o", "TCPKeepAlive=yes",
        "-R", f"127.0.0.1:{PORT}:127.0.0.1:{PORT}", "-l", user, "--", host,
    ]
    return command


def find_vrserver_pid():
    """Locate vrserver only in the Windows session where SteamVR launched us."""
    session = wintypes.DWORD()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    tasklist = Path(os.environ["SYSTEMROOT"]) / "System32/tasklist.exe"
    result = subprocess.run([str(tasklist), "/FI", "IMAGENAME eq vrserver.exe",
                             "/FI", f"SESSION eq {session.value}", "/FO", "CSV", "/NH"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=6)
    for row in csv.reader(result.stdout.decode(errors="replace").splitlines()):
        if len(row) >= 2 and row[0].lower() == "vrserver.exe":
            return int(row[1])
    return None


class _Runtime:
    """Hold the kernel process handle so a reused PID cannot extend the session."""
    def __init__(self, pid):
        import _winapi
        self.pid = pid
        self.handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE | 0x1000, False, pid)
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.QueryFullProcessImageNameW(wintypes.HANDLE(self.handle), 0, name, ctypes.byref(length)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        if Path(name.value).name.lower() != "vrserver.exe":
            self.close()
            raise ProcessLookupError("SteamVR process exited during startup")

    def wait(self, seconds=None):
        import _winapi
        timeout = 0xffffffff if seconds is None else int(seconds * 1000)
        return _winapi.WaitForSingleObject(self.handle, timeout) == _winapi.WAIT_OBJECT_0

    def close(self):
        import _winapi
        _winapi.CloseHandle(self.handle)


def _open_runtime(timeout=12):
    deadline = time.monotonic() + timeout
    while True:
        pid = find_vrserver_pid()
        if pid is not None:
            try:
                return _Runtime(pid)
            except (ProcessLookupError, OSError) as error:
                # OpenProcess on a process that just exited returns ERROR_INVALID_PARAMETER.
                if not isinstance(error, ProcessLookupError) and error.winerror != 87:
                    raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.5, remaining))


def _protect_tunnel_on_exit():
    """Put this worker and its future SSH processes in a process-lifetime job.

    The single non-inheritable job handle is deliberately left open until worker
    exit. Windows then closes the handle and terminates any remaining SSH child,
    including when SteamVR or an update terminates the bridge forcibly.
    """
    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                    ("memory_limits_and_peaks", ctypes.c_size_t * 4)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    job = wintypes.HANDLE(handle)
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(-1))):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error


def _run_tunnel(command, stop):
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW) as child:
        try:
            while not stop.is_set():
                try:
                    _, message = child.communicate(timeout=0.4)
                    return message.decode(errors="replace").strip() or "disconnected"
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()
    return ""


def forward_to_frame(directory, stop):
    prior_error = None
    while not stop.is_set():
        try:
            error = _run_tunnel(tunnel_command(directory), stop)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            error = str(exc)
        if not stop.is_set() and error != prior_error:
            logging.info("Frame tunnel: %s", error)
            prior_error = error
        stop.wait(10)


class _BridgeServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can permit two live workers to claim the same port.
    allow_reuse_address = False


def _serve_runtime(directory, runtime):
    deadline = time.monotonic() + 8
    while not runtime.wait(0):
        try:
            server = _BridgeServer(("127.0.0.1", PORT), Handler)
            break
        except OSError as error:
            if error.errno not in (errno.EADDRINUSE, 10048) or time.monotonic() >= deadline:
                raise
            # A previous SteamVR instance may still be releasing its listener.
            runtime.wait(0.25)
    else:
        return
    with server:
        server.manager = BindingManager(directory / "bindings")
        config = directory / "connection.json"
        server.instance_id = json.loads(config.read_text(encoding="utf-8")).get("instance_id") if config.exists() else None
        stop = threading.Event()
        tunnel = threading.Thread(target=forward_to_frame, args=(directory, stop))
        http = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2})
        http.start()
        try:
            tunnel.start()
            logging.info("SteamVR PID %d, listening on 127.0.0.1:%d", runtime.pid, PORT)
            runtime.wait()
        finally:
            stop.set()
            server.shutdown()
            http.join()
            if tunnel.ident is not None:
                tunnel.join()
        logging.info("SteamVR stopped; bridge and tunnel closed")


def main():
    directory = data_directory()
    directory.mkdir(exist_ok=True, parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        RotatingFileHandler(directory / "bridge.log", maxBytes=120000, backupCount=1, encoding="utf-8")
    ])
    try:
        runtime = _open_runtime()
        if runtime is None:
            logging.info("SteamVR is not running; exiting")
            return 0
        with closing(runtime):
            _protect_tunnel_on_exit()
            _serve_runtime(directory, runtime)
    except (OSError, ValueError, subprocess.SubprocessError):
        logging.exception("SteamVR bridge startup failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
