import json
import os
from pathlib import Path
import sys
import threading


ROUTE_PREFIX = "HARNESS_LINK_ROUTE "


def _cache_root():
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root).expanduser() if root else Path.home() / ".cache"


def route_id(provider, model):
    slug = provider.slug if hasattr(provider, "slug") else str(provider)
    return f"{slug}/{model}"


def route_status_path(provider, owner_pid=None):
    slug = provider.slug if hasattr(provider, "slug") else str(provider)
    pid = os.getpid() if owner_pid is None else owner_pid
    return _cache_root() / "harness-link" / "routes" / f"{slug}-{pid}.json"


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
        "owner_pid": os.getpid(),
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


def start_route_monitor(path, primary, enabled=False, stream=None):
    stop = threading.Event()
    stream = sys.stderr if stream is None else stream

    def monitor():
        last = None
        while not stop.wait(0.1):
            payload = read_route_status(path)
            if not payload:
                continue
            route = payload.get("route")
            if route == last:
                continue
            label = "fallback" if payload.get("fallback") else "primary"
            if enabled:
                print(f"[hlink] {label} -> {route}", file=stream, flush=True)
            last = route

    thread = threading.Thread(target=monitor, name="harness-link-route", daemon=True)
    thread.start()
    return stop, thread


def stop_route_monitor(handle):
    if not handle:
        return
    stop, thread = handle
    stop.set()
    thread.join(timeout=0.5)
