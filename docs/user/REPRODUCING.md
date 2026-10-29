# Reproduce the demo

The public repository includes the runnable source, curated evidence pack, tests, evaluation code and five-slide PDF. Use the [installation guide](INSTALLATION.md) from a fresh clone, record `git rev-parse HEAD` and run commands from the repository root. Core operation needs no other workspace, signing identity or third-party runtime package.

Choose unused names under `output/` for each new run. Reusing a disposable historical-replay database changes its initial state; it is not a fresh reproduction.

## Offline examples

These need no model, SDK or API key:

```sh
python scripts/verify_foundation.py
python evidence/london_office_evidence_pack/query.py demo
python agent_tools.py get_fact --fact-id F01
python agent_tools.py compare_facts --fact-id-a F01 --fact-id-b F02
python agent_tools.py coverage_gap --request 'Canary Wharf vacancy in Q2 2026'
```

Expected: integrity checks exit 0; the demo/comparison gives Central London vacancy 7.4% → 7.2%, −0.2 percentage points / −20 basis points; F01 retains S01 provenance; the coverage result limits its absence statement to the fixture. The foundation check verifies the included fixture records and rebuilds a temporary database from their JSON inputs.

For a deliberately incompatible comparison:

```sh
python agent_tools.py compare_facts --fact-id-a F01 --fact-id-b F07
```

Expected: `COMPARISON_REFUSED`, exit **2**. Other examples above exit 0. This refusal is intentional.

An explicitly scripted conversation can demonstrate the loop mechanics without a model:

```sh
python agent.py --provider scripted --case market_change --trace output/offline-example-01.jsonl
```

It is labelled offline and follows a fixed tool plan; it does not demonstrate model inference. For a live provider run, the same loop cycles through provider decisions and checked tool observations until it selects an evidence-backed answer or reaches a clarification/error boundary.

To inspect that cycle, open the generated JSONL trace and follow each turn's `provider_step`, `tool_result` and `turn_end` events. A multi-step turn shows repeated provider steps and tool results; the ending event records the rendered answer and its supported claim references. Python renders that answer from the recorded evidence. The six-case evaluation below exercises this live loop over complete conversations; it checks evidence references and calculations, while the human worksheet separately judges whether the responses and qualifications are useful.

## Offline checks

```sh
python scripts/verify_python_cp6.py
python scripts/verify_slides.py
```

Expected: exit 0 for both. The first runs installation-boundary, agent, tool and historical-replay checks without model generation. The second checks the published PDF and five preview image hashes. These offline checks do not judge live answer quality or the business presentation.

## Codex-only simulated OpenAI endpoint

This adds a real local HTTP round trip without changing the existing CP6, CP5, CP4 or gateway verification commands. It uses the actual `agent.py` OpenAI provider and runs a deterministic Responses-compatible simulator bound only to `127.0.0.1`. No real key, external API request, Apple SDK or model inference is involved.

Run the combined Codex-only suite from a fresh clone after Python installation:

```sh
python scripts/verify_codex_only.py --output-dir output/codex-local-suite-01
```

The suite first runs `python scripts/verify_python_cp6.py` exactly as before. It then launches the agent with a disposable local model/key, exercises three HTTP exchanges (search tool call, comparison tool call, final structured answer), and runs the existing trace checker. Expected: exit 0, `PASS | Codex-only local suite`, an answer showing Central London vacancy from 7.4% to 7.2% and −20 basis points, and a simulator trace classified as offline. The simulated trace must fail `--require-live`; that refusal is a passing boundary check. Inspect `simulated-openai.jsonl`, `agent-console.txt`, `trace-check.txt`, `live-boundary-check.txt` and `simulator-report.json` in the chosen output directory.

The result proves that the local HTTP transport, Responses payload parsing, provider/agent loop, evidence tool execution, constrained answer selection, Python rendering and trace checks work together. The simulator sends fixed decisions and does not infer from the prompt. It does **not** prove that the OpenAI-hosted endpoint is reachable, that a key/model is authorized, that quota is available or that a live model will behave correctly. The trace checker explicitly refuses to treat this simulation as live acceptance.

