# Installation

Run commands from the repository root. Use the [candidate clone command](../../README.md#get-started), then record `git rev-parse HEAD` if you intend to compare results with another machine.

## Python core

Install Python 3.10 or later with venv/pip support; Python 3.12 is recommended. Check the interpreter you actually run:

```sh
python3 --version
python3 scripts/install.py --profile core
```

Expected: `PASS | installation | profile=core`, exit 0. No third-party runtime packages, Apple SDK, ResearchWorkspace or signing certificate are required for this profile. Activate the environment:

| Terminal | Command |
|---|---|
| macOS/Linux shell | `. .venv/bin/activate` |
| Windows cmd.exe | `.venv\Scripts\activate` |

The installer preserves an incomplete or incompatible environment under `output/venv-backups/` before creating a replacement. A reused environment may retain optional packages; a fresh clone gives the clearest baseline.

## Apple Intelligence

Apple is an optional on-device provider. The official `apple-fm-sdk` package calls Apple’s Foundation Models framework from Python; the adapter does not require a separate application-level Swift bridge. Requirements:

- Native macOS on an Apple Intelligence-compatible Apple silicon Mac.
- macOS 26.4+ for the adapter's token/context APIs, with Apple Intelligence enabled and its system model downloaded.
- A compatible Xcode/SDK with its local setup and agreements completed.
- Python 3.10+; Xcode's Python 3.9 is insufficient. Select your installed Python 3.12 explicitly if necessary.

```sh
python scripts/install.py --profile apple
python apple_provider.py
python agent.py --provider apple --interactive
```

The optional lock files pin Apple's official `apple-fm-sdk==0.2.1` and its build dependencies. The availability probe should return `availability: available` and `execution: on_device`, exiting 0. An installation alone does not establish model readiness. The logical model identifier is `apple.system.default`; Apple manages the underlying model through system updates.

Run this directly in macOS. A Linux container or remote Linux terminal cannot use the host Mac's Foundation Models framework. If setup fails, check `uname -s`, `uname -m`, `sw_vers`, the Python version and the probe output from the same terminal. The installer does not upgrade macOS/Xcode or enable/download Apple Intelligence.

The reviewed environment includes macOS 27.0.1 arm64, Python 3.12.16, Xcode/SDK 27.2 and SDK package 0.2.1. The system model is named `apple.system.default`; its weight revision is managed by macOS rather than pinned by this repository. Older supported versions and other machines need their own run; a minimum version is not a claim that every such configuration was tested.

## OpenAI API

The core provider is `openai`. Copy `.env.example` to `.env` if it does not exist, then edit it locally:

```dotenv
MODEL_PROVIDER=openai
MODEL_NAME=your_available_model_id
MODEL_API_KEY=your_local_api_key
```

Use a model available to your account that supports Responses tool calling and structured outputs. No model name or key is supplied. `OPENAI_API_KEY` can replace `MODEL_API_KEY`.

```sh
python agent.py --provider openai --interactive
```

The selected provider receives the user message, scoped conversation context and retrieved evidence. Apple runs locally; the OpenAI route sends its requests to the API. Keys are not part of the evidence or trace output. Missing configuration stops before a request; provider errors remain visible. Check local account access/quota if an API request is refused. This adapter is covered by offline wire tests; live end-to-end verification remains outstanding.

## Configuration and output

CLI `--provider` overrides environment settings; OS environment values override `.env`. Existing local preferences are preserved. There is no automatic fallback to another provider or to the offline script.

`/state` displays current context; `/new` clears it. State is in memory and is not restored after restarting the program. Traces are created under ignored `output/`; they include user messages and evidence, so review them before sharing. To choose a trace path:

```sh
python agent.py --provider apple --interactive --trace output/my-first-conversation.jsonl
```

Use a new filename for each run; existing traces are preserved. To run without a model or credentials, follow the [offline examples](REPRODUCING.md#offline-examples).

## Troubleshooting and precedence

The installer needs Python with `venv`/`ensurepip` support. Check both the selected Python and the environment it creates. On macOS, Xcode may put Python 3.9 first on `PATH`; choose an installed Python 3.12 explicitly, for example:

```sh
python3.12 --version
python3.12 scripts/install.py --profile core
. .venv/bin/activate
python --version
```

The script refuses Python below 3.10 before changing the environment. If `.venv` is incomplete or too old, it preserves the directory before creating a replacement. Core installation does not contact package registries; the Apple profile installs the hash-pinned packages in its separate locks.

Provider precedence is: explicit `--provider`, then operating-system environment variables, then supported values in `.env`, then the `openai` default. The `.env` reader accepts simple unique `KEY=VALUE` lines; it does not execute shell code. Copy without overwriting prior local settings:

```sh
cp -n .env.example .env
```

Apple ignores OpenAI model/key settings. A missing API model or key returns a clear setup error before network access. Neither provider is silently substituted when unavailable.
