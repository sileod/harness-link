import io
import os
import threading
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from harness_link import cli, hlink, routing
from harness_link.providers import PROVIDERS


class RoutingTests(TestCase):
    def test_litellm_config_loads_route_callback(self):
        inferx = PROVIDERS["inferx"]
        albert = PROVIDERS["albert"]
        config = cli.litellm_config(
            inferx,
            inferx.default_model,
            fallback=albert,
            fallback_model=albert.default_model,
            route_callback=True,
        )
        self.assertIn("callbacks: route_callback.proxy_handler_instance", config)
        self.assertIn('"harness-link-primary": ["harness-link-fallback"]', config)

    def test_bridge_route_resolver_identifies_fallback(self):
        inferx = PROVIDERS["inferx"]
        albert = PROVIDERS["albert"]
        resolve = cli._bridge_route_resolver(
            inferx,
            inferx.default_model,
            albert,
            albert.default_model,
        )
        self.assertEqual(
            resolve(f"openai/{inferx.default_model}", inferx.base_url),
            f"inferx/{inferx.default_model}",
        )
        self.assertEqual(
            resolve(f"openai/{albert.default_model}", albert.base_url),
            f"albert/{albert.default_model}",
        )
        self.assertEqual(
            resolve(cli.FALLBACK_SECONDARY_MODEL, ""),
            f"albert/{albert.default_model}",
        )

    def test_bridge_output_records_and_displays_fallback(self):
        inferx = PROVIDERS["inferx"]
        albert = PROVIDERS["albert"]
        resolve = cli._bridge_route_resolver(
            inferx,
            inferx.default_model,
            albert,
            albert.default_model,
        )
        primary = routing.route_id(inferx, inferx.default_model)
        line = f"{routing.ROUTE_PREFIX}openai/{albert.default_model}\t{albert.base_url}\n"
        stderr = io.StringIO()
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), patch.object(
            cli.sys, "stderr", stderr
        ):
            path = routing.route_status_path(inferx)
            cli._read_bridge_output(
                io.StringIO(line),
                io.StringIO(),
                False,
                resolve,
                path,
                primary,
                True,
                [None],
                threading.Lock(),
            )
            payload = routing.read_route_status(path)
        self.assertEqual(payload["route"], f"albert/{albert.default_model}")
        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["primary"], primary)
        self.assertIn(f"[hlink] fallback -> albert/{albert.default_model}", stderr.getvalue())

    def test_status_formats_fallback(self):
        payload = {
            "route": "albert/deepseek-v4-flash",
            "primary": "inferx/deepseek-v4.1-flash",
            "fallback": True,
        }
        self.assertEqual(
            routing.format_route_status(payload),
            "albert/deepseek-v4-flash (fallback from inferx/deepseek-v4.1-flash)",
        )

    def test_hlink_forwards_show_routing(self):
        with patch.object(hlink.provider_cli, "main") as provider_main:
            hlink.run_provider(
                "inferx",
                "codex",
                "deepseek-v4.1-flash",
                ["exec", "review"],
                fallback="albert",
                show_routing=True,
            )
        provider_main.assert_called_once_with(
            [
                "inferx",
                "codex",
                "--model",
                "deepseek-v4.1-flash",
                "--fallback",
                "albert",
                "--show-routing",
                "--",
                "exec",
                "review",
            ]
        )

    def test_hlink_status_uses_provider_filter(self):
        with patch.object(hlink, "show_route_status") as show:
            hlink.main(["status", "--provider", "inferx"])
        show.assert_called_once_with("inferx")

    def test_provider_status_uses_provider_filter(self):
        with patch.object(cli, "show_route_status") as show:
            cli.main(["inferx", "status"])
        show.assert_called_once_with("inferx")
