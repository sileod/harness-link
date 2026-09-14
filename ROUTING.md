# Routed model visibility

Fallback routing uses a local LiteLLM bridge. The harness still sees the synthetic routed model (`harness-link-primary`) so Claude Code, Codex, OpenCode, Hermes, and mini-SWE-agent keep a stable configured model throughout the session.

Harness Link separately records which provider/model actually served the latest bridged request.

## Show route changes

Add `--show-routing` to a provider-backed harness command:

```sh
hlink codex --provider inferx --fallback albert --show-routing -p "review the code"
```

When the primary serves a request:

```text
[hlink] primary -> inferx/deepseek-v4.1-flash
```

If LiteLLM falls back:

```text
[hlink] fallback -> albert/deepseek-v4-flash
```

Messages are printed only when the selected route changes, so normal harness output is not repeated for every completion.

The provider-first commands expose the same flag:

```sh
inferx codex --fallback albert --show-routing
inferx claude --fallback albert --show-routing
```

For direct, non-bridged integrations, `--show-routing` reports the configured provider/model once because there is no router involved.

## Query the latest route

Harness Link stores the latest observed route under the XDG cache directory, normally:

```text
~/.cache/harness-link/routes/<provider>.json
```

Query it without parsing the file:

```sh
hlink status --provider inferx
# albert/deepseek-v4-flash (fallback from inferx/deepseek-v4.1-flash)
```

or with the provider-first command:

```sh
inferx status
```

`hlink status` without `--provider` shows all recorded provider routes.

The status file is informational and contains provider/model routing metadata only, never API keys.

## Design

Route observation does not rewrite the model field returned to the harness. The LiteLLM deployment-success hook reports the real deployment attempt out of band, and Harness Link maps that deployment back to the configured provider and model. This avoids changing harness session/model identity while still making fallback behavior visible.
