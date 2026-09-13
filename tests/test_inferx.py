import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from harness_link import cli, credentials
from harness_link.providers import PROVIDERS, require_key


class InferxHarnessTests(TestCase):
    def setUp(self):
        self.provider = PROVIDERS["inferx"]

    def test_provider_defaults(self):
        self.assertEqual(self.provider.default_base, "https://model.inferx.net/endpoints/v1")
        self.assertEqual(self.provider.default_model, "deepseek-v4.1-flash")
        self.assertEqual(self.provider.key_env, "INFERX_" + "API" + "_" + "KEY")

    def test_opencode_config(self):
        config = cli.opencode_config(self.provider, self.provider.default_model)
        self.assertEqual(config["model"], "inferx/deepseek-v4.1-flash")
        self.assertEqual(
            config["provider"]["inferx"]["options"]["baseURL"],
            "https://model.inferx.net/endpoints/v1",
        )

    def test_hermes_receives_provider_endpoint_key_and_model(self):
        args = SimpleNamespace(model=self.provider.default_model, harness_args=["-z", "hello"])
        with patch.object(cli, "provider_key", return_value="inferx-secret"), patch.object(
            cli, "require_command", return_value="/usr/bin/hermes"
        ), patch.object(cli.subprocess, "call", return_value=0) as call:
            with self.assertRaises(SystemExit):
                cli.cmd_hermes(self.provider, args)

        command, = call.call_args.args
        child_env = call.call_args.kwargs["env"]
        self.assertEqual(command, ["/usr/bin/hermes", "-z", "hello"])
        self.assertEqual(child_env["OPENAI_BASE_URL"], self.provider.base_url)
        self.assertEqual(child_env["OPENAI_API_KEY"], "inferx-secret")
        self.assertEqual(child_env["LLM_MODEL"], self.provider.default_model)

    def test_key_is_prompted_saved_and_reused(self):
        key_env = self.provider.key_env
        old_key = os.environ.pop(key_env, None)
        try:
            with TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                stdin = SimpleNamespace(isatty=lambda: True)
                with patch.object(credentials.sys, "stdin", stdin), patch.object(
                    credentials.getpass, "getpass", return_value="inferx-test-key"
                ) as prompt:
                    self.assertEqual(require_key(self.provider), "inferx-test-key")
                prompt.assert_called_once()
                path = Path(tmp) / "harness-link" / "inferx.key"
                self.assertEqual(path.read_text().strip(), "inferx-test-key")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

                os.environ.pop(key_env, None)
                with patch.object(credentials.getpass, "getpass") as prompt:
                    self.assertEqual(require_key(self.provider), "inferx-test-key")
                prompt.assert_not_called()
        finally:
            os.environ.pop(key_env, None)
            if old_key is not None:
                os.environ[key_env] = old_key

    def test_albert_fallback_config_changes_model(self):
        albert = PROVIDERS["albert"]
        config = cli.litellm_config(
            self.provider,
            self.provider.default_model,
            fallback=albert,
            fallback_model=albert.default_model,
        )
        self.assertIn('model_name: "harness-link-primary"', config)
        self.assertIn('model: "openai/deepseek-v4.1-flash"', config)
        self.assertIn('model_name: "harness-link-fallback"', config)
        self.assertIn('model: "openai/deepseek-v4-flash"', config)
        self.assertIn('"harness-link-primary": ["harness-link-fallback"]', config)

    def test_codex_bridge_uses_local_credentials_and_endpoint(self):
        args = SimpleNamespace(model=self.provider.default_model, harness_args=["--help"])
        with patch.object(cli, "provider_key"), patch.object(
            cli, "require_command", return_value="/usr/bin/codex"
        ), patch.object(cli, "run_with_bridge") as bridge, patch.object(
            cli.subprocess, "call", return_value=0
        ) as call:
            bridge.side_effect = lambda provider, model, callback: callback(43123, model)
            with patch.dict(os.environ, {self.provider.key_env: "inferx-secret"}, clear=False):
                with self.assertRaises(SystemExit):
                    cli.cmd_codex(self.provider, args)

        command, = call.call_args.args
        child_env = call.call_args.kwargs["env"]
        self.assertIn('base_url = "http://127.0.0.1:43123/v1"', " ".join(command))
        self.assertEqual(child_env["HARNESS_LINK_INFERX_PROXY_KEY"], "local")
        self.assertNotIn(self.provider.key_env, child_env)

    def test_claude_bridge_uses_local_credentials_and_endpoint(self):
        args = SimpleNamespace(model=self.provider.default_model, harness_args=["--help"])
        with patch.object(cli, "provider_key"), patch.object(
            cli, "require_command", return_value="/usr/bin/claude"
        ), patch.object(cli, "run_with_bridge") as bridge, patch.object(
            cli.subprocess, "call", return_value=0
        ) as call:
            bridge.side_effect = lambda provider, model, callback: callback(43124, model)
            with patch.dict(os.environ, {self.provider.key_env: "inferx-secret"}, clear=False):
                with self.assertRaises(SystemExit):
                    cli.cmd_claude(self.provider, args)

        command, = call.call_args.args
        child_env = call.call_args.kwargs["env"]
        self.assertEqual(command[0], "/usr/bin/claude")
        self.assertEqual(child_env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:43124")
        self.assertEqual(child_env["ANTHROPIC_AUTH_TOKEN"], "local")
        self.assertEqual(child_env["ANTHROPIC_API_KEY"], "")
        self.assertNotIn(self.provider.key_env, child_env)
