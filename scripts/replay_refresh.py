#!/usr/bin/env python3
"""Historical replay of reviewed fixture evidence, never network ingestion."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'evidence/london_office_evidence_pack'
BASE_IDS = ('F22', 'F23', 'F24', 'F25')
LATER_IDS = ('F26', 'F39')
SCHEMA = '''
CREATE TABLE replay_meta (version TEXT PRIMARY KEY, fixture_sha256 TEXT NOT NULL);
CREATE TABLE observations (fact_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE findings (finding_id TEXT PRIMARY KEY, entity TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT NOT NULL, fact_ids TEXT NOT NULL);
'''

def replay(path, reset=False, repeat=False):
    if Path(path).is_symlink(): raise ValueError('Symlink replay state refused')
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / 'output') or path.suffix != '.sqlite':
        raise ValueError('Replay state must be a .sqlite file under this checkout\'s ignored output/')
    if path.is_symlink(): raise ValueError('Symlink replay state refused')
    manifest=json.loads((ROOT/'inputs/manifest.json').read_text())
    for relative,expected in manifest['sha256'].items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError('Original evidence hash mismatch: '+relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    facts = {f['fact_id']:f for f in json.loads((FIXTURE/'facts.json').read_text())}
    sources = {s['source_id']:s for s in json.loads((FIXTURE/'sources.json').read_text())}
    fixture_hash = hashlib.sha256((ROOT/'inputs/manifest.json').read_bytes()).hexdigest()
    fresh = not path.exists()
    with sqlite3.connect(path) as db:
        if fresh:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO replay_meta VALUES (?,?)', ('cp5-v2',fixture_hash))
        if db.execute('SELECT * FROM replay_meta').fetchall() != [('cp5-v2',fixture_hash)]:
            raise ValueError('Replay state identity mismatch')
        if reset:
            db.execute('DELETE FROM findings'); db.execute('DELETE FROM observations')
        def add(ids):
            count=0
            for fid in ids:
                payload=json.dumps(facts[fid],sort_keys=True,separators=(',',':'))
                digest=hashlib.sha256(payload.encode()).hexdigest()
                old=db.execute('SELECT sha256,payload FROM observations WHERE fact_id=?',(fid,)).fetchone()
                if old and old!=(digest,payload): raise ValueError('Existing reviewed observation changed: '+fid)
                count += db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?)',(fid,payload,digest)).rowcount
            return count
        if repeat:
            if {x[0] for x in db.execute('SELECT fact_id FROM observations')} != set(BASE_IDS+LATER_IDS):
                raise ValueError('Repeat requires a completed first replay')
            new=add(LATER_IDS)
        else:
            if not fresh and not reset: raise ValueError('Existing state: use --repeat-reviewed-source or explicit --reset')
            add(BASE_IDS)
            new=add(LATER_IDS)
        findings=db.execute('INSERT OR IGNORE INTO findings VALUES (?,?,?,?,?)',
                            ('2-aldermanbury-practical-completion','2 Aldermanbury Square','S08',sources['S08']['url'],json.dumps(list(LATER_IDS)))).rowcount
        rows={fid:json.loads(payload) for fid,payload in db.execute('SELECT fact_id,payload FROM observations')}
        if any(row != facts[fid] for fid,row in rows.items()):
            raise ValueError('Historical reviewed observation changed')
        if rows['F23']['status'] != 'forecast' or rows['F25']['status'] != 'forecast':
            raise ValueError('Historical forecast status changed')
        return {'label':'historical replay; newly loaded does not mean newly occurred',
                'fixture_manifest_sha256':fixture_hash,'new_observations':new,'new_findings':findings,
                'new_project_milestones':findings,'observation_count':len(rows),
                'forecast_fact_ids_preserved':['F23','F25'],
                'finding':{'entity':'2 Aldermanbury Square','source_id':'S08','url':sources['S08']['url'],'published_at':sources['S08']['published_at'],'fact_ids':list(LATER_IDS)},
                'available_speculative_supply_inferred':False,'network_requests':0}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state',type=Path,required=True)
    g=p.add_mutually_exclusive_group(); g.add_argument('--reset',action='store_true'); g.add_argument('--repeat-reviewed-source',action='store_true')
    a=p.parse_args()
    try:
        print(json.dumps(replay(a.state,a.reset,a.repeat_reviewed_source),indent=2)); return 0
    except (OSError,ValueError,sqlite3.Error) as e:
        print('FAIL | historical replay | '+str(e),file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
