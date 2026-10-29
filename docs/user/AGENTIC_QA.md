# Agentic Q&A

Follow this guided trial to explore the London office evidence agent in about 10–15 minutes. It demonstrates the agentic loop, conversation continuity, checked calculations, source inspection and useful recovery when the fixture cannot answer a question.

The prompts use the supplied historical evidence. Expected results describe evidence and behavior; model decisions and tool order may vary. Each answer should finish with supported evidence or an explicit limitation. A loop of repeated tool calls, an exhausted step budget or a provider error are failed trial steps, not missing market observations.

## Why this evidence pack is structured this way

The trial is built around a small, requirements-led London office evidence collection: **39 fact records, 20 source records and 10 coverage records**. The current market sample is selective rather than exhaustive. Sources S01–S16 underpin the selected observations; S17–S20 record optional or expansion sources with access and qualification notes, and do not imply that those integrations are active. The pack covers rents, vacancy and take-up, development and refurbishment, pre-lets, economic context, hybrid working and building quality.

Each fact is a compact record of what an identified source reports. It retains the metric, value and unit; publisher's geography or named entity; observation or target period; status (such as reported estimate, event, assessment or forecast); source ID and locator; and relevant caveats. This design makes the evidence reusable across questions while keeping distinctions visible. A broker estimate, official statistic, developer milestone, registered assessment and forecast have different evidential meanings. A reference expectation in the evaluation file is a check on retrieval, arithmetic, citations and qualifications; it is not itself evidence and is not supplied to the model during replay.

The SQLite database is ready to query and can be rebuilt from the curated JSON inputs using the evidence pack's `query.py build` command. Runtime agent tools open the database read-only. New evidence is added through review of the source and curated records, validation and an intentional rebuild. The agent does not crawl websites or ingest live market data.

The six multi-turn paths were chosen to exercise distinct, practical questions while making their limits inspectable:

| Conversation path | What the evidence supports | Design point to notice |
|---|---|---|
| **Market change** | Vacancy moves 7.4% → 7.2% and take-up 2.2 → 2.4 million sq ft in Central London (F01/F02, F07/F08); City Core and City Fringe vacancy then move in opposite directions (F03–F06). | Preserve Q1/Q2, units and publisher geographies; distinguish percentage-point change from approximate growth calculated from rounded take-up. Carry the selected period into the follow-up and lead with the checked directional conclusion. |
| **Rents and incentives** | H1 Grade A average rent is £76.21/sq ft in City and £106.17 in West End (F09/F10); Midtown annual prime headline rent growth slows from 1.4% to 0.7% but stays positive (F15/F16); Canary Wharf has lease-length-specific incentive ranges (F17/F18). | An average rent, a growth rate and a rent-free incentive are different measures. The incentive ranges are not signed offers; absence of a Canary Wharf vacancy series remains a fixture gap. |
| **Pipeline and project history** | Deloitte records 2025 starts, deliveries and refurbishment share (F19–F21); GPE records the evolving 2 Aldermanbury Square timeline (F22–F26, F39). | Keep the 2025 observation year separate from 2026 publication; preserve old forecasts as historical forecasts. Practical completion and 321,650 sq ft do not establish newly available speculative space: offices were pre-let and fit-out was to follow. |
| **Pre-let and monitoring** | British Land's 1 Appold Street agreement covers 268,000 sq ft with area options and forecast Q1 2029 delivery (F27/F28); the minimum 60% pre-let is measured by building ERV (F29). | ERV share is not floor-area share. The scheme, tenant entrance and forecast status stay explicit; “40% of the office area remains free” is not supported. |
| **Macro context** | Bank Rate is 3.75%; London employment and unemployment are 74.5% and 6.6%; UK real GDP growth is 0.7% for its stated three-month comparison (F32–F35). | Keep geographies, reference periods, publication dates and denominators separate. Possible transmission to office demand is interpretation, not a measured causal result. |
| **Quality and asset evidence** | H1 Grade A and BREEAM shares (F11/F12), a Great Britain hybrid-work survey (F36), and an EPC for the second floor of 20 Triton Street (F30/F31) provide different kinds of context; F39 is a landlord-reported BREEAM claim. | Hybrid working is not London office occupancy. The EPC is premises-specific; EPC, BREEAM and Grade A are distinct classifications. Do not generalise a building record or portfolio claim into a market-wide measure. |

