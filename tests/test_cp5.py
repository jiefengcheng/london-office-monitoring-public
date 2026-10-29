"""CP5 negative gates and replay custody; no model calls."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import replay_refresh as refresh
from scripts import verify_replay as review
from scripts import check_cp5_review as human

ROOT=Path(__file__).resolve().parents[1]

class RefreshTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'output').mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=ROOT/'output')
        self.addCleanup(self.temporary.cleanup)
        self.state=Path(self.temporary.name)/'refresh.sqlite'
        self.fixture=ROOT/'evidence/london_office_evidence_pack/evidence.sqlite'
        self.before=hashlib.sha256(self.fixture.read_bytes()).hexdigest()
    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.fixture.read_bytes()).hexdigest(),self.before)
    def test_first_and_duplicate_preserve_history(self):
        first=refresh.replay(self.state)
        self.assertEqual((first['new_observations'],first['new_findings']),(2,1))
        with sqlite3.connect(self.state) as db:
            forecasts=db.execute("SELECT payload FROM observations WHERE fact_id IN ('F23','F25') ORDER BY fact_id").fetchall()
            self.assertEqual([json.loads(f[0])['value_text'] for f in forecasts],['December 2025','Q1 2026'])
        again=refresh.replay(self.state,repeat=True)
        self.assertEqual((again['new_observations'],again['new_findings'],again['observation_count']),(0,0,6))
    def test_repeat_without_baseline_refused(self):
        with self.assertRaisesRegex(ValueError,'completed first'): refresh.replay(self.state,repeat=True)
    def test_unknown_database_is_not_reset(self):
        with sqlite3.connect(self.state) as db:
            db.execute('CREATE TABLE precious (value TEXT)')
            db.execute("INSERT INTO precious VALUES ('keep')")
        before=self.state.read_bytes()
        with self.assertRaises(sqlite3.Error): refresh.replay(self.state,reset=True)
        self.assertEqual(self.state.read_bytes(),before)
    def test_existing_requires_explicit_action(self):
        refresh.replay(self.state)
        with self.assertRaisesRegex(ValueError,'Existing state'): refresh.replay(self.state)
    def test_reset_is_repeatable(self):
        refresh.replay(self.state)
        self.assertEqual(refresh.replay(self.state,reset=True)['new_findings'],1)
    def test_frozen_fixture_cannot_be_reset(self):
        with self.assertRaisesRegex(ValueError,'ignored output'): refresh.replay(self.fixture,reset=True)
    def test_changed_observation_fails(self):
        refresh.replay(self.state)
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE observations SET sha256='tampered' WHERE fact_id='F26'")
        with self.assertRaisesRegex(ValueError,'changed'): refresh.replay(self.state,repeat=True)
    def test_changed_old_forecast_fails_on_repeat(self):
        refresh.replay(self.state)
        with sqlite3.connect(self.state) as db:
            payload=json.loads(db.execute("SELECT payload FROM observations WHERE fact_id='F23'").fetchone()[0])
            payload['value_text']='changed old forecast'
            db.execute("UPDATE observations SET payload=? WHERE fact_id='F23'",(json.dumps(payload),))
        with self.assertRaisesRegex(ValueError,'observation changed'): refresh.replay(self.state,repeat=True)
    def test_symlink_refused(self):
        refresh.replay(self.state)
        link=self.state.parent/'link.sqlite'; link.symlink_to(self.state)
        with self.assertRaisesRegex(ValueError,'Symlink'): refresh.replay(link,reset=True)

class EvaluationTests(unittest.TestCase):
    def test_every_original_expectation_has_binding(self):
        cases=json.loads(review.CASES.read_text())['cases']
        oracle=json.loads(review.ORACLE.read_text())['cases']
        facts={f['fact_id']:f for f in json.loads((review.CASES.parent/'facts.json').read_text())}
        self.assertEqual(len(cases),6)
        self.assertEqual(sum(len(c['prompts']) for c in cases),18)
        for c in cases:
            spec=oracle[c['id']]
            self.assertEqual(set(spec['numeric_bindings']),set(c['expected']))
            self.assertEqual(len(spec['turn_required_fact_ids']),len(c['prompts']))
            for key,expected in c['expected'].items():
                observed=review.bound_value(spec['numeric_bindings'][key],facts)
                self.assertEqual(observed,review.Decimal(str(expected)) if isinstance(expected,(int,float)) else expected)
    def test_incomplete_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'run.jsonl';p.write_text('')
            with self.assertRaisesRegex(ValueError,'six ordered'): review.verify(p)
    def test_runtime_does_not_import_evaluation_oracles(self):
        for name in ['agent.py','agent_loop.py','apple_provider.py','apple_worker.py','providers.py']:
            source=(ROOT/name).read_text()
            self.assertNotIn('cp5-oracle',source)
            self.assertNotIn('chat_cases.json',source)

class HumanGateTests(unittest.TestCase):
    def setUp(self):
        self.automated={'automated_result':'PASS_EVIDENCE_CHECKS_ONLY','bundle_sha256':'bundle','oracle_sha256':'oracle','cases':[{'case_id':'market_change','manual_checks':[{'condition':'scope'}]}]}
        self.sheet={'bundle_sha256':'bundle','oracle_sha256':'oracle','human_verification':'PASS','reviewer':'test reviewer','reviewed_at':'2026-10-28','cases':[{'case_id':'market_change','manual_checks':[{'condition':'scope','verdict':'PASS','evidence_and_reason':'Observed dates and geography on turn 1','trace_turns':[1]}],'numeric_checks':{}}]}
        self.automated['cases'][0]['numeric_checks']={}
    def gate(self):
        with patch.object(human,'verify',return_value=self.automated): return human.check_review('unused',self.sheet)
    def test_complete_manual_record_accepted(self):
        self.assertEqual(self.gate()['reviewer'],'test reviewer')
    def test_manual_cannot_override_failed_live_checks(self):
        self.automated['automated_result']='FAIL'
        with self.assertRaisesRegex(ValueError,'cannot override'): self.gate()
    def test_missing_scope_condition_refused(self):
        self.sheet['cases'][0]['manual_checks']=[]
        with self.assertRaisesRegex(ValueError,'condition missing'): self.gate()
    def test_pending_verdict_refused(self):
        self.sheet['cases'][0]['manual_checks'][0]['verdict']='PENDING_USER'
        with self.assertRaisesRegex(ValueError,'Unaccepted'): self.gate()
    def test_missing_evidence_refused(self):
        self.sheet['cases'][0]['manual_checks'][0]['evidence_and_reason']=''
        with self.assertRaisesRegex(ValueError,'reason missing'): self.gate()
    def test_review_for_other_bundle_refused(self):
        self.sheet['bundle_sha256']='another'
        with self.assertRaisesRegex(ValueError,'identity mismatch'): self.gate()
    def test_pending_numeric_answer_refused(self):
        self.automated['cases'][0]['numeric_checks']={'change':{'answer_arithmetic':'PENDING_USER'}}
        self.sheet['cases'][0]['numeric_checks']={'change':{'answer_arithmetic':'PENDING_USER'}}
        with self.assertRaisesRegex(ValueError,'Numeric answer'): self.gate()

if __name__=='__main__': unittest.main()
