# London Office Monitoring

Ask questions about London office-market evidence, compare compatible observations and follow a topic across a conversation. The Python agent returns cited facts, calculated comparisons and explicit gaps from a curated evidence pack.

The agent already uses a bounded **agentic loop**: it interprets the request, chooses one evidence action, observes the checked result, then continues, answers, clarifies or stops with an explicit error. Python executes the tools and validates and renders supported answers; the model does not write factual results directly.

The pack contains **39 facts from 20 source records**, covering market indicators, rents, projects, pre-lets, economic context and asset quality. It is a historical research sample dated 27 October 2026; it does not fetch live market updates.

[Installation](docs/user/INSTALLATION.md) · [Reproduce the examples](docs/user/REPRODUCING.md) · [Architecture](docs/user/ARCHITECTURE.md) · [Evidence and limits](docs/user/EVIDENCE.md) · [Presentation](slides/README.md)

## Get started

The repository is public; no GitHub account is needed to clone it.

```sh
git clone --branch codex/publication-candidate https://github.com/jiefengcheng/london-office-monitoring-public.git
cd london-office-monitoring-public
cp .env.example .env
```

**Choose the provider path before running the installer or agent.** For live OpenAI, the endpoint is the Responses API at `https://api.openai.com/v1/responses`. Edit `.env` and set `MODEL_NAME` to a tool-calling/structured-output model available to your account and `MODEL_API_KEY` to your local API key. Leave `MODEL_API_ENDPOINT` blank for the official service; the local simulator sets its own loopback endpoint. Keep the key in `.env`; the file is gitignored. For Apple Intelligence, set `MODEL_PROVIDER=apple`; it is on-device and needs no API key. To run the Codex-only simulator first, leave model/key fields blank: the verification script supplies an ephemeral loopback endpoint and disposable dummy key. See [provider setup](docs/user/INSTALLATION.md#provider-setup) for details.

Before installing, use Python **3.10+** with `venv`/`pip` support (Python 3.12 is recommended). On macOS, Xcode may put its Python 3.9 on `PATH`; that version is unsupported. Check `python3 --version` first. If it reports less than 3.10, install/select Python 3.12. If another virtual environment is active, deactivate it, then use `python3.12` for the install command below. Core install has no third-party runtime dependencies:

```sh
python3 scripts/install.py --profile core
. .venv/bin/activate
```

On Windows cmd.exe, activate with `.venv\Scripts\activate`. For a credential-free evidence example:

```sh
python evidence/london_office_evidence_pack/query.py demo
```

The example reports Central London vacancy falling from **7.4% to 7.2%**, a change of **−0.2 percentage points / −20 basis points**, with source links. This is a deterministic evidence query. For interactive model conversations, choose a provider below.

<a id="install-and-run-cp4--apple-intelligence-by-default"></a>
<a id="install-and-run-cp4--optional-apple-intelligence"></a>

## Choose a model

**Apple Intelligence:** on a supported Apple silicon Mac with macOS 26.4+, a compatible Xcode/SDK and Apple Intelligence ready, install the optional official Python SDK:

```sh
python scripts/install.py --profile apple
python apple_provider.py
python agent.py --provider apple --interactive
```

This provider runs on-device and needs no API key. See [Apple setup](docs/user/INSTALLATION.md#apple-intelligence) for requirements and troubleshooting. The optional SDK profile is installed after selecting the Apple provider:

**OpenAI API:** the default provider uses the Responses API. After the setup step above, set `MODEL_NAME` to a model available to your account and set `MODEL_API_KEY` (or `OPENAI_API_KEY`) in `.env`. Run:

```sh
python agent.py --provider openai --interactive
```

Credentials stay local. There is no automatic provider fallback. The Apple profile has been exercised in independent live reviews.

## Verify the OpenAI pipeline without API credentials

Run the Codex-only local suite after installation:

```sh
python scripts/verify_codex_only.py --output-dir output/codex-local-suite-01
```

It runs the existing Python CP6 offline checks unchanged, then sends the real Python agent and OpenAI Responses HTTP client through a deterministic server bound to `127.0.0.1`. The simulated exchange requests vacancy evidence, calls the comparison tool, renders cited F01/F02 results, and checks that the resulting trace is rejected by the live-acceptance checker. It needs no API key, Apple SDK, or external network access.

**What this proves:** the local HTTP request/response format, provider parsing, tool-call loop, evidence enforcement, Python answer rendering and trace boundaries work together. **What it does not prove:** that `api.openai.com` is reachable, that your credentials or model are enabled, that quota is available, or that a live model will make useful decisions. The simulator returns fixed decisions and is not model inference. For live OpenAI behavior, run the [configured OpenAI profile](docs/user/REPRODUCING.md#reproduce-an-interactive-provider-profile); Apple Intelligence remains a separate on-device profile.

## Try a conversation

You can start with `Hello` for a welcome and an example question. Bare greetings are handled locally; evidence questions then use your selected provider.

```text
What changed in Central London between Q1 and Q2 2026?
Focus on City Core and City Fringe. Did they move in the same direction?
Give me two cited points for our meeting.
```

Answers include fact/source references, observation periods, units and qualifications. Use `/state` to inspect conversation context, `/new` to start again and `/quit` to exit. Conversation state lasts for the current process.

When a requested comparison period is missing from the fixture, the agent can return the available observations alongside a scoped coverage gap. For example, Q2 versus Q3 2026 in Central London provides Q2 records and identifies the missing Q3 evidence; it cannot calculate that change from the supplied fixture.

If both requested quarters are missing, the answer identifies the checked gaps for those dates and topics. A monthly office-market comparison receives an explanation of the quarterly data's limits, rather than reusing prior dates or treating a quarterly total as a monthly observation. The fixture contains selected historical evidence, not a complete market time series.

The [reproduction guide](docs/user/REPRODUCING.md) covers offline checks, a live interactive profile, the six-conversation evaluation and historical project replay. Model choices and wording can vary; inspect the returned evidence and calculations.

## Presentation and source

The [five-slide presentation](slides/README.md) is supplied as a PDF with page previews. The Python source, pinned optional dependencies, curated evidence inputs, tests and evaluation tools are included in this repository. Everything needed to run the Python agent is in the checkout; optional model setup is described above.

See the [delivery plan](docs/PLAN.md) for the project's longer-term direction. The runnable demo is Python-first; Apple Intelligence is optional and requires a supported local Mac.
