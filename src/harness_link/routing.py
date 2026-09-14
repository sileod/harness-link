import json
import os
from pathlib import Path
import sys
import time


ROUTE_PREFIX = "HARNESS_LINK_ROUTE "


def _cache_root():
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root).expanduser() if root else Path.home() / ".cache"


def route_id(provider, model):
    slug = provider.slug if hasattr(provider, "slug") else str(provider)
    return f"{slug}/{model}"


def route_status_path(provider):
    slug = provider.slug if hasattr(provider, "slug") else str(provider)
    return _cache_root() / "harness-link" / "routes" / f"{slug}.json"


def read_route_status(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_route_status(path, route, primary):
    provider, separator, model = route.partition("/")
    if not separator:
        provider, model = "", route
    payload = {
        "route": route,
        "provider": provider,
        "model": model,
        "primary": primary,
        "fallback": bool(primary and route != primary),
        "updated_at": time.time(),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return payload


def format_route_status(payload):
    route = payload.get("route", "unknown")
    primary = payload.get("primary", "")
    if payload.get("fallback") and primary:
        return f"{route} (fallback from {primary})"
    return f"{route} (primary)"


def route_statuses(provider=None):
    root = _cache_root() / "harness-link" / "routes"
    paths = [route_status_path(provider)] if provider else sorted(root.glob("*.json"))
    return [payload for path in paths if (payload := read_route_status(path))]


def show_route_status(provider=None, stream=None):
    stream = sys.stdout if stream is None else stream
    statuses = route_statuses(provider)
    if not statuses:
        suffix = f" for {provider}" if provider else ""
        print(f"hlink: no recorded route{suffix}", file=stream)
        return False
    for payload in statuses:
        print(format_route_status(payload), file=stream)
    return True
