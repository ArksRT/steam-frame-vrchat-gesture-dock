"""Offline checks for the distributed executable. Never connects to a headset."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from gesture_bindings import make_quest_binding
from pc_bridge import tunnel_command


def run_self_test():
    import paramiko
    from dock_i18n import preferred_language, save_language
    from release_setup import install
    from steamvr_lifecycle import APP_KEY, _manifest

    if not callable(install) or not callable(paramiko.SSHClient):
        raise RuntimeError("Missing installer or SSH client")
    root = Path(__file__).resolve().parent
    assets = {"dashboard.js": "vrchat-gesture-toggle", "frame_dock.py": "def is_bar",
              "install-frame.sh": "systemctl --user", "THIRD-PARTY-NOTICES.txt": "Paramiko"}
    for name, marker in assets.items():
        if marker.lower() not in (root / name).read_text(encoding="utf-8").lower():
            raise RuntimeError("Missing or unreadable bundled resource: " + name)
    using = {"app_key": "steam.app.438100", "controller_type": "frame_controller",
             "bindings": {"/actions/global": {"sources": [], "skeleton": [
                 {"output": "/actions/global/in/skeletonLeftHand"}]}}}
    preset = make_quest_binding(using)["bindings"]["/actions/global"]
    if preset["skeleton"] or not preset["sources"] or not using["bindings"]["/actions/global"]["skeleton"]:
        raise RuntimeError("Gesture profile conversion failed")
    with TemporaryDirectory(prefix="vrchat-dock-check-") as temp:
        directory = Path(temp) / "安装 路径"
        directory.mkdir()
        for language in ("en", "zh-CN"):
            save_language(language, directory)
            if preferred_language(directory) != language:
                raise RuntimeError("Could not switch the packaged installer's language")
        executable = directory / "VRChatGestureDock.exe"
        app, = json.loads(_manifest(directory, executable, ["--bridge"]))["applications"]
        if app["app_key"] != APP_KEY or not app["is_dashboard_overlay"] or app["arguments"] != "--bridge":
            raise RuntimeError("SteamVR startup overlay is missing from the package")
        (directory / "connection.json").write_text(
            json.dumps({"host": "example.invalid", "username": "test-user"}), encoding="utf-8")
        command = tunnel_command(directory)
        if command[-4:] != ["-l", "test-user", "--", "example.invalid"]:
            raise RuntimeError("Custom SSH destination did not survive packaging")
    return 0
