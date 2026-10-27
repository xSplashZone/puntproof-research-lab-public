"""Authored gate canaries: temporary metadata only, no football or model loads.

Git's committed-byte response is simulated from a separate fixture snapshot.
All reference hashing, manifest/readiness validation, SQLite attempt checks and
claim consumption below exercise the real gate. No source path is opened.
"""
from contextlib import closing, contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nfl_model import gamd_gate as gate
from nfl_model import research_lab_gate as old
from nfl_model import stage


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 27, 1, tzinfo=timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)


class PostgameClock(FixedClock):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 11, 2, 1, tzinfo=timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)


class AuthoredFixture:
    def __init__(self, root):
        self.root = root
        self.committed = {}
        self.campaign = {
            'campaign': gate.CAMPAIGN, 'active': True,
            'mechanism': 'AUTHORED_FIXED_MECHANISM', 'recipe_version': 'AUTHORED_V1',
            'population': {'target': 'AUTHORED_VERIFIED_APPEARANCE'}, 'seed': 20261027,
            'folds': [{'train': 2022, 'evaluate': 2023}, {'train': 2023, 'evaluate': 2024}],
            'recipes': [{'family': family, 'channel': channel}
                        for family in ('hurdle_glm', 'hist_gradient_boosting', 'extra_trees')
                        for channel in ('receiving', 'rushing')],
        }
        self.campaign_ref = self.put(gate.BASE + 'CAMPAIGN.json', self.campaign)
        self.catalogue = {'source_root': str(root), 'historical_inputs': [],
                          'coverage_inputs': [], 'identity_inputs': []}
        self.catalogue_ref = self.put(gate.BASE + 'CATALOGUE.json', self.catalogue)
        foundations = {key: self.put(gate.BASE + key.upper() + '.json', {'authored': key})
                       for key in ('contract', 'certificate')}
        self.v3_policy = self.put('research/research_lab_v1/SOURCE_POLICY.json', {'protected_access': False})
        policy = self.put(gate.BASE + 'SOURCE_POLICY.json', {
            'authored': True, 'coverage': {'existing_policy': self.v3_policy}})
        owner = self.put(gate.BASE + 'OWNER_APPROVAL.txt', b'Authored test owner evidence only.\n')
        implementation = [self.put(name, b'# Authored fixture; never imported.\n') for name in (
            'src/nfl_model/gamd_gate.py', 'tools/research_lab/gamd/source.py',
            'tools/research_lab/gamd/trainer.py', 'tools/codex_task_guard.py',
            'src/nfl_model/stage.py', 'src/nfl_model/data/semantic_certificate.py')]
        canary = self.put(gate.BASE + 'CANARY.json', {
            'status': 'PASS', 'provider_calls': 0, 'real_model_fits': 0,
            'code_sha256': {r['path']: r['sha256'] for r in implementation},
            'passed_defects': list(gate.CANARIES),
        })
        self.consumers = {}
        self.consumer_refs = {}
        for operation, label in zip(gate.OPS, ('SOURCE', 'DEVELOPMENT', 'FORWARD')):
            body = {'operation': operation, 'family': gate.FAMILY,
                    'status': 'REGISTERED_CONDITIONAL', 'limits': deepcopy(gate.LIMITS),
                    'foundations': foundations, 'source_policy': policy,
                    'campaign': self.campaign_ref, 'catalogue': self.catalogue_ref,
                    'owner_approval': owner, 'adapter_canary': canary,
                    'implementation': implementation, 'expires_at': '2027-01-18T00:00:00Z'}
            self.consumers[operation] = body
            self.consumer_refs[operation] = self.put(gate.BASE + label + '_CONSUMER.json', body)
        self.authority = {
            'CURRENT_AUTHORISED_STAGE': 8, 'REAL_DATA_MODELLING': 'BLOCKED',
            'LATER_STAGE_REAL_DATA_MODELLING': 'BLOCKED', 'PROTECTED_EVALUATION': 'DISABLED',
            'allowed_real_data_operations': list(gate.OPS),
            'pre_model_semantics': {'operations': {op: [gate.FAMILY] for op in gate.OPS},
                                    'consumers': self.consumer_refs,
                                    'certificates': {gate.FAMILY: foundations}},
        }
        self.registry()
        self.historical = self.source_manifest('HISTORICAL_PROJECTION')
        self.coverage = self.source_manifest('COVERAGE_RECONCILIATION')
        self.put(gate.BASE + 'ACTIVE.json', {
            'campaign': self.campaign_ref, 'catalogue': self.catalogue_ref,
            'historical_manifest': self.historical, 'coverage_manifest': self.coverage,
        })
        projection = self.put(gate.PRIVATE + 'projection/AUTHORED.json', {'authored': True})
        receipt = self.put(gate.PRIVATE + 'projection/RECEIPT.json', {
            'projection': projection, 'source_manifest': self.historical,
            'audit': {'unknowns_imputed_zero': False, 'snap_fields_used': False,
                      'fixtures_by_season': {'2021': 272, '2022': 271, '2023': 272, '2024': 272}},
        })
        self.plan = {
            'campaign': gate.CAMPAIGN, 'family': 'hurdle_glm', 'channel': 'receiving',
            'mechanism': self.campaign['mechanism'], 'population': self.campaign['population']['target'],
            'endpoint': 'MATCHED_SIGNED_YARD_MAE_CRPS', 'recipe_version': self.campaign['recipe_version'],
            'folds': self.campaign['folds'], 'seed': self.campaign['seed'],
            'data_sha256': projection['sha256'], 'projection': projection,
            'projection_receipt': receipt,
        }
        identity = {k: self.plan[k] for k in ('mechanism', 'population', 'endpoint', 'data_sha256',
                    'family', 'channel', 'recipe_version', 'folds', 'seed')}
        self.key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':'),
                                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.attempt = self.key[:32]
        self.fit_body = {**self.common(gate.DEVELOPMENT), **self.plan, 'attempt_id': self.attempt,
                         'output': gate.PRIVATE + 'attempts/' + self.attempt}
        self.fit = self.seal(self.fit_body['output'] + '/MANIFEST.json', self.fit_body)
        self.dbpath = root / 'data/private/research-lab/lab.sqlite3'
        self.dbpath.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('CREATE TABLE gamd_attempts(id TEXT PRIMARY KEY, scientific_key TEXT, plan TEXT, status TEXT)')
            db.execute('INSERT INTO gamd_attempts VALUES(?,?,?,?)',
                       (self.attempt, self.key, json.dumps(self.plan), 'PLANNED'))

    def put(self, name, value):
        raw = value if isinstance(value, bytes) else gate.encoded(value)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.committed[name] = raw
        return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}

    def git(self, argv, **kwargs):
        if argv[:3] != [gate.GIT, '-C', str(self.root)] or argv[3] != 'show':
            raise AssertionError('Unexpected subprocess in authored gate fixture')
        name = argv[4].removeprefix('HEAD:')
        return SimpleNamespace(returncode=0 if name in self.committed else 1,
                               stdout=self.committed.get(name, b''), stderr=b'')

    def registry(self):
        self.put('config/source_registry.json', {gate.FAMILY: {
            'operation': gate.SOURCE, 'consumer': self.consumer_refs[gate.SOURCE],
            'source_policy': self.consumers[gate.SOURCE]['source_policy']}})

    def replace_consumer(self, operation, **changes):
        self.consumers[operation].update(changes)
        self.consumer_refs[operation] = self.put(self.consumer_refs[operation]['path'], self.consumers[operation])
        self.registry()

    def common(self, operation):
        return {'operation': operation, 'consumer': self.consumer_refs[operation],
                'campaign_ref': self.campaign_ref, 'source_policy': self.consumers[operation]['source_policy'],
                'provider_calls': 0, 'cost_cents': 0, 'protected_access': False, 'production': False,
                'as_of': '2026-10-26T00:00:00Z'}

    def seal(self, name, body):
        body['spec_hash'] = gate.spec_hash(body)
        operation = body['operation']
        historical = operation == gate.DEVELOPMENT or body.get('purpose') == 'HISTORICAL_PROJECTION'
        witness = {'status': 'PASS', 'seasons': [2021, 2022, 2023, 2024] if historical else [2026],
                   'provider_calls': 0, 'records': 1, 'catalogue': self.catalogue_ref}
        if operation == gate.DEVELOPMENT:
            witness.update(projection=body['projection'], projection_receipt=body['projection_receipt'])
        witness_ref = self.put(name + '.WITNESS.json', witness)
        body['readiness'] = self.put(name + '.READY.json', {
            'status': 'READY', 'operation': operation, 'consumer': body['consumer'],
            'manifest_spec_sha256': body['spec_hash'],
            'implementation': self.consumers[operation]['implementation'],
            'adapter_canary': self.consumers[operation]['adapter_canary'], 'target_witness': witness_ref})
        body['pretask_satisfiability'] = self.put(name + '.PRETASK.json', {'authored': True})
        return self.put(name, body)

    def source_manifest(self, purpose):
        return self.seal(gate.BASE + purpose + '.json', {
            **self.common(gate.SOURCE), 'purpose': purpose, 'catalogue': self.catalogue_ref})

    def update_status(self, status):
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('UPDATE gamd_attempts SET status=? WHERE id=?', (status, self.attempt))

    def append_v3_event(self, origin, state, at, **evidence):
        directory = self.root / origin['execution_directory'] / 'lifecycle'
        events = [json.loads(p.read_bytes()) for p in sorted(directory.glob('*.json'))]
        event = {'sequence': len(events), 'state': state,
                 'previous_hash': events[-1]['sha256'] if events else None, 'at': at, **evidence}
        event['sha256'] = hashlib.sha256(old.encoded(event)).hexdigest()
        return self.put((directory / f'{len(events):03}.json').relative_to(self.root).as_posix(), event)

    def forward(self, *, history_changes=None, fixture_changes=None,
                kickoff='2026-11-01T18:00:00Z', **changes):
        """Mirror source_adapter DATA.records, fixture lists and actual journal format."""
        base = gate.PRIVATE + 'attempts/' + 'b' * 32
        fit = self.put(base + '/MANIFEST.json', {'campaign': gate.CAMPAIGN, 'campaign_ref': self.campaign_ref})
        model = self.put(base + '/artifacts/development_2024/FITTED_MODEL.joblib', b'AUTHORED: NOT A MODEL')
        terminal = self.put(base + '/TERMINAL.json', {
            'status': 'COMPLETED', 'decision': {'status': 'COMPLETED'}, 'channel': 'receiving',
            'manifest_sha256': fit['sha256'],
            'artifacts': [{'path': str(self.root / model['path']), 'sha256': model['sha256']}]})
        source_dir = 'data/private/research-lab/sources/prepared-authored'
        fixture_rows = [{'season': 2026, 'week': 8, 'game_id': '2026_08_DAL_PHI',
                         'gsis': f'00-{i:07}', 'team': 'DAL', 'opponent': 'PHI',
                         'position': 'WR', 'channel': 'receiving'} for i in (1, 2)]
        if fixture_changes:
            fixture_rows[0].update(fixture_changes)
        fixtures = self.put(source_dir + '/receiving-fixtures.json', fixture_rows)
        lineage = self.put(source_dir + '/AUTHORED_LINEAGE.json', {'authored': True})
        history = [{**{k: v for k, v in row.items() if k != 'channel'}, 'week': 7,
                    'game_id': '2026_07_DAL_PHI', 'identity_state': 'EXACT_DATED_ROSTER_GSIS',
                    'appeared': i == 0, 'targets': 3 if i == 0 else None,
                    'receiving_yards': 20 if i == 0 else None, 'carries': None, 'rushing_yards': None,
                    'offense_snaps': None, 'snap_share': None, 'snap_denominator': None,
                    'state': 'APPEARED_VERIFIED' if i == 0 else 'PARTICIPATION_UNKNOWN',
                    'observed_at': '2026-10-24T00:00:00Z', 'source_lineage': [lineage]}
                   for i, row in enumerate(fixture_rows)]
        if history_changes:
            history[0].update(history_changes)
        data = self.put(source_dir + '/revised-source-01/DATA.json', {
            'records': history, 'team_context': [], 'advanced': []})
        consumer = self.put('research/research_lab_v1/AUTHORED_FORWARD_CONSUMER.json', {
            'operation': old.FORWARD, 'source_policy': self.v3_policy, 'implementation': []})
        source = self.put(source_dir + '/SOURCE_RECEIPT.json', {
            'status': 'PASS', 'operation': old.SOURCE, 'season': 2026, 'source_policy': self.v3_policy,
            'inputs': [data, fixtures], 'identity_conflicts': 0, 'duplicate_keys': 0,
            'unknowns_preserved': True, 'target_weeks_verified': [8],
            'latest_observed_at': '2026-10-25T00:00:00Z'})
        directory = 'data/private/research-lab/executions/forecast-authored'
        origin = {'schema_version': 'puntproof-lab-execution-manifest-v2',
                  'purpose': 'FORECAST_CYCLE', 'operation': old.FORWARD, 'consumer': consumer,
                  'source_policy': self.v3_policy, 'season': 2026, 'week': 8,
                  'execution_directory': directory, 'provider_calls': 0, 'cost_cents': 0,
                  'protected_access': False, 'production': False, 'source_receipt': source,
                  'input_files': [data, fixtures], 'channels': ['receiving'],
                  'cutoff': '2026-10-25T00:00:00Z',
                  'fixtures': [{'season': 2026, 'week': 8, 'game_id': '2026_08_DAL_PHI', 'kickoff': kickoff}],
                  'channel_inputs': {'receiving': {'fixtures': fixtures, 'input_root': {
                      'path': source_dir, 'files': {'revised-source-01/DATA.json': data['sha256']}}}}}
        origin_ref = self.put(directory + '/MANIFEST.json', origin)
        ready = self.put(directory + '/READINESS.json', {
            'manifest': origin_ref, 'status': 'READY', 'capability': 'FORECAST', 'operation': old.FORWARD,
            'consumer': consumer, 'implementation': []})
        if not (self.root / directory / 'lifecycle').exists():
            for state in old.FORWARD_STATES[:5]:
                self.append_v3_event(origin, state, '2026-10-26T00:00:00Z')
        chain = {'manifest': origin_ref, 'readiness': ready, 'source_receipt': source,
                 'data': data, 'fixtures': fixtures}
        targets = [{**row, 'appeared': None, 'opportunity': None, 'yards': None,
                    'participation_state': 'PREGAME_UNKNOWN', 'source_observed_at': '2026-10-25T00:00:00Z',
                    'provenance': [source, fixtures], 'kickoff_at': kickoff} for row in fixture_rows]
        body = {**self.common(gate.FORWARD), 'purpose': 'PREGAME_INFERENCE', 'season': 2026, 'week': 8,
                'channel': 'receiving', 'fit': False, 'game_id': '2026_08_DAL_PHI',
                'model': model, 'model_terminal': terminal, 'input_files': [data, fixtures],
                'source_cutoff': '2026-10-25T00:00:00Z',
                'v3_source': chain, 'targets': targets, 'population_scope': 'EXISTING_V3_ADMITTED_COHORT_ONLY',
                'output': gate.forward_directory(model, 8, '2026_08_DAL_PHI', 'receiving')}
        body.update(changes)
        return self.seal(body['output'] + '/MANIFEST.json', body)

    def forward_rows(self, manifest):
        body = gate.read(self.root, manifest)
        return [{**{k: t[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'opponent', 'position', 'channel')},
                 'id': 'authored-' + t['gsis'], 'family': 'hurdle_glm', 'model_sha256': body['model']['sha256'],
                 'kickoff_at': t['kickoff_at'], 'training_through': [2023, 12], 'calibration_through': [2023, 18],
                 'expected_opportunities': 3., 'law': {'support': [0, 20], 'pmf': [.5, .5]},
                 'maximum_source_observed_at': body['source_cutoff'], 'forecast_ref': None, 'seal_ref': None}
                for t in body['targets']]

    def observation(self, forecast, sealed, *, outcome_changes=None, **changes):
        original = gate.read(self.root, forecast)
        origin = gate.read(self.root, original['v3_source']['manifest'])
        directory = 'data/private/research-lab/sources/completion-authored'
        inputs = [self.put(directory + '/AUTHORED_SOURCE.json', {'records': []})]
        rows = [{**{k: t[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'opponent', 'position', 'channel')},
                 'status': 'APPEARED' if i == 0 else 'UNKNOWN',
                 'yards': 23 if i == 0 else None, 'opportunities': 4 if i == 0 else None}
                for i, t in enumerate(original['targets'])]
        if outcome_changes:
            rows[0].update(outcome_changes)
        proof = self.put(directory + '/SOURCE_PROOF.json', {
            'status': 'VERIFIED_COMPLETE', 'full_fixture_census_verified': True, 'provider_calls': 0,
            'source_inputs': inputs, 'outcomes': {'receiving': rows},
            'source_observed_at': '2026-11-02T00:00:00Z'})
        outcomes = self.put(directory + '/receiving-outcomes.json', {'rows': rows})
        completion = self.put(directory + '/COMPLETION.json', {
            'status': 'VERIFIED_COMPLETE', 'season': 2026, 'week': 8, 'all_fixtures_complete': True,
            'full_fixture_census_verified': True, 'outcomes': {'receiving': outcomes},
            'fixtures': [{'game_id': original['game_id'], 'kickoff': original['targets'][0]['kickoff_at'],
                          'completed_at': '2026-11-01T22:00:00Z', 'final': True, 'source_ref': proof,
                          'completion_clock_semantics': 'OBSERVED_COMPLETE_IN_RETAINED_PBP'}]})
        response = self.append_v3_event(origin, 'RESPONSE_ACCESS_ENABLED', '2026-11-02T00:01:00Z',
                                        completion=completion, outcomes={'receiving': outcomes})
        self.append_v3_event(origin, 'RESPONSES_COMPLETE', '2026-11-02T00:01:30Z', evidence=[outcomes])
        source_consumer = self.put('research/research_lab_v1/AUTHORED_SOURCE_CONSUMER.json', {
            'operation': old.SOURCE, 'source_policy': self.v3_policy, 'implementation': []})
        source_dir = 'data/private/research-lab/executions/prepare_forecast-authored'
        source_manifest = self.put(source_dir + '/MANIFEST.json', {
            'operation': old.SOURCE, 'purpose': 'FEATURE_PREPARATION', 'season': 2026, 'week': 8,
            'consumer': source_consumer, 'input_files': inputs, 'source_policy': self.v3_policy,
            'provider_calls': 0, 'cost_cents': 0, 'protected_access': False, 'production': False,
            'as_of': '2026-11-01T23:59:00Z'})
        source_readiness = self.put(source_dir + '/READINESS.json', {
            'manifest': source_manifest, 'status': 'READY', 'capability': 'PREPARE_FORECAST',
            'operation': old.SOURCE, 'consumer': source_consumer, 'implementation': []})
        body = {**self.common(gate.FORWARD), 'purpose': 'VERIFIED_OBSERVATIONS',
                'season': 2026, 'week': 8, 'channel': 'receiving', 'game_id': original['game_id'], 'fit': False,
                'as_of': '2026-11-02T00:02:00Z', 'model': original['model'], 'model_terminal': original['model_terminal'],
                'forecast_manifest': forecast, 'forecast_seal': sealed['seal'], 'forecast_rows': sealed['forecasts'],
                'v3_source': original['v3_source'], 'v3_response_event': response, 'completion': completion,
                'source_proof': proof, 'channel_outcomes': outcomes, 'source_manifest': source_manifest,
                'source_readiness': source_readiness, 'input_files': [sealed['forecasts'], outcomes]}
        body.update(changes)
        return self.seal(original['output'] + '/OBSERVATIONS_MANIFEST.json', body)


class GAMDGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.fixture = AuthoredFixture(self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(gate, 'datetime', FixedClock))
        self.stack.enter_context(patch.object(stage, 'current_authority', return_value=self.fixture.authority))
        self.stack.enter_context(patch.object(gate.subprocess, 'run', side_effect=self.fixture.git))
        self.token = gate._ACTIVE.set(None)
        self.addCleanup(gate._ACTIVE.reset, self.token)

    @contextmanager
    def active(self, operation, reference, consumed=False):
        token = gate._ACTIVE.set({'root': self.root, 'operation': operation, 'manifest': reference,
                                  'execution_consumed': consumed})
        try:
            yield
        finally:
            gate._ACTIVE.reset(token)

    def test_registered_consumer_requires_exact_family_and_limits(self):
        value, ref = gate.consumer_for(self.root, gate.DEVELOPMENT)
        self.assertEqual(ref, self.fixture.consumer_refs[gate.DEVELOPMENT])
        self.assertEqual(value['limits']['max_attempts'], 6)
        self.fixture.authority['pre_model_semantics']['operations'][gate.DEVELOPMENT] = ['wrong_family']
        with self.assertRaisesRegex(ValueError, 'FAMILY_BINDING'):
            gate.consumer_for(self.root, gate.DEVELOPMENT)
        self.fixture.authority['pre_model_semantics']['operations'][gate.DEVELOPMENT] = [gate.FAMILY]
        self.fixture.replace_consumer(gate.DEVELOPMENT, limits={**gate.LIMITS, 'provider_calls': 1})
        with self.assertRaisesRegex(ValueError, 'CONSUMER_SCOPE'):
            gate.consumer_for(self.root, gate.DEVELOPMENT)

    def test_protected_or_generic_model_authority_does_not_open_gamd(self):
        for key, value in (('PROTECTED_EVALUATION', 'ENABLED'), ('REAL_DATA_MODELLING', 'AUTHORISED')):
            original = self.fixture.authority[key]
            self.fixture.authority[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'GLOBAL_BOUNDARY'):
                gate.consumer_for(self.root, gate.DEVELOPMENT)
            self.fixture.authority[key] = original

    def test_valid_hash_cannot_substitute_uncommitted_consumer(self):
        path = self.fixture.consumer_refs[gate.DEVELOPMENT]['path']
        self.fixture.consumers[gate.DEVELOPMENT]['extra_authored_field'] = True
        raw = gate.encoded(self.fixture.consumers[gate.DEVELOPMENT])
        (self.root / path).write_bytes(raw)
        self.fixture.consumer_refs[gate.DEVELOPMENT] = {'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'UNCOMMITTED_BINDING'):
            gate.consumer_for(self.root, gate.DEVELOPMENT)

    def test_missing_canary_and_expired_authority_are_closed(self):
        canary = self.fixture.consumers[gate.DEVELOPMENT]['adapter_canary']
        body = gate.read(self.root, canary)
        body['passed_defects'].remove('consumed_attempt')
        self.fixture.replace_consumer(gate.DEVELOPMENT, adapter_canary=self.fixture.put(canary['path'], body))
        with self.assertRaisesRegex(ValueError, 'ADAPTER_CANARIES'):
            gate.consumer_for(self.root, gate.DEVELOPMENT)
        self.fixture.replace_consumer(gate.DEVELOPMENT, expires_at='2026-10-01T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'AUTHORITY_EXPIRED'):
            gate.consumer_for(self.root, gate.DEVELOPMENT)

    def test_reference_custody_and_path_escape(self):
        for path in ('../escape', '/escape', 'C:/escape', 'a//b', 'a/./b', 'a\\b'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'REFERENCE_PATH'):
                gate.path_for(self.root, {'path': path, 'sha256': 'a' * 64})
        ref = self.fixture.put('authored.json', {'v': 1})
        (self.root / ref['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'HASH_CHANGED'):
            gate.checked(self.root, ref)

    def test_exact_six_recipe_campaign_and_live_task_precedence(self):
        self.assertEqual(len(gate.require_campaign(self.root)['recipes']), 6)
        (self.root / 'TASK_CHARTER.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'LIVE_TASK_PRECEDENCE'):
            gate.require_campaign(self.root)

    def test_standing_source_needs_active_context_and_yields_to_live_task(self):
        self.assertIsNone(gate.scheduled_execution(self.root, gate.SOURCE))
        with self.active(gate.SOURCE, self.fixture.historical), patch.object(gate, '_pretask', return_value={'status': 'PASS'}) as pretask:
            self.assertEqual(gate.scheduled_execution(self.root, gate.SOURCE)['status'], 'PASS')
            pretask.assert_called_once()
            (self.root / 'TASK_CHARTER.json').write_text('{}')
            self.assertIsNone(gate.scheduled_execution(self.root, gate.SOURCE))

    def test_live_execution_rejects_other_operation_and_input_manifest(self):
        self.fixture.put('TASK_CHARTER.json', {'stage_authority': {'operations': [gate.FORWARD]}})
        with self.assertRaisesRegex(ValueError, 'LIVE_TASK_PRECEDENCE'):
            with gate.execution_authority(self.root, gate.DEVELOPMENT, self.fixture.fit):
                self.fail('wrong operation yielded')
        self.fixture.put('TASK_CHARTER.json', {
            'stage_authority': {'operations': [gate.DEVELOPMENT]},
            'model_execution': {'inputs': {gate.FAMILY: [self.fixture.historical]}}})
        with self.assertRaisesRegex(ValueError, 'LIVE_CHARTER_INPUTS_DIFFER'):
            with gate.execution_authority(self.root, gate.DEVELOPMENT, self.fixture.fit):
                self.fail('wrong manifest yielded')

    def test_context_is_revoked_when_source_action_raises(self):
        def enforce(operation):
            gate.require_active_execution(self.root, operation)
        with patch.object(stage, 'require_stage', side_effect=enforce):
            with self.assertRaisesRegex(RuntimeError, 'authored failure'):
                with gate.execution_authority(self.root, gate.SOURCE, self.fixture.historical):
                    raise RuntimeError('authored failure')
        self.assertIsNone(gate._ACTIVE.get())

    def test_manifest_hash_and_fixed_population_cannot_drift(self):
        body = deepcopy(self.fixture.fit_body)
        body['population'] = 'different population'
        ref = self.fixture.put(self.fixture.fit['path'], body)
        with self.assertRaisesRegex(ValueError, 'MANIFEST_SPEC_CHANGED'):
            gate._manifest(self.root, gate.DEVELOPMENT, ref)
        body['spec_hash'] = gate.spec_hash(body)
        ref = self.fixture.put(self.fixture.fit['path'], body)
        with self.assertRaisesRegex(ValueError, 'FIXED_POPULATION_ENDPOINT'):
            gate._manifest(self.root, gate.DEVELOPMENT, ref)

    def test_readiness_must_witness_exact_seasons_and_projection(self):
        body = self.fixture.fit_body
        ready = gate.read(self.root, body['readiness'])
        witness = gate.read(self.root, ready['target_witness'])
        for changes, expected in (({'seasons': [2025]}, 'TARGET_SEASON_WITNESS'),
                                  ({'projection': self.fixture.historical}, 'FIT_WITNESS_INPUTS')):
            bad = {**witness, **changes}
            ready['target_witness'] = self.fixture.put(ready['target_witness']['path'], bad)
            body['readiness'] = self.fixture.put(body['readiness']['path'], ready)
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                gate._readiness(self.root, gate.DEVELOPMENT, self.fixture.fit, body,
                                self.fixture.consumers[gate.DEVELOPMENT], self.fixture.consumer_refs[gate.DEVELOPMENT])

    def test_fit_requires_actual_database_plan_and_unconsumed_state(self):
        with patch.object(gate, '_pretask', return_value={'status': 'PASS'}):
            self.assertEqual(gate.require_fit(self.root, self.root / self.fixture.fit['path'])['attempt_id'], self.fixture.attempt)
            self.fixture.update_status('CLAIMED')
            with self.assertRaisesRegex(ValueError, 'ATTEMPT_ALREADY_CONSUMED'):
                gate.require_fit(self.root, self.root / self.fixture.fit['path'])
        bad = {**self.fixture.fit_body, 'family': 'extra_trees'}
        with self.assertRaisesRegex(ValueError, 'ATTEMPT_PLAN_MANIFEST_MISMATCH'):
            gate._attempt(self.root, bad)

    def test_attempt_id_and_scientific_key_are_not_caller_chosen(self):
        with closing(sqlite3.connect(self.fixture.dbpath)) as db, db:
            db.execute('UPDATE gamd_attempts SET scientific_key=?', ('f' * 64,))
        with self.assertRaisesRegex(ValueError, 'ATTEMPT_PLAN_MANIFEST_MISMATCH'):
            gate._attempt(self.root, self.fixture.fit_body)

    def test_consumed_recipe_cannot_reopen_with_different_data_hash(self):
        other = {**self.fixture.plan, 'data_sha256': 'd' * 64}
        with closing(sqlite3.connect(self.fixture.dbpath)) as db, db:
            db.execute('INSERT INTO gamd_attempts VALUES(?,?,?,?)', ('e' * 32, 'e' * 64, json.dumps(other), 'FAILED'))
        with self.assertRaisesRegex(ValueError, 'RECIPE_ALREADY_CONSUMED'):
            gate._attempt(self.root, self.fixture.fit_body)

    def test_claim_requires_registry_consumption_and_cannot_repeat(self):
        path = self.root / self.fixture.fit['path']
        with self.assertRaisesRegex(ValueError, 'REGISTRY_CLAIM_REQUIRED'):
            gate.claim_fit(self.root, path)
        self.fixture.update_status('CLAIMED')
        token = gate.claim_fit(self.root, path)
        self.assertEqual(len(token), 64)
        with self.assertRaisesRegex(ValueError, 'FIT_CLAIM_ALREADY_EXISTS'):
            gate.claim_fit(self.root, path)

    def test_child_token_parent_and_execution_are_single_use(self):
        path = self.root / self.fixture.fit['path']
        self.fixture.update_status('CLAIMED')
        token = gate.claim_fit(self.root, path)
        with patch.object(gate.os, 'getppid', return_value=-1):
            with self.assertRaisesRegex(ValueError, 'FIT_PARENT_CLAIM'):
                with gate.fitting_authority(self.root, path, token):
                    self.fail('wrong parent yielded')
        with patch.object(gate.os, 'getppid', return_value=os.getpid()):
            with self.assertRaisesRegex(ValueError, 'FIT_PARENT_CLAIM'):
                with gate.fitting_authority(self.root, path, 'wrong token'):
                    self.fail('wrong token yielded')
            @contextmanager
            def entered(root, operation, manifest, **kwargs):
                with self.active(operation, manifest, consumed=kwargs['execution_consumed']):
                    gate.require_active_execution(root, operation)
                    yield
            with patch.object(gate, 'execution_authority', side_effect=entered):
                with gate.fitting_authority(self.root, path, token):
                    self.assertTrue((path.parent / 'EXECUTION.json').is_file())
                with self.assertRaisesRegex(ValueError, 'FIT_EXECUTION_ALREADY_CONSUMED'):
                    with gate.fitting_authority(self.root, path, token):
                        self.fail('consumed child yielded')

    def test_live_charter_does_not_replace_consumed_execution_evidence(self):
        self.fixture.put('TASK_CHARTER.json', {'stage_authority': {'operations': [gate.DEVELOPMENT]}})
        with self.assertRaisesRegex(ValueError, 'ACTIVE_EXECUTION_REQUIRED'):
            gate.require_active_execution(self.root, gate.DEVELOPMENT)
        with self.active(gate.DEVELOPMENT, self.fixture.fit, consumed=True):
            with self.assertRaisesRegex(ValueError, 'CHILD_EXECUTION_NOT_CLAIMED'):
                gate.require_active_execution(self.root, gate.DEVELOPMENT)

    def test_source_context_does_not_authorize_different_source_manifest(self):
        with self.active(gate.SOURCE, self.fixture.historical):
            with self.assertRaisesRegex(ValueError, 'ACTIVE_SOURCE_MANIFEST_MISMATCH'):
                gate.require_source(self.root, self.fixture.coverage)

    def source_catalogue(self, **first_changes):
        entries = [{'id': f'authored-{year}-{dataset}', 'season': year, 'dataset': dataset,
                    'source_root': 'C:/Dev/nfl-model', 'path': f'authored-not-opened/{year}-{dataset}.json',
                    'sha256': 'a' * 64, 'observed_at': '2026-10-25T00:00:00Z'}
                   for year in (2021, 2022, 2023, 2024)
                   for dataset in ('player_stats', 'pbp', 'rosters', 'schedule')]
        entries[0].update(first_changes)
        catalogue = {**self.fixture.catalogue, 'historical_inputs': entries}
        original_read = gate.read
        def authored_read(root, ref, committed=False):
            if ref == self.fixture.catalogue_ref:
                return catalogue
            return original_read(root, ref, committed)
        return patch.object(gate, 'read', side_effect=authored_read)

    def test_2025_partition_is_rejected_before_source_hash_or_open(self):
        original_sha = gate.sha
        def metadata_only_sha(path):
            if not Path(path).resolve().is_relative_to(self.root):
                raise AssertionError('source must remain unopened')
            return original_sha(path)
        with self.source_catalogue(season=2025), self.active(gate.SOURCE, self.fixture.historical), patch.object(gate, 'sha', side_effect=metadata_only_sha):
            with self.assertRaisesRegex(ValueError, 'EXACT_HISTORICAL_PARTITIONS'):
                gate.require_source(self.root, self.fixture.historical)

    def test_source_requires_registered_root_before_source_access(self):
        with self.source_catalogue(source_root=str(self.root)), self.active(gate.SOURCE, self.fixture.historical):
            with self.assertRaisesRegex(ValueError, 'SOURCE_ROOT'):
                gate.require_source(self.root, self.fixture.historical)

    def test_future_source_observation_cannot_improve_earlier_cutoff(self):
        original_sha = gate.sha
        def authored_sha(path):
            if 'authored-not-opened' in Path(path).parts:
                return 'a' * 64
            return original_sha(path)
        with self.source_catalogue(observed_at='2026-10-27T00:00:00Z'), self.active(gate.SOURCE, self.fixture.historical), patch.object(gate, 'sha', side_effect=authored_sha):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CLOCK'):
                gate.require_source(self.root, self.fixture.historical)

    def test_future_shadow_projects_exact_v3_cohort_and_preserves_unknowns(self):
        ref = self.fixture.forward()
        self.assertEqual(gate._manifest(self.root, gate.FORWARD, ref)[2]['purpose'], 'PREGAME_INFERENCE')
        with self.active(gate.FORWARD, ref):
            history, targets = gate.verified_forward_inputs(self.root, ref)
        self.assertEqual(len(targets), 2)
        self.assertEqual(history[0]['yards'], 20)
        self.assertIsNone(history[1]['yards'])
        self.assertIsNone(history[1]['opportunity'])
        self.assertTrue(all(t['appeared'] is None and t['yards'] is None for t in targets))
        self.assertNotIn('offense_snaps', history[0])

    def test_future_shadow_cannot_backdate_actual_pregame_clock(self):
        ref = self.fixture.forward(kickoff='2026-10-26T18:00:00Z')
        with self.assertRaisesRegex(ValueError, 'PREGAME_CLOCK'):
            gate._manifest(self.root, gate.FORWARD, ref)

    def test_future_shadow_rejects_cohort_selection_or_target_outcomes(self):
        ref = self.fixture.forward()
        original = gate.read(self.root, ref)
        for targets in (original['targets'][:1], [{**t, 'yards': 20} for t in original['targets']]):
            body = {**original, 'targets': targets}
            bad = self.fixture.seal(ref['path'], body)
            with self.subTest(targets=targets), self.assertRaisesRegex(ValueError, 'FORWARD_COMPLETE_ADMITTED_COHORT'):
                gate._manifest(self.root, gate.FORWARD, bad)

    def test_future_shadow_refuses_same_week_2025_unknown_zero_and_snaps(self):
        cases = (({'week': 8}, 'FORWARD_HISTORY_PERIOD_OR_IDENTITY'),
                 ({'season': 2025}, 'FORWARD_HISTORY_PERIOD_OR_IDENTITY'),
                 ({'appeared': False, 'targets': 0, 'receiving_yards': 0}, 'FORWARD_UNKNOWN_NOT_ZERO'),
                 ({'offense_snaps': 1}, 'FORWARD_SNAP_FIELDS_DENIED'),
                 ({'observed_at': '2026-10-26T00:00:00Z'}, 'FORWARD_HISTORY_CLOCK'))
        for changes, expected in cases:
            ref = self.fixture.forward(history_changes=changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, expected):
                gate._manifest(self.root, gate.FORWARD, ref)

    def test_forward_requires_original_v3_consumed_seal_and_exact_source_hash(self):
        ref = self.fixture.forward()
        body = gate.read(self.root, ref)
        origin = gate.read(self.root, body['v3_source']['manifest'])
        journal = self.root / origin['execution_directory'] / 'lifecycle'
        (journal / '004.json').unlink()
        with self.assertRaisesRegex(ValueError, 'V3_ORIGINAL_SEAL_REQUIRED'):
            gate._manifest(self.root, gate.FORWARD, ref)
        self.fixture.append_v3_event(origin, 'ATTEMPT_CONSUMED', '2026-10-26T00:00:00Z')
        (self.root / body['v3_source']['data']['path']).write_bytes(b'{"records":[]}')
        with self.assertRaisesRegex(ValueError, 'HASH_CHANGED'):
            gate._manifest(self.root, gate.FORWARD, ref)

    def test_failed_or_unrecommended_fit_cannot_issue_shadow(self):
        ref = self.fixture.forward()
        body = gate.read(self.root, ref)
        terminal = gate.read(self.root, body['model_terminal'])
        terminal['decision']['status'] = 'REJECTED'
        body['model_terminal'] = self.fixture.put(body['model_terminal']['path'], terminal)
        bad = self.fixture.seal(ref['path'], body)
        with self.assertRaisesRegex(ValueError, 'SHADOW_MODEL_NOT_RECOMMENDED'):
            gate._manifest(self.root, gate.FORWARD, bad)

    def seal_authored_forward(self):
        ref = self.fixture.forward()
        with self.active(gate.FORWARD, ref):
            claim = gate.claim_forward(self.root, ref)
            sealed = gate.seal_forward(self.root, ref, self.fixture.forward_rows(ref))
        self.assertEqual(gate.read(self.root, sealed['seal'])['claim'], claim)
        return ref, sealed

    def test_actual_own_seal_and_claim_are_immutable_without_reusing_v3_lease(self):
        ref, sealed = self.seal_authored_forward()
        original = gate.read(self.root, ref)
        journal = self.root / gate.read(self.root, original['v3_source']['manifest'])['execution_directory'] / 'lifecycle'
        self.assertEqual(len(list(journal.glob('*.json'))), 5)
        self.assertEqual(gate.stamp(sealed['sealed_at']), FixedClock.now(timezone.utc))
        self.assertTrue(all(row['classification'] == 'GENUINE_PROSPECTIVE' for row in sealed['rows']))
        with self.active(gate.FORWARD, ref), self.assertRaisesRegex(ValueError, 'FORWARD_ALREADY_CLAIMED_NO_RETRY'):
            gate.claim_forward(self.root, ref)
        m = {**original, 'forecast_manifest': ref, 'forecast_seal': sealed['seal'],
             'forecast_rows': sealed['forecasts'], 'as_of': '2026-11-02T00:02:00Z'}
        self.assertEqual(len(gate._own_forward_seal(self.root, m)[2]), 2)
        self.fixture.put(original['output'] + '/LATE.json', {'status': 'LATE_NONISSUED'})
        with self.assertRaisesRegex(ValueError, 'OWN_FORECAST_LATE'):
            gate._own_forward_seal(self.root, m)

    def test_pregame_seal_rejects_missing_cohort_and_poisoned_probability_law(self):
        ref = self.fixture.forward()
        rows = self.fixture.forward_rows(ref)
        with self.active(gate.FORWARD, ref):
            gate.claim_forward(self.root, ref)
            with self.assertRaisesRegex(ValueError, 'FORECAST_SEAL_COHORT'):
                gate.seal_forward(self.root, ref, rows[:1])
            rows[0]['law']['pmf'] = [.8, .8]
            with self.assertRaisesRegex(ValueError, 'SEALED_LAW_MASS_OR_SUPPORT_INVALID'):
                gate.seal_forward(self.root, ref, rows)
        out = self.root / gate.read(self.root, ref)['output']
        self.assertFalse((out / 'SEAL.json').exists())
        self.assertTrue((out / 'CLAIM.json').exists())

    def test_verified_observations_use_producer_chain_and_keep_unknowns(self):
        forecast, sealed = self.seal_authored_forward()
        ref = self.fixture.observation(forecast, sealed)
        with patch.object(gate, 'datetime', PostgameClock), self.active(gate.FORWARD, ref):
            forecasts, outcomes = gate.verified_forward_outcomes(self.root, ref)
        self.assertEqual(len(forecasts), 2)
        self.assertEqual(outcomes[0]['yards'], 23)
        self.assertTrue(outcomes[0]['verified'])
        self.assertEqual(outcomes[1]['status'], 'UNKNOWN')
        self.assertIsNone(outcomes[1]['yards'])
        self.assertEqual(forecasts[0]['forecast_ref'], sealed['forecasts'])
        self.assertEqual(forecasts[0]['seal_ref'], sealed['seal'])

    def test_observations_refuse_corrections_without_new_authority(self):
        forecast, sealed = self.seal_authored_forward()
        ref = self.fixture.observation(forecast, sealed, correction={'authored': True})
        with patch.object(gate, 'datetime', PostgameClock), self.assertRaisesRegex(ValueError, 'UNREGISTERED_OUTCOME_CORRECTION'):
            gate._manifest(self.root, gate.FORWARD, ref)

    def test_observations_refuse_unknown_zero(self):
        forecast, sealed = self.seal_authored_forward()
        ref = self.fixture.observation(forecast, sealed, outcome_changes={'status': 'UNKNOWN', 'yards': 0, 'opportunities': 0})
        with patch.object(gate, 'datetime', PostgameClock), self.assertRaisesRegex(ValueError, 'OUTCOME_UNKNOWN_NOT_ZERO'):
            gate._manifest(self.root, gate.FORWARD, ref)

    def test_verified_outcome_source_hash_is_rechecked(self):
        forecast, sealed = self.seal_authored_forward()
        ref = self.fixture.observation(forecast, sealed)
        body = gate.read(self.root, ref)
        source = gate.read(self.root, body['source_proof'])['source_inputs'][0]
        (self.root / source['path']).write_bytes(b'changed source bytes')
        with patch.object(gate, 'datetime', PostgameClock), self.assertRaisesRegex(ValueError, 'HASH_CHANGED'):
            gate._manifest(self.root, gate.FORWARD, ref)

    def test_lifecycle_retry_or_different_manifest_is_rejected(self):
        body = {'manifest': self.fixture.fit, 'operation': gate.DEVELOPMENT,
                'attempts': 1, 'retry_after_consumption': False}
        charter = {'stage_authority': {'operations': [gate.DEVELOPMENT]}, 'external_access': 'NONE',
                   'model_execution': {'operation': gate.DEVELOPMENT, 'inputs': {gate.FAMILY: [self.fixture.fit]}},
                   'preexecution_lifecycle': self.fixture.put('LIFECYCLE.json', body)}
        self.assertEqual(gate.check_lifecycle(self.root, charter)['status'], 'PASS')
        for changes in ({'retry_after_consumption': True}, {'manifest': self.fixture.historical}, {'attempts': 2}):
            charter['preexecution_lifecycle'] = self.fixture.put('LIFECYCLE.json', {**body, **changes})
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'LIFECYCLE_BINDING'):
                gate.check_lifecycle(self.root, charter)


if __name__ == '__main__':
    unittest.main()
