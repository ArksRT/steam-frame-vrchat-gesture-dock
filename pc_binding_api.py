"""Switch VRChat's Frame bindings through the running SteamVR binding editor."""

import hashlib
import json
import os
import tempfile
import threading
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from gesture_bindings import make_quest_binding
from dock_i18n import tr


APP = "steam.app.438100"
CONTROLLER = "frame_controller"


class BindingError(Exception):
    pass


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _same_url(left, right):
    if left == right:
        return True
    first, second = urlsplit(left), urlsplit(right)
    if first.scheme != "file" or second.scheme != "file" or first.netloc != second.netloc:
        return False
    return os.path.normcase(unquote(first.path)) == os.path.normcase(unquote(second.path))


class BindingManager:
    def __init__(self, state_dir: Path, steamvr_port=27062):
        self.directory = Path(state_dir).resolve()
        self.steamvr_port = steamvr_port
        self.record_path = self.directory / "vrchat-gesture-selection.json"
        self.lock = threading.Lock()

    def _request(self, resource, *, query=None, body=None):
        target = "/input/" + resource
        if query:
            target += "?" + urlencode(query)
        origin = f"http://127.0.0.1:{self.steamvr_port}"
        connection = HTTPConnection("127.0.0.1", self.steamvr_port, timeout=2.5)
        try:
            connection.request("GET" if body is None else "POST", target,
                               body=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", "Origin": origin,
                                        "Referer": origin + "/dashboard/controllerbinding.html"})
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise BindingError(tr("The SteamVR bindings API returned HTTP {code}.", code=response.status))
            raw = response.read().strip()
        except (OSError, HTTPException) as error:
            raise BindingError(tr("Connect the Frame and start SteamVR on the PC that runs VRChat.")) from error
        finally:
            connection.close()
        # Valve's editor treats this HTTP reply as an acknowledgement and waits
        # for the selection separately. Only the subsequent current URL confirms it.
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            if body is not None:
                return {}
            raise BindingError(tr("SteamVR returned unrecognized binding data."))
        if not isinstance(data, dict):
            raise BindingError(tr("SteamVR returned unrecognized binding data."))
        if data.get("error") or data.get("success") is False:
            raise BindingError(str(data.get("error") or tr("SteamVR rejected the binding request.")))
        return data

    def _current_url(self):
        manifest = self._request("getactions.json", query={"app_key": APP})
        selection = manifest.get("current_binding_url") or {}
        url = selection.get(CONTROLLER)
        if not isinstance(url, str) or not url:
            raise BindingError(tr("Connect the Frame controllers and run VRChat through SteamVR at least once."))
        return url

    def _load(self, url):
        result = self._request("loadbindingfromurl.json", query={
            "app_key": APP, "controller_type": CONTROLLER, "binding_url": url,
        })
        binding = result.get("binding_config")
        if not isinstance(binding, dict) or not isinstance(binding.get("bindings"), dict):
            raise BindingError(tr("SteamVR could not read this VRChat controller binding."))
        return binding

    def _record(self):
        if not self.record_path.exists():
            return None
        with self.record_path.open(encoding="utf-8") as stream:
            record = json.load(stream)
        if not isinstance(record, dict) or not all(
            isinstance(record.get(key), str) and record[key] for key in ("original", "quest")
        ):
            raise BindingError(tr("Could not read the saved original binding."))
        return record

    def _select(self, url):
        self._request("selectconfig.action", body={
            "app_key": APP, "controller_type": CONTROLLER, "url": url,
        })
        deadline = time.monotonic() + 5
        while True:
            if _same_url(self._current_url(), url):
                return
            if time.monotonic() >= deadline:
                raise BindingError(tr("SteamVR has not confirmed the switch. Check the current binding."))
            time.sleep(0.18)

    def _prepare_quest(self, original):
        binding = make_quest_binding(self._load(original))
        encoded = json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()[:16]
        path = self.directory / ("vrchat-quest-" + digest + ".json")
        if not path.exists():
            _atomic_json(path, binding)
        # Let SteamVR open the file using the physical Frame controller profile.
        quest_url = path.as_uri()
        self._load(quest_url)
        return quest_url

    def _run(self, change, expected_mode=None):
        with self.lock:
            try:
                current = self._current_url()
                record = self._record()
                quest = bool(record and _same_url(current, record["quest"]))
                mode = "quest" if quest else "frame"
                if change and expected_mode is not None and expected_mode != mode:
                    return {"mode": mode, "detail": tr("The binding changed. Try again using the current mode.")}
                if change:
                    if quest:
                        self._load(record["original"])
                        self._select(record["original"])
                    else:
                        target = self._prepare_quest(current)
                        # Write the recovery information before changing SteamVR's state.
                        _atomic_json(self.record_path, {"original": current, "quest": target})
                        self._select(target)
                    quest = not quest
                return {"mode": "quest" if quest else "frame"}
            except (BindingError, ValueError, OSError) as error:
                return {"mode": "unavailable", "detail": str(error)}

    def state(self):
        return self._run(False)

    def toggle(self, expected_mode=None):
        return self._run(True, expected_mode)
