# Architecture

The project separates model decisions from evidence access and answer rendering. Providers may choose a question's scope or request an evidence action, but Python and the curated data store determine what the application can actually return. `query.py` remains the deterministic evidence-package CLI and database build/check utility; the agent-facing interface is the validated tool gateway in `agent_tools.py`.

```text
Curated JSON inputs ── query.py build ──► evidence.sqlite
 sources.json · facts.json · coverage.json     │
                                               │ read-only at runtime
                                               ▼
User → agent.py → agent_loop.py ↔ Apple or OpenAI provider
                         │       one structured decision at a time
                         ▼
                   agent_tools.py
      search_facts · get_fact · compare_facts
          get_source · coverage_gap
                         │
                         ├──► query.py retrieval/comparison functions
                         └──► parameterized SQLite access
                                               │
                                               ▼
                                      evidence.sqlite
                                               │ JSON observation
                                               ▼
agent_loop.py evidence ledger → validation → Python answer renderer → User
                         └── optional local JSONL trace
```

This replaces the initial design sketch in which `query.py` itself was the proposed agent entry point and the tool list had four functions. The current runtime has a separate five-tool agent contract; `coverage_gap` is a first-class tool, while `query.py` continues to serve direct deterministic queries, comparisons, validation and rebuilds.

## The implemented agentic loop

This is a real, running agent loop in `agent_loop.py`, not a future infrastructure proposal. Each turn begins with the current message, selected conversation scope and a compact ledger of evidence already observed in that conversation. The Python host starts the provider turn; the provider proposes exactly one next action—one of the five evidence tools or an answer selection. Python validates and executes a tool request, records the result in the evidence ledger, and returns that observation to the provider. The provider then decides whether to take another action, select an answer, request clarification through the supported answer path, or stop with an error. The cycle is:

**interpret → choose one action → validate and execute → observe → continue or finish**

The core guidance directs the model to search first unless the user names a fact/source ID or the relevant evidence is already in the ledger; it forbids outside knowledge, web fetching and invented IDs. Providers share the same Python `Provider` interface. A request cannot go straight from model-written prose to the user as a factual answer.

The model-facing contract and the evidence implementation are deliberately separate. `agent_tools.py` exposes five structured JSON operations: `search_facts`, `get_fact`, `compare_facts`, `get_source` and `coverage_gap`. It validates arguments and IDs, applies scope rules and returns observed records. It reuses `query.py` for fact lookup and compatible comparison, with parameterized read-only SQLite queries for bounded search, source inspection and coverage. The model never receives arbitrary SQL or direct access to the fixture files.

On Apple, the worker first extracts a structured intent/scope plan from the current message and selected state. Later steps choose one allowed action and fill only that action's argument schema. Python grounds place names in the user's words, retains selected periods and checks that every requested geography is covered before accepting an answer. This makes ambiguity and incomplete submarket comparisons visible rather than letting a fluent summary conceal them.

Python parses tool arguments strictly and validates them against the versioned contract. It rejects unknown tools, extra fields, malformed values, unobserved fact/source IDs and repeated successful identical searches. All database values are bound parameters; SQLite opens read-only with query-only mode enabled. The model has no SQL tool, general file-reading tool or network search tool.

A tool result is recorded in an evidence ledger. A final selection must refer to a fact, source, comparison or coverage observation that is already in that ledger or to an ID named by the user and then retrieved. Python validates the selection schema, verifies every reference, and renders the user-facing claim from the stored evidence. The provider cannot write the displayed value, source URL, locator or arithmetic into a free-text answer field. If selection is invalid, the provider receives bounded feedback and may retry within the step limit; otherwise the turn fails visibly. The loop allows at most 12 provider steps per turn by default (configurable from 1 to 20), limits each message to 4,000 characters, and caps a conversation at 200 evidence observations. A turn that uses its step budget returns an explicit message to narrow the request or start a new conversation; other provider and input-limit errors are surfaced to the caller.

Conversation state contains selected topics, publisher geographies, periods, entity and evidence references. It is in memory for one process. A new session starts empty. A null selection retains an existing field; an explicit change can narrow geography while retaining the dates. Ambiguous “there” references after multiple or no selected places trigger a question instead of a guess. `/state`, `/new` and `/quit` expose, clear and end the session.

