"""Installer regression checks with disposable keys and isolated temporary storage."""

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

import paramiko

import release_setup as setup


HOST = "demo-headset.test"
USERNAME = "test-user"
PASSWORD = "disposable-fixture-password"


class AuthRecorder(paramiko.ServerInterface):
    def __init__(self, events):
        self.events = events

    def check_auth_password(self, username, password):
        self.events.append("password")
        if username == USERNAME and password == PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED


@contextmanager
def ssh_peer(key, events):
    """Complete a real SSH handshake on a private socket pair."""
    # On Windows socketpair uses a loopback listener. Sandboxed runners can
    # block the local connection, so bound the creation handshake as well.
    previous_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(3)
        client_socket, server_socket = socket.socketpair()
    finally:
        socket.setdefaulttimeout(previous_timeout)
    server = paramiko.Transport(server_socket)
    server.add_server_key(key)
    ready = threading.Event()
    try:
        server.start_server(event=ready, server=AuthRecorder(events))
        yield client_socket
    finally:
        client_socket.close()
        server.close()


@unittest.skipUnless(os.name == "nt", "The installer uses Windows OpenSSH")
class HostTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(2048)
        cls.other_key = paramiko.RSAKey.generate(2048)
        cls.keygen = setup._keygen()

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.pin = self.home / "frame-known-hosts"
        self.events = []

    def connect(self, key, answer):
        def decide(host, kind, fingerprint):
            self.assertEqual((host, kind, fingerprint), (HOST, key.get_name(), key.fingerprint))
            self.events.append("confirm")
            return answer

        with ssh_peer(key, self.events) as sock:
            with patch.object(socket, "create_connection", return_value=sock) as dial:
                with setup._connect(HOST, USERNAME, PASSWORD, self.keygen, self.pin, decide) as remote:
                    self.assertTrue(remote.is_authenticated())
                dial.assert_called_once_with((HOST, 22), timeout=8)

    def test_first_use_before_password_then_reuse_pin(self):
        self.connect(self.key, True)
        self.assertEqual(self.events, ["confirm", "password"])
        pin = self.pin.read_bytes()
        self.events.clear()
        self.connect(self.key, False)
        self.assertEqual(self.events, ["password"])
        self.assertEqual(self.pin.read_bytes(), pin)

    def test_decline_never_sends_password_or_creates_pin(self):
        with self.assertRaises(RuntimeError):
            self.connect(self.key, False)
        self.assertEqual(self.events, ["confirm"])
        self.assertFalse(self.pin.exists())

    def test_mismatched_pinned_key_is_rejected_before_password(self):
        contents = f"{HOST} {self.other_key.get_name()} {self.other_key.get_base64()}\n".encode()
        self.pin.write_bytes(contents)
        with self.assertRaisesRegex(RuntimeError, "公钥"):
            self.connect(self.key, True)
        self.assertEqual(self.events, [])
        self.assertEqual(self.pin.read_bytes(), contents)

    def test_existing_openssh_trust_skips_first_use_prompt(self):
        folder = self.home / ".ssh"
        folder.mkdir()
        (folder / "known_hosts").write_text(f"{HOST} {self.key.get_name()} {self.key.get_base64()}\n")
        self.connect(self.key, False)
        self.assertEqual(self.events, ["password"])
        self.assertIn(self.key.get_base64(), self.pin.read_text())


@unittest.skipUnless(os.name == "nt", "The installer uses Windows file locking")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, LOCALAPPDATA=str(self.temp), APPDATA=str(self.temp)))
        source = self.temp / "setup.exe"
        source.write_bytes(b"fixture executable, never launched")
        self.enterContext(patch.object(sys, "executable", str(source)))
        self.enterContext(patch.object(sys, "frozen", True, create=True))
        self.enterContext(patch.object(sys, "_MEIPASS", str(self.temp), create=True))
        self.spawn = self.enterContext(patch.object(setup, "_start"))
        self.verify = self.enterContext(patch.object(setup, "_wait_for_bridge"))
        self.powershell = self.enterContext(patch.object(setup, "_powershell"))
        self.enterContext(patch.object(setup, "_keygen"))
        self.enterContext(patch.object(setup, "_public_key", return_value=b"fixture public key"))
        self.deploy = self.enterContext(patch.object(setup, "_deploy"))
        self.preflight = self.enterContext(patch.object(setup, "_preflight"))
        self.enterContext(patch.object(setup, "_remote_command", return_value="mounted"))
        self.enterContext(patch.object(setup, "_connect", return_value=nullcontext(object())))
        self.directory = self.temp / "VRChatGestureDock"
        self.directory.mkdir()
        self.config = self.directory / "connection.json"
        self.messages = []

    def install(self):
        return setup.install("  DeMo-HeAdSeT.TeSt.  ", USERNAME, PASSWORD,
                             confirm_host_key=self.fail, progress=self.messages.append)

    def test_install_preserves_binding_and_saves_only_connection_metadata(self):
        bindings = self.directory / "bindings"
        bindings.mkdir()
        original = bindings / "selection.json"
        original.write_bytes(b"original binding")
        target = self.install()
        self.assertTrue(target.is_file())
        self.spawn.assert_called_once_with(self.directory, target)
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual((config["host"], config["username"]), (HOST, USERNAME))
        self.assertTrue(config["instance_id"])
        self.assertEqual(original.read_bytes(), b"original binding")
        for file in self.directory.rglob("*"):
            if file.is_file():
                self.assertNotIn(PASSWORD.encode(), file.read_bytes())
        self.assertNotIn(PASSWORD, " ".join(self.messages))
        self.assertEqual(self.powershell.call_args.args[0], setup._REGISTER)

    def test_bridge_failure_restores_prior_config_without_registering_startup(self):
        previous = b'{"host":"old.example","username":"prior","instance_id":"old-id"}\n'
        self.config.write_bytes(previous)
        self.verify.side_effect = RuntimeError("simulated tunnel failure")
        with self.assertRaisesRegex(RuntimeError, "simulated tunnel failure"):
            self.install()
        self.assertEqual(self.config.read_bytes(), previous)
        self.assertNotIn(setup._REGISTER, [call.args[0] for call in self.powershell.call_args_list])

    def test_remote_preflight_failure_leaves_prior_installation_alone(self):
        original = b'{"host":"my-own-frame","username":"prior"}'
        self.config.write_bytes(original)
        self.preflight.side_effect = RuntimeError("simulated unsupported headset")
        with self.assertRaisesRegex(RuntimeError, "simulated unsupported headset"):
            self.install()
        self.deploy.assert_not_called()
        self.powershell.assert_not_called()
        self.spawn.assert_not_called()
        self.assertEqual(self.config.read_bytes(), original)

    def test_invalid_destination_rejected_before_connect(self):
        for host, user in [("-invalid", USERNAME), ("name two", USERNAME), (HOST, "-invalid")]:
            with self.subTest(host=host, user=user), self.assertRaises(ValueError):
                setup._address(host, user)
        self.assertEqual(setup._address("[::1]", USERNAME), ("::1", USERNAME))


if __name__ == "__main__":
    unittest.main()

