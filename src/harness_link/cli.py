import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .providers import PROVIDERS, Provider, fetch_free_models, require_key, resolve_model
from .routing import ROUTE_PREFIX, route_id, route_status_path, show_route_status, write_route_status


MODEL_COMMANDS = {"opencode", "hermes", "codex", "claude", "mini"}
FALLBACK_PRIMARY_MODEL = "harness-link-primary"
FALLBACK_SECONDARY_MODEL = "harness-link-fallback"


def die(provider: Provider, message: str, code: int = 1):
    print(f"{provider.slug}: {message}", file=sys.stderr)
    raise SystemExit(code)


def provider_key(provider: Provider, prompt: bool = False) -> str:
    try:
        return require_key(provider, prompt=prompt)
    except RuntimeError as exc:
        die(provider, str(exc))


def require_command(provider: Provider, name: str, install_hint: str) -> str:
    path = shutil.which(name)
    if not path:
        die(provider, f"{name} is not installed. {install_hint}")
    return path


def deep_merge(base, overlay):
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    result = dict(base)
    for key, value in overlay.items():
        result[key] = deep_merge(result.get(key), value) if key in result else value
    return result


def opencode_config(provider: Provider, model: str, existing=None):
    model_config = {"name": model}
    if provider.opencode_limits:
        model_config["limit"] = provider.opencode_limits
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            provider.slug: {
                "npm": "@ai-sdk/openai-compatible",
                "name": provider.name,
                "options": {
                    "baseURL": provider.base_url,
                    "apiKey": f"{{env:{provider.key_env}}}",
                },
                "models": {model: model_config},
            }
        },
        "model": f"{provider.slug}/{model}",
        "small_model": f"{provider.slug}/{model}",
    }
    return deep_merge(existing or {}, config)


def cmd_opencode(provider: Provider, args):
    provider_key(provider)
    executable = require_command(provider, "opencode", "See https://opencode.ai/docs/")
    existing = {}
    raw = os.environ.get("OPENCODE_CONFIG_CONTENT")
    if raw:
        try:
            existing = json.loads(raw)
        except json.JSONDecodeError as exc:
            die(provider, f"OPENCODE_CONFIG_CONTENT is not valid JSON: {exc}")
    env = os.environ.copy()
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        opencode_config(provider, args.model, existing), separators=(",", ":")
    )
    raise SystemExit(subprocess.call([executable, *args.harness_args], env=env))


def cmd_hermes(provider: Provider, args):
    key = provider_key(provider)
    executable = require_command(provider, "hermes", "See https://github.com/NousResearch/hermes-agent")
    env = os.environ.copy()
    env.update(
        {
            "OPENAI_BASE_URL": provider.base_url,
            "OPENAI_API_KEY": key,
            "LLM_MODEL": args.model,
        }
    )
    raise SystemExit(subprocess.call([executable, *args.harness_args], env=env))


def mini_config(provider: Provider, model: str) -> str:
    return (
        "model:\n"
        f"  model_name: {json.dumps('openai/' + model)}\n"
        "  model_kwargs:\n"
        '    custom_llm_provider: "openai"\n'
        f"    api_base: {json.dumps(provider.base_url)}\n"
        '  cost_tracking: "ignore_errors"\n'
    )


def _extract_mini_configs(arguments):
    configs = []
    rest = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg in {"-c", "--config"}:
            if index + 1 >= len(arguments):
                rest.append(arg)
                index += 1
                continue
            configs.append(arguments[index + 1])
            index += 2
            continue
        if arg.startswith("--config="):
            configs.append(arg.split("=", 1)[1])
            index += 1
            continue
        rest.append(arg)
        index += 1
    return configs, rest


def cmd_mini(provider: Provider, args):
    key = provider_key(provider)
    executable = require_command(
        provider,
        "mini",
        "Install mini-SWE-agent with `uv tool install mini-swe-agent` or `pipx install mini-swe-agent`.",
    )
    configs, forwarded = _extract_mini_configs(args.harness_args)
    with tempfile.TemporaryDirectory(prefix=f"harness-link-mini-{provider.slug}-") as tmp:
        override = Path(tmp) / "provider.yaml"
        override.write_text(mini_config(provider, args.model), encoding="utf-8")
        command = [executable]
        if configs:
            for config in configs:
                command.extend(["-c", config])
        else:
            command.extend(["-c", "mini.yaml"])
        command.extend(["-c", str(override), *forwarded])
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = key
        env["MSWEA_CONFIGURED"] = "1"
        raise SystemExit(subprocess.call(command, env=env))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _litellm_model_entry(name: str, provider: Provider, model: str) -> str:
    return (
        f"  - model_name: {json.dumps(name)}\n"
        "    litellm_params:\n"
        f"      model: {json.dumps('openai/' + model)}\n"
        f"      api_base: {json.dumps(provider.base_url)}\n"
        f"      api_key: os.environ/{provider.key_env}\n"
        "      use_chat_completions_api: true\n"
    )


