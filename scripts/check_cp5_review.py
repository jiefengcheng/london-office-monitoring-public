#!/usr/bin/env python3
"""Validate a completed human worksheet against the exact live replay bundle."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.verify_replay import verify
from scripts.check_cp4_trace import require

def check_review(bundle,worksheet):
    current=verify(bundle)
    require(current['automated_result']=='PASS_EVIDENCE_CHECKS_ONLY','Automated replay checks failed; human approval cannot override them')
    require(worksheet['bundle_sha256']==current['bundle_sha256'] and worksheet['oracle_sha256']==current['oracle_sha256'],'Review identity mismatch')
    require(worksheet.get('human_verification')=='PASS','Human result is not PASS')
    require(isinstance(worksheet.get('reviewer'),str) and worksheet['reviewer'].strip(),'Reviewer missing')
    require(isinstance(worksheet.get('reviewed_at'),str) and worksheet['reviewed_at'].strip(),'Review date missing')
    require([c['case_id'] for c in worksheet['cases']]==[c['case_id'] for c in current['cases']],'Review cases missing/reordered')
    for reviewed,expected in zip(worksheet['cases'],current['cases']):
        require([m['condition'] for m in reviewed['manual_checks']]==[m['condition'] for m in expected['manual_checks']],'Manual condition missing/changed')
        for row in reviewed['manual_checks']:
            require(row['verdict']=='PASS','Unaccepted manual condition: '+row['condition'])
            require(isinstance(row['evidence_and_reason'],str) and bool(row['evidence_and_reason'].strip()),'Manual evidence/reason missing')
            turns=row['trace_turns']
            require(isinstance(turns,list) and bool(turns) and all(type(t) is int and 1<=t<=3 for t in turns),'Trace turn references must be 1–3')
        require(set(reviewed['numeric_checks'])==set(expected['numeric_checks']),'Numeric condition missing')
        for key,row in reviewed['numeric_checks'].items():
            require({k:v for k,v in row.items() if k!='answer_arithmetic'}=={k:v for k,v in expected['numeric_checks'][key].items() if k!='answer_arithmetic'},'Numeric review fields changed: '+key)
            require(row['answer_arithmetic']=='PASS','Numeric answer not manually verified: '+key)
    return {'result':'PASS_RECORDED_HUMAN_AND_AUTOMATED_CP5_PYTHON_REPLAY',
            'bundle_sha256':current['bundle_sha256'],'reviewer':worksheet['reviewer'],
            'scope':'Six-case Python replay only; historical refresh requires its separate receipt; not native parity or release'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle',type=Path);p.add_argument('worksheet',type=Path)
    a=p.parse_args()
    try:
        print(json.dumps(check_review(a.bundle,json.loads(a.worksheet.read_text())),indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError) as e:
        print('FAIL | CP5 human review | '+str(e),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
