# Harness Link

Thin provider adapters for coding harnesses.

Harness Link aims for OpenRouter Ori-like ergonomics: configure a provider once, then launch the coding agent you already use. The agent keeps its normal tools, permissions, sessions, skills, and UI. Harness Link only adapts provider configuration and protocol where necessary.

```sh
export ALBERT_API_KEY=...
albert opencode
albert mini

export NVIDIA_API_KEY=...
nim opencode
nim mini

export OPENROUTER_API_KEY=...
orfree opencode
orfree mini
```

Native provider configuration is preferred. A loopback compatibility bridge is used only when a harness requires a protocol the provider does not expose directly.

## Unified harness CLI

`hlink` adds a thin harness-first interface while keeping the existing provider-first commands unchanged.

```sh
hlink claude -p "review the code" --yolo
hlink codex -p "review the code" --yolo
hlink opencode -p "review the code" --yolo
hlink hermes -p "review the code" --yolo
hlink mini -p "review the code" --yolo
hlink agy -p "review the code" --yolo
```

The normalized surface is deliberately small:

```text
-p, --prompt TEXT       run one task and exit; use "-" to read stdin
-m, --model MODEL       override model
-y, --yolo              use the harness native unattended mode
-C, --cwd PATH          run in this working directory
    --provider NAME     run through a Harness Link provider
--                      pass all remaining arguments to the native harness
```

Without `-p`, the selected harness keeps its normal interactive behavior. `--yolo` maps to the closest native unattended/auto-approval mode; exact permission semantics remain harness-specific.

```sh
hlink claude
hlink codex -C ~/work/repo -p "fix the failing tests" -y
git diff | hlink hermes -p -
hlink agy -p "review this" -- --effort high
```

Provider selection composes with the same interface:

```sh
hlink opencode --provider albert -p "review the code" -y
hlink hermes --provider nim -m openai/gpt-oss-20b -p "fix the tests" -y
hlink mini --provider orfree -p "review this repository" -y
```

Provider overrides currently apply to `claude`, `codex`, `opencode`, `hermes`, and `mini`. `agy` uses its native provider configuration. Long aliases `claude-code`, `mini-swe-agent`, and `antigravity` are accepted, but the short harness names are canonical.

The original interfaces remain supported for backward compatibility:

```sh
harness-link albert opencode
albert opencode
nim hermes
orfree mini
```

## Quota

`hlink quota` shows a boxed summary of the remaining subscription quota exposed by the installed harnesses, reusing their existing local authentication. Use `--json` for machine-readable output.

```sh
hlink quota
hlink quota claude codex
hlink quota antigravity opencode
hlink quota --json
```

Current sources:

- Claude: Claude Code/OpenCode OAuth and Anthropic's OAuth usage endpoint.
- Codex: the installed Codex `app-server` `account/rateLimits/read` RPC, so Codex keeps ownership of its credential store.
- Antigravity: the native `agy -p /usage --output-format json` command.
- OpenCode: OpenCode Go's usage endpoint and, when present, the Zen Console session in OpenCode's local state database.

No additional API keys are required. Missing or unauthenticated harnesses are reported as unavailable instead of preventing the other checks. OpenCode Zen's Console routes are not a stable public API and may change.

## Install

From the repository:

```sh
curl -fsSL https://raw.githubusercontent.com/sileod/harness-link/main/install.sh | sh
```

The first PyPI release is prepared as `0.3.0`. Once published:

```sh
uv tool install harness-link
# or
pipx install harness-link
```

Harness Link itself requires Python 3.9 or newer. Individual harnesses have their own requirements; current mini-SWE-agent requires Python 3.10 or newer.

## Providers

### Albert

The French government's Albert API uses `ALBERT_API_KEY` and defaults to `deepseek-v4-flash`.

```sh
albert models
albert opencode
albert hermes
albert mini
albert codex
albert claude
```

OpenCode, Hermes, and mini-SWE-agent connect directly. Codex and Claude Code require the experimental local LiteLLM protocol bridge because Albert currently exposes Chat Completions rather than their native wire APIs.

```sh
python -m pip install 'litellm[proxy]'
```

Configuration:

```sh
export ALBERT_MODEL=deepseek-v4-flash
export ALBERT_BASE_URL=https://albert.api.etalab.gouv.fr/v1
```

### NVIDIA NIM