def litellm_config(
    provider: Provider,
    model: str,
    fallback: Provider = None,
    fallback_model: str = None,
    route_callback: bool = False,
) -> str:
    settings = "litellm_settings:\n  drop_params: true\n"
    if route_callback:
        settings += "  callbacks: route_callback.proxy_handler_instance\n"
    if fallback is None:
        return settings + "model_list:\n" + _litellm_model_entry(model, provider, model)
    return (
        settings
        + "router_settings:\n"
        "  num_retries: 0\n"
        "  fallbacks:\n"
        f"    - {json.dumps(FALLBACK_PRIMARY_MODEL)}: [{json.dumps(FALLBACK_SECONDARY_MODEL)}]\n"
        "model_list:\n"
        + _litellm_model_entry(FALLBACK_PRIMARY_MODEL, provider, model)
        + _litellm_model_entry(FALLBACK_SECONDARY_MODEL, fallback, fallback_model)
    )


def bridge_timeout() -> float:
    raw = os.environ.get("HARNESS_LINK_BRIDGE_TIMEOUT", "20")
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return max(2.0, value)


def wait_for_bridge(provider: Provider, process, port: int, timeout=None):
    timeout = bridge_timeout() if timeout is None else timeout
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health/liveliness"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            die(provider, f"LiteLLM bridge exited during startup; set {provider.debug_env}=1 for logs")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            pass
        time.sleep(0.15)
    if process.poll() is None:
        process.terminate()
    die(provider, f"LiteLLM bridge did not become HTTP-ready; set {provider.debug_env}=1 for logs")


def _bridge_route_resolver(provider, model, fallback=None, fallback_model=None):
    entries = [(provider, model, FALLBACK_PRIMARY_MODEL)]
    if fallback is not None:
        entries.append((fallback, fallback_model, FALLBACK_SECONDARY_MODEL))

    def resolve(model_name, api_base):
        base = api_base.rstrip("/")
        if base:
            for candidate, candidate_model, _alias in entries:
                if candidate.base_url == base:
                    return route_id(candidate, candidate_model)
        normalized = model_name.removeprefix("openai/")
        for candidate, candidate_model, alias in entries:
            if model_name == alias or normalized == candidate_model:
                return route_id(candidate, candidate_model)
        return model_name

    return resolve


def _read_bridge_output(stream, target, debug, resolve_route, status_path, primary_route, show_routing, state, lock):
    for line in stream:
        if line.startswith(ROUTE_PREFIX):
            model_name, _separator, api_base = line[len(ROUTE_PREFIX) :].rstrip("\n").partition("\t")
            route = resolve_route(model_name, api_base)
            payload = write_route_status(status_path, route, primary_route)
            with lock:
                if show_routing and state[0] != route:
                    label = "fallback" if payload["fallback"] else "primary"
                    print(f"[hlink] {label} -> {route}", file=sys.stderr, flush=True)
                state[0] = route
        elif debug:
            print(line, end="", file=target, flush=True)