To check the service and model you actually intend to use, configure a real local OpenAI key and available model in `.env`, leave `MODEL_API_ENDPOINT` blank, and use the live OpenAI profile below. Keep the Apple Intelligence probe/profile and the existing six-case Python replay as separate gates; the simulator does not replace them.

### Keep the three provider paths separate

| Path | Prerequisite | Interactive-profile command | What it checks |
|---|---|---|---|
| Codex-only simulated OpenAI | Core Python install; no real key/model | `python scripts/verify_codex_only.py --output-dir output/codex-local-suite-01` | Existing offline gates plus local HTTP/provider/agent integration; explicitly non-live |
| Configured OpenAI | `MODEL_NAME` and a valid `MODEL_API_KEY` in `.env`; `MODEL_API_ENDPOINT` blank | `python scripts/run_python_cp6_live.py --provider openai --output-dir output/openai-live-01` | Real Responses API access and the existing three-session provider profile |
| Apple Intelligence | Install `python scripts/install.py --profile apple`; `python apple_provider.py` reports available/on-device | `python scripts/run_python_cp6_live.py --provider apple --output-dir output/apple-live-01` | On-device model access and the same three-session provider profile |

For the six-case Python replay, keep the same evaluator and manual worksheet flow for either live provider; select `openai` or `apple` explicitly and use a fresh output basename:

```sh
python scripts/replay_cases.py --host python --provider openai --output output/openai-six-cases-01.jsonl
python scripts/verify_replay.py output/openai-six-cases-01.jsonl --report output/openai-six-cases-01-pending.json
```

For Apple, replace `openai` and the two basename stems with `apple`. Complete the same `check_cp5_review.py` worksheet steps documented below. The Codex-only simulator neither changes nor substitutes any live-provider, six-case, manual-review or historical-replay checkpoint.

## Reproduce an interactive provider profile

For an opening-interaction smoke check, start `python agent.py --provider apple --interactive` (or select `openai`), enter `Hello`, then `Show fact F01 and its original source.`, and finally `/quit`. Expected: a local welcome first, followed by a provider-driven F01/S01 answer with 7.4% and provenance. The greeting must preserve state, create no tool/model-generation event, and leave the next evidence request usable. A greeting alone is not a live-provider check. The full three-session profile below remains the broader verification.

Also try `What changed in Central London between Q2 and Q3 2026?` with Apple. Expected: available Q2 vacancy (F02, 7.2%) and take-up (F08, 2.4 million sq ft), plus separate Q3 fixture gaps for those metrics. No Q2-to-Q3 change is supported. The turn should finish without repeated fact lookups or step-budget exhaustion. This variation supplements the original six-case replay.

In the same session, follow with `What changed in Central London between May and July 2026?`, then the Q3/Q4 comparison for 2026 and for 2025. Expected: the monthly request explains that quarterly office figures cannot establish a monthly change, with no reused numeric claims. Each Q3/Q4 request returns scoped vacancy/leasing fixture gaps for its actual year; it must not return the previous Q2 figures, unresolved-coverage metadata or a catalogue dump. All turns should complete without provider errors or exhausted step budgets.

After installing a live provider, create a new local run directory with the supplied driver:

```sh
python scripts/run_python_cp6_live.py --provider apple --output-dir output/my-live-profile-01
```

The driver records three sessions and checks provider identity, evidence support, scope continuity, clarification, fixture-gap handling, reset behavior and direct lookups. It writes JSONL traces, console output and a run receipt under the chosen directory. Read the answers and traces yourself; the automated checks do not supply a human quality judgment. For a separately configured API model, pass `--provider openai`; that route needs your own model/key and has not been live-verified by the recorded Apple profile.

## Run six new conversations

After installing the chosen live provider, run the complete Apple profile shown in the preceding section or set up your API profile. For a fresh six-case evaluation, first verify Apple with `python apple_provider.py`; the API must have a working local model/key. Then run:

```sh
python scripts/replay_cases.py --host python --provider apple --output output/my-six-cases-01.jsonl
python scripts/verify_replay.py output/my-six-cases-01.jsonl --report output/my-six-cases-01-pending.json
```

The runner uses the original six cases / 18 prompts, resets between conversations and preserves raw traces and console output. Expected: all turns answered, no unresolved errors, runner exit 0; the evaluator returns `PASS_EVIDENCE_CHECKS_ONLY`, exit 0. The model does not receive reference answers. A provider error, exhausted step budget or missing required evidence fails the run.

