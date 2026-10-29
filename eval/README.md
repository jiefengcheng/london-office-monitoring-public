# Evaluation and replay

The six independent conversation cases are defined in `evidence/london_office_evidence_pack/chat_cases.json`. They cover market change, rents and incentives, project history, pre-letting, macro indicators and asset quality. Each case starts a fresh conversation; the model receives the user prompts, not the reference expectations.

`cp5-oracle.json` maps expected numeric results to the supporting fact IDs and records scope/qualification conditions that a reviewer should inspect. The evaluator checks session isolation, tool traces, required evidence references and arithmetic bindings. These checks do not judge whether an answer is useful or whether its explanation preserves every qualification; review the generated worksheet and trace separately.

Run the complete evaluation after installing a live provider:

```sh
python scripts/replay_cases.py --host python --provider apple --output output/my-six-cases-01.jsonl
python scripts/verify_replay.py output/my-six-cases-01.jsonl --report output/my-six-cases-01-pending.json
```

Use `--provider openai` for a separately configured API account. Runs create local JSONL traces and reports under `output/`; inspect them before sharing because they contain user prompts and retrieved evidence. Do not place model keys or expected answers in prompts or traces.

The historical project replay is separate from the conversation evaluation. It applies already-reviewed project facts to a disposable SQLite database, preserves forecast status, and deduplicates repeated additions. It makes no network requests and does not update the agent's read-only fixture. See the [reproduction guide](../docs/user/REPRODUCING.md#replay-historical-project-monitoring) for commands.
