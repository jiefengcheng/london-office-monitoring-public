#!/usr/bin/env python3
"""Check genuine six-case evidence coverage; explicitly retain human semantic checks."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.check_cp4_trace import check_trace, require

CASES=ROOT/'evidence/london_office_evidence_pack/chat_cases.json'
ORACLE=ROOT/'eval/cp5-oracle.json'

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def bound_value(binding,facts):
    rows=[facts[fid] for fid in binding['fact_ids']]
    op=binding['operation']
    if op=='period_start': return rows[0]['period_start']
    if op in ('rating','score'):
        rating,score=rows[0]['value_text'].split('; score ')
        return rating if op=='rating' else Decimal(score)
    values=[r['value_num'] if r['value_num'] is not None else r['value_text'] for r in rows]
    if op=='value': return Decimal(str(values[0])) if isinstance(values[0],(int,float)) else values[0]
    a,b=map(lambda v:Decimal(str(v)),values)
    if op=='difference': return b-a
    if op=='bps': return (b-a)*100
    if op=='relative_percent': return ((b-a)/a*100).quantize(Decimal('0.1'))
    raise ValueError('Unknown oracle operation')

def verify(bundle):
    bundle=Path(bundle).resolve()
    records=[json.loads(line) for line in bundle.read_text().splitlines()]
    cases=json.loads(CASES.read_text())['cases']
    oracle=json.loads(ORACLE.read_text())['cases']
    facts={f['fact_id']:f for f in json.loads((CASES.parent/'facts.json').read_text())}
    require([r['case_id'] for r in records]==[c['id'] for c in cases],'Expected exactly six ordered cases')
    report={'bundle_sha256':digest(bundle),'oracle_sha256':digest(ORACLE),'cases':[],
            'human_verification':'PENDING_USER','model_turns':0,'scope':'CP5 Python replay only; not native parity or release'}
    identities=set()
    for record,case in zip(records,cases):
        item={'case_id':case['id'],'failures':[],'numeric_checks':{},'manual_checks':[]}
        def test(ok,message):
            if not ok: item['failures'].append(message)
        test(record['host']=='python' and record['provider'] in ('apple','openai'),'Unsupported host/provider')
        test(record['cases_sha256']==digest(CASES),'Case fixture identity mismatch')
        test(record['exit_code']==0,'Agent CLI nonzero exit')
        test(record.get('source_changed_during_case') is False,'Replay lacks an unchanged-source guard')
        evaluation=record.get('evaluation_code_sha256',{})
        test(all(evaluation.get(name)==digest(ROOT/name) for name in ['scripts/replay_cases.py','scripts/verify_replay.py','eval/cp5-oracle.json']),'Evaluation source identity missing/mismatched')
        trace=(bundle.parent/record['trace']).resolve()
        require(trace.is_relative_to(bundle.parent),'Trace escapes bundle directory')
        if not trace.exists():
            item['failures'].append('Missing raw trace'); report['cases'].append(item); continue
        test(digest(trace)==record['trace_sha256'],'Raw trace hash mismatch')
        events=[json.loads(line) for line in trace.read_text().splitlines()]
        try: check_trace(trace,require_live=True,expect_provider=record['provider'])
        except (ValueError,KeyError,TypeError) as e: item['failures'].append('Support/live check: '+str(e))
        header=events[0]
        identities.add((header['source_commit'],header['fixture_manifest_sha256'],json.dumps(header['provider'],sort_keys=True),json.dumps(header['code_sha256'],sort_keys=True)))
        starts=[e for e in events if e['event']=='turn_start']
        ends=[e for e in events if e['event']=='turn_end']
        report['model_turns'] += len(ends)
        test([e['user_message'] for e in starts]==case['prompts'],'Original three prompts changed/missing')
        test(len([e for e in events if e['event']=='session_start'])==1,'Expected fresh single session per case')
        if starts:
            state=starts[0]['state_before']
            test(not any(state.values()),'Case began with inherited state')
        test(len(ends)==3 and all(e['status']=='answered' for e in ends),'Not all three turns answered')
        claimed=set()
        for index,required in enumerate(oracle[case['id']]['turn_required_fact_ids']):
            ids={fid for c in (ends[index]['claims'] if index<len(ends) else []) for fid in c['fact_ids']}
            claimed.update(ids)
            test(set(required)<=ids,f'Turn {index+1}: missing required evidence '+','.join(sorted(set(required)-ids)))
        for key,expected in case['expected'].items():
            binding=oracle[case['id']]['numeric_bindings'][key]
            observed=bound_value(binding,facts)
            equal= observed==Decimal(str(expected)) if isinstance(expected,(int,float)) else observed==expected
            covered=set(binding['fact_ids'])<=claimed
            item['numeric_checks'][key]={'expected':expected,'fixture_calculation':str(observed),'required_fact_ids':binding['fact_ids'],'evidence_claimed':covered,'fixture_binding_matches':equal,
                                        'answer_arithmetic':'PENDING_USER; fixture calculation and evidence presence alone do not establish the answer stated this result'}
            test(equal and covered,'Numeric expectation not supported: '+key)
        for condition in case['must_preserve']:
            item['manual_checks'].append({'condition':condition,'verdict':'PENDING_USER','trace_turns':None,'evidence_and_reason':None})
        item['manual_checks'].append({'condition':'All requested arithmetic is stated correctly; answer intent, continuity, citations and unsupported-claim handling are appropriate','verdict':'PENDING_USER','trace_turns':None,'evidence_and_reason':None})
        item['automated_result']='FAIL' if item['failures'] else 'PASS_EVIDENCE_CHECKS_ONLY'
        report['cases'].append(item)
    require(len(identities)==1,'Mixed source/fixture/provider configurations across cases')
    report['automated_result']='FAIL' if any(c['failures'] for c in report['cases']) else 'PASS_EVIDENCE_CHECKS_ONLY'
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle',type=Path)
    p.add_argument('--report',type=Path,required=True,help='New JSON report/manual worksheet; never overwrite')
    a=p.parse_args()
    try:
        result=verify(a.bundle)
        with a.report.open('x') as f: json.dump(result,f,indent=2); f.write('\n')
        print(result['automated_result']+' | CP5 six-case evidence checks')
        print('PENDING_USER | every numeric answer and must_preserve condition requires manual review in '+str(a.report))
        return int(result['automated_result']=='FAIL')
    except (OSError,ValueError,KeyError,TypeError) as e:
        print('FAIL | CP5 replay | '+str(e),file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
