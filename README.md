# Weasley Assistant

![Weasley Assistant: local-first, transparent, extensible](https://raw.githubusercontent.com/potatohoney-p/weasley-assistant/main/assets/weasley-assistant-hero.png)

Weasley Assistant is a readable, local-first AI assistant for developers who want to own the
agent loop, tools, memory, gateways, and evaluation harness. Its public command and Python
package use the `weasley-assistant` / `weasley_assistant` names throughout.

## Features

- **Transparent agent loop:** model calls, tool results, iteration limits, and traces are explicit.
- **Layered memory:** SQLite-backed semantic and episodic memory plus file-based procedural skills.
- **Local dashboard:** inspect conversations, tools, memory, graph routes, traces, and evaluation.
- **Multiple gateways:** terminal, Telegram, Discord, WhatsApp, and optional local voice.
- **Model choice:** Anthropic, OpenAI, Gemini, DeepSeek, MiniMax, Kimi, GLM, OpenRouter, and
  compatible endpoints.
- **Safe extensibility:** opt-in Apple/Google integrations, read-only GitHub tools, MCP connectors,
  and deterministic regression tests.
- **Operations:** JSONL tracing, deterministic evaluation, optional LLM judging, and release gates.

## Requirements

- Python 3.11 or newer
- An API key for the provider you select
- Optional platform dependencies for voice and Apple integrations

## Installation

From PyPI:

```bash
python -m pip install weasley-assistant
```

For development:

```bash
git clone https://github.com/potatohoney-p/weasley-assistant.git
cd weasley-assistant
python -m venv .venv
python -m pip install -e ".[dev]"
```

Copy the environment template, add one provider key, and start the CLI:

```bash
cp .env.example .env
weasley-assistant
```

Common commands:

```bash
weasley-assistant dashboard
weasley-assistant brief
weasley-assistant gather
weasley-assistant voice
weasley-assistant skill install <skill-url>
```

`make run`, `make dashboard`, `make eval`, and the other Make targets are equivalent shortcuts.

## Configuration

Runtime state defaults to `~/.weasley/assistant`. Set configuration with the
`WEASLEY_ASSISTANT_*` namespace:

```dotenv
WEASLEY_ASSISTANT_PROVIDER=anthropic
WEASLEY_ASSISTANT_MODEL=
WEASLEY_ASSISTANT_SMALL_MODEL=
WEASLEY_ASSISTANT_HOME=~/.weasley/assistant
WEASLEY_ASSISTANT_MAX_ITERATIONS=10
WEASLEY_ASSISTANT_MAX_TOKENS=8192
WEASLEY_ASSISTANT_HISTORY_TURNS=12
```

Provider credentials keep their standard names, such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
or `GEMINI_API_KEY`. See [`.env.example`](.env.example) for all gateways and optional backends.

### Weasley namespace

Configuration, documentation, dashboard writes, and package imports use the
`WEASLEY_ASSISTANT_*`, `~/.weasley/assistant`, and `weasley_assistant` namespaces exclusively.

### MCP connectors

Install the optional dependency and create `~/.weasley/assistant/mcp.json`:

```bash
python -m pip install "weasley-assistant[mcp]"
```

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/safe/path"],
      "env": {}
    }
  ]
}
```

The file is validated against [`weasley_assistant/tools/mcp.schema.json`](weasley_assistant/tools/mcp.schema.json) before a
connector thread starts. Server failures are isolated, and all sessions and the event-loop thread
are closed when the assistant is rebuilt or stopped.

## Project structure

| Area | Location |
|---|---|
| Public package and CLI | [`weasley_assistant/`](weasley_assistant) |
| Agent loop and providers | [`weasley_assistant/loop/`](weasley_assistant/loop) |
| Memory | [`weasley_assistant/memory/`](weasley_assistant/memory) |
| Tools and MCP | [`weasley_assistant/tools/`](weasley_assistant/tools) |
| Gateways | [`weasley_assistant/gateway/`](weasley_assistant/gateway) |
| Dashboard and evaluation ops | [`weasley_assistant/ops/`](weasley_assistant/ops), [`evals/`](evals) |
| Procedural skills | [`skills/`](skills) |

## Security

- The dashboard binds to `127.0.0.1` by default.
- Runtime state, OAuth material, tokens, and local environment files are git-ignored.
- Discord server channels are deny-by-default until explicitly allowlisted.
- WhatsApp webhooks require signature verification.
- GitHub access is read-only by construction.
- MCP configuration is schema-validated before subprocesses start; each connector is isolated.
- Tool access and third-party gateways can incur API costs or mutate external systems. Enable only
  the integrations you understand and scope credentials to the minimum permissions required.

Please report vulnerabilities privately as described in [`SECURITY.md`](SECURITY.md). Do not put
credentials or exploit details in a public issue.

## Testing

```bash
python -m pytest -q evals/deterministic
python -m ruff check weasley_assistant evals scripts
```

Judge-based evaluations are optional and require provider credentials:

```bash
python -m pytest -q evals/judge
```

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md), add deterministic tests for behavior changes, and keep
optional integrations fail-safe when dependencies or credentials are absent. Issues and pull
requests are tracked at
[`potatohoney-p/weasley-assistant`](https://github.com/potatohoney-p/weasley-assistant).

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