NVIDIA's hosted API uses `NVIDIA_API_KEY` and defaults to `openai/gpt-oss-120b`.

```sh
nim models
nim opencode --model openai/gpt-oss-20b
nim hermes --model openai/gpt-oss-20b
nim mini --model openai/gpt-oss-20b
nim codex --model openai/gpt-oss-20b
nim claude --model openai/gpt-oss-20b
```

OpenCode, Hermes, and mini-SWE-agent connect directly. Codex and Claude Code use the experimental local bridge for the hosted Integrate API.

Configuration:

```sh
export NIM_MODEL=openai/gpt-oss-120b
export NIM_BASE_URL=https://integrate.api.nvidia.com/v1
```

### OpenRouter free-only

`orfree` is intentionally not a general OpenRouter wrapper. Use Ori for arbitrary or paid OpenRouter models. Harness Link's OpenRouter backend is constrained to free routes.

By default, `orfree` asks the OpenRouter Models API for tool-capable models sorted by weekly popularity, requires both prompt and completion prices to be zero, and selects the first `:free` model. The result is cached for five minutes. If discovery is unavailable, it falls back to `openrouter/free`.

```sh
export OPENROUTER_API_KEY=...

orfree models                    # free tool-capable models, most popular first
orfree opencode                  # automatically chosen free model
orfree hermes
orfree mini
orfree codex
orfree claude
```

Pin a free model when desired:

```sh
orfree opencode --model minimax/minimax-m3:free
orfree mini --model minimax/minimax-m3:free
```

Paid model IDs are rejected. `ORFREE_MODEL` can pin a `:free` model globally, and `ORFREE_CACHE_TTL=0` forces fresh discovery on every launch.

Unlike Albert and hosted NIM, OpenRouter already exposes Responses and Anthropic Messages APIs, so `orfree codex` and `orfree claude` connect directly with no compatibility proxy.

## mini-SWE-agent

mini-SWE-agent already uses LiteLLM as a client library, so Harness Link does not run a proxy for it. It supplies a small provider override, keeps mini's normal configuration, disables first-run provider setup, and passes the API key only in the child process environment.

Install mini separately:

```sh
uv tool install mini-swe-agent
# or
pipx install mini-swe-agent
```

Then:

```sh
albert mini -t "fix the failing tests"
nim mini --model openai/gpt-oss-20b -t "implement this feature"
orfree mini -t "review and improve this repository"
```

User `-c/--config` files are preserved and merged before Harness Link's final provider override.

## Isolated execution

Spawn integration is optional. It provides disposable local Docker sandboxes and remote machines while keeping the same provider commands.

```sh
cd my-project
albert spawn opencode sandbox
nim spawn opencode sandbox
orfree spawn opencode sandbox
```

For `sandbox`, the current directory is mounted read-write at `/workspace`. Edits and generated files persist on the host while the rest of the container is disposable.

Use another workspace explicitly:

```sh
albert spawn hermes sandbox --workspace ~/work/experiment
```

Remote execution keeps Spawn's normal command shape:

```sh
albert spawn opencode gcp
nim spawn hermes hetzner --fast
orfree spawn codex sprite
```

Albert and NIM patch a pinned Spawn revision to use their providers. `orfree` keeps Spawn's native OpenRouter integration and only adds the persistent local sandbox workspace behavior. Spawn support requires `git` and Bun.

## Design

Harness Link stays deliberately small:

1. native harness configuration when possible;
2. a tiny provider adapter when configuration differs;
3. a loopback bridge only when protocol translation is unavoidable;
4. no hidden provider fallback;
5. `orfree` never intentionally selects a paid OpenRouter route.

Arguments not consumed by Harness Link are passed through to the selected harness. The `hlink` frontend requires an explicit `--` before native-only arguments so misspelled normalized options fail early instead of being silently forwarded.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m py_compile src/harness_link/*.py bin/harness-link bin/hlink bin/harness-link-spawn bin/albert bin/albert-spawn bin/nim bin/nim-spawn bin/orfree bin/orfree-spawn
python -m build
```

## Release

PyPI publishing is configured through GitHub Actions Trusted Publishing. Create the `harness-link` project (or pending publisher) on PyPI and trust:

- owner: `sileod`
- repository: `harness-link`
- workflow: `release.yml`
- environment: `pypi`

Publishing a GitHub Release then builds the sdist/wheel and publishes them through OIDC; no PyPI API token is stored in the repository.
