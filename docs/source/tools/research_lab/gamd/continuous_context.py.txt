"""Full retained team-game reconciliation for the existing GAMD feature contract.

This module never certifies a selected-player subtotal. It compares all admitted
dated box-score rows with an independent, ended play-by-play game. Source reads
are made only by the existing admitted source operation.
"""
from collections import defaultdict, Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from . import licensed_source as ls

REVISION = 'puntproof-gamd-foundation-v2'
CHANNEL_FIELDS = {'receiving': ('targets', 'receptions', 'receiving_yards'),
                  'rushing': ('carries', 'rushing_yards')}


def sources(*items):
    found = {}
    def add(value):
        if isinstance(value, list):
            for item in value: add(item)
        elif isinstance(value, dict) and value:
            found[json.dumps(value, sort_keys=True)] = deepcopy(value)
    for item in items: add(item)
    return list(found.values())


def latest_observation(refs, fallback=None):
    clocks = [r.get('observed_at') or r.get('acquired') for r in refs]
    clocks = [value for value in clocks if value]
    if fallback: clocks.append(fallback)
    return max(clocks, key=ls.clock) if clocks else None


def load(root, catalog, cutoff):
    from .source import read_partition
    data = defaultdict(list)
    for entry in catalog.get('historical_inputs', []):
        if entry['season'] not in (2023, 2024):
            raise ValueError('CONTEXT_SEASON_NOT_ADMITTED')
        base = entry.get('source_root') or catalog['historical_source_root']
        data[entry['dataset']].extend(read_partition(base, entry, years=(2023, 2024), context=True))
    return reconcile(data, cutoff)


