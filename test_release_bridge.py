import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import url2pathname

from pc_binding_api import APP, CONTROLLER, BindingManager
import pc_bridge
from pc_bridge import Handler, ORIGIN, PORT


class SimulatedSteamVR(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data):
        encoded = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        path = urlsplit(self.path)
        params = parse_qs(path.query)
        if params.get("app_key") != [APP]:
            return self.send_error(400)
        if path.path == "/input/getactions.json":
            return self.reply({"current_binding_url": {CONTROLLER: self.server.selection}})
        if path.path == "/input/loadbindingfromurl.json" and params.get("controller_type") == [CONTROLLER]:
            url = params["binding_url"][0]
            if url.startswith("file:"):
                data = json.loads(Path(url2pathname(urlsplit(url).path)).read_text(encoding="utf-8"))
            elif url == self.server.original:
                data = self.server.source
            else:
                return self.send_error(404)
            return self.reply({"binding_config": data})
        self.send_error(404)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if (self.path == "/input/selectconfig.action" and request.get("app_key") == APP
                and request.get("controller_type") == CONTROLLER):
            self.server.selection = request["url"]
            self.server.changes.append(request["url"])
            return self.reply({"success": True})
        self.send_error(400)


class BridgeReleaseTest(unittest.TestCase):
    def test_dashboard_round_trip_after_restart_preserves_original(self):
        with ExitStack() as stack:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "带空格 Profiles"

            def serve(handler):
                server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), handler))
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                stack.callback(server.shutdown)
                return server

            runtime = serve(SimulatedSteamVR)
            runtime.original = runtime.selection = "vr-input-workshop://example"
            runtime.changes = []
            runtime.source = {
                "app_key": APP, "controller_type": CONTROLLER,
                "bindings": {"/actions/global": {"skeleton": [
                    {"output": "/actions/global/in/skeletonLeftHand"}]}}}
            bridge = serve(Handler)
            bridge.instance_id = "test-instance"
            bridge.manager = BindingManager(directory, runtime.server_port)

            def request(method, path, expected=None, origin=ORIGIN):
                conn = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
                try:
                    body = None if expected is None else json.dumps({"expectedMode": expected})
                    conn.request(method, path, body, {"Host": f"127.0.0.1:{PORT}", "Origin": origin,
                                                      "Content-Type": "application/json"})
                    response = conn.getresponse()
                    return response.status, json.loads(response.read() or b"null")
                finally:
                    conn.close()

            self.assertEqual(request("GET", "/health")[1]["instance_id"], "test-instance")
            self.assertEqual(request("POST", "/toggle", origin="https://example.invalid")[0], 403)
            self.assertEqual(request("POST", "/toggle", "frame"), (200, {"mode": "quest"}))
            self.assertTrue(runtime.selection.startswith("file:"))
            bridge.manager = BindingManager(directory, runtime.server_port)
            self.assertEqual(request("GET", "/state")[1], {"mode": "quest"})
            self.assertEqual(request("POST", "/toggle", "frame")[1]["mode"], "quest")
            self.assertEqual(request("POST", "/toggle", "quest")[1], {"mode": "frame"})
            self.assertEqual(runtime.original, runtime.selection)
            self.assertEqual(len(runtime.changes), 2)


@unittest.skipUnless(os.name == "nt", "SteamVR bridge lifecycle is Windows-specific")
class BridgeLifetimeTests(unittest.TestCase):
    def test_offline_and_early_launch(self):
        with patch.object(pc_bridge, "find_vrserver_pid", return_value=None):
            self.assertIsNone(pc_bridge._open_runtime(timeout=0))
        with (patch.object(pc_bridge, "find_vrserver_pid", side_effect=[None, 77]),
              patch.object(pc_bridge, "_Runtime") as runtime,
              patch.object(pc_bridge.time, "sleep")):
            self.assertIs(pc_bridge._open_runtime(), runtime.return_value)
            runtime.assert_called_once_with(77)

    def test_runtime_exit_closes_real_listener_and_child(self):
        import _winapi
        stopped = threading.Event()
        class Session:
            pid = 12345
            wait = staticmethod(stopped.wait)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ready = directory / "child-pid"
            # Run the interpreter directly; the Windows venv launcher may spawn another process.
            command = [sys._base_executable, "-c",
                       "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",
                       str(ready)]
            server = pc_bridge._BridgeServer(("127.0.0.1", 0), Handler)
            errors = []
            def serve():
                try:
                    pc_bridge._serve_runtime(directory, Session())
                except BaseException as exc:
                    errors.append(exc)

            with (patch.object(pc_bridge, "_BridgeServer", return_value=server),
                  patch.object(pc_bridge, "tunnel_command", return_value=command)):
                worker = threading.Thread(target=serve)
                worker.start()
                try:
                    deadline = time.monotonic() + 6
                    while not ready.exists() and time.monotonic() < deadline and not errors:
                        time.sleep(0.04)
                    self.assertFalse(errors)
                    pid = int(ready.read_text())
                    handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
                    self.addCleanup(_winapi.CloseHandle, handle)
                    self.assertEqual(_winapi.WaitForSingleObject(handle, 0), _winapi.WAIT_TIMEOUT)
                    http = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                    try:
                        http.request("GET", "/health", headers={"Host": f"127.0.0.1:{PORT}"})
                        self.assertEqual(http.getresponse().status, 200)
                    finally:
                        http.close()
                finally:
                    stopped.set()
                    worker.join(timeout=6)
                self.assertFalse(worker.is_alive())
                self.assertFalse(errors)
                self.assertEqual(_winapi.WaitForSingleObject(handle, 0), _winapi.WAIT_OBJECT_0)
                self.assertEqual(server.socket.fileno(), -1)

    def test_force_terminating_worker_also_terminates_its_child(self):
        import _winapi
        with tempfile.TemporaryDirectory() as temp:
            ready = Path(temp) / "child-pid"
            owner = subprocess.Popen([
                sys._base_executable, "-c",
                "from pc_bridge import _protect_tunnel_on_exit; import pathlib,subprocess,sys,time; "
                "_protect_tunnel_on_exit(); "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)", str(ready),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 6
                while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.04)
                if owner.poll() is not None:
                    self.fail(owner.stderr.read().decode(errors="replace"))
                handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, int(ready.read_text()))
                self.addCleanup(_winapi.CloseHandle, handle)
                owner.kill()
                owner.wait(timeout=3)
                self.assertEqual(_winapi.WaitForSingleObject(handle, 2500), _winapi.WAIT_OBJECT_0)
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
