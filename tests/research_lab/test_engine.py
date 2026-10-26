"""Stdlib-only bridge boundary checks; never import supplied engine libraries."""
from pathlib import Path
import copy
import json
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import engine_bridge as bridge


def fixture(week=7):
    return {'season':2026,'week':week,'game_id':'2026_07_SYN_A_B','gsis':'00-0000001',
            'team':'AAA','opponent':'BBB','position':'RB','channel':'rushing'}


class EngineBoundaryTests(unittest.TestCase):
    def test_fixture_forbids_outcome_and_unknown_fields(self):
        for key in ('y','workload','actual_snap_share','yards','targets','appeared'):
            row=fixture();row[key]=0
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'METADATA_ONLY'):
                bridge.validate_fixtures([row],[2026,7])

    def test_fixture_rejects_duplicate_wrong_week_qb_and_bad_identity(self):
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):
            bridge.validate_fixtures([fixture(),fixture()],[2026,7])
        for key,value in [('week',8),('position','QB'),('gsis','unknown'),('season',True)]:
            row=fixture();row[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                bridge.validate_fixtures([row],[2026,7])

    def test_signed_zero_unknown_and_nonappearance_are_distinct(self):
        for yards in (-8,0,31):
            row=dict(fixture(),status='APPEARED',yards=yards,opportunities=0)
            self.assertEqual(bridge.validate_outcomes([row],[fixture()],[2026,7])[0]['yards'],yards)
        for status in ('UNKNOWN','NONAPPEARANCE'):
            row=dict(fixture(),status=status,yards=None,opportunities=None)
            bridge.validate_outcomes([row],[fixture()],[2026,7])
            row['yards']=0
            with self.assertRaisesRegex(ValueError,'UNKNOWN_IS_NOT_ZERO'):
                bridge.validate_outcomes([row],[fixture()],[2026,7])

    def test_settlement_exact_census_and_integer_labels(self):
        valid=dict(fixture(),status='APPEARED',yards=0,opportunities=0)
        for rows in ([],[valid,valid],[dict(valid,gsis='00-0000002')]):
            with self.assertRaises(ValueError):bridge.validate_outcomes(rows,[fixture()],[2026,7])
        for key,value in [('yards',True),('yards',.5),('opportunities',-1),('opportunities',None)]:
            with self.assertRaises(ValueError):
                bridge.validate_outcomes([dict(valid,**{key:value})],[fixture()],[2026,7])

    def test_json_rejects_duplicates_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'x.json'
            for text in ('{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}'):
                path.write_text(text)
                with self.assertRaises(ValueError):bridge.read_json(path)

    def test_safe_path_stays_within_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(bridge.safe_path(root,'child/x'),root/'child/x')
            with self.assertRaisesRegex(ValueError,'PATH_ESCAPE'):bridge.safe_path(root,'../outside')
            with self.assertRaisesRegex(ValueError,'PATH_ESCAPE'):bridge.safe_path(root,'.')

    def test_network_denial_restores_originals(self):
        original=socket.socket
        with bridge.no_network():
            with self.assertRaisesRegex(RuntimeError,'NETWORK_DENIED'):socket.getaddrinfo('example.test',443)
            with self.assertRaisesRegex(RuntimeError,'NETWORK_DENIED'):socket.create_connection(('127.0.0.1',1))
            with socket.socket() as sock:
                with self.assertRaisesRegex(RuntimeError,'NETWORK_DENIED'):sock.connect(('127.0.0.1',1))
        self.assertIs(socket.socket,original)

    def test_phase_denial_happens_before_package_or_source(self):
        def denied(capability):raise ValueError('phase denied')
        gate=SimpleNamespace(require_phase=denied)
        request={'action':'forecast','channel':'receiving','period':[2026,7]}
        with patch.object(bridge,'verify_package') as package,patch.object(bridge,'read_json') as source:
            with self.assertRaisesRegex(ValueError,'phase denied'):
                bridge._execute_admitted(bridge.ROOT,request,{'season':2026,'week':7},gate)
            package.assert_not_called();source.assert_not_called()

    def test_settle_requires_learning_phase_first(self):
        called=[]
        def denied(capability):called.append(capability);raise ValueError('phase denied')
        request={'action':'settle','channel':'rushing','period':[2026,7]}
        with self.assertRaisesRegex(ValueError,'phase denied'):
            bridge._execute_admitted(bridge.ROOT,request,{'season':2026,'week':7},SimpleNamespace(require_phase=denied))
        self.assertEqual(called,['learn'])

    def test_unadmitted_input_rejected_before_payload(self):
        gate=SimpleNamespace(reference=lambda *a: self.fail('unadmitted path resolved'))
        with self.assertRaisesRegex(ValueError,'INPUT_NOT_ADMITTED'):
            bridge._bound_input(bridge.ROOT,{'path':'somewhere','sha256':'0'*64},{'input_files':[]},gate)

    def test_manifest_tamper_rejected_before_engine_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            package=Path(tmp);(package/'PACKAGE_MANIFEST.json').write_text('{}')
            with patch.object(bridge,'import_engine') as imported:
                with self.assertRaisesRegex(ValueError,'PACKAGE_MANIFEST_CHANGED'):
                    bridge.verify_package(package)
                imported.assert_not_called()

    def test_unrecognized_package_manifest_not_accepted_from_caller(self):
        with self.assertRaisesRegex(ValueError,'UNRECOGNIZED_PACKAGE_MANIFEST'):
            bridge.verify_package(Path('not-read'),'0'*64)


    def test_swapped_seed_channel_rejected_before_deserialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'engine';package.mkdir()
            binding={'root':'engine'}
            for key,name in (('package_manifest','PACKAGE_MANIFEST.json'),('lock','LOCK.json'),('protocol','PROTOCOL.json'),('feature_schema','outputs/feature_names.json')):
                binding[key]={'path':'engine/'+name,'sha256':bridge.PACKAGE_MANIFEST_SHA256}
            rec={'path':'receiving.joblib','sha256':'1'*64};rush={'path':'rushing.joblib','sha256':'2'*64}
            consumer={'engine_package':binding,'seed_artifacts':[rec,rush],'seed_channels':{'receiving':rec,'rushing':rush}}
            request={'engine_root':'engine','package_manifest_sha256':bridge.PACKAGE_MANIFEST_SHA256,
                'channel':'receiving','parent_session':dict(rush,through_period=[2026,6])}
            gate=SimpleNamespace(reference=lambda base,ref:base/ref['path'],checked=lambda *args:b'{}')
            with self.assertRaisesRegex(ValueError,'SWAPPED_SEED_CHANNEL'):
                bridge.validate_consumer_binding(root,request,consumer,gate)

    def test_model_and_response_admission_precede_imports_and_payload_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);execution='data/private/research-lab/executions/test'
            request={'action':'settle','channel':'receiving','period':[2026,7],'engine_root':'engine',
                'package_manifest_sha256':bridge.PACKAGE_MANIFEST_SHA256,
                'output_dir':'data/private/research-lab/sessions/test/receiving',
                'parent_session':{'path':execution+'/pending.joblib','sha256':'0'*64,'through_period':[2026,6]},
                'outcomes':{'path':'data/private/research-lab/sources/outcomes.json','sha256':'1'*64}}
            manifest={'season':2026,'week':7,'execution_directory':execution}
            def denied(*args):raise ValueError('response admission denied')
            gate=SimpleNamespace(require_phase=lambda x:None,require_model_artifact=lambda *args:None,require_response_artifact=denied)
            with patch.object(bridge,'validate_consumer_binding'),patch.object(bridge,'verify_package'),patch.object(bridge,'runtime_versions'),patch.object(bridge,'import_engine') as imported,patch.object(bridge,'read_json') as read:
                with self.assertRaisesRegex(ValueError,'response admission denied'):
                    bridge._execute_admitted(root,request,manifest,gate,{})
                imported.assert_not_called();read.assert_not_called()



