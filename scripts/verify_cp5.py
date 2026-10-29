#!/usr/bin/env python3
"""CP5 offline replay infrastructure gate; not six-case model acceptance."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.replay_refresh import replay

def main():
    for command in (['-m','unittest','tests.test_cp5'],['scripts/verify_cp4.py']):
        result=subprocess.run([sys.executable,*command],cwd=ROOT,text=True,capture_output=True)
        print(result.stdout+result.stderr,end='')
        if result.returncode: return result.returncode
    (ROOT/'output').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'output',prefix='cp5-verify-') as tmp:
        path=Path(tmp)/'refresh.sqlite'
        first=replay(path)
        second=replay(path,repeat=True)
        if (first['new_observations'],first['new_findings'],second['new_observations'],second['new_findings']) != (2,1,0,0):
            raise ValueError('Refresh did not preserve expected milestone/deduplication')
        print(json.dumps({'historical_replay_first':first,'historical_replay_repeat':second},indent=2))
    print('PASS | CP5 offline infrastructure and historical replay')
    print('PENDING_USER | CP5 six-case live and manual semantic acceptance; no model called by this verifier')
    return 0
if __name__=='__main__': raise SystemExit(main())
