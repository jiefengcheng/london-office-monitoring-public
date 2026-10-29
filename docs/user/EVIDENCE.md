# Evidence, behavior and limits

The agent answers against a curated historical fixture, not a web search or live market feed. Its research date is 27 October 2026. The supplied inputs contain 20 source records, 39 facts, 10 coverage records and six reference conversations. Not every source is equally accessible, and optional source records may have no extracted facts.

## How a question becomes an answer

```text
Question + conversation scope
          ↓
Selected model → read-only evidence tools → observed results
          ↑                                  ↓
          └──────── continue or clarify ─────┘
                         ↓
           Python validates and renders
           facts, comparisons, citations or gaps
```

This is an implemented agentic loop: the model proposes one typed decision at a time, Python validates and executes any tool call, and the resulting observation goes back into the next decision. The loop can gather more evidence, select a supported answer, clarify an ambiguous request or stop visibly on an error or limit. The Python host controls the evidence boundary, authorizes compatible comparisons and renders the supported results; providers do not return the final factual display text. The conversation retains selected topics, publisher geographies, periods, entity and previously observed evidence. A completed answer records its supported references, so follow-ups can reuse them without repeating a search.

This loop does not let the model search arbitrarily or create evidence. Every tool action is checked against the fixed contract and read-only database; every displayed factual claim is rendered from retrieved observations. A fixture gap remains a gap, an unresolved scope requires clarification, and a rejected comparison stays rejected even if the model asks again.

The five logical tools search facts, retrieve a fact, compare compatible facts, inspect a source and assess fixture coverage. They use fixed, read-only SQLite access. The model has no arbitrary SQL or general fixture-file tool. For integration details, see the [tool contract](../../contracts/README.md).

## Reading an answer

A factual result carries its fact ID, observation period, geography, unit, source URL and locator, with relevant caveats. Comparison values are calculated by the gateway. For example, a vacancy move from 7.4% to 7.2% is −0.2 percentage points, or −20 basis points. It is not a relative change of −0.2%.

### Follow a result back to its source

Use F01 as a concrete example. The fact says **Central London vacancy was 7.4% at Q1 2026 quarter end**. Its provenance chain is:

1. **Fact record F01** identifies the measure, publisher-defined geography, observation date, unit, status, caveat and source ID.
2. **Source record S01** identifies Savills' *Central London Office Market Watch Q1 2026*, publication and retrieval dates, access status and original [source page](https://www.savills.co.uk/research_articles/229130/390337-0).
3. **Locator** points to “Supply and vacancy; named geography paragraph,” so a reviewer knows where the selected observation came from. The fixture stores the selected observation and locator; it does not reproduce the full report.
4. **Rendered answer** links the value to F01/S01 and carries the scope and caveat. In a trace, `tool_result` contains the retrieved fact and joined source metadata; `turn_end.claims` identifies the fact and source IDs used by the Python renderer.

Reproduce the two read-only lookups from the repository root:

```sh
python agent_tools.py get_fact --fact-id F01
python agent_tools.py get_source --source-id S01
```

Check that F01 returns `value_num: 7.4`, `unit: percent`, the quarter-end date `2026-03-31`, `source_id: S01`, the locator and the publisher's qualifications. S01 should return the source title, URL, dates, access status and associated fact IDs. The source lookup describes what was inspected during evidence preparation; the link lets a reviewer inspect the publisher's page independently.