class SeedInspectionTests(unittest.TestCase):
    def session(self):
        class Labels:
            def __len__(self):return 2
            def __iter__(self):raise AssertionError('label values must not be inspected')
        return SimpleNamespace(through_period=(2026,6),feature_names=['f'+str(n) for n in range(228)],
            history_x=SimpleNamespace(shape=(2,228)),history_y=Labels(),history_workload=Labels(),
            history_periods=[(2026,5),(2026,6)],history_keys=[('one',),('two',)],pending=None,
            selection={'channel':'rushing','expert1':'first','expert2':'second'},
            configs={'first':{'update':'4weeks'},'second':{'update':'annual'}},
            models={'first':SimpleNamespace(train_cutoff=(2026,5)),'second':SimpleNamespace(train_cutoff=(2026,1))})
    def test_actual_period_derived_without_labels_or_fit(self):
        result=bridge.inspect_seed_metadata(self.session(),'rushing')
        self.assertEqual(result['through_period'],[2026,6]);self.assertEqual(result['next_period'],[2026,7])
        self.assertEqual(result['history_rows'],2);self.assertEqual(result['feature_count'],228)
        self.assertEqual(result['fits'],0);self.assertFalse(any(result['refit_due_next'].values()))
    def test_inspection_phase_precedes_package_access(self):
        seen=[]
        def deny(capability):seen.append(capability);raise ValueError('inspection denied')
        with patch.object(bridge,'verify_package') as package:
            with self.assertRaisesRegex(ValueError,'inspection denied'):
                bridge._execute_admitted(bridge.ROOT,{'action':'inspect_seed','channel':'rushing','period':None},
                    {'season':2026,'week':None},SimpleNamespace(require_phase=deny))
        self.assertEqual(seen,['inspect']);package.assert_not_called()
    def test_bad_history_and_swapped_channel_rejected(self):
        s=self.session();s.history_periods[1]=(2026,7)
        with self.assertRaisesRegex(ValueError,'SEED_FUTURE_HISTORY'):bridge.inspect_seed_metadata(s,'rushing')
        with self.assertRaisesRegex(ValueError,'SESSION_CHANNEL'):bridge.inspect_seed_metadata(self.session(),'receiving')


    def test_period_container_truthiness_is_not_used(self):
        class Periods(list):
            def __bool__(self):raise AssertionError('array-like truth value is ambiguous')
        session=self.session();session.history_periods=Periods(session.history_periods)
        result=bridge.inspect_seed_metadata(session,'rushing')
        self.assertEqual(result['history_max_period'],[2026,6])


