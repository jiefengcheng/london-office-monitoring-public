#!/usr/bin/env python3
"""Run the unchanged six cases against a genuine model; no oracle enters the agent."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / 'evidence/london_office_evidence_pack/chat_cases.json'

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', choices=['python'], default='python')
    p.add_argument('--provider', choices=['apple', 'openai'], default='openai')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    output = a.output.resolve()
    if not output.is_relative_to(ROOT / 'output'):
        p.error('Write replay bundles under this checkout\'s ignored output/ directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    source_commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    def source_is_clean():
        return not subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,text=True).strip() and subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip() == source_commit
    if not source_is_clean(): p.error('Live replay requires a clean committed checkout; preserve dirty work and use a fresh clone')
    cases = json.loads(CASES.read_text())['cases']
    directory = output.parent / (output.stem + '-' + uuid.uuid4().hex[:8])
    directory.mkdir()
    failures = 0
    with output.open('x') as bundle:
        for case in cases:
            if not source_is_clean():
                print('FAIL | source changed during replay; preserve this incomplete bundle',file=sys.stderr)
                return 1
            print('Starting '+case['id'],flush=True)
            trace = directory / (case['id'] + '.jsonl')
            # Only original prompts cross into the CLI. expected/must_preserve stay here.
            run = subprocess.run([sys.executable, str(ROOT / 'agent.py'), '--provider', a.provider,
                                  '--interactive', '--trace', str(trace)],
                                 input='\n'.join(case['prompts'] + ['/quit'])+'\n',
                                 text=True, capture_output=True, cwd=ROOT)
            console = directory / (case['id'] + '.console.txt')
            console.write_text(run.stdout + run.stderr)
            record = {'case_id':case['id'], 'host':a.host, 'provider':a.provider,
                      'cases_sha256':hashlib.sha256(CASES.read_bytes()).hexdigest(),
                      'trace':str(trace.relative_to(output.parent)),
                      'trace_sha256':hashlib.sha256(trace.read_bytes()).hexdigest() if trace.exists() else None,
                      'exit_code':run.returncode,'source_changed_during_case':not source_is_clean(),
                      'evaluation_code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['scripts/replay_cases.py','scripts/verify_replay.py','eval/cp5-oracle.json']}}
            bundle.write(json.dumps(record)+'\n'); bundle.flush()
            print(f"{case['id']}: exit {run.returncode}; {trace}", flush=True)
            failures += run.returncode != 0
            if record['source_changed_during_case']:
                print('FAIL | source changed during case; preserve this diagnostic bundle',file=sys.stderr)
                return 1
    print('Recorded six independent conversations; run verify_replay.py and complete manual review.')
    return 1 if failures else 0

if __name__ == '__main__':
    raise SystemExit(main())