To see the same chain in a live agent interaction, follow [Reproduce the interactive follow-ups](REPRODUCING.md#reproduce-the-interactive-follow-ups). The `fact.jsonl` session asks for F01 and then follows up with “What about there?”; inspect its `tool_result` for observed F01/S01 metadata and `turn_end.claims` for the references behind the rendered answer. The trace shows the agent's evidence path and rendered references; it does not replace checking the linked publisher source.

Publisher geography, denominator, status and period matter. A floor-only EPC certificate does not establish a whole-building rating. A completed building whose offices are pre-let is not automatically available speculative supply. Old forecasts remain forecasts, even when later completion evidence exists.

## Coverage and monitoring

A missing observation means missing evidence in this fixture, not zero market activity. For example, Canary Wharf vacancy in Q2 2026 is a fixture gap. Incompatible comparisons are refused rather than repaired through invented assumptions.

The historical project replay demonstrates adding already-reviewed later evidence and deduplicating it. It does not crawl websites, call an authenticated market-data API or refresh the runtime fixture. It uses a separate disposable database under `output/`.

## What provider verification establishes

The Codex-only suite runs the actual OpenAI Responses client over HTTP to a deterministic simulator on `127.0.0.1`, then sends the resulting trace through the normal support checker. This verifies the local request/response contract, function-call exchange, evidence gateway, answer-selection schema, Python renderer and the boundary that marks simulation as non-live. It makes no external API request and uses no account credential. The fixed simulator does not infer from prompts.

That run cannot establish that the OpenAI-hosted endpoint is reachable, that a credential is valid, that a model is enabled for an account, that quota is available or that a live model will plan useful evidence actions. Those require the configured OpenAI live profile. Apple Intelligence uses a separate on-device profile and is not covered by the simulator. See the [provider verification steps](REPRODUCING.md#codex-only-simulated-openai-endpoint) for the exact commands and acceptance boundaries.

## Practical limits

The interface is a Python CLI with in-memory sessions and trace inspection. It does not provide persistent conversations or a graphical evidence workspace. The bounded agentic loop can fail during planning, select an incomplete scope or reach its step/context limit; errors remain visible and a successful offline check does not guarantee every future answer. Its fixed tools and evidence controls make actions inspectable, but do not guarantee that a model will choose the best next action every time.

Independent live evaluation covered the chosen Apple profile across six conversations and 18 prompts, with 20 tool calls and no errors in that recorded run. It is a bounded result, not a general accuracy claim. A local simulated endpoint run is integration evidence only; it is not an OpenAI service or live-model result. See [reproduction](REPRODUCING.md) to inspect the included recording or create a new run.

ResearchWorkspace is a possible future second host of the same logical contract. It is not required to run this demo. No vector database, graph service, model training or live ingestion is part of the Python application.

## Scope discipline in the data

Each fact keeps its fact ID, publisher geography and geography basis, metric, value/unit, observation or target period, status, source ID, original locator, caveat and series key. Each source keeps the publisher, title, publication/retrieval dates, access status, role and notes. Search dates filter observation/target intervals, not the date on which a source was published.

Examples from the included records:

- F01/F02 are Central London vacancy observations; F03/F04 and F05/F06 are separate City Core and City Fringe series. The Core and Fringe do not have to move together.
- F07/F08 are take-up observations, a different measure from vacancy. For the displayed rounded values, 2.2 to 2.4 million sq ft is a 0.2 million sq ft change and about 9.1% relative growth.
- F09/F10 are H1 Grade A average rents. The same-period cross-section yields £29.96/sq ft; it does not mean that the average is a prime headline rent.
- F27/F28/F29 describe British Land's 1 Appold Street agreement. It includes area options, at least 60% of building ERV, and forecast Q1 2029 delivery; ERV is not the office floor-area denominator.
- F26/F39 record GPE's reported practical completion of 2 Aldermanbury Square on 25 March 2026 and 321,650 sq ft. The offices were pre-let and fit-out was to follow, so these figures do not show that area became available to the speculative market.
- F32–F35 combine Bank Rate, London employment/unemployment and UK GDP context. The observation periods and denominators differ; they are not an identified causal model of office demand.
- The EPC example is for the second floor of 20 Triton Street only. EPC, BREEAM, Grade A and portfolio-level leasing measures are distinct evidence types.

These examples show why provenance and scope travel with the number. A credible-looking result can still be wrong if it switches the publisher's geography, denominator, period or evidence status.

## Application-owned guidance

The model receives short task guidance alongside the shared core policy:

- **Market briefing** asks for vacancy and take-up when the user asks broadly what changed, compares each named submarket separately, uses the deterministic comparison tool and selects only the requested number of supported findings.
- **Project history** keeps planning, pre-letting, target dates, practical completion, fit-out and occupation as different statuses. It preserves historic forecasts and denominators.
- **Coverage check** distinguishes matching evidence, fixture gaps and unresolved scope. It does not replace one geography/metric with another to produce an answer.
- **Apple generation guidance** maps each small action onto schemas that the on-device SDK supports. It cannot relax the original contract checked by the Python host.

This guidance encourages useful tool selection; code-level schema, evidence and renderer checks remain the authority.