The specific prompts make these distinctions observable in conversation. The rents path asks whether Midtown rents “fell” after the growth rate slowed, so the answer has to distinguish a positive but slower rate from a falling level. The pipeline path follows a real project through an old forecast, a later forecast and reported practical completion, then asks whether completed area became newly available. The pre-let path tests the ERV denominator directly. The macro path separates London observations from UK-wide context before inviting interpretation. The quality path moves from market-level demand composition to hybrid-work context and then to a single premises certificate. In each case, follow-up context helps the user refine the question without letting the agent silently change a measure, place or date.

Together these paths cover retrieval, follow-up scope, deterministic comparison, provenance inspection, fixture-gap handling, chronology and denominator discipline. They are a practical evaluation set, not a comprehensive London market panel or a claim that every answer from every model will be correct. For the exact prompt sequence and reproducible evaluation procedure, see [`chat_cases.json`](../../evidence/london_office_evidence_pack/chat_cases.json) and [Reproduce the examples](REPRODUCING.md).

## 1. Start a real model conversation

Complete [installation and provider setup](INSTALLATION.md) first. Run from the repository root with the virtual environment activated. For an available Apple Intelligence provider:

```sh
python apple_provider.py
python agent.py --provider apple --interactive --trace output/guided-qa-01.jsonl
```

The probe must report available/on-device. For a configured OpenAI Responses model, use:

```sh
python agent.py --provider openai --interactive --trace output/guided-qa-01.jsonl
```

Choose one command and an unused trace filename. The API route needs your own working model/key; the Apple route needs the optional SDK and a ready system model. There is no automatic fallback. The local HTTP simulator and scripted examples are explained in [Reproducing](REPRODUCING.md); they exercise mechanics without model inference.

A blank Enter simply returns to the prompt without creating a conversation turn. At the `You:` prompt, enter:

```text
Hello
```

Expect a welcome and an example question. This greeting is handled locally and preserves conversation state; it is not a model or evidence check. The next step exercises the live agentic loop.

## 2. Establish a market comparison

```text
What changed in Central London between Q1 and Q2 2026?
```

Look for both measures:

| Measure | Q1 → Q2 2026 | Interpretation supported by the fixture |
|---|---|---|
| Vacancy | 7.4% → 7.2% | −0.2 percentage points / −20 basis points; F01/F02 |
| Take-up | 2.2 → 2.4 million sq ft | +0.2 million sq ft; approximately +9.1% from rounded inputs; F07/F08 |

Each finding should link its source and retain dates, units and qualifications. Percentage points and percentage growth are different calculations. This step demonstrates **interpret → tool → observe → continue/answer**: the model selects evidence actions, while Python checks and renders the result.

## 3. Narrow the geography without repeating the whole question

Continue in the same conversation:

```text
Focus on City Core and City Fringe. Did they move in the same direction?
```

The question inherits Q1/Q2 2026. City Core vacancy falls from 6.0% to 5.9% (−0.1 pp; F03/F04); City Fringe rises from 7.9% to 8.0% (+0.1 pp; F05/F06). The answer should lead with that opposite-direction conclusion, then show the cited comparison cards. If another requested measure lacks matching submarket evidence, its absence belongs under secondary **Coverage limits**, rather than before the answer. A secondary gap must not recommend asking the vacancy question again.

```text
Give me two cited points for our meeting.
/state
```

Expect two relevant cited Core/Fringe points. `/state` lets you inspect the selected geography, topics, periods and evidence references. This is **conversation continuity with explicit scope**, not permanent memory; restarting the process clears it.

## 4. Inspect provenance directly

Start a separate scope:

```text
/new
Show fact F01 and its original source.
```

Expect 7.4% Central London vacancy at Q1 2026, linked to Savills source S01 with its publication date, URL, locator and qualifications. Follow the link to inspect the source yourself. Observation dates and publication dates serve different purposes.

```text
Show source S17 and its access qualifications.
```