class RecoveryBoundaryTests(unittest.TestCase):
    def test_windows_fsync_reopens_for_write_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'artifact.bin'; path.write_bytes(b'immutable pending checkpoint')
            before=bridge.sha(path)
            bridge._fsync(path)
            self.assertEqual(bridge.sha(path),before)

    def test_recovery_claim_denial_precedes_imports_and_payload_reads(self):
        def denied(*args):raise ValueError('exclusive recovery claim missing')
        request={'action':'recover_forecast','channel':'receiving','period':[2026,7]}
        gate=SimpleNamespace(require_phase=lambda phase:None,require_forecast_recovery=denied)
        with patch.object(bridge,'validate_consumer_binding'),patch.object(bridge,'verify_package') as package, \
             patch.object(bridge,'import_engine') as imported,patch.object(bridge,'read_json') as payload:
            with self.assertRaisesRegex(ValueError,'exclusive recovery claim missing'):
                bridge._execute_admitted(bridge.ROOT,request,{'season':2026,'week':7},gate,{})
            package.assert_not_called(); imported.assert_not_called(); payload.assert_not_called()

    def test_recovery_caller_cannot_replace_committed_references(self):
        gate=SimpleNamespace(require_forecast_recovery=lambda *args:{'original_archive':{'path':'bound','sha256':'1'*64}},
            checked=lambda *args:self.fail('unbound reference must not be read'))
        request={'channel':'receiving','recovery':{'original_archive':{'path':'other','sha256':'2'*64}}}
        with patch.object(bridge,'read_json') as payload:
            with self.assertRaisesRegex(ValueError,'RECOVERY_REFERENCES'):
                bridge._recovery_inputs(bridge.ROOT,request,{},gate)
            payload.assert_not_called()

    def test_recovery_uses_forecast_phase_before_any_file_access(self):
        phases=[]
        def denied(phase):phases.append(phase);raise ValueError('forecast phase denied')
        with self.assertRaisesRegex(ValueError,'forecast phase denied'):
            bridge._execute_admitted(bridge.ROOT,{'action':'recover_forecast','channel':'receiving','period':[2026,7]},
                {'season':2026,'week':7},SimpleNamespace(require_phase=denied),{})
        self.assertEqual(phases,['forecast'])

if __name__=='__main__':unittest.main(verbosity=2)
