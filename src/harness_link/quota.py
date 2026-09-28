import argparse
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROVIDERS = ("claude", "codex", "agy", "opencode")
ALIASES = {"antigravity": "agy"}
MICROCENTS_PER_DOLLAR = 100_000_000


def _json_file(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _opencode_data_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "opencode"


def _opencode_auth():
    candidates = [_opencode_data_dir() / "auth.json"]
    if platform.system() == "Darwin":
        candidates.append(Path.home() / "Library/Application Support/opencode/auth.json")
    for path in candidates:
        data = _json_file(path)
        if isinstance(data, dict):
            return data
    return {}


def _request_json(url, *, headers=None, method="GET", body=None, timeout=10):
    payload = None if body is None else json.dumps(body).encode()
    request = Request(url, data=payload, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from None


def _window(name, remaining, reset_at=None, **extra):
    row = {"name": name, "remaining": max(0.0, min(100.0, float(remaining)))}
    if reset_at:
        row["reset_at"] = reset_at
    row.update(extra)
    return row


def _normalize_claude(data):
    windows = []
    labels = {
        "five_hour": "5h",
        "seven_day": "7d",
        "seven_day_opus": "7d opus",
        "seven_day_sonnet": "7d sonnet",
        "seven_day_oauth_apps": "7d oauth",
        "seven_day_cowork": "7d cowork",
        "seven_day_omelette": "7d omelette",
    }
    for key, label in labels.items():
        value = data.get(key)
        if not isinstance(value, dict):
            continue
        used = value.get("utilization")
        if isinstance(used, (int, float)):
            windows.append(_window(label, 100 - used, value.get("resets_at")))
    return windows


def _claude_token():
    entry = _opencode_auth().get("anthropic")
    if isinstance(entry, dict) and entry.get("type") == "oauth" and entry.get("access"):
        return entry["access"]

    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    data = _json_file(config / ".credentials.json")
    token = (data or {}).get("claudeAiOauth", {}).get("accessToken")
    if token:
        return token

    if platform.system() == "Darwin" and shutil.which("security"):
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout).get("claudeAiOauth", {}).get("accessToken")
            except json.JSONDecodeError:
                pass
    return None


def quota_claude():
    token = _claude_token()
    if not token:
        return {"ok": False, "error": "no Claude OAuth login found"}
    try:
        data = _request_json(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "harness-link/quota",
                "Accept": "application/json",
            },
        )
        windows = _normalize_claude(data)
        if not windows:
            return {"ok": False, "error": "Claude returned no quota windows"}
        result = {"ok": True, "windows": windows}
        extra = data.get("extra_usage")
        if isinstance(extra, dict):
            result["extra_usage"] = extra
        return result
    except RuntimeError as exc:
        return {"ok": False, "error": f"Claude quota: {exc}"}


def _normalize_codex(data):
    snapshot = data.get("rateLimits", data)
    if not isinstance(snapshot, dict):
        return []
    windows = []
    for key in ("primary", "secondary"):
        value = snapshot.get(key)
        if not isinstance(value, dict):
            continue
        used = value.get("usedPercent")
        if not isinstance(used, (int, float)):
            continue
        minutes = value.get("windowDurationMins")
        name = {300: "5h", 10080: "7d", 43200: "30d"}.get(minutes, f"{minutes}m" if minutes else key)
        reset = value.get("resetsAt")
        if isinstance(reset, (int, float)):
            from datetime import datetime, timezone
            reset = datetime.fromtimestamp(reset, timezone.utc).isoformat().replace("+00:00", "Z")
        windows.append(_window(name, 100 - used, reset))
    individual = snapshot.get("individualLimit")
    if isinstance(individual, dict) and isinstance(individual.get("remainingPercent"), (int, float)):
        reset = individual.get("resetsAt")
        if isinstance(reset, (int, float)):
            from datetime import datetime, timezone
            reset = datetime.fromtimestamp(reset, timezone.utc).isoformat().replace("+00:00", "Z")
        windows.append(_window("spend", individual["remainingPercent"], reset))
    return windows


