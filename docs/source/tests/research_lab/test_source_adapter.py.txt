"""Authored source semantics; no real payloads, providers or model artifacts."""
from copy import deepcopy
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import source_adapter as a
from core import Store

OBS='2026-10-01T00:00:00Z'
CUT='2026-10-20T00:00:00Z'
GS='00-0000001';QB='00-0000002';OL='00-0000003';OTHER='00-0000004'

def fixture(week):
    return {'season':2026,'week':week,'game_id':f'g-{week}','home_team':'BUF','away_team':'NE',
            'kickoff_utc':f'2026-09-{week+1:02d}T17:00:00Z','identity_verified':True,
            'observed_at':'2026-09-01T00:00:00Z','completed':True,
            'completion_observed_at':f'2026-09-{week+1:02d}T21:00:00Z','snap_complete':True,'pbp_complete':True}

def roster(week,gs=GS,pos='RB',tm='BUF',**kw):
    return dict(season=2026,week=week,game_type='REG',gsis_id=gs,team=tm,position=pos,
                pfr_id='alias-'+gs,observed_at=OBS,**kw)

def stat(week,gs=GS,**kw):
    return dict(season=2026,week=week,season_type='REG',game_id=f'g-{week}',player_id=gs,team='BUF',
                observed_at=OBS,**dict(dict(targets=0,receptions=0,receiving_yards=0,carries=2,rushing_yards=-3),**kw))

def snap(week,gs=GS,n=20,**kw):
    return dict(season=2026,week=week,game_type='REG',game_id=f'g-{week}',pfr_player_id='alias-'+gs,
                team='BUF',offense_snaps=n,observed_at=OBS,**kw)

def play(week,pid,**kw):
    return dict(season=2026,week=week,season_type='REG',game_id=f'g-{week}',play_id=str(pid),
                home_team='BUF',away_team='NE',posteam='BUF',observed_at=OBS,**kw)

def data():
    fixtures=[fixture(w) for w in (1,2,3)]
    rosters=[roster(w,gs,pos) for w in (1,2,3) for gs,pos in ((GS,'RB'),(QB,'QB'),(OL,'T'))]
    stats=[stat(w) for w in (1,2)]
    snaps=[snap(w,gs,n) for w in (1,2) for gs,n in ((GS,20),(QB,40),(OL,80))]
    pbp=[]
    for w in (1,2):
        pbp.extend([play(w,1,play_type='run',rush_attempt=1,rusher_player_id=GS,rushing_yards=-3),
                    play(w,2,play_type='game_end',desc='END GAME')])
    return fixtures,rosters,stats,snaps,pbp

def project(bundle=None,**kw):
    return a.project_canonical(*(bundle or data()),cutoff=CUT,target_period=(2026,3),classification='SYNTHETIC',**kw)

