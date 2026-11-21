"""Serve the Frame's Dashboard on loopback through a restricted SSH reverse tunnel."""

import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep

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


def forward_to_frame(directory):
    prior_error = None
    while True:
        try:
            child = subprocess.run(tunnel_command(directory), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            error = child.stderr.decode(errors="replace").strip() or "disconnected"
        except (ValueError, KeyError, TypeError, OSError) as exc:
            error = str(exc)
        if error != prior_error:
            logging.info("Frame tunnel: %s", error)
            prior_error = error
        sleep(10)


def main():
    directory = data_directory()
    directory.mkdir(exist_ok=True, parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
        RotatingFileHandler(directory / "bridge.log", maxBytes=120000, backupCount=1, encoding="utf-8")
    ])
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.manager = BindingManager(directory / "bindings")
    config = directory / "connection.json"
    server.instance_id = json.loads(config.read_text(encoding="utf-8")).get("instance_id") if config.exists() else None
    threading.Thread(target=forward_to_frame, args=(directory,), daemon=True).start()
    logging.info("Listening on 127.0.0.1:%d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
