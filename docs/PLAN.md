# Delivery plan

The project is an independently runnable Python evidence agent for London office-market questions. Its current design separates provider decisions from evidence access and answer rendering, uses a bounded agentic loop, and returns claims tied to a curated, inspectable evidence pack.

## Current scope

- Reproduce the Python CLI from a public Git clone with the included source, pinned optional dependencies, fixture, tests and evaluation tools.
- Keep the five read-only evidence tools and their shared schemas as the contract between model providers and the Python host.
- Support OpenAI Responses and optional on-device Apple Foundation Models through the same Python host. Provider setup and live behavior are environment-specific; users must configure their own access.
- Demonstrate a historical, local project-event replay against a disposable database. The application does not crawl the web or refresh live market data.
- Keep conversation state in process memory, expose trace inspection and preserve source, scope, dates, units and qualifications with factual answers.

## Next development

1. Reproduce the API provider profile on a clean machine and document its tested model and platform requirements.
2. Measure gaps in continuity, evidence selection, provenance inspection and loop behavior before adding new runtime infrastructure.
3. Consider a second host only if it provides a demonstrated user benefit. Reuse the same logical evidence/tool contract and migrate individual components only after behavior is verified in Python.
4. Add live ingestion only if a specific monitoring need and source-access plan justify it; keep retrieved content reviewable and separate from the frozen demonstration fixture.
5. Keep the presentation and reproduction guide aligned with the actual shipped behavior and environment-specific limits.

## Design boundaries

The current scope needs no vector database, graph/C7 service, model training or live ingestion. Add those only when an observed requirement cannot be handled by the existing evidence contract and deterministic Python tools. Keep every new capability reproducible from a clean clone and make its evidence boundary visible to users.