def _codex_app_server():
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("codex is not installed")
    process = subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    replies = queue.Queue()

    def read_replies():
        for line in process.stdout:
            try:
                replies.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        replies.put(None)

    threading.Thread(target=read_replies, daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def receive(message_id):
        while True:
            try:
                message = replies.get(timeout=12)
            except queue.Empty:
                raise RuntimeError("Codex app-server timed out") from None
            if message is None:
                raise RuntimeError("Codex app-server closed before responding")
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(message["error"].get("message", "Codex RPC error"))
            return message.get("result", {})

    try:
        send({
            "id": "init",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "harness-link", "title": "Harness Link", "version": "0"},
                "capabilities": {"experimentalApi": True},
            },
        })
        receive("init")
        send({"method": "initialized"})
        send({"id": "quota", "method": "account/rateLimits/read", "params": {}})
        return receive("quota")
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def quota_codex():
    try:
        data = _codex_app_server()
        windows = _normalize_codex(data)
        if not windows:
            return {"ok": False, "error": "Codex returned no quota windows"}
        result = {"ok": True, "windows": windows}
        snapshot = data.get("rateLimits", {})
        if isinstance(snapshot, dict) and snapshot.get("planType"):
            result["plan"] = snapshot["planType"]
        return result
    except RuntimeError as exc:
        return {"ok": False, "error": f"Codex quota: {exc}"}