Expect the source's recorded One Login/token/API qualifications. Inspecting an access note does not call the API or retrieve additional facts. This demonstrates **provenance inspection and access boundaries**.

## 5. See a fixture gap and recover a missing comparison baseline

```text
/new
Canary Wharf vacancy in Q2 2026
```

Expect a scoped fixture gap. There is no matching Canary Wharf vacancy observation; that does not mean zero vacancy or zero activity. The answer should explain the limitation and suggest one supported nearby question, explicitly naming that alternative's actual geography and dates.

Without taking that suggestion yet, enter:

```text
Focus on City Core and City Fringe. Did they move in the same direction?
```

This conversation has selected only Q2, so a trend has no baseline. Expect a specific missing-baseline explanation, preserving vacancy and both requested places, followed by one supported question such as:

```text
Did City Core and City Fringe vacancy rate move in the same direction between Q1 2026 and Q2 2026 (F03, F04, F05, F06)?
```

Submit the suggested question. It should return both comparisons from step 3. The alternative is validated through the gateway, but it becomes the requested scope only when you ask it. The agent must not silently invent a baseline or substitute one geography for another.

## 6. Change the dates and observe the evidence limits

```text
/new
What changed in Central London between Q2 and Q3 2026?
```

Expect Q2 vacancy (F02, 7.2%) and take-up (F08, 2.4 million sq ft), with separate Q3 fixture gaps. A Q2–Q3 change cannot be calculated from this pack.

```text
What changed in Central London between May and July 2026?
```

Expect an explanation that quarterly office observations cannot establish this monthly change, plus one supported alternative. The answer must not reuse Q2/Q3 dates or present a quarterly total as a monthly value. This demonstrates **evidence enforcement when a plausible question exceeds coverage**.

## 7. Explore a project history

```text
/new
What development and refurbishment evidence do we have?
Trace 2 Aldermanbury Square from pre-let to completion.
How much newly available space did that completion add?
```

Look for dated milestones and source links. Earlier completion forecasts remain historical forecasts; practical completion was announced on 25 March 2026. The completed office area is 321,650 sq ft, but all offices were pre-let. The agent must not label that area as newly available speculative supply or assume operational occupation. This shows why **provenance, chronology and denominators** matter beyond retrieving a number.

## 8. Check the conversation and finish

```text
/new
What about there?
```

After reset, there is no selected referent. Expect clarification and one supported suggestion, not reuse of the prior building. `/quit` ends the process.

Your trace contains provider steps, checked tool results, selected evidence, state changes and recovery guidance. To check its recorded support:

```sh
python scripts/check_cp4_trace.py output/guided-qa-01.jsonl --require-live --expect-provider apple
```

Use `openai` for an API session. This check verifies recorded provider/support boundaries; read the answers yourself for relevance and usefulness. Keep traces local unless reviewed for sharing: they contain your prompts and retrieved evidence.

For the complete six-conversation evaluation and historical event replay, continue to [Reproduce the examples](REPRODUCING.md). See [Architecture](ARCHITECTURE.md) for the implemented agent loop and [Evidence and limits](EVIDENCE.md) for the scope of supported claims.

## Explore other evidence topics

Use `/new` before each route. These are additional starting points from the supplied six-conversation evaluation:

| Topic | Starting question | What to inspect |
|---|---|---|
| Rents | `Compare the H1 Grade A rent observations for City and West End.` | West End minus City is £29.96/sq ft (F09/F10). Average Grade A rent is distinct from prime headline rent. |
| Pre-lets | `Show one significant office pre-let in our source collection.` Then `What is the committed area and expected delivery?` | British Land's 1 Appold Street evidence (F27–F29); area options, delivery forecast and the ERV denominator must remain qualified. |
| Macro context | `What does our newest macro evidence say?` Then `Separate London observations from UK-wide context.` | London labour observations and UK GDP/policy have different geographies, reference periods and publication dates. They do not identify a causal office-market effect. |
| Quality | `What evidence is relevant to quality, sustainability and hybrid working?` Then `Show a concrete London office energy certificate.` | Keep Grade A, BREEAM and EPC distinct. The certificate concerns the second floor of 20 Triton Street; the hybrid-work survey covers Great Britain, not London office occupancy. |