Bare greetings such as “Hello” receive a deterministic welcome and example question from the Python host. This makes no model-generation or evidence-tool call and preserves all selected scope and observed evidence. The trace records `response_origin: host_greeting`, with no fabricated provider event or factual claim. A mixed request such as “Hello, show F01” follows the normal provider loop. A greeting alone does not demonstrate live model interaction.

## Five fixed evidence tools

| Tool | Purpose | What it returns |
|---|---|---|
| `search_facts` | Discover records by topic, metric, publisher geography, entity or observation dates | Bounded, ordered matching facts, provenance and truncation status |
| `get_fact` | Inspect one fact by ID | The complete fact and joined source metadata |
| `compare_facts` | Compare two observed fact IDs | Deterministic change and units, or an explicit refusal |
| `get_source` | Inspect a source by ID | Publisher, title, access qualifications and associated facts |
| `coverage_gap` | Check the requested scope against the fixture | Matching, missing or unresolved coverage, with available evidence |

The shared v0.1 schema and 17 golden examples are in `contracts/`. Time-series comparisons require compatible series, metric, unit and scope. A cross-section comparison is a separate explicit mode limited to an approved pair. Percentage points and basis points are computed from stored values; estimated relative change is identified as approximate. Forecasts, range values and incompatible denominators are not forced into a comparison.

A “no matching facts” result applies only to this supplied fixture. It never establishes that there is no market activity. If the request does not specify enough scope, the agent asks for clarification rather than broadening a publisher's geography or substituting a related metric.

For explicit quarter comparisons, the Apple adapter checks whether the retrieved evidence covers each requested period. When one period is missing, it requests a separate coverage check bound to the observed metric, geography and missing dates. A checked missing-period result can complete a partial answer together with the available fact cards. A matching result over the combined date range is insufficient to prove coverage of both quarters. The adapter also excludes already-loaded IDs from individual fact/source lookup choices.

Explicit month names replace the prior date window. Monthly vacancy/take-up change requests receive a granularity clarification: overlapping quarterly totals or quarter-end snapshots cannot supply monthly changes. Completely missing quarter pairs use explicit topic-wide coverage checks without inventing metric identifiers. Unresolved coverage is excluded from final answer selections and leads to clarification. Coverage catalogue notes remain inspectable in tool results and traces; the user-facing gap answer shows its requested scope and assessment.

## Provider boundary

The CLI, tool gateway, session state, schemas, evidence ledger, answer validator and renderer are provider-neutral. Provider selection is explicit and there is no automatic fallback.

- **OpenAI Responses** uses the Python standard library for HTTPS. It sends serial tool calls, passes observed results back under their call IDs and requests a final answer selection using a strict JSON schema. Requests set `store:false`. The exact model name and credentials come from the user’s local configuration.
- **Local Responses simulator** uses the same OpenAI provider and agent code but redirects the request to an HTTP server bound to `127.0.0.1`. It returns a fixed search/comparison/answer sequence for integration checks. `MODEL_API_ENDPOINT` accepts the official OpenAI endpoint or a loopback `/v1/responses` endpoint only; arbitrary hosts and insecure remote HTTP are rejected. Simulator traces explicitly set `live_model:false` and cannot pass the live-acceptance checker.
- **Apple Foundation Models** uses Apple's official Python SDK, `apple-fm-sdk`, for the on-device system model. Its Python host runs the schema adapter in an isolated worker. The adapter projects supported fields into Apple's generation schema, then the Python host validates the complete original tool and answer contracts. The worker receives only its required process environment, not OpenAI credentials. Input is checked against the smaller of the model’s exposed context size and 4,096 tokens, with requested output and a 256-token reserve; evidence is not silently truncated. Overflow, timeout and model errors stop the turn.
- **Scripted** is a separate offline mechanics demonstration with a fixed tool plan. It is useful for installing or inspecting the loop without a model, but it does not perform model inference or count as a live interaction.

All providers return the same decision shape to the Python host. Model choice does not change the evidence database or calculation rules. The local simulator verifies the HTTP/provider/agent integration, not the OpenAI-hosted endpoint, API credentials, account model access, quota or live model behavior. The configured OpenAI live profile and the Apple on-device profile remain separate execution paths.

## Application components