def reconcile(data, cutoff):
    """Pure full-category checks, using the frozen native RB/FB lateral rule."""
    from source_adapter import project_rbfb, schedule_timezone
    # Manager-normalized JSON uses numeric strings; historical projection uses ints.
    data={kind:[dict(row,season=ls.integer(row.get('season')),week=ls.integer(row.get('week'))) for row in rows] for kind,rows in data.items()}
    cut = ls.clock(cutoff)
    fixtures, dated, statmap = {}, {}, {}
    team_week = defaultdict(set)
    refs = defaultdict(list)
    problems = defaultdict(set)
    samples = defaultdict(list)
    def visible(row):
        at = row.get('_source', {}).get('observed_at')
        return at is not None and ls.clock(at) <= cut
    for row in data.get('schedule', []):
        if not visible(row): continue
        y, w = ls.integer(row.get('season')), ls.integer(row.get('week'))
        home, away = ls.team(row.get('home_team')), ls.team(row.get('away_team'))
        gid = row.get('game_id')
        if y not in (2023, 2024, 2026) or not w or row.get('game_type') != 'REG': continue
        if gid != f'{y}_{w:02d}_{away}_{home}' or home == away: continue
        kickoff = datetime.fromisoformat(row['gameday']+'T'+row['gametime']).replace(tzinfo=schedule_timezone()).astimezone(timezone.utc)
        fixture = dict(season=y, week=w, game_id=gid, home_team=home, away_team=away,
                       kickoff_utc=kickoff.isoformat(), observed_at=row['_source']['observed_at'],
                       completed=False, completion_observed_at=None, pbp_complete=True,
                       snap_complete=False, identity_verified=True)
        if gid in fixtures and fixtures[gid] != fixture: problems[gid].add('CONFLICTING_FIXTURE')
        fixtures[gid] = fixture
        for tm in (home, away):
            team_week[y, w, tm].add(gid); refs[gid, tm].append(row['_source'])
    for ids in team_week.values():
        if len(ids) != 1:
            for gid in ids: problems[gid].add('AMBIGUOUS_TEAM_WEEK')
    for row in data.get('rosters', []):
        if not visible(row): continue
        k = (row['season'], row['week'], ls.team(row.get('team')), row.get('gsis_id'))
        if not k[-1]: continue
        if k in dated and dated[k]['position'] != row.get('position'):
            for gid in team_week[k[:3]]: problems[gid].add('CONFLICTING_DATED_POSITION')
        dated[k] = row
    for row in data.get('player_stats', []):
        if not visible(row): continue
        tm = ls.team(row.get('recent_team') or row.get('team'))
        ids = team_week[row['season'], row['week'], tm]
        if len(ids) != 1: continue
        gid = next(iter(ids)); gs = row.get('player_id'); k = (gid, tm, gs)
        f=fixtures[gid];opponent=f['away_team'] if tm==f['home_team'] else f['home_team']
        if row.get('opponent_team') and ls.team(row['opponent_team'])!=opponent:
            problems[gid].add('STAT_OPPONENT_IDENTITY_CONFLICT');continue
        if not gs: problems[gid].add('MISSING_STAT_IDENTITY'); continue
        if k in statmap:
            problems[gid].add('DUPLICATE_STAT_IDENTITY'); continue
        statmap[k] = row; refs[gid, tm].append(row['_source'])
        rr = dated.get((row['season'], row['week'], tm, gs))
        if rr: refs[gid, tm].append(rr['_source'])
        if len(samples[gid, tm]) < 2:
            samples[gid, tm].append({'kind': 'player_stat', 'row': row})
    contributions = defaultdict(lambda: dict(targets=0, receptions=0, receiving_yards=0))
    ended, seen, pbp = {}, set(), []
    bad_receiving = set()
    for row in data.get('pbp', []):
        gid = row.get('game_id'); f = fixtures.get(gid)
        if f is None: continue
        if not visible(row): problems[gid].add('PBP_CLOCK_UNKNOWN_OR_LATE'); continue
        if (row['season'], row['week'], ls.team(row.get('home_team')), ls.team(row.get('away_team'))) != (f['season'], f['week'], f['home_team'], f['away_team']):
            problems[gid].add('PBP_FIXTURE_CONFLICT'); continue
        pk = gid, row.get('play_id')
        if pk[1] is None or pk in seen: problems[gid].add('MISSING_OR_DUPLICATE_PLAY'); continue
        seen.add(pk); pbp.append(row)
        for tm in (f['home_team'], f['away_team']): refs[gid, tm].append(row['_source'])
        if row.get('play_type') == 'game_end' or row.get('game_half') == 'End of Game' or row.get('desc') == 'END GAME':
            ended[gid] = row['_source']['observed_at']
            for tm in (f['home_team'], f['away_team']): samples[gid, tm].append({'kind': 'game_end', 'row': row})
            continue
        yes = lambda field: row.get(field) in (1, '1', '1.0', True)
        if row.get('play_type') == 'no_play' or yes('two_point_attempt'): continue
        tm = ls.team(row.get('posteam'))
        if tm not in (f['home_team'], f['away_team']): continue
        if yes('pass_attempt'):
            gs = row.get('receiver_player_id')
            if gs:
                vals = contributions[gid, tm, gs]; vals['targets'] += 1
                if yes('complete_pass'):
                    yards = ls.integer(row.get('receiving_yards')); vals['receptions'] += 1
                    if yards is None: bad_receiving.add((gid, tm))
                    else: vals['receiving_yards'] += yards
            elif yes('complete_pass'): bad_receiving.add((gid, tm))
            lateral = row.get('lateral_receiver_player_id')
            if lateral:
                yards = ls.integer(row.get('lateral_receiving_yards'))
                if yards is None: bad_receiving.add((gid, tm))
                else: contributions[gid, tm, lateral]['receiving_yards'] += yards
        if row.get('lateral_receiver_player_id') or row.get('lateral_rusher_player_id'):
            samples[gid, tm].append({'kind': 'lateral_play', 'row': row})
    for gid, f in fixtures.items():
        if gid in ended and not problems[gid]: f.update(completed=True, completion_observed_at=ended[gid])
    last_year=max((f['season'] for f in fixtures.values()),default=2024)
    rb_rows, rb_counts, rb_gaps = project_rbfb(pbp, fixtures, dated, cutoff=cutoff, target_period=(last_year, 19))
    rb = {(r['game_id'], r['team']): r for r in rb_rows}
    table = []
    for gid, f in sorted(fixtures.items()):
        for tm in (f['home_team'], f['away_team']):
            for ch, fields in CHANNEL_FIELDS.items():
                reasons = set(problems[gid]); players = {}
                if gid not in ended: reasons.add('NO_EXPLICIT_PBP_END_MARKER')
                if ch == 'receiving' and (gid, tm) in bad_receiving: reasons.add('UNKNOWN_RECEIVING_CONTRIBUTION')
                for (game, side, gs), row in statmap.items():
                    if (game, side) != (gid, tm): continue
                    rr = dated.get((f['season'], f['week'], tm, gs))
                    if rr is None:
                        if any(ls.integer(row.get(v)) not in (None, 0) for v in fields): reasons.add('STAT_WITHOUT_DATED_MEMBERSHIP')
                        continue
                    if ch=='rushing' and rr['position'] not in ('RB', 'FB'): continue
                    vals = {v: ls.integer(row.get(v)) for v in fields}
                    if any(v is None for v in vals.values()): reasons.add('UNKNOWN_INDIVIDUAL_CATEGORY_VALUE'); continue
                    if any(vals[v] < 0 for v in fields if not v.endswith('_yards')): reasons.add('NEGATIVE_CATEGORY_COUNT'); continue
                    players[gs] = vals
                    if ch == 'receiving' and vals != contributions.get((gid, tm, gs), dict.fromkeys(fields, 0)):
                        reasons.add('PBP_PLAYER_STAT_RECONCILIATION_MISMATCH')
                totals = {v: sum(p[v] for p in players.values()) for v in fields}
                if not players: reasons.add('NO_DATED_CATEGORY_STATS')
                if ch == 'receiving':
                    for (game, side, gs), vals in contributions.items():
                        if (game, side) != (gid, tm): continue
                        rr = dated.get((f['season'], f['week'], tm, gs))
                        if rr is None or gs not in players:
                            reasons.add('PBP_CONTRIBUTOR_NOT_RECONCILED')
                else:
                    r = rb.get((gid, tm), {})
                    if r.get('state') != 'READY': reasons.add('UNKNOWN_RBFB_PBP_CONTEXT')
                    elif (totals['carries'], totals['rushing_yards']) != (r['rb_carries'], r['rb_yards']):
                        reasons.add('RBFB_PBP_STAT_RECONCILIATION_MISMATCH')
                lineage = sources(refs[gid, tm]); valid = not reasons
                table.append(dict(season=f['season'], week=f['week'], game_id=gid, team=tm, channel=ch,
                    complete_reconciled=valid, players=players, team_totals=totals if valid else dict.fromkeys(fields),
                    independently_reconciled_totals=True if valid else False,
                    basis='FULL_DATED_STATS_VS_ENDED_PBP_RECEIVING_AND_RBFB_LATERALS',
                    category_scope='ALL_DATED_PLAYERS' if ch=='receiving' else 'RB_FB_ONLY_WITH_LATERALS',
                    observed_at=latest_observation(lineage), source_lineage=lineage,
                    unresolved_reasons=sorted(reasons), source_player_count=len(players)))
    return {'contexts': table, 'schedule': data.get('schedule', []), 'rosters': data.get('rosters', []),
            'summary': dict(Counter((r['channel']+('_supported' if r['complete_reconciled'] else '_unknown')) for r in table)),
            'rbfb_accounting': rb_counts, 'rbfb_gaps': rb_gaps,
            'evidence_samples': [{'game_id': k[0], 'team': k[1], 'samples': v} for k, v in samples.items()]}