class ProjectionTests(unittest.TestCase):
    def test_signed_yards_and_max_individual_snap_denominator(self):
        result=project();row=result['records'][0]
        self.assertEqual(row['rushing_yards'],-3)
        self.assertEqual(row['snap_denominator'],80)
        self.assertEqual(row['snap_share'],.25)
        self.assertEqual(row['value_states']['receiving_yards'],'ZERO')
        self.assertFalse(result['source_certificate_created'])
        self.assertFalse(result['ready_for_model'])
    def test_omitted_stat_row_is_not_zero_without_category(self):
        bundle=list(data());bundle[2]=[]
        row=project(bundle)['records'][0]
        self.assertTrue(row['appeared']);self.assertIsNone(row['targets'])
        self.assertEqual(row['value_states']['targets'],'UNKNOWN')
    def test_complete_category_restores_positive_snap_zero(self):
        bundle=list(data());bundle[2]=[]
        cats=[dict(game_id=f'g-{w}',team='BUF',channel=channel,complete=True,identity_verified=True,
                   players={},totals=dict.fromkeys(a.CHANNEL_FIELDS[channel],0),observed_at=OBS)
              for w in (1,2) for channel in ('receiving','rushing')]
        row=project(bundle,categories=cats)['records'][0]
        self.assertEqual(row['receiving_yards'],0)
        self.assertEqual(row['rushing_yards'],0)
        self.assertIn('POSITIVE_SNAPS_COMPLETE_CATEGORY',row['source_basis'])
    def test_absent_or_unreconciled_category_never_certifies_zero(self):
        bundle=list(data());bundle[2]=[]
        cat=dict(game_id='g-1',team='BUF',channel='receiving',complete=True,identity_verified=True,
                 players={},totals={'targets':1,'receptions':0,'receiving_yards':0},observed_at=OBS)
        result=project(bundle,categories=[cat]);self.assertIsNone(result['records'][0]['targets'])
        self.assertTrue(any(r['code']=='CATEGORY_TOTALS_NOT_RECONCILED' for r in result['quarantine']))
    def test_dnp_is_distinct_from_unwitnessed_participation(self):
        bundle=list(data());bundle[2]=[];bundle[3]=[]
        bundle[1][0]['did_not_play']=True
        rows=project(bundle)['records'];self.assertEqual(rows[0]['state'],'DNP')
        self.assertIsNone(rows[0]['receiving_yards']);self.assertEqual(rows[1]['state'],'UNKNOWN_PARTICIPATION')
    def test_missing_completion_withholds_actuals(self):
        bundle=list(data());bundle[0][0].pop('completion_observed_at')
        row=project(bundle)['records'][0]
        self.assertEqual(row['state'],'COMPLETION_UNVERIFIED');self.assertIsNone(row['rushing_yards'])
    def test_zero_snap_positive_stat_is_conflict(self):
        bundle=list(data());bundle[3][0]['offense_snaps']=0
        row=project(bundle)['records'][0]
        self.assertEqual(row['state'],'PARTICIPATION_CONFLICT');self.assertIsNone(row['rushing_yards'])
    def test_target_label_poisoning_changes_nothing(self):
        base=data();poison=deepcopy(base)
        poison[2].append(stat(3,carries={'poison':'must not parse'},rushing_yards=10**15))
        poison[3].append(snap(3,n={'poison':'must not parse'}))
        poison[4].append(play(3,99,rushing_yards={'poison':'must not parse'}))
        self.assertEqual(project(base),project(poison))
    def test_future_observation_is_withheld(self):
        bundle=list(data());bundle[2][0]['observed_at']='2027-01-01T00:00:00Z'
        row=project(bundle)['records'][0]
        self.assertIsNone(row['rushing_yards'])
    def test_temporal_membership_does_not_borrow_future_alias(self):
        bundle=list(data());bundle[1]=[r for r in bundle[1] if not(r['week']==1 and r['gsis_id']==GS)]
        result=project(bundle)
        self.assertFalse(any(r['week']==1 and r['gsis']==GS for r in result['records']))
        self.assertTrue(any('DATED_' in r['code'] for r in result['quarantine']))
    def test_player_transfer_remains_week_specific(self):
        bundle=list(data());future=next(r for r in bundle[1] if r['week']==3 and r['gsis_id']==GS);future['team']='NE'
        result=project(bundle)
        self.assertEqual(result['census'][0]['team'],'NE')
        self.assertEqual({r['team'] for r in result['records']},{'BUF'})
    def test_lateral_credit_precedes_qb_scramble_exclusion(self):
        bundle=list(data());bundle[4].insert(0,play(1,8,play_type='run',rush_attempt=1,qb_scramble=1,
            rusher_player_id=QB,rushing_yards=40,lateral_rusher_player_id=GS,lateral_rushing_yards=7))
        row=next(r for r in project(bundle)['rbfb_context'] if r['game_id']=='g-1' and r['team']=='BUF')
        self.assertEqual(row['rb_carries'],1);self.assertEqual(row['rb_yards'],4)
    def test_qb_kneels_excluded_and_unknown_rusher_blocks_context(self):
        bundle=list(data());bundle[4].insert(0,play(1,8,play_type='run',rush_attempt=1,qb_kneel=1,rusher_player_id=QB,rushing_yards=-1))
        result=project(bundle);self.assertEqual(result['rbfb_context'][0]['rb_yards'],-3)
        bundle[4].insert(0,play(1,9,play_type='run',rush_attempt=1,rusher_player_id=OTHER,rushing_yards=5))
        row=project(bundle)['rbfb_context'][0];self.assertIsNone(row['rb_yards'])
    def test_full_week_context_is_not_truncated_at_week_one(self):
        result=project();self.assertEqual({r['week'] for r in result['team_context']},{1,2})
        self.assertEqual(len(result['team_context']),4)
    def test_no_fixture_id_construction_from_stats(self):
        bundle=list(data());bundle[0]=[]
        result=project(bundle);self.assertEqual(result['records'],[]);self.assertEqual(result['census'],[])
    def test_unknown_denominator_not_filled_from_partial_rows(self):
        bundle=list(data());bundle[0][0]['snap_complete']=False
        row=project(bundle)['records'][0];self.assertIsNone(row['snap_share'])
        bundle=list(data());bundle[3][2]['offense_snaps']=None
        row=project(bundle)['records'][0];self.assertIsNone(row['snap_share'])
    def test_conflicting_fixture_withheld(self):
        bundle=list(data());conflict=dict(bundle[0][0],home_team='MIA');bundle[0].append(conflict)
        result=project(bundle);self.assertFalse(any(r['game_id']=='g-1' for r in result['records']))
    def test_conflicting_dated_positions_withheld(self):
        bundle=list(data());bundle[1].append(roster(1,GS,'WR'))
        result=project(bundle);self.assertFalse(any(r['game_id']=='g-1' and r['gsis']==GS for r in result['records']))

    def test_phase_not_assumed_and_end_marker_is_not_full_pbp(self):
        bundle=list(data());bundle[1][0].pop('game_type')
        self.assertFalse(any(r['week']==1 for r in project(bundle)['records']))
        bundle=list(data());bundle[2][0].pop('season_type')
        self.assertIsNone(project(bundle)['records'][0]['rushing_yards'])
        bundle=list(data());bundle[0][0]['pbp_complete']=False
        self.assertIsNone(project(bundle)['rbfb_context'][0]['rb_yards'])

    def test_team_receiving_denominator_requires_full_category(self):
        self.assertIsNone(project()['team_context'][0]['targets'])
        cat=dict(game_id='g-1',team='BUF',channel='receiving',complete=True,identity_verified=True,
                 players={GS:dict(targets=2,receptions=1,receiving_yards=4)},
                 totals=dict(targets=2,receptions=1,receiving_yards=4),observed_at=OBS)
        result=project(categories=[cat])
        self.assertEqual(result['team_context'][0]['targets'],2)
        duplicated=project(categories=[cat,deepcopy(cat)])
        self.assertIsNone(duplicated['team_context'][0]['targets'])

    def test_dnp_requires_completed_fixture(self):
        bundle=list(data());bundle[2]=[];bundle[3]=[];bundle[1][0]['did_not_play']=True
        bundle[0][0]['completed']=False
        self.assertEqual(project(bundle)['records'][0]['state'],'COMPLETION_UNVERIFIED')

