import ast
import json
import tempfile
import unittest
from pathlib import Path
from string import Formatter
from unittest.mock import patch

import dock_i18n as i18n
from release_setup import _address


class LanguagePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())) / "手势设置"
        self.enterContext(patch.object(i18n, "_system_language", return_value="en"))

    def test_first_launch_reads_the_system_language_without_writing(self):
        self.assertEqual(i18n.preferred_language(self.directory), "en")
        self.assertFalse(self.directory.exists())

    def test_explicit_choice_precedes_installed_language_and_survives_relaunch(self):
        self.directory.mkdir()
        connection = self.directory / "connection.json"
        original = json.dumps({"host": "headset.example", "language": "zh-CN"}).encode()
        connection.write_bytes(original)
        self.assertEqual(i18n.preferred_language(self.directory), "zh-CN")
        i18n.save_language("en", self.directory)
        self.assertEqual(i18n.preferred_language(self.directory), "en")
        i18n.save_language("zh-CN", self.directory)
        self.assertEqual(i18n.preferred_language(self.directory), "zh-CN")
        self.assertEqual(connection.read_bytes(), original)

    def test_invalid_choices_preserve_previous_preference(self):
        i18n.save_language("zh-CN", self.directory)
        with self.assertRaises(ValueError):
            i18n.save_language("unknown", self.directory)
        self.assertEqual(i18n.preferred_language(self.directory), "zh-CN")
        (self.directory / "language.json").write_text("damaged", encoding="utf-8")
        self.assertEqual(i18n.preferred_language(self.directory), "en")


class TranslationTests(unittest.TestCase):
    def test_backend_validation_uses_requested_language_and_restores_context(self):
        with i18n.language_context("en"):
            with self.assertRaisesRegex(ValueError, "hostname"):
                _address("-test", "steamos")
            with i18n.language_context("zh-CN"), self.assertRaisesRegex(ValueError, "主机名"):
                _address("-test", "steamos")
            with self.assertRaisesRegex(ValueError, "hostname"):
                _address("-test", "steamos")

    def test_backend_catalog_covers_call_sites_and_dynamic_values(self):
        root = Path(__file__).resolve().parent
        for source in ("release_setup.py", "pc_binding_api.py", "steamvr_lifecycle.py", "gesture_bindings.py"):
            code = ast.parse((root / source).read_text(encoding="utf-8"))
            for node in ast.walk(code):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                    self.assertIn(node.args[0].value, i18n._CHINESE, source)
        for english, chinese in i18n._CHINESE.items():
            en_fields = {field for _, field, _, _ in Formatter().parse(english) if field}
            zh_fields = {field for _, field, _, _ in Formatter().parse(chinese) if field}
            self.assertEqual(en_fields, zh_fields, english)
