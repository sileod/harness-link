from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import List, Optional
import urllib.error
import urllib.parse
import urllib.request

from .credentials import require_key as _require_key


INFERX_KEY_ENV = "INFERX_" + "API" + "_" + "KEY"


@dataclass(frozen=True)
class Provider:
    slug: str
    name: str
    key_env: str
    base_env: str
    default_base: str
    model_env: str
    default_model: str
    debug_env: str
    spawn_ref_env: str
    opencode_limits: Optional[dict] = None
    claude_experimental: bool = False
    direct_responses: bool = False
    direct_messages: bool = False
    anthropic_base: Optional[str] = None
    dynamic_free: bool = False
    spawn_native: bool = False

    @property
    def base_url(self):
        return os.environ.get(self.base_env, self.default_base).rstrip("/")


PROVIDERS = {
    "albert": Provider(
        slug="albert",
        name="Albert API",
        key_env="ALBERT_API_KEY",
        base_env="ALBERT_BASE_URL",
        default_base="https://albert.api.etalab.gouv.fr/v1",
        model_env="ALBERT_MODEL",
        default_model="deepseek-v4-flash",
        debug_env="ALBERT_DEBUG",
        spawn_ref_env="ALBERT_SPAWN_REF",
        opencode_limits={"context": 131072, "output": 65536},
        claude_experimental=True,
    ),
    "nim": Provider(
        slug="nim",
        name="NVIDIA NIM",
        key_env="NVIDIA_API_KEY",
        base_env="NIM_BASE_URL",
        default_base="https://integrate.api.nvidia.com/v1",
        model_env="NIM_MODEL",
        default_model="openai/gpt-oss-120b",
        debug_env="NIM_DEBUG",
        spawn_ref_env="NIM_SPAWN_REF",
        claude_experimental=True,
    ),
    "inferx": Provider(
        slug="inferx",
        name="InferX",
        key_env=INFERX_KEY_ENV,
        base_env="INFERX_BASE_URL",
        default_base="https://model.inferx.net/endpoints/v1",
        model_env="INFERX_MODEL",
        default_model="deepseek-v4.1-flash",
        debug_env="INFERX_DEBUG",
        spawn_ref_env="INFERX_SPAWN_REF",
        claude_experimental=True,
    ),
    "orfree": Provider(
        slug="orfree",
        name="OpenRouter Free",
        key_env="OPENROUTER_API_KEY",
        base_env="ORFREE_BASE_URL",
        default_base="https://openrouter.ai/api/v1",
        model_env="ORFREE_MODEL",
        default_model="openrouter/free",
        debug_env="ORFREE_DEBUG",
        spawn_ref_env="ORFREE_SPAWN_REF",
        direct_responses=True,
        direct_messages=True,
        anthropic_base="https://openrouter.ai/api",
        dynamic_free=True,
        spawn_native=True,
    ),
}


def require_key(provider: Provider, prompt: bool = False) -> str:
    return _require_key(provider, prompt=prompt)


def is_free_model_name(model: str) -> bool:
    return model == "openrouter/free" or model.endswith(":free")


def validate_model(provider: Provider, model: str) -> str:
    model = model.strip()
    if not model:
        raise ValueError("model cannot be empty")
    if provider.dynamic_free and not is_free_model_name(model):
        raise ValueError(
            f"{provider.slug} only accepts OpenRouter free routes: use a model ending in :free or openrouter/free"
        )
    return model


def _price_is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _orfree_query_url(provider: Provider) -> str:
    query = urllib.parse.urlencode(
        {
            "sort": "most-popular",
            "supported_parameters": "tools",
            "max_price": "0",
        }
    )
    return f"{provider.base_url}/models?{query}"


def fetch_free_models(provider: Provider, timeout: float = 5.0) -> List[str]:
    if not provider.dynamic_free:
        raise ValueError("free model discovery is only available for orfree")
    key = require_key(provider)
    request = urllib.request.Request(
        _orfree_query_url(provider),
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "harness-link/0.3",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    models = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        pricing = item.get("pricing") or {}
        supported = item.get("supported_parameters") or []
        if not isinstance(model_id, str) or not model_id.endswith(":free"):
            continue
        if "tools" not in supported:
            continue
        if not _price_is_zero(pricing.get("prompt")):
            continue
        if not _price_is_zero(pricing.get("completion")):
            continue
        models.append(model_id)
    return models


def _cache_root() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root).expanduser() if root else Path.home() / ".cache"


def _orfree_cache_path() -> Path:
    return _cache_root() / "harness-link" / "orfree-model.json"


def _orfree_cache_ttl() -> float:
    raw = os.environ.get("ORFREE_CACHE_TTL", "300")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 300.0


def _read_cached_orfree_model(now: Optional[float] = None) -> Optional[str]:
    now = time.time() if now is None else now
    path = _orfree_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        model = payload.get("model", "")
        fetched_at = float(payload.get("fetched_at", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if now - fetched_at > _orfree_cache_ttl():
        return None
    return model if is_free_model_name(model) else None


def _write_cached_orfree_model(model: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    path = _orfree_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": model, "fetched_at": now}) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def resolve_orfree_model(provider: Provider) -> str:
    cached = _read_cached_orfree_model()
    if cached:
        return cached
    try:
        models = fetch_free_models(provider)
    except (OSError, RuntimeError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        models = []
    model = models[0] if models else provider.default_model
    _write_cached_orfree_model(model)
    return model


def resolve_model(provider: Provider, explicit: Optional[str] = None) -> str:
    if explicit:
        return validate_model(provider, explicit)
    env_model = os.environ.get(provider.model_env, "").strip()
    if env_model:
        return validate_model(provider, env_model)
    if provider.dynamic_free:
        return resolve_orfree_model(provider)
    return provider.default_model
