# London Office Monitoring

Ask questions about London office-market evidence, compare compatible observations and follow a topic across a conversation. The Python agent returns cited facts, calculated comparisons and explicit gaps from a curated evidence pack.

The agent already uses a bounded **agentic loop**: it interprets the request, chooses one evidence action, observes the checked result, then continues, answers, clarifies or stops with an explicit error. Python executes the tools and validates and renders supported answers; the model does not write factual results directly.

The pack contains **39 facts from 20 source records**, covering market indicators, rents, projects, pre-lets, economic context and asset quality. It is a historical research sample dated 27 October 2026; it does not fetch live market updates.

[Installation](docs/user/INSTALLATION.md) · [Reproduce the examples](docs/user/REPRODUCING.md) · [Architecture](docs/user/ARCHITECTURE.md) · [Evidence and limits](docs/user/EVIDENCE.md) · [Presentation](slides/README.md)

## Get started

Before installing, use Python **3.10+** with `venv`/`pip` support (Python 3.12 is recommended). On macOS, Xcode may put its Python 3.9 on `PATH`; that version is unsupported. Check `python3 --version` first. If it reports less than 3.10, install/select Python 3.12. If another virtual environment is active, deactivate it, then use `python3.12` for the install command below. The repository is public; no GitHub account is needed to clone it.

```sh
git clone --branch codex/publication-candidate https://github.com/jiefengcheng/london-office-monitoring-public.git
cd london-office-monitoring-public
python3 scripts/install.py --profile core
. .venv/bin/activate
```

On Windows cmd.exe, activate with `.venv\Scripts\activate`. Core installation uses only Python's standard library. For a credential-free evidence example:

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

This provider runs on-device and needs no API key. See [Apple setup](docs/user/INSTALLATION.md#apple-intelligence) for requirements and troubleshooting.

**OpenAI API:** the default provider uses the Responses API. Copy `.env.example` to a local `.env` without replacing existing settings, then set `MODEL_NAME` to a model available to your account and set `MODEL_API_KEY` (or `OPENAI_API_KEY`). Run:

```sh
python agent.py --provider openai --interactive
```

Credentials stay local. There is no automatic provider fallback. The Apple profile has been exercised in independent live reviews; the API adapter has offline tests but has not completed end-to-end live verification.

## Try a conversation

```text
What changed in Central London between Q1 and Q2 2026?
Focus on City Core and City Fringe. Did they move in the same direction?
Give me two cited points for our meeting.
```

Answers include fact/source references, observation periods, units and qualifications. Use `/state` to inspect conversation context, `/new` to start again and `/quit` to exit. Conversation state lasts for the current process.

The [reproduction guide](docs/user/REPRODUCING.md) covers offline checks, a live interactive profile, the six-conversation evaluation and historical project replay. Model choices and wording can vary; inspect the returned evidence and calculations.

## Presentation and source

The [five-slide presentation](slides/README.md) is supplied as a PDF with page previews. The Python source, pinned optional dependencies, curated evidence inputs, tests and evaluation tools are included in this repository. Everything needed to run the Python agent is in the checkout; optional model setup is described above.

See the [delivery plan](docs/PLAN.md) for the project's longer-term direction. The runnable demo is Python-first; Apple Intelligence is optional and requires a supported local Mac.
