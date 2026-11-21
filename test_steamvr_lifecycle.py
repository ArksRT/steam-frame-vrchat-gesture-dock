import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import steamvr_lifecycle as lifecycle


class StartupRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.root / "Steam Config"
        self.config.mkdir()
        self.index = self.config / "appconfig.json"
        self.initial = {"manifest_paths": [str(self.root / "another.vrmanifest")], "version": 1}
        self.index.write_text(json.dumps(self.initial), encoding="utf-8")
        self.directory = self.root / "手势 App"
        self.directory.mkdir()
        self.worker = self.directory / "VRChatGestureDock.exe"
        self.worker.touch()
        self.enterContext(patch.object(lifecycle, "_paths", return_value=(None, self.config)))
        self.enterContext(patch.object(lifecycle, "_applications", side_effect=lambda _: nullcontext(None)))

    def test_first_install_adds_a_visible_overlay_and_can_roll_back(self):
        undo = lifecycle.register(self.directory, self.worker, ["--bridge"])
        manifest = self.directory / lifecycle.MANIFEST
        app, = json.loads(manifest.read_text(encoding="utf-8"))["applications"]
        self.assertTrue(app["is_dashboard_overlay"])
        self.assertFalse(app.get("is_internal"))
        self.assertFalse(app.get("is_hidden"))
        self.assertEqual(app["binary_path_windows"], str(self.worker))
        self.assertEqual(app["arguments"], "--bridge")
        self.assertTrue(lifecycle.startup_enabled())
        self.assertEqual(json.loads(self.index.read_text(encoding="utf-8"))["manifest_paths"],
                         self.initial["manifest_paths"] + [str(manifest)])
        undo()
        self.assertFalse(manifest.exists())
        self.assertFalse(lifecycle.startup_enabled())
        self.assertEqual(json.loads(self.index.read_text(encoding="utf-8")), self.initial)

    def test_upgrade_uses_new_binary_and_preserves_user_off(self):
        lifecycle.register(self.directory, self.worker, ["--bridge"])
        prefs = lifecycle._preferences(self.config)
        prefs.write_text('{"autolaunch": false, "unrelated": "keep"}', encoding="utf-8")
        replacement = self.directory / "next-version.exe"
        replacement.touch()
        undo = lifecycle.register(self.directory, replacement, ["--bridge"])
        app, = json.loads((self.directory / lifecycle.MANIFEST).read_text(encoding="utf-8"))["applications"]
        self.assertEqual(app["binary_path_windows"], str(replacement))
        self.assertFalse(lifecycle.startup_enabled())
        undo()
        app, = json.loads((self.directory / lifecycle.MANIFEST).read_text(encoding="utf-8"))["applications"]
        self.assertEqual(app["binary_path_windows"], str(self.worker))
        self.assertEqual(json.loads(prefs.read_text(encoding="utf-8")), {"autolaunch": False, "unrelated": "keep"})
