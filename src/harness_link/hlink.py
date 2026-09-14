import argparse
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from . import cli as provider_cli
from .providers import PROVIDERS
from .routing import show_route_status


ALIASES = {
    "claude-code": "claude",
    "mini-swe-agent": "mini",
    "antigravity": "agy",
}
HARNESS_NAMES = ("claude", "codex", "opencode", "hermes", "mini", "agy")
PROVIDER_HARNESSES = {"claude", "codex", "opencode", "hermes", "mini"}


def canonical_harness(name):
    return ALIASES.get(name, name)


def parser():
    root = argparse.ArgumentParser(
        prog="hlink",
        description="Run coding harnesses through one thin command-line interface",
    )
    root.add_argument("--version", action="version", version=f"hlink {__version__}")
    root.add_argument("harness", choices=[*HARNESS_NAMES, *ALIASES, "status"])
    root.add_argument("-p", "--prompt", help='run one task and exit; use "-" to read stdin')
    root.add_argument("-m", "--model", help="override model")
    root.add_argument("-y", "--yolo", action="store_true", help="use the harness native unattended mode")
    root.add_argument("-C", "--cwd", type=Path, help="run in this working directory")
    root.add_argument("--provider", choices=tuple(PROVIDERS), help="run through a Harness Link provider")
    root.add_argument("--fallback", choices=tuple(PROVIDERS), help="fallback provider if the primary provider fails")
    root.add_argument("--show-routing", action="store_true", help="show the actual routed model when it changes")
    return root


def split_argv(argv):
    if "--" not in argv:
        return argv, []
    index = argv.index("--")
    return argv[:index], argv[index + 1 :]


def resolve_prompt(prompt, stdin=None):
    if prompt != "-":
        return prompt
    return (sys.stdin if stdin is None else stdin).read()


def harness_args(harness, prompt=None, model=None, yolo=False, extra_args=None):
    extra_args = list(extra_args or [])
    options = []

    if model:
        options.extend(["--model", model])

    if yolo:
        options.append(
            {
                "claude": "--dangerously-skip-permissions",
                "codex": "--dangerously-bypass-approvals-and-sandbox",
                "opencode": "--auto",
                "hermes": "--yolo",
                "mini": "--yolo",
                "agy": "--dangerously-skip-permissions",
            }[harness]
        )

    options.extend(extra_args)

    if prompt is None:
        return options
    if harness == "claude":
        return [*options, "-p", prompt]
    if harness == "codex":
        return ["exec", *options, prompt]
    if harness == "opencode":
        return ["run", *options, prompt]
    if harness == "hermes":
        return [*options, "-z", prompt]
    if harness == "mini":
        return [*options, "-t", prompt]
    if harness == "agy":
        return [*options, "-p", prompt]
    raise ValueError(f"unsupported harness: {harness}")


def run_native(harness, args):
    executable = shutil.which(harness)
    if not executable:
        print(f"hlink: {harness} is not installed or not on PATH", file=sys.stderr)
        raise SystemExit(127)
    os.execvpe(executable, [executable, *args], os.environ.copy())


def run_provider(provider, harness, model, args, fallback=None, show_routing=False):
    if harness not in PROVIDER_HARNESSES:
        print(f"hlink: {harness} does not support --provider", file=sys.stderr)
        raise SystemExit(2)
    argv = [provider, harness]
    if model:
        argv.extend(["--model", model])
    if fallback:
        argv.extend(["--fallback", fallback])
    if show_routing:
        argv.append("--show-routing")
    if args:
        argv.extend(["--", *args])
    provider_cli.main(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    normalized, extra_args = split_argv(argv)
    root = parser()
    args = root.parse_args(normalized)

    if args.harness == "status":
        invalid = extra_args or args.prompt is not None or args.model or args.yolo or args.cwd or args.fallback or args.show_routing
        if invalid:
            root.error("status only accepts --provider")
        show_route_status(args.provider)
        return

    harness = canonical_harness(args.harness)
    prompt = resolve_prompt(args.prompt)

    if args.cwd:
        try:
            os.chdir(args.cwd.expanduser())
        except OSError as exc:
            print(f"hlink: cannot use working directory {args.cwd}: {exc}", file=sys.stderr)
            raise SystemExit(2)

    if args.fallback and not args.provider:
        print("hlink: --fallback requires --provider", file=sys.stderr)
        raise SystemExit(2)

    if args.show_routing and not args.provider:
        print("hlink: --show-routing requires --provider", file=sys.stderr)
        raise SystemExit(2)

    if args.provider:
        forwarded = harness_args(harness, prompt=prompt, yolo=args.yolo, extra_args=extra_args)
        run_provider(
            args.provider,
            harness,
            args.model,
            forwarded,
            fallback=args.fallback,
            show_routing=args.show_routing,
        )
        return

    forwarded = harness_args(
        harness,
        prompt=prompt,
        model=args.model,
        yolo=args.yolo,
        extra_args=extra_args,
    )
    run_native(harness, forwarded)


if __name__ == "__main__":
    main()
