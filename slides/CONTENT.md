# Engineering presentation content and presenter notes

The editable source is [the PowerPoint deck](london-office-monitoring.pptx). The [PDF](london-office-monitoring.pdf) and [page previews](previews/) show the same five slides. The narrative centres on the implemented Python engineering deliverable. The example calculation illustrates the evidence controls.

The notes below preserve the source pointers and qualifications included in the deck. The recorded live evaluation describes its selected Apple profile and source revision, rather than guaranteeing every future model invocation.

## 1. The delivered Python agent

London Office Monitoring

An evidence-controlled
Python agent

The model chooses the next evidence action.
Python checks its scope and renders the supported answer.

39 curated facts

5 fixed tools

1 standalone repo

### Presenter notes and sources

Engineering overview. The deliverable is a standalone Python CLI, a curated historical fixture, optional model adapters, evaluation tools and public documentation. Core operation uses Python 3.10+ and the standard library. Apple needs its optional SDK and a supported Mac. The fixture has 39 facts, 20 source records and 10 coverage records. It is selective research dated 27 October 2026. No live ingestion, vector database, graph infrastructure or model training is required. Repository: https://github.com/jiefengcheng/london-office-monitoring-public/tree/codex/publication-candidate . Architecture: docs/user/ARCHITECTURE.md at source commit 983c08bb07110a0ad5816c6f1e06f03373a4fe4e.

## 2. The agentic loop

The agentic loop

One structured decision at a time, with checked observations returned to the model

search_facts, get_fact, compare_facts, get_source, coverage_gap

Provider decision
Apple or OpenAI

Validated tool call
agent_tools.py

Read-only evidence
SQLite + query.py

Evidence ledger
observed results

Answer validation
Python renderer

Continue with the observation

Finish only with checked references

Session state retains topic, geography, period and entity. /new clears it.
The host exposes tool errors, unresolved scope and bounded-loop failures.

### Presenter notes and sources

The diagram summarises the implemented cycle in agent_loop.py. The selected provider proposes one tool action or an answer selection. The host checks arguments, runs the tool and records the result. A next provider step receives the observation and scoped state. A final answer selection must reference observed evidence and pass the strict schema and reference checks. Python renders values, calculations, source links, locators and qualifications. The dashed return denotes another provider step, rather than persistent autonomous execution. The default turn budget is 12 provider steps. CLI commands include /state, /new and /quit. Greetings have a local deterministic path. Apple uses the official Python SDK via an isolated worker. Source: agent_loop.py, agent_protocol.py, providers.py, apple_provider.py, apple_worker.py and docs/user/ARCHITECTURE.md at commit 983c08b.

## 3. Modules and repository structure

Modules and repository structure

| Repository path | Responsibility |
|---|---|
| `agent.py` / `agent_loop.py` | CLI, bounded loop, session state, evidence ledger and rendering |
| `providers.py` / `apple_provider.py` / `apple_worker.py` | OpenAI Responses or optional Apple SDK adapter with an isolated worker |
| `agent_tools.py` / `agent_protocol.py` / `contracts/` / `contract_validation.py` | Five typed tools, strict decisions, comparison safeguards and contract validation |
| `evidence/london_office_evidence_pack/` | Curated JSON, `evidence.sqlite` and `query.py` for deterministic access and rebuild |
| `scripts/` / `tests/` / `eval/` | Integrity checks, offline integration, six-case replay and human review |
| `docs/user/` / `agent_skills/` / `slides/` | User guides, task guidance, editable deck and presentation artifacts |

### Presenter notes and sources

This map follows the current repository rather than the earlier four-tool proposal. agent_tools.py exposes search_facts, get_fact, compare_facts, get_source and coverage_gap as validated JSON operations. It reuses query.py fact lookup and conservative time-series comparison and binds values in its additional SQLite queries. agent_protocol.py describes provider decisions and answer selections. contract_validation.py and contracts/ enforce the shared tool contract. agent_skills/ contains application-owned task guidance rather than a general plugin ecosystem. The fixture directory also contains chat_cases.json, which supplies prompts and reference expectations to evaluation tooling. Those expectations are not passed to the model. Source: repository tree and docs/user/ARCHITECTURE.md at commit 983c08b.