def _find_agy_groups(value):
    if isinstance(value, dict):
        groups = value.get("groups")
        if isinstance(groups, list):
            return groups
        for child in value.values():
            found = _find_agy_groups(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_agy_groups(child)
            if found is not None:
                return found
    return None


def _normalize_agy(data):
    groups = _find_agy_groups(data) or []
    windows = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        family = group.get("displayName") or group.get("display_name") or group.get("name") or "models"
        for bucket in group.get("buckets", []):
            if not isinstance(bucket, dict) or bucket.get("disabled"):
                continue
            remaining = bucket.get("remainingFraction", bucket.get("remaining_fraction"))
            if not isinstance(remaining, (int, float)):
                continue
            window = bucket.get("window") or bucket.get("displayName") or bucket.get("display_name") or "quota"
            reset = bucket.get("resetTime", bucket.get("reset_time"))
            windows.append(_window(f"{family} / {str(window).lower()}", 100 * remaining, reset))
    return windows


def quota_agy():
    agy = shutil.which("agy")
    if not agy:
        return {"ok": False, "error": "Antigravity CLI (agy) is not installed"}
    try:
        result = subprocess.run(
            [agy, "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "AGY quota command timed out"}
    if result.returncode:
        return {"ok": False, "error": f"AGY quota command exited {result.returncode}"}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "AGY quota command did not return JSON"}
    windows = _normalize_agy(data)
    return {"ok": True, "windows": windows} if windows else {"ok": False, "error": "AGY returned no quota windows"}


def _opencode_api_key():
    key = os.environ.get("OPENCODE_API_KEY")
    if key:
        return key
    auth = _opencode_auth()
    for name in ("opencode-go", "opencode"):
        entry = auth.get(name)
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "api":
            for field in ("key", "apiKey", "access"):
                if entry.get(field):
                    return entry[field]
    return None


def _normalize_opencode_go(data):
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return []
    windows = []
    for key, label in (("rolling", "5h"), ("weekly", "7d"), ("monthly", "30d")):
        value = usage.get(key)
        if not isinstance(value, dict):
            continue
        used = value.get("percent")
        if isinstance(used, (int, float)):
            remaining = 0 if value.get("status") == "rate-limited" else 100 - used
            windows.append(_window(label, remaining, value.get("resetsAt")))
    return windows


def _quota_opencode_go():
    key = _opencode_api_key()
    if not key:
        return None
    try:
        data = _request_json(
            "https://opencode.ai/zen/go/v1/usage",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        windows = _normalize_opencode_go(data)
        return {"ok": True, "windows": windows} if windows else {"ok": False, "error": "OpenCode Go returned no quota windows"}
    except RuntimeError as exc:
        return {"ok": False, "error": f"OpenCode Go quota: {exc}"}


def _quota_opencode_zen():
    db = _opencode_data_dir() / "opencode.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        state = con.execute("SELECT active_account_id, active_org_id FROM account_state LIMIT 1").fetchone()
        if not state or not all(state):
            return None
        account = con.execute(
            "SELECT url, access_token, token_expiry FROM account WHERE id = ? LIMIT 1",
            (state[0],),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        try:
            con.close()
        except UnboundLocalError:
            pass
    if not account or not account[0] or not account[1]:
        return None

    headers = {
        "Authorization": f"Bearer {account[1]}",
        "x-org-id": state[1],
        "Accept": "application/json",
    }
    base = account[0].rstrip("/")
    try:
        budget = _request_json(f"{base}/api/budgets/org", headers=headers)
        if isinstance(budget, dict) and budget.get("limitMicroCents") is not None:
            limit = float(budget["limitMicroCents"]) / MICROCENTS_PER_DOLLAR
            spent = float(budget.get("spentMicroCents") or 0) / MICROCENTS_PER_DOLLAR
            if limit <= 0:
                return {"ok": False, "error": "OpenCode Zen has no positive monthly budget"}
            remaining = 100 * max(0, limit - spent) / limit
            return {
                "ok": True,
                "windows": [_window("monthly budget", remaining, budget.get("resetsAt"))],
                "balance": {"spent_usd": spent, "limit_usd": limit},
            }

        account_data = _request_json(f"{base}/api/billing/account", headers=headers)
        usage = _request_json(f"{base}/api/usage/cost-by-day", headers=headers)
        limit_raw = account_data.get("creditLimitMicroCents") if isinstance(account_data, dict) else None
        if limit_raw is None:
            return {"ok": False, "error": "OpenCode Zen has no monthly budget"}
        limit = float(limit_raw) / MICROCENTS_PER_DOLLAR
        if limit <= 0:
            return {"ok": False, "error": "OpenCode Zen has no positive monthly credit limit"}
        from datetime import datetime, timezone

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        rows = usage if isinstance(usage, list) else []
        spent = (
            sum(
                float(row.get("totalCostMicroCents", 0))
                for row in rows
                if isinstance(row, dict) and str(row.get("date", "")).startswith(month)
            )
            / MICROCENTS_PER_DOLLAR
        )
        remaining = 100 * max(0, limit - spent) / limit
        return {
            "ok": True,
            "windows": [_window("monthly budget", remaining)],
            "balance": {"spent_usd": spent, "limit_usd": limit},
        }
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"OpenCode Zen quota: {exc}"}


def quota_opencode():
    sources = {}
    go = _quota_opencode_go()
    zen = _quota_opencode_zen()
    if go is not None:
        sources["go"] = go
    if zen is not None:
        sources["zen"] = zen
    if not sources:
        return {"ok": False, "error": "no OpenCode Go key or Zen console login found"}

    windows = []
    errors = []
    for source, result in sources.items():
        if result.get("ok"):
            windows.extend({**row, "name": f"{source} / {row['name']}"} for row in result.get("windows", []))
        else:
            errors.append(result.get("error"))
    out = {"ok": bool(windows), "windows": windows, "sources": sources}
    if errors and not windows:
        out["error"] = "; ".join(filter(None, errors))
    return out


FETCHERS = {
    "claude": quota_claude,
    "codex": quota_codex,
    "agy": quota_agy,
    "opencode": quota_opencode,
}


def fetch(providers):
    return {provider: FETCHERS[provider]() for provider in providers}


def _fmt_remaining(value):
    value = float(value)
    return f"{value:.0f}%" if value.is_integer() else f"{value:.1f}%"


def print_human(results, out=None):
    out = sys.stdout if out is None else out
    lines = ["Quota"]
    for provider, result in results.items():
        lines.append(provider)
        if not result.get("ok"):
            lines.append(f"  unavailable: {result.get('error', 'unknown error')}")
            continue
        windows = result.get("windows", [])
        if not windows:
            lines.append("  no quota windows")
            continue
        for row in windows:
            reset = f"  resets {row['reset_at']}" if row.get("reset_at") else ""
            lines.append(f"  {row['name']}: {_fmt_remaining(row['remaining'])} left{reset}")
    width = max(map(len, lines))
    border = "+" + "-" * (width + 2) + "+"
    print(border, file=out)
    for line in lines:
        print(f"| {line:<{width}} |", file=out)
    print(border, file=out)


def parser():
    p = argparse.ArgumentParser(prog="hlink quota", description="Show remaining coding-agent subscription quota")
    p.add_argument("providers", nargs="*", metavar="PROVIDER")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    unknown = [name for name in args.providers if name not in PROVIDERS and name not in ALIASES]
    if unknown:
        p.error(f"unknown provider: {unknown[0]}")
    providers = [ALIASES.get(name, name) for name in args.providers] or list(PROVIDERS)
    providers = list(dict.fromkeys(providers))
    results = fetch(providers)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print_human(results)
    return 0 if any(result.get("ok") for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
