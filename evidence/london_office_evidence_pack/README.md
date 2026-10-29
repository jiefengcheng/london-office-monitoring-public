# London office evidence pack

Research date: **27 October 2026**.

This is a small, requirements-led data source for a London office market chat demo. It contains **39 selected evidence records from 16 primary sources**, plus **four optional data-source entries**. It supports retrieval, period-aware comparisons, project histories and cited discussion. It does not implement an LLM chat interface, live monitoring or a market prediction model.

The market sample is mainly Q1/Q2 2026. Project announcements, a hybrid-work survey and an EPC deliberately retain their older dates. The macro sample includes September/October 2026 releases. This collection is not an exhaustive search for the latest observation of every metric, and it is not a complete London lease database.

## Run immediately

Unzip the folder and run with **Python 3.10 or later**. No dependencies, API key or network connection are needed for the included queries.

```bash
python query.py demo
python query.py check
```

The supplied `evidence.sqlite` is ready to query. Rebuild it from the editable JSON inputs with:

```bash
python query.py build
```

Build replaces only the generated database, after validating the inputs. All other commands open the database read-only.

```bash
python query.py sources
python query.py facts --topic rent
python query.py facts --topic vacancy --geography City
python query.py facts --entity "2 Aldermanbury Square"
python query.py facts --topic macro
python query.py source S02
python query.py compare F01 F02
python query.py compare F15 F16
```

## What each file contains

| File | Role |
|---|---|
| `sources.json` | Publisher, title, URL, publication date, retrieval date, access result, source role and limitations. |
| `facts.json` | Selected values/events with unit, geography, segment, time basis, status and source locator. |
| `coverage.json` | Mapping to the assessment topics, including gaps and optional connectors. |
| `evidence.sqlite` | Queryable `sources`, `facts`, `coverage` tables and an `evidence` join view. |
| `query.py` | Standard-library loader, read-only retrieval, conservative comparison and integrity checks. |
| `chat_cases.json` | Proposed multi-turn prompts and reference expectations for a later chat interface. The query tool does not load this file. |
| `SOURCE_CATALOG.md` | Clickable source register generated from `sources.json`. |
| `verification.txt` | Results of the executed checks on this pack. |

## Requirements and the corresponding evidence

| Assessment topic | Included source IDs | What is actually available |
|---|---|---|
| Prime / Grade A rents | S02–S04 | H1 City and West End Grade A averages; Midtown prime-rent growth in two quarters; Canary Wharf incentive ranges by lease length. |
| Vacancy / availability | S01–S02 | Two-quarter vacancy observations for Central London, City Core and City Fringe; selected Q2 snapshots. A separate availability series is not included. |
| Leasing / take-up | S01–S02, S06, S09, S15 | Rounded quarterly Central London take-up; named pre-lets; landlord campus leasing. |
| Development / refurbishment / pre-lets | S05–S09 | Annual starts, deliveries and refurbishment share; one building timeline; a future pre-let. |
| Named submarkets | S01–S04 | Some evidence for City, West End, Fringe, Midtown and Canary Wharf. Coverage is uneven and retains each publisher's names. |
| Rates / economy / employment | S10–S12 | UK policy rate and GDP; London regional employment and unemployment. |
| Quality / ESG / hybrid work | S02, S08, S13–S15 | Quality-related take-up shares, reported BREEAM achievement, one scoped EPC, hybrid survey and portfolio leasing. |
| News / significant events | S06–S09, S16 | Dated project, pre-let, completion and planning announcements. |
| Raw data expansion | S17–S20 | EPC bulk/API, official rating floorspace CSV, planning data, and modelled employment-site context; access qualifications retained. |

No publisher-specific geography is silently converted to a borough boundary. Canary Wharf is not treated as equivalent to East London or Docklands. Midtown and Fringe are separate labels.

## What “ground truth” means here

There are several distinct evidential roles:

1. **Published market measurements.** Broker figures ground claims about what that publisher reported. They are not an independently verified census of every lease or vacant unit.
2. **Official statistics and policy decisions.** These ground dated economic context with the publisher's methodology and release vintage. Survey estimates remain estimates.
3. **Project and planning events.** A developer announcement or council decision grounds a specific reported milestone. Planning approval, structural completion, practical completion, pre-letting and occupation are separate stages.
4. **Registered building assessments.** The EPC applies to the assessed premises on its assessment date. A floor-level certificate does not automatically classify the entire building.
5. **Forecasts and interpretations.** A future delivery target is stored with `status: forecast`. It is not silently promoted to an observed outcome.
6. **Reference answers.** The separate chat cases provide expectations for arithmetic, citations and scope. They are evaluation material, not extra evidence to supply to a language model as if it were a source.