class MetadataAndGateTests(unittest.TestCase):
    def test_metadata_selects_2026_and_never_opens_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'data/private/puntproof/data-manager';directory.mkdir(parents=True)
            with sqlite3.connect(directory/'acquisitions.sqlite3') as db:
                db.execute('CREATE TABLE snapshots(id,source,dataset,season,acquired,raw_path,raw_hash,records_path,records_hash)')
                for year in (2025,2026):
                    db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)',(str(year),'player_stats','player_stats',year,OBS,'absent.raw','a'*64,'absent.jsonl.gz','b'*64))
            db.close()
            result=a.select_snapshot_metadata(root,as_of=CUT,sources=('player_stats',))
            self.assertEqual(len(result['snapshots']),1);self.assertEqual(result['snapshots'][0]['season'],2026)
            self.assertEqual(result['payloads_opened'],0)
            with self.assertRaises(ValueError):a.select_snapshot_metadata(root,as_of=CUT,sources=('snap_counts',))
    def test_real_reader_fails_before_file_access_without_source_context(self):
        snapshot=dict(records_path='never-open-this.gz',records_hash='a'*64,dataset='player_stats',season=2026,source='player_stats',acquired=OBS)
        with self.assertRaisesRegex(ValueError,'SOURCE_CONTEXT_REQUIRED'):
            a.read_bound_snapshot(Path.cwd(),snapshot)
    def test_bound_reader_honors_field_allowlist_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'authored.jsonl.gz'
            native={'season':'2026','week':'1','player_id':GS,'team':'BUF','targets':'0','private_field':'never project'}
            p.write_bytes(gzip.compress((json.dumps(native)+'\n').encode(),mtime=0))
            snapshot=dict(records_path=str(p),records_hash=hashlib.sha256(p.read_bytes()).hexdigest(),dataset='player_stats',season=2026,source='player_stats',acquired=OBS)
            snapshot.update(id='authored',raw_hash='f'*64)
            snapshot['metadata_row']={k:snapshot[k] for k in ('id','source','dataset','season','acquired','raw_hash','records_hash','records_path')}
            witness={'schema_version':'puntproof-lab-producer-metadata-witness-v1','row':dict(snapshot['metadata_row'])}
            grant={'allowed_fields':list(a.FIELD_ALLOWLISTS['player_stats']),'permission':'ADMITTED_RETAINED_SOURCE','metadata_witness':{'path':'authored-metadata.json','sha256':'d'*64}}
            with patch('nfl_model.research_lab_gate.require_retained_source',return_value=grant) as gate, patch('nfl_model.research_lab_gate.value',return_value=witness):
                result=a.read_bound_snapshot(Path(tmp),snapshot)
                gate.assert_called_once();self.assertNotIn('private_field',result['records'][0])
                self.assertEqual(result['records'][0]['targets'],'0')
                with self.assertRaisesRegex(ValueError,'SOURCE_ROW_LIMIT'):a.read_bound_snapshot(Path(tmp),snapshot,max_rows=0)
                with self.assertRaisesRegex(ValueError,'SOURCE_COMPRESSED_SIZE_LIMIT'):a.read_bound_snapshot(Path(tmp),snapshot,max_compressed_bytes=1)
                wrong=dict(snapshot,records_hash='0'*64)
                with self.assertRaisesRegex(ValueError,'SOURCE_SNAPSHOT_RECORDS_HASH_CHANGED'):a.read_bound_snapshot(Path(tmp),wrong)
                backdated=dict(snapshot,acquired='2026-01-01T00:00:00Z')
                with self.assertRaisesRegex(ValueError,'SOURCE_SNAPSHOT_ACQUIRED_CHANGED'):a.read_bound_snapshot(Path(tmp),backdated)
                p.write_bytes(p.read_bytes()+b'changed')
                with self.assertRaisesRegex(ValueError,'SOURCE_BYTES_CHANGED'):a.read_bound_snapshot(Path(tmp),snapshot)
    def test_next_week_metadata_report_never_claims_source_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'lab')
            result=a.persist_source_readiness(store,Path(tmp)/'absent',as_of=CUT)
            self.assertEqual(result['status'],'SOURCE_NOT_READY');self.assertEqual(result['required_next_period'],[2026,7])
            self.assertEqual(result['payloads_opened'],0);self.assertFalse(result['forecast_issued'])
            self.assertTrue(store.records('receipt'));self.assertTrue(store.alerts())
            again=a.persist_source_readiness(store,Path(tmp)/'absent',as_of='2026-10-20T00:01:00Z')
            self.assertEqual(result,again);self.assertEqual(len(store.records('receipt')),1)
            self.assertEqual(store.get_setting('last_source_readiness'),result)
            self.assertEqual(store.get_setting('source_readiness_last_checked'),'2026-10-20T00:01:00Z')
            self.assertTrue(all(row['occurrences']==1 for row in store.alerts()))
            self.assertTrue(all(row['next_action'] for row in result['blockers']))

    def test_metadata_future_clock_denied(self):
        with self.assertRaisesRegex(ValueError,'METADATA_FUTURE_AS_OF'):
            a.select_snapshot_metadata(Path('unused'),as_of='2099-01-01T00:00:00Z')

    def test_producer_week_coverage_is_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'data/private/puntproof/data-manager';directory.mkdir(parents=True)
            db=sqlite3.connect(directory/'acquisitions.sqlite3')
            try:
                db.execute('CREATE TABLE snapshots(id,source,dataset,season,acquired,raw_path,raw_hash,records_path,records_hash,records,metadata)')
                for source,weeks in [('player_stats',['1','6']),('rosters',['7']),('pbp',['1','6'])]:
                    db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',(source,source,source,2026,OBS,'absent.raw','a'*64,'absent.jsonl.gz','b'*64,10,json.dumps({'validation':{'weeks':weeks,'validated':10,'rejected':0}})))
                db.commit()
            finally:db.close()
            result=a.next_week_metadata_readiness(root,as_of=CUT)
            gaps=[r for r in result['blockers'] if r['code']=='HISTORY_OR_IDENTITY_SOURCE_GAP']
            self.assertEqual(gaps,[])
            self.assertIn('target outcomes',result['dependencies']['forecast'])
            self.assertEqual(next(r for r in result['source_coverage'] if r['source']=='rosters')['declared_max_week'],7)
            self.assertEqual(result['payloads_opened'],0)