## 4. Evidence enforcement and provenance

Evidence enforcement and provenance

Curated inputs and controlled access

sources.json  /  facts.json  /  coverage.json

query.py build validates and rebuilds SQLite.
Runtime tools open the database read-only.

Each claim keeps its period, unit, geography,
status, source ID, URL, locator and caveats.

Compatible comparisons use Python arithmetic.
Missing coverage stays explicit.

A checked market comparison

7.4% to 7.2%

−0.2 pp / −20 bps

Central London vacancy
Q1 and Q2 2026
F01/S01 and F02/S02

Q3 is missing in this fixture.
A Q2–Q3 change is unsupported.

### Presenter notes and sources

The evidence record is a traceable account of what an identified source reported, preserving scope, date and evidential status. Unknown source dates remain unknown. The database build step stays outside the model runtime. Facts F01 and F02 report Central London vacancy 7.4% at 31 March 2026 and 7.2% at 30 June 2026. The checked difference is −0.2 percentage points or −20 basis points. S01: https://www.savills.co.uk/research_articles/229130/390337-0 . S02: https://www.savills.co.uk/research_articles/229130/393427-0 . Locator: Supply and vacancy, named geography paragraph. Publisher-defined geography is not interchangeable with another broker series. Missing Q3 facts do not imply zero market activity. Other designed constraints preserve a growth rate versus a rent level, forecast versus completion, ERV versus floor area and floor-level EPC versus whole-building rating. A completed, pre-let project does not establish speculative availability. See docs/user/EVIDENCE.md, docs/user/AGENTIC_QA.md and the immutable fixture records.

## 5. Reproduction and verification

Reproduction and verification

A new checkout can run the core checks

After provider setup in the installation guide:

python3 scripts/install.py --profile core
. .venv/bin/activate
python scripts/verify_python_cp6.py

Optional Apple model profile

python scripts/install.py --profile apple
python apple_provider.py
python agent.py --provider apple --interactive

Recorded live evaluation

18 / 18

answered prompts
6 conversations
20 tool calls, 0 errors

Proved with Apple profile.
Automated evidence checks
plus completed human review.

### Presenter notes and sources

Run from the public repository root. Python 3.10+ with venv/pip is required, with 3.12 recommended. Choose a provider before installing. Core has no third-party runtime dependency. The Apple profile requires compatible native macOS, Apple Intelligence readiness, Xcode/SDK setup and the pinned optional apple-fm-sdk. OpenAI uses the Responses API and local model/key settings. It has a separate live verification boundary. The local Responses simulator exercises real HTTP/provider/agent integration using fixed decisions and explicitly cannot pass live acceptance. No automatic fallback occurs. To reproduce six conversations: python scripts/replay_cases.py --host python --provider apple --output output/my-six-cases-01.jsonl . Then python scripts/verify_replay.py output/my-six-cases-01.jsonl --report output/my-six-cases-01-pending.json . Inspect the answers and complete a separate worksheet before running check_cp5_review.py. Reference expectations are not model input. The recorded independent revised Python CP7 run used source 9970e1f, with 18 answered turns, 20 tool calls and zero errors. The completed human worksheet accepted usefulness and qualifications in that bounded run. Later code changes and future model invocations need their own checks. Historical replay uses a disposable local database, deduplicates already reviewed records and preserves old forecasts, with no live source polling. Source: docs/user/REPRODUCING.md and docs/user/EVIDENCE.md at commit 983c08b. Public repository: https://github.com/jiefengcheng/london-office-monitoring-public .

## Editing and distribution

Edit the native diagram, table and text in the PPTX using presentation software. Export the edited deck to a five-page PDF and render its pages to replace the five PNG previews. Keep the content notes and source qualifications aligned with the slides. The manifest records hashes of the published PPTX, PDF, previews and this content document. Run `python scripts/verify_slides.py` from the repository root to check those files. Presentation authoring is separate from running the Python agent.
