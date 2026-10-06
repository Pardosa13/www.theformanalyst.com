import json
from types import SimpleNamespace

import app as appmod
from notes_parsing import parse_notes_components


def horse(age=5, sex='Mare'):
    return SimpleNamespace(csv_data={'horse age': age, 'horse sex': sex})


def badges(names, h=None):
    return appmod.evaluate_component_edge_badges(h or horse(), names)


def test_proven_edge_single_signals():
    for name in appmod.PROVEN_EDGE_COMPONENTS:
        assert badges([name]) == {appmod.PROVEN_EDGE_BADGE: [name]}


def test_proven_edge_plus_replaces_proven_edge():
    out = badges(['Career Win Rate - Elite 40%+', 'Trainer - Hot Form (L100 22%+ SR)'], horse(4, 'Gelding'))
    assert appmod.PROVEN_EDGE_PLUS_BADGE in out
    assert appmod.PROVEN_EDGE_BADGE not in out
    out = badges(['Weight vs Field - Below (2-3kg)', 'Track Win Rate - Exceptional (51%+)'])
    assert list(out) == [appmod.PROVEN_EDGE_PLUS_BADGE]


def test_elite_career_needs_4yo_gelding_for_plus():
    assert list(badges(['Career Win Rate - Elite 40%+'], horse(4, 'Colt'))) == [appmod.PROVEN_EDGE_BADGE]
    assert list(badges(['Career Win Rate - Elite 40%+'], horse(5, 'Gelding'))) == [appmod.PROVEN_EDGE_BADGE]


def test_watch_badges():
    assert badges(['Second Up - Has Won Second Up']) == {}
    out = badges(['Second Up - Has Won Second Up', 'Specialist - Undefeated Condition',
                  'Running Position - Midfield Staying', 'Interstate State Move - SA → VIC'])
    assert list(out) == [appmod.WATCH_BADGE]
    assert len(out[appmod.WATCH_BADGE]) == 3


def test_badge_names_match_parsed_notes():
    notes = '\n'.join([
        '+20.0 : Trainer hot form (25% L100)',
        '+ 10.0 : Weight 2.5kg below race avg',
        '+20.0 : Elite career win rate (40%+, 18.5% SR lift confirmed)',
        '+ 15.0 : Dropped 3.5kg from last start',
        '+ 5.0 : BACKMARKER in Mile',
        '+ 6.0 : Exceptional win rate (60%) at this track',
        '+ 3.0 : Second-up winner',
        '+ 10.0 : UNDEFEATED on good condition - specialist',
        '+ 0.0 : MIDFIELD in Staying',
        '+ 2.0 : Interstate state move — SA → VIC',
    ])
    parsed = set(parse_notes_components(notes))
    wanted = set(appmod.PROVEN_EDGE_COMPONENTS) | set(appmod.WATCH_COMPONENTS) | {
        'Track Win Rate - Exceptional (51%+)', 'Second Up - Has Won Second Up',
        'Specialist - Undefeated Condition'}
    assert wanted <= parsed
    assert 'Elite career win rate' not in parsed


def test_track_low_and_track_distance_low_no_longer_overlap():
    td_only = parse_notes_components('+ 0.0 : No wins at this track\n+ 1.0 : Low win rate (10%) at this track+distance\n')
    assert 'Track+Distance Win Rate - Low' in td_only and 'Track Win Rate - Low (1-15%)' not in td_only
    track_only = parse_notes_components('+ 1.0 : Low win rate (10%) at this track\n+ 0.0 : No runs at this track+distance\n')
    assert 'Track Win Rate - Low (1-15%)' in track_only and 'Track+Distance Win Rate - Low' not in track_only
    dist_only = parse_notes_components('+ 1.0 : Low win rate (10%) at this distance\n+ 0.0 : No wins at this track\n')
    assert 'Track Win Rate - Low (1-15%)' not in dist_only


def test_tracking_merges_and_never_shrinks():
    p = SimpleNamespace(best_bet_routes=None, best_bet_badges=None, best_bet_qualified_at=None)
    appmod.merge_best_bet_tracking(p, ['favourite'], {appmod.WATCH_BADGE: ['Running Position - Midfield Staying']})
    first_seen = p.best_bet_qualified_at
    appmod.merge_best_bet_tracking(p, ['proven_edge'], {appmod.PROVEN_EDGE_BADGE: ['Weight Change - Dropped 3kg+']})
    assert p.best_bet_routes == 'favourite,proven_edge'
    assert set(json.loads(p.best_bet_badges)) == {appmod.WATCH_BADGE, appmod.PROVEN_EDGE_BADGE}
    assert p.best_bet_qualified_at == first_seen