Open each answer/trace and the generated worksheet. Inspect the facts, arithmetic, citations, requested scope and each listed qualification. Save a separate completed worksheet as `output/my-six-cases-01-reviewed.json`, retaining the pending original. Record the actual reviewer/date and evidence for every judgment. Then:

```sh
python scripts/check_cp5_review.py output/my-six-cases-01.jsonl output/my-six-cases-01-reviewed.json
```

Only a complete, supported review can exit 0. Evidence checks alone do not judge semantic usefulness. For example, the macro response may offer qualified indicator cards rather than rank actions or invent a causal story.

For the separately configured API route, use `--provider openai` and a new output basename. It needs your own model/key and genuine access; the recorded Apple result does not establish API live behavior. Model planning/wording and tool counts may vary between runs.

## Replay historical project monitoring

Use a new state path:

```sh
python scripts/replay_refresh.py --state output/my-project-history-01.sqlite --reset
python scripts/replay_refresh.py --state output/my-project-history-01.sqlite --repeat-reviewed-source
```

Expected: the labelled historical replay first adds 2 observations, 1 finding and 1 milestone. Repeating the reviewed source adds 0/0/0. Old forecast facts F23/F25 remain; no network requests occur. The replay state is separate from the unchanged runtime fixture. Completion does not imply available speculative supply.

## What is included

| Content | Location |
|---|---|
| Agent, providers, state and answer validation | Root Python modules |
| Prompt skills and five-tool contract | `agent_skills/`, `contracts/` |
| Curated input manifest | `inputs/manifest.json` |
| Curated JSON, read-only SQLite and query explorer | `evidence/london_office_evidence_pack/` |
| Tests, six-case oracle and reproduction tools | `tests/`, `eval/`, `scripts/` |
| Five-slide PDF and previews | `slides/` |

Core execution has no third-party runtime dependencies. Optional Apple SDK/build inputs are pinned in separate lock files. Live traces are created locally under `output/`; review them before sharing because they contain prompts and retrieved evidence.

## Verify the gateway and database

The foundation verifier checks hashes for the included evidence inputs, the curated inventories, SQLite integrity/provenance, public comparison examples and read-only runtime access. It also rebuilds a temporary database using the supplied build command and compares logical rows.

```sh
python scripts/verify_foundation.py
python evidence/london_office_evidence_pack/query.py check
python evidence/london_office_evidence_pack/query.py sources
python evidence/london_office_evidence_pack/query.py facts
```

The current fixture should report 20 sources, 39 facts, 10 coverage rows, six conversations and 18 prompts. The bundled `evidence.sqlite` remains the application input. To rebuild it after an intentional JSON fixture edit, run `python evidence/london_office_evidence_pack/query.py build`, then `check`; do not edit the database directly. The application opens the database read-only.

The tool CLI also supports direct source inspection, scoped searches and cross-section comparisons:

```sh
python agent_tools.py --help
python agent_tools.py get_source --source-id S01
python agent_tools.py search_facts --topic vacancy --geography 'City Core' --period-start 2026-04-01 --period-end 2026-06-30
python agent_tools.py compare_facts --fact-id-a F09 --fact-id-b F10 --mode cross_section
```

Search uses publisher geography labels as stored; City Core is not silently expanded to all of London. F09/F10 compare West End with City in the same H1 period and return West End minus City: **GBP 29.96/sq ft**. Successful calls exit 0. Invalid, unknown or refused requests return a JSON error envelope and exit 2.

## Reproduce the interactive follow-ups

For the fuller three-session profile, install the selected provider and run the supplied driver. It enters the prompts, creates one JSONL trace and console log for each session, then runs support and scope/semantics checkers:

```sh
python scripts/run_python_cp6_live.py --provider apple --output-dir output/my-live-profile-01
```

For a separately configured API model, use `--provider openai` and another fresh output directory. The runner requires a clean Git checkout and never overwrites an existing run. For Apple it exercises:

