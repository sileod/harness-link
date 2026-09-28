import io
import json
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