The fixture contains paraphrased factual records and source locators, not full publisher reports. A source URL opens the original evidence for verification. The offline pack does not reproduce every source passage.

## Useful conversation paths

The full prompts and acceptance expectations are in `chat_cases.json`.

### A. Market change, then a more local question

Retrieve F01–F08. Compare Central London vacancy and take-up across Q1/Q2; then retain the period selection while narrowing to City Core and City Fringe. The calculation tool should distinguish percentage points, basis points, square footage and relative growth. Rounded take-up values support an approximate growth calculation, not extra precision.

### B. Rent evidence and a different submarket

Retrieve F09–F10 for a same-report H1 comparison, then F15–F18 for the available Midtown and Canary Wharf evidence. An annual growth rate, an average rent level and a rent-free range are different quantities. Changing the area must not change the requested metric without saying what evidence is available.

### C. Supply, commitments and project stages

Retrieve F19–F29. Show annual construction evidence, the historical 2 Aldermanbury Square timeline and the 1 Appold Street agreement. The pre-let share at Appold is a **building-level ERV denominator**, not an office floor-area share. The refurbishment share is **new-start floorspace**, not a count of projects.

### D. Economic context

Retrieve F32–F35, carrying their observation periods and publication dates into the answer. An answer can identify channels through which macro conditions could matter, explicitly as interpretation. These four records alone cannot identify a causal effect on office rents or yield a rental forecast.

### E. Quality, working patterns and one building

Retrieve F11–F12, F30–F31 and F36–F39 as appropriate. Distinguish demand composition, survey context and building assessment. EPC, BREEAM and Grade A are different classifications. F37 describes one landlord's campus portfolio, not the whole London market.

### F. Event monitoring and an unchanged refresh

The dated GPE records provide real source versions for a future replay. Show the older announcement, then introduce the later practical-completion announcement. Loading the same reviewed observation twice should create no duplicate finding. A replay should be labelled as historical source ingestion; all records here were assembled in October 2026. The included CLI does not implement a live crawler or the refresh operation.

## Minimal data and tool design for the later chat app

Start with structured retrieval over this SQLite database. Four tool capabilities are sufficient:

- Filter facts by topic, geography, entity and period.
- Retrieve a source record and its locator.
- Compare compatible observations with deterministic arithmetic.
- Return the coverage table when a requested metric or area is missing.

Keep conversation state small: selected geography, selected periods and selected entity. Let the model phrase the answer and suggest a useful follow-up; keep values, source references and calculations in the tool response. A vector database or graph neural network is not required for this collection.

The comparator requires a common curated `series_key`, metric, publisher, unit, geography, geography basis, segment, evidence class and period kind. It refuses forecasts, textual ranges and incompatible series. Cross-sectional comparisons such as City versus West End can be added as a separate tool with explicit same-period checks; the included comparator is intentionally for time comparisons.

## Extending the sources

S17 is the current government EPC data service. Bulk download and API access need authentication; the individual certificate in S14 was readable without login. An authenticated connector was not tested.

S18 supplies genuine structured office-stock data. Stock is useful context, but it is not a replacement for observed market vacancy, take-up or rents.

S19 is a documented planning integration candidate. The landing page returned 403 here and no actual API record was retrieved. It is not marked operational.

S20 is an official employment-sites report with modelled capacity. Keep its assumptions, baseline and forecasts separate from measured employment and completed office space.

Before expanding, decide which missing question deserves more evidence. For example, a true City/West End rent trend needs two compatible observations; a Canary Wharf vacancy comparison needs that specific geography and definition. Do not fill gaps with values from a wider area or another asset class.

## Verification and limits

The executed checks validate the JSON/database, source references and selected calculations. Separate research reviewers checked the project denominators and macro/EPC values; two denominator ambiguities were corrected. This is not a human-certified dataset or a benchmark of an LLM's factual accuracy. The expected chat cases have not been run through a language model in this pack.

The included source selection can support a small, useful demonstration. It does not establish market-wide causality, investment outperformance, complete current availability or a representative commercial transaction training set.