- Central London Q1/Q2 vacancy and take-up, followed by separate City Core and City Fringe comparisons while preserving the periods.
- A two-point cited briefing, then an ambiguous “there” question after two geographies.
- A Canary Wharf fixture gap, `/state`, `/new`, and another deliberately ambiguous referent after reset.
- Source S17's access qualifications; cataloguing a source does not call its API.
- Fresh F01/S01 and City Core F04/S02 fact lookups, each followed by “there” to check observed-scope continuity.

Expected for the recorded Apple profile: three sessions, 11 model turns, all five evidence tools, no errors; both support and semantic checkers exit 0. The runner writes `core.jsonl`, `fact.jsonl`, `value.jsonl`, a console file for each session, the two checker outputs and `receipt.json` with source identity, results and artifact hashes. The core session follows the nine user inputs shown below; `/state`, `/new` and `/quit` are CLI commands, not model prompts:

1. `What changed in Central London between Q1 and Q2 2026?`
2. `Focus on City Core and City Fringe. Did they move in the same direction?`
3. `Give me two cited points for our meeting.`
4. `What about there?` (after two places, the agent should clarify.)
5. `Canary Wharf vacancy in Q2 2026`
6. `/state`
7. `/new`
8. `What about there?` (after reset, the missing referent should again be clarified.)
9. `Show source S17 and its access qualifications.`

The fact session asks `Show fact F01 and its original source.` then `What about there?`; the value session asks `What is City Core vacancy in Q2 2026?` then `What about there?`. Each session ends with `/quit`. F01's observed record should carry Central London/Q1 into its follow-up; F04/S02 should carry City Core/Q2 into its follow-up. The second checker checks the required comparison scopes, two meeting points, clarification, gap, reset, S17 qualification and both lookup continuities. It does not trust model prose for values; it checks the emitted evidence references and the Python renderer.

Read each console/trace, check whether the answer is useful and every claim answers the requested question, then record your own judgment. The runner reports a machine check, not your human judgment.

## Understand the six-case evaluation

Each case is a new conversation, so context cannot leak between cases:

| Case | Main question | Distinction the answer should preserve |
|---|---|---|
| `market_change` | Central London, then City Core and Fringe | Vacancy versus take-up; Q1/Q2 period; distinct publisher geographies |
| `rents_and_incentives` | City/West End rents, Midtown growth and incentives | Grade A average versus prime headline; positive but slower growth; offer ranges |
| `pipeline_and_project_history` | Starts, delivery and 2 Aldermanbury Square | Survey flows; forecasts versus milestones; completion versus newly available supply |
| `prelet_and_monitoring` | British Land pre-let and committed delivery | Area options, delivery forecast and building ERV versus office-area denominator |
| `macro_context` | Bank rate, London labour market and UK GDP | Observation periods, publication dates, geographies and interpretation versus causal evidence |
| `quality_and_asset_evidence` | Leasing mix, hybrid work and an EPC | Half-year shares, GB versus London, certificate premises and assessment dates |

The six prompt triplets are the assessment cases: `market_change`, `rents_and_incentives`, `pipeline_and_project_history`, `prelet_and_monitoring`, `macro_context`, and `quality_and_asset_evidence`. The replay program passes only those original prompts to the agent. The reference expectations remain in a separate evaluation file; they are not inserted into model context. The evaluator checks code/fixture identity, session isolation, tool traces, required fact references and arithmetic bindings. It produces `PASS_EVIDENCE_CHECKS_ONLY` while each human answer/qualification judgment is still pending.

For manual review, check every displayed value against the answer's cited facts, then each numeric binding and every condition in `must_preserve`. A useful review records the case, turn, observed fact IDs, source/locator and reason for its decision. Preserve the generated pending file and create a separate reviewed copy. The checker verifies the completed worksheet against the exact live bundle hash, oracle hash, case order, numeric fields and all manual conditions. It only records a pass after every arithmetic and human condition is completed; it cannot determine that the prose was semantically good. Review the answers from your own run rather than relying on a previous model output.

## Record your run

For comparable results, record the exact commit, Python version, OS, architecture, provider/model and SDK version with each live run. Keep a fresh trace for every attempt. The interactive profile, six-case evaluation and historical replay test different behaviors; a result from one does not establish the others. Counts and wording can vary by model while supported facts, scope, calculations and citations remain the review criteria.