| Component | Responsibility |
|---|---|
| `agent.py` | CLI, provider selection, local environment loading, interactive commands and trace creation |
| `agent_protocol.py` | Shared provider decision types and strict answer-schema validation |
| `agent_loop.py` | Bounded turn loop, session state, evidence ledger, tool execution, answer checks and deterministic rendering |
| `providers.py` | OpenAI Responses adapter |
| `apple_provider.py`, `apple_worker.py` | Optional Apple SDK adapter, isolated model worker and provider-specific schema mapping |
| `agent_tools.py` | Five validated read-only tool functions |
| `contract_validation.py`, `contracts/` | Local contract validator, shared JSON schemas, comparison allowlist and golden examples |
| `agent_skills/` | Core policy plus market briefing, project history, coverage and Apple-specific generation guidance |
| `evidence/london_office_evidence_pack/` | Curated JSON inputs, SQLite database and deterministic query/rebuild utility |
| `scripts/`, `tests/`, `eval/` | Integrity checks, local simulated Responses endpoint, automated cases, trace/evidence evaluation and human review worksheets |
| `slides/` | Five-slide PPTX, PDF, source content, presenter notes and previews |

## Evidence lifecycle and extension boundary

The pack currently contains 39 facts, 20 source records and 10 coverage records. Curated `sources.json`, `facts.json` and `coverage.json` are the reviewable inputs; `evidence.sqlite` is the queryable runtime representation. `query.py build` validates and rebuilds that database. The build operation is an explicit maintenance step, separate from answering a user. The agent opens the existing database read-only and does not alter source records.

New evidence follows a deliberate path: identify and review a source, extract a scoped fact with its locator and status, update the curated JSON, run validation, then rebuild the database. Historical project replay is a local evaluation of already reviewed records. There is no live crawler, background polling, authenticated source connector or model-driven ingestion. Optional sources in the register retain access/qualification notes and do not count as integrated feeds.

This makes the initial four-step proposal concrete in the current implementation: `query.py` remains the deterministic data layer; `agent_tools.py` is the thin, structured gateway; `agent.py` and `agent_loop.py` provide the interactive provider-driven loop and conversation state; and evidence maintenance remains outside the runtime. The boundary keeps the Python assessment deliverable self-contained while allowing another host to implement the same logical tool contract later.

No service or database migration is required for the Python application. The supplied database is read-only at runtime and can be rebuilt from the checked JSON arrays using the included `query.py build` command. Rebuild validation refuses invalid provenance or incompatible records.

## Trace and privacy properties

A JSONL trace is created only when a conversation starts. It records the source commit and dirty state, code/fixture hashes, provider/model identity, turn state, tool arguments/results, supported answer references, errors and timing. It excludes API keys and raw model reasoning. Apple generation identifiers are local IDs, not Apple-issued response IDs. Traces can contain the user's prompts and retrieved evidence, so inspect them before sharing.

The loop rejects an answer after provider errors, invalid evidence references, context limits or tool-step exhaustion. It does not replace a failed turn with an offline answer. See [reproduction commands](REPRODUCING.md) for checks and examples.

## Recovery without changing the question

Clarification, missing fixture coverage, refusals and execution failures include a reason and one suggested supported question. `question_guidance.py` ranks alternatives from gateway-returned records and validates every suggested lookup or comparison through the same gateway. Suggestions are separate from factual answer claims: they do not enter the observed evidence ledger or change conversation scope. Ask the suggested question explicitly to select its geography and dates. An execution failure remains an error even when a verified alternative is offered.

For example, after `Canary Wharf vacancy in Q2 2026`, a follow-up asking whether City Core and City Fringe moved in the same direction retains vacancy and Q2. One quarter cannot establish a trend. The agent identifies the missing baseline and suggests a Core/Fringe Q1–Q2 question supported by two independently validated series. It does not silently replace Canary Wharf data or assume a baseline for the answer.

Compatible time-series comparisons across places can also produce a deterministic direction summary. Python groups only comparisons sharing a metric, unit and observation periods, reads their checked differences and renders same/opposite/unchanged movement before the evidence cards. Coverage gaps follow the supported answer. A secondary gap does not turn an answered comparison into a request to ask the same question again. The trace checker recomputes this summary from the recorded comparison observations.
