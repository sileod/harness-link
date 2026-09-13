import getpass
import os
from pathlib import Path
import sys


def _config_root():
    root = os.environ.get("XDG_CONFIG_HOME")
    return Path(root).expanduser() if root else Path.home() / ".config"


def _key_path(provider):
    return _config_root() / "harness-link" / f"{provider.slug}.key"


def _read_saved_key(provider):
    try:
        key = _key_path(provider).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def _save_key(provider, key):
    path = _key_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(key + "\n", encoding="utf-8")
    path.chmod(0o600)


def require_key(provider, prompt=False):
    key = os.environ.get(provider.key_env, "").strip()
    if not key:
        key = _read_saved_key(provider) or ""
    should_prompt = provider.slug == "inferx" or prompt
    if not key and should_prompt and sys.stdin.isatty():
        try:
            key = getpass.getpass(f"{provider.name} API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            try:
                _save_key(provider, key)
            except OSError as exc:
                raise RuntimeError(f"cannot save {provider.name} API key: {exc}") from exc
    if not key:
        raise RuntimeError(f"{provider.key_env} is not set")
    os.environ[provider.key_env] = key
    return key
