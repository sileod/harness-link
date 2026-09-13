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
