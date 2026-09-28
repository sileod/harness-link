import io
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from harness_link import hlink, quota


class QuotaTests(TestCase):
    def test_claude_windows(self):
        windows = quota._normalize_claude(
            {
                "five_hour": {"utilization": 27, "resets_at": "2026-09-28T18:00:00Z"},
                "seven_day": {"utilization": 41, "resets_at": "2026-10-02T12:00:00Z"},
            }
        )
        self.assertEqual([row["remaining"] for row in windows], [73, 59])
        self.assertEqual([row["name"] for row in windows], ["5h", "7d"])

    def test_codex_windows(self):
        windows = quota._normalize_codex(
            {
                "rateLimits": {
                    "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1790618400},
                    "secondary": {"usedPercent": 44, "windowDurationMins": 10080, "resetsAt": 1791043200},
                }
            }
        )
        self.assertEqual([row["remaining"] for row in windows], [88, 56])
        self.assertEqual([row["name"] for row in windows], ["5h", "7d"])

    def test_codex_app_server_waits_for_rate_limit_response(self):
        server = '''
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({"id": "init", "result": {}}), flush=True)
    elif message.get("method") == "account/rateLimits/read":
        print(json.dumps({"id": "quota", "result": {"rateLimits": {"primary": {"usedPercent": 25}}}}), flush=True)
'''
        popen = subprocess.Popen

        def fake_popen(_command, **kwargs):
            return popen([sys.executable, "-u", "-c", server], **kwargs)

        with patch.object(quota.shutil, "which", return_value="codex"), patch.object(
            quota.subprocess, "Popen", side_effect=fake_popen
        ):
            result = quota._codex_app_server()
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 25)

    def test_codex_quota_uses_running_daemon_socket(self):
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home) / "app-server-control"
            directory.mkdir()
            path = directory / "app-server-control.sock"
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                listener.listen(1)

                def read_exact(connection, length):
                    data = b""
                    while len(data) < length:
                        data += connection.recv(length - len(data))
                    return data

                def fake_daemon():
                    with listener.accept()[0] as connection:
                        request = b""
                        while not request.endswith(b"\r\n\r\n"):
                            request += connection.recv(1)
                        key = request.split(b"Sec-WebSocket-Key: ", 1)[1].split(b"\r\n", 1)[0]
                        accept = base64.b64encode(hashlib.sha1(
                            key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                        ).digest())
                        connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                                           b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                        for _ in range(3):
                            first, second = read_exact(connection, 2)
                            self.assertEqual(first & 0x0F, 1)
                            self.assertTrue(second & 0x80)
                            length = second & 0x7F
                            if length == 126:
                                length = struct.unpack("!H", read_exact(connection, 2))[0]
                            mask = read_exact(connection, 4)
                            payload = read_exact(connection, length)
                            message = json.loads(bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))
                            if message.get("id") == "init":
                                response = {"id": "init", "result": {}}
                            elif message.get("id") == "quota":
                                response = {"id": "quota", "result": {"rateLimits": {
                                    "primary": {"usedPercent": 25, "windowDurationMins": 300}
                                }}}
                            else:
                                continue
                            encoded = json.dumps(response).encode()
                            connection.sendall(bytes((0x81, len(encoded))) + encoded)

                thread = threading.Thread(target=fake_daemon)
                thread.start()
                try:
                    with patch.dict(os.environ, {"CODEX_HOME": home}):
                        with patch.object(quota, "_codex_app_server", side_effect=AssertionError("should use daemon")):
                            result = quota.quota_codex()
                    self.assertEqual(result["windows"][0]["remaining"], 75)
                finally:
                    thread.join(timeout=2)

    def test_agy_windows_accept_snake_case(self):
        windows = quota._normalize_agy(
            {
                "command": {
                    "data": {
                        "groups": [
                            {
                                "display_name": "Gemini Models",
                                "buckets": [
                                    {
                                        "window": "WEEKLY",
                                        "remaining_fraction": 0.63,
                                        "reset_time": "2026-10-01T12:00:00Z",
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        )
        self.assertEqual(windows[0]["remaining"], 63)
        self.assertEqual(windows[0]["name"], "Gemini Models / weekly")

    def test_opencode_go_windows(self):
        windows = quota._normalize_opencode_go(
            {
                "usage": {
                    "rolling": {"status": "ok", "percent": 25, "resetsAt": "2026-09-28T18:00:00Z"},
                    "weekly": {"status": "ok", "percent": 50, "resetsAt": "2026-10-01T00:00:00Z"},
                    "monthly": {"status": "ok", "percent": 10, "resetsAt": "2026-10-28T00:00:00Z"},
                }
            }
        )
        self.assertEqual([row["remaining"] for row in windows], [75, 50, 90])

    def test_human_output(self):
        out = io.StringIO()
        quota.print_human(
            {
                "claude": {
                    "ok": True,
                    "windows": [{"name": "5h", "remaining": 73, "reset_at": "later"}],
                },
                "codex": {"ok": False, "error": "not logged in"},
            },
            out=out,
        )
        text = out.getvalue()
        self.assertIn("73% left", text)
        self.assertIn("not logged in", text)
        self.assertIn("| claude", text)
        self.assertIn("| codex", text)
        lines = text.splitlines()
        self.assertEqual(lines[0], lines[-1])
        self.assertTrue(all(len(line) == len(lines[0]) for line in lines))

    def test_json_output_stays_unboxed(self):
        out = io.StringIO()
        results = {"claude": {"ok": True, "windows": [{"name": "5h", "remaining": 73}]}}
        with patch.object(quota, "fetch", return_value=results), patch("sys.stdout", out):
            self.assertEqual(quota.main(["claude", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), results)

    def test_hlink_dispatches_quota(self):
        with patch.object(quota, "main", return_value=0) as quota_main:
            self.assertEqual(hlink.main(["quota", "claude"]), 0)
        quota_main.assert_called_once_with(["claude"])

    def test_main_defaults_to_all_providers(self):
        with patch.object(quota, "fetch", return_value={"claude": {"ok": True, "windows": []}}) as fetch:
            with patch.object(quota, "print_human"):
                self.assertEqual(quota.main([]), 0)
        fetch.assert_called_once_with(list(quota.PROVIDERS))
