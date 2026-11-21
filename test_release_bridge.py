import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import url2pathname

from pc_binding_api import APP, CONTROLLER, BindingManager
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
            self.assertEqual(request("POST", "/toggle", "frame", "https://example.invalid")[0], 403)
            self.assertEqual(request("POST", "/toggle", "frame"), (200, {"mode": "quest"}))
            self.assertTrue(runtime.selection.startswith("file:"))
            bridge.manager = BindingManager(directory, runtime.server_port)
            self.assertEqual(request("GET", "/state")[1], {"mode": "quest"})
            self.assertEqual(request("POST", "/toggle", "frame")[1]["mode"], "quest")
            self.assertEqual(request("POST", "/toggle", "quest")[1], {"mode": "frame"})
            self.assertEqual(runtime.original, runtime.selection)
            self.assertEqual(len(runtime.changes), 2)


if __name__ == "__main__":
    unittest.main()
