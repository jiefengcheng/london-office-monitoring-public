#!/usr/bin/env python3
"""Record the CP4 prompt sequence as revised CP6 chosen-provider install proof."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = {
    'core': ['What changed in Central London between Q1 and Q2 2026?',
             'Focus on City Core and City Fringe. Did they move in the same direction?',
             'Give me two cited points for our meeting.', 'What about there?',
             'Canary Wharf vacancy in Q2 2026', '/state', '/new', 'What about there?',
             'Show source S17 and its access qualifications.', '/quit'],
    'fact': ['Show fact F01 and its original source.', 'What about there?', '/quit'],
    'value': ['What is City Core vacancy in Q2 2026?', 'What about there?', '/quit'],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['apple', 'openai'], required=True)
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory under output/; preserve failed attempts')
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    if not directory.is_relative_to(ROOT/'output') or directory.exists():
        parser.error('Use a new directory under this checkout\'s ignored output/')
    def clean():
        return not subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,text=True).strip()
    if not clean(): parser.error('Use a clean committed checkout; no dirty-source live handoff')
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    directory.mkdir(parents=True)
    results = {}
    for name, prompts in SESSIONS.items():
        if not clean() or subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()!=commit:
            raise RuntimeError('Source changed; preserve incomplete output')
        command = [sys.executable,str(ROOT/'agent.py'),'--provider',args.provider,'--interactive',
                   '--trace',str(directory/(name+'.jsonl'))]
        print('Starting chosen-provider live session: '+name,flush=True)
        process = subprocess.run(command,input='\n'.join(prompts)+'\n',text=True,capture_output=True,cwd=ROOT)
        (directory/(name+'.console.txt')).write_text(process.stdout+process.stderr)
        results[name] = process.returncode
        print(name+': exit '+str(process.returncode),flush=True)
    commands = {
        'support': [sys.executable,str(ROOT/'scripts/check_cp4_trace.py'),str(directory/'core.jsonl'),
                    '--require-live','--expect-provider',args.provider],
        'semantics': [sys.executable,str(ROOT/'scripts/check_cp4_live_review.py'),str(directory/'core.jsonl'),
                      '--expect-provider',args.provider,'--fact-lookup',str(directory/'fact.jsonl'),
                      '--value-follow-up',str(directory/'value.jsonl')],
    }
    for name, command in commands.items():
        process = subprocess.run(command,text=True,capture_output=True,cwd=ROOT)
        (directory/(name+'.txt')).write_text(process.stdout+process.stderr)
        results[name] = process.returncode
    unchanged = clean() and subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==commit
    passed = unchanged and all(code==0 for code in results.values())
    receipt = {'profile':'python-independent-v1/CP6','provider':args.provider,'source_commit':commit,
               'source_unchanged':unchanged,'exit_codes':results,
               'result':'PASS_LOCAL_LIVE_CHECKS_ONLY' if passed else 'FAIL_LOCAL_LIVE_CHECKS',
               'scope':'Chosen provider installation and explicit-provider interaction only; no API/other-platform/native/release acceptance',
               'human_verification':'PENDING_USER',
               'artifact_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir())}}
    (directory/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(receipt['result']+' | '+str(directory/'receipt.json'))
    print('PENDING_USER | inspect every answer and independently reproduce; no automated human verdict')
    return 0 if passed else 1


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as error:
        print('FAIL | Python CP6 live runner | '+str(error),file=sys.stderr)
        raise SystemExit(1)