class RetainedPreparationTests(unittest.TestCase):
    def bundle(self):
        old=data();schedule=[]
        for w in (1,2,3):
            schedule.append(dict(season='2026',week=str(w),game_type='REG',game_id=f'2026_{w:02d}_NE_BUF',
                home_team='BUF',away_team='NE',gameday=f'2026-09-{w+1:02d}',gametime='13:00',observed_at='2026-09-01T00:00:00Z'))
        rosters=deepcopy(old[1]);stats=deepcopy(old[2]);pbp=deepcopy(old[4])
        for r in stats+pbp:r['game_id']=f"2026_{r['week']:02d}_NE_BUF"
        for w in (1,2):pbp.append(dict(play(w,3,play_type='run',rush_attempt=1,rusher_player_id=GS,rushing_yards=0),game_id=f'2026_{w:02d}_NE_BUF'))
        return schedule,rosters,stats,pbp
    def prepare(self,bundle=None):
        return a.prepare_retained_inputs(*(bundle or self.bundle()),cutoff=CUT,target_period=(2026,3))
    def test_supported_subset_with_null_snaps(self):
        bundle=list(self.bundle())
        bundle[1].extend(roster(w,OTHER,'WR') for w in (1,2,3))
        result=self.prepare(bundle)
        self.assertEqual(len(result['fixtures']['rushing']),1)
        self.assertTrue(all(r['offense_snaps'] is None and r['snap_share'] is None for r in result['data']['records']))
        self.assertTrue(any(r['fixture']['gsis']==OTHER and r['reason']=='UNRESOLVED_PRIOR_PARTICIPATION' for r in result['exclusions']))
        self.assertFalse(result['full_fixture_census_verified'])
        self.assertEqual(result['target_outcomes_used'],0)
        self.assertEqual(result['history_through'],(2026,2))
    def test_future_production_never_enters_features(self):
        base=self.bundle();poison=deepcopy(base)
        poison[2].append(dict(stat(3),game_id='2026_03_NE_BUF',targets={'not':'a statistic'}))
        poison[3].append(dict(play(3,99),game_id='2026_03_NE_BUF',rushing_yards={'poison':True}))
        one=self.prepare(base);two=self.prepare(poison)
        for key in ('data','rbfb_context','fixtures'):self.assertEqual(one[key],two[key])
    def test_missing_kickoff_or_duplicate_fixture_is_not_invented(self):
        bundle=list(self.bundle());bundle[0][2]['gametime']=None
        self.assertFalse(self.prepare(bundle)['fixtures']['rushing'])
        self.assertTrue(any(r['code']=='SCHEDULE_KICKOFF_UNKNOWN' for r in self.prepare(bundle)['quarantine']))
    def test_inconsistent_receiving_total_becomes_unknown(self):
        bundle=list(self.bundle());bundle[2][0]['targets']=1
        result=self.prepare(bundle)
        self.assertIsNone(next(r for r in result['data']['records'] if r['week']==1)['targets'])
        self.assertFalse(result['fixtures']['receiving'])
        self.assertTrue(result['fixtures']['rushing'])
    def test_unresolved_prior_roster_week_is_excluded(self):
        bundle=list(self.bundle());bundle[1]=[r for r in bundle[1] if not(r['week']==2 and r['gsis_id']==GS)]
        result=self.prepare(bundle)
        self.assertFalse(result['fixtures']['rushing'])
        self.assertTrue(any(r['reason']=='UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP' for r in result['exclusions']))
    def test_settlement_waits_for_completion_without_invented_rows(self):
        bundle=list(self.bundle());bundle[3]=[r for r in bundle[3] if r['week']!=2]
        census={'verified':True,'target_game_ids':['2026_02_NE_BUF']}
        with patch.object(a,'verify_schedule_census',return_value=census):
            result=a.prepare_retained_settlement(*bundle,[],target_period=(2026,2),observed_at=CUT)
        self.assertEqual(result['status'],'WAITING');self.assertEqual(result['outcomes'],{})
        self.assertEqual(result['dependency'],'TARGET_WEEK_COMPLETED_PBP')
    def test_settlement_exact_population_known_and_unknown(self):
        forecasts=[dict(season=2026,week=2,game_id='2026_02_NE_BUF',gsis=gs,team='BUF',opponent='NE',position='RB',channel='rushing') for gs in (GS,OTHER)]
        census={'verified':True,'target_game_ids':['2026_02_NE_BUF']}
        with patch.object(a,'verify_schedule_census',return_value=census):
            result=a.prepare_retained_settlement(*self.bundle(),forecasts,target_period=(2026,2),observed_at=CUT)
        self.assertEqual(result['status'],'VERIFIED_COMPLETE')
        known,unknown=result['outcomes']['rushing'];self.assertEqual(known['yards'],-3)
        self.assertEqual(known['opportunities'],2);self.assertEqual(unknown['status'],'UNKNOWN');self.assertIsNone(unknown['yards'])
    def test_schedule_reader_projects_only_2026_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'schedule.csv'
            path.write_text('season,game_type,game_id,week,gameday,gametime,away_team,home_team,home_score\n2025,REG,protected,1,never,never,XX,YY,DO_NOT_USE\n2026,REG,2026_03_NE_BUF,3,2026-09-04,13:00,NE,BUF,DO_NOT_USE\n')
            h=hashlib.sha256(path.read_bytes()).hexdigest()
            grant={'format':'CSV_2026_REG_IDENTITY_ONLY','allowed_fields':list(a.SCHEDULE_FIELDS),'observed_at':OBS,'permission':'ADMITTED_RETAINED_SOURCE','source_id':'authored'}
            with patch('nfl_model.research_lab_gate.require_retained_source',return_value=grant):
                result=a.read_bound_schedule(Path(tmp),{'path':str(path),'sha256':h})
            self.assertEqual(result['row_count'],1)
            self.assertEqual(set(result['records'][0]),a.SCHEDULE_FIELDS|{'_source'})
            self.assertNotIn('DO_NOT_USE',json.dumps(result))


    def test_verified_timezone_uses_eastern_dst(self):
        from datetime import timedelta
        zone=a.schedule_timezone()
        self.assertEqual(datetime(2026,9,1,13,tzinfo=zone).utcoffset(),timedelta(hours=-4))
        self.assertEqual(datetime(2026,12,1,13,tzinfo=zone).utcoffset(),timedelta(hours=-5))
    def test_full_retained_census_requires_272_unique_games(self):
        teams=sorted(a.NFL_TEAMS);rounds=[]
        for n in range(17):
            matches=[(teams[i],teams[-1-i]) for i in range(16)]
            for i,(away,home) in enumerate(matches):
                week=n+1 if n<9 else (10 if i<8 else 11) if n==9 else n+2
                rounds.append(dict(season=2026,week=week,game_type='REG',away_team=away,home_team=home,game_id=f'2026_{week:02d}_{away}_{home}'))
            teams=[teams[0],teams[-1],*teams[1:-1]]
        self.assertTrue(a.verify_schedule_census(rounds,7)['verified'])
        self.assertFalse(a.verify_schedule_census(rounds[:-1],7)['verified'])
        self.assertFalse(a.verify_schedule_census(rounds+[rounds[0]],7)['verified'])
    def test_payload_reader_withholds_future_numeric_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'authored.jsonl.gz'
            rows=[{'season':'2026','week':str(w),'player_id':GS,'team':'BUF','targets':value} for w,value in ((1,'0'),(2,{'must':'not project'}))]
            p.write_bytes(gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode(),mtime=0))
            snapshot=dict(id='authored',records_path=str(p),records_hash=hashlib.sha256(p.read_bytes()).hexdigest(),raw_hash='f'*64,dataset='player_stats',season=2026,source='player_stats',acquired=OBS)
            snapshot['metadata_row']={k:snapshot[k] for k in ('id','source','dataset','season','acquired','raw_hash','records_hash','records_path')}
            witness={'schema_version':'puntproof-lab-producer-metadata-witness-v1','row':snapshot['metadata_row']}
            grant={'allowed_fields':list(a.FIELD_ALLOWLISTS['player_stats']),'permission':'ADMITTED_RETAINED_SOURCE','metadata_witness':{'path':'authored.json','sha256':'d'*64}}
            with patch('nfl_model.research_lab_gate.require_retained_source',return_value=grant),patch('nfl_model.research_lab_gate.value',return_value=witness):
                result=a.read_bound_snapshot(Path(tmp),snapshot,max_period=(2026,1))
            self.assertEqual(result['scanned_rows'],2);self.assertEqual(result['withheld_future_rows'],1)
            self.assertEqual(result['row_count'],1);self.assertEqual(result['records'][0]['targets'],'0')

    def test_native_stats_missing_game_id_join_only_verified_schedule(self):
        bundle=list(self.bundle())
        for r in bundle[2]:r.pop('game_id')
        result=self.prepare(bundle)
        self.assertEqual(len(result['fixtures']['rushing']),1)
        self.assertEqual({r['game_id'] for r in result['data']['records']},{'2026_01_NE_BUF','2026_02_NE_BUF'})
        bundle[2][0]['opponent_team']='MIA'
        self.assertFalse(self.prepare(bundle)['fixtures']['rushing'])
    def test_ambiguous_native_team_week_join_is_withheld(self):
        bundle=list(self.bundle());bundle[2][0].pop('game_id')
        duplicate=dict(bundle[0][0],game_id='2026_01_NYG_BUF',away_team='NYG')
        bundle[0].append(duplicate)
        result=self.prepare(bundle)
        self.assertTrue(any(r['code']=='EXACT_FIXTURE_JOIN_UNRESOLVED' for r in result['quarantine']))
        self.assertFalse(result['fixtures']['rushing'])