def run_with_bridge(
    provider: Provider,
    model: str,
    callback,
    fallback: Provider = None,
    fallback_model: str = None,
    show_routing: bool = False,
):
    provider_key(provider)
    if fallback is not None:
        provider_key(fallback, prompt=True)
    litellm = require_command(
        provider,
        "litellm",
        "Install the bridge with `python -m pip install 'litellm[proxy]'`.",
    )
    port = free_port()
    primary_route = route_id(provider, model)
    status_path = route_status_path(provider)
    resolve_route = _bridge_route_resolver(provider, model, fallback, fallback_model)
    with tempfile.TemporaryDirectory(prefix=f"harness-link-{provider.slug}-") as tmp:
        config_path = Path(tmp) / "litellm.yaml"
        config_path.write_text(
            litellm_config(
                provider,
                model,
                fallback=fallback,
                fallback_model=fallback_model,
                route_callback=True,
            ),
            encoding="utf-8",
        )
        callback_path = Path(tmp) / "route_callback.py"
        callback_path.write_text(
            Path(__file__).with_name("route_callback.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        debug = os.environ.get(provider.debug_env) == "1"
        bridge_env = os.environ.copy()
        bridge_env["LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES"] = "true"
        process = subprocess.Popen(
            [litellm, "--config", str(config_path), "--host", "127.0.0.1", "--port", str(port)],
            env=bridge_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        state = [None]
        lock = threading.Lock()
        readers = [
            threading.Thread(
                target=_read_bridge_output,
                args=(process.stdout, sys.stdout, debug, resolve_route, status_path, primary_route, show_routing, state, lock),
                daemon=True,
            ),
            threading.Thread(
                target=_read_bridge_output,
                args=(process.stderr, sys.stderr, debug, resolve_route, status_path, primary_route, show_routing, state, lock),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        routed_model = FALLBACK_PRIMARY_MODEL if fallback is not None else model
        try:
            wait_for_bridge(provider, process, port)
            return callback(port, routed_model)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for reader in readers:
                reader.join(timeout=0.5)


def _codex_provider_config(provider: Provider, base_url: str, env_key: str) -> str:
    return (
        f'{{ name = "{provider.name}", '
        f'base_url = "{base_url}", '
        f'env_key = "{env_key}", wire_api = "responses", requires_openai_auth = false }}'
    )


def cmd_codex(provider: Provider, args):
    executable = require_command(provider, "codex", "See https://developers.openai.com/codex/cli/")
    if provider.direct_responses:
        provider_key(provider)
        env = os.environ.copy()
        config = _codex_provider_config(provider, provider.base_url, provider.key_env)
        command = [
            executable,
            "-c",
            f'model_provider="{provider.slug}"',
            "-c",
            f'model="{args.model}"',
            "-c",
            f"model_providers.{provider.slug}={config}",
            *args.harness_args,
        ]
        raise SystemExit(subprocess.call(command, env=env))

    def launch(port, routed_model):
        proxy_env = f"HARNESS_LINK_{provider.slug.upper()}_PROXY_KEY"
        config = _codex_provider_config(provider, f"http://127.0.0.1:{port}/v1", proxy_env)
        env = os.environ.copy()
        env.pop(provider.key_env, None)
        env[proxy_env] = "local"
        command = [
            executable,
            "-c",
            f'model_provider="{provider.slug}"',
            "-c",
            f'model="{routed_model}"',
            "-c",
            f"model_providers.{provider.slug}={config}",
            *args.harness_args,
        ]
        return subprocess.call(command, env=env)

    raise SystemExit(run_with_bridge(provider, args.model, launch, show_routing=args.show_routing))


def _claude_env(provider: Provider, model: str, base_url: str, token: str):
    env = os.environ.copy()
    env.update(
        {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": model,
        }
    )
    return env


def cmd_claude(provider: Provider, args):
    executable = require_command(provider, "claude", "See https://docs.anthropic.com/en/docs/claude-code/")
    if provider.direct_messages:
        key = provider_key(provider)
        base = provider.anthropic_base or provider.base_url.rsplit("/v1", 1)[0]
        env = _claude_env(provider, args.model, base, key)
        raise SystemExit(subprocess.call([executable, "--model", args.model, *args.harness_args], env=env))

    def launch(port, routed_model):
        env = _claude_env(provider, routed_model, f"http://127.0.0.1:{port}", "local")
        env.pop(provider.key_env, None)
        return subprocess.call([executable, "--model", routed_model, *args.harness_args], env=env)

    if provider.claude_experimental:
        print(f"{provider.slug}: Claude Code bridge is experimental", file=sys.stderr)
    raise SystemExit(run_with_bridge(provider, args.model, launch, show_routing=args.show_routing))


def _fallback_proxy_provider(provider: Provider, fallback: Provider, port: int) -> Provider:
    return Provider(
        slug=provider.slug,
        name=f"{provider.name} with {fallback.name} fallback",
        key_env="HARNESS_LINK_PROXY_KEY",
        base_env="HARNESS_LINK_PROXY_BASE_URL",
        default_base=f"http://127.0.0.1:{port}/v1",
        model_env=provider.model_env,
        default_model=FALLBACK_PRIMARY_MODEL,
        debug_env=provider.debug_env,
        spawn_ref_env=provider.spawn_ref_env,
        direct_responses=True,
        direct_messages=True,
        anthropic_base=f"http://127.0.0.1:{port}",
    )


def run_with_fallback(provider: Provider, args):
    fallback = PROVIDERS[args.fallback]
    if fallback.slug == provider.slug:
        die(provider, "fallback provider must differ from the primary provider")
    try:
        fallback_model = resolve_model(fallback)
    except (RuntimeError, ValueError) as exc:
        die(fallback, str(exc))
    print(
        f"{provider.slug}: fallback enabled: {args.model} -> {fallback.slug}/{fallback_model}",
        file=sys.stderr,
    )

    def launch(port, routed_model):
        proxy = _fallback_proxy_provider(provider, fallback, port)
        os.environ[proxy.key_env] = "local"
        os.environ[proxy.base_env] = proxy.default_base
        saved_keys = {name: os.environ.pop(name, None) for name in {provider.key_env, fallback.key_env}}
        args.model = routed_model
        args.fallback = None
        try:
            return args.func(proxy, args)
        finally:
            for name, value in saved_keys.items():
                if value is not None:
                    os.environ[name] = value

    return run_with_bridge(
        provider,
        args.model,
        launch,
        fallback=fallback,
        fallback_model=fallback_model,
        show_routing=args.show_routing,
    )


def cmd_models(provider: Provider, _args):
    if provider.dynamic_free:
        try:
            models = fetch_free_models(provider, timeout=15)
        except RuntimeError as exc:
            die(provider, str(exc))
        except urllib.error.HTTPError as exc:
            die(provider, f"OpenRouter returned HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError) as exc:
            die(provider, f"cannot reach OpenRouter: {exc}")
        for model in models:
            print(model)
        return

    key = provider_key(provider)
    request = urllib.request.Request(
        f"{provider.base_url}/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        die(provider, f"{provider.name} returned HTTP {exc.code}")
    except urllib.error.URLError as exc:
        die(provider, f"cannot reach {provider.name}: {exc.reason}")
    models = payload.get("data", payload if isinstance(payload, list) else [])
    for model in models:
        if isinstance(model, dict) and model.get("id"):
            print(model["id"])


def cmd_status(provider: Provider, _args):
    show_route_status(provider.slug)


def cmd_spawn(provider: Provider, args):
    executable = require_command(
        provider,
        f"{provider.slug}-spawn",
        "Re-run the Harness Link installer to add Spawn support.",
    )
    os.execvpe(executable, [executable, *args.harness_args], os.environ.copy())


def parser(provider: Provider):
    root = argparse.ArgumentParser(
        prog=provider.slug,
        description=f"Run coding harnesses against {provider.name}",
    )
    root.add_argument("--version", action="version", version=f"harness-link {provider.slug} {__version__}")
    sub = root.add_subparsers(dest="command", required=True)

    commands = [
        ("opencode", cmd_opencode, f"Run OpenCode directly against {provider.name}"),
        ("hermes", cmd_hermes, f"Run Hermes directly against {provider.name}"),
        ("mini", cmd_mini, f"Run mini-SWE-agent directly against {provider.name}"),
        (
            "codex",
            cmd_codex,
            f"Run Codex {'directly' if provider.direct_responses else 'through the experimental Responses bridge'}",
        ),
        (
            "claude",
            cmd_claude,
            f"Run Claude Code {'directly' if provider.direct_messages else 'through the experimental Messages bridge'}",
        ),
    ]
    for name, handler, help_text in commands:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("-m", "--model", default=None)
        command.add_argument("--fallback", choices=tuple(PROVIDERS), default=None)
        command.add_argument("--show-routing", action="store_true", help="show the actual routed model when it changes")
        command.set_defaults(func=handler)

    models = sub.add_parser("models", help=f"List model IDs returned by {provider.name}")
    models.set_defaults(func=cmd_models)

    status = sub.add_parser("status", help="Show the last model selected by a local routing bridge")
    status.set_defaults(func=cmd_status)

    spawn = sub.add_parser("spawn", help="Run an agent through the Spawn execution backend")
    spawn.set_defaults(func=cmd_spawn)
    return root


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--version"]:
        print(f"harness-link {__version__}")
        return
    if not argv or argv[0] not in PROVIDERS:
        names = ", ".join(PROVIDERS)
        print(f"usage: harness-link <provider> ...\nproviders: {names}", file=sys.stderr)
        raise SystemExit(2)
    provider = PROVIDERS[argv.pop(0)]
    root = parser(provider)
    args, rest = root.parse_known_args(argv)
    if args.command in {"models", "status"} and rest:
        root.error(f"unrecognized arguments: {' '.join(rest)}")
    args.harness_args = rest[1:] if rest[:1] == ["--"] else rest
    if args.command in MODEL_COMMANDS:
        auto = provider.dynamic_free and args.model is None and not os.environ.get(provider.model_env, "").strip()
        try:
            args.model = resolve_model(provider, args.model)
        except (RuntimeError, ValueError) as exc:
            die(provider, str(exc))
        if auto:
            print(f"{provider.slug}: using {args.model}", file=sys.stderr)
        if args.fallback:
            run_with_fallback(provider, args)
            return
        direct = args.command in {"opencode", "hermes", "mini"}
        direct |= args.command == "codex" and provider.direct_responses
        direct |= args.command == "claude" and provider.direct_messages
        if args.show_routing and direct:
            print(f"[hlink] direct -> {route_id(provider, args.model)}", file=sys.stderr)
    args.func(provider, args)


if __name__ == "__main__":
    main()
