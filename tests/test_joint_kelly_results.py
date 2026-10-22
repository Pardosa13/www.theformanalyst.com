"""ML Data's long-run table for the saved multi-horse (joint Kelly) stakes."""
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod


def _row(race_id, start, kelly, finish, sp, sized_price=None, edge=5.0, prob=0.2):
    return (
        SimpleNamespace(kelly_stake_pct=kelly, value_edge_price=sized_price, value_edge_pct=edge,
                        ml_win_probability=prob,
                        value_edge_captured_at=start - timedelta(minutes=5) if kelly else None),
        SimpleNamespace(finish_position=finish, sp=sp),
        race_id,
        start,
    )


def _results(rows, monkeypatch):
    monkeypatch.setattr(appmod, '_load_joint_kelly_rows', lambda *a, **k: rows)
    return appmod.calculate_joint_kelly_results()


def test_races_are_settled_as_a_whole_with_a_fixed_bank(monkeypatch):
    bank = appmod.KELLY_TRACKING_BANKROLL
    old = datetime(2026, 9, 20, 5)
    new = datetime(2026, 10, 18, 5)
    rows = [
        # Race 1 (old prices): two runners backed, one wins at SP 5.0, sized at 6.0.
        _row(1, old, 0.02, 1, 5.0, sized_price=6.0),
        _row(1, old, 0.01, 3, 4.0, sized_price=4.0),
        # Race 2 (fresh prices): one runner backed, loses.
        _row(2, new, 0.03, 2, 3.0, sized_price=3.2),
    ]
    out = _results(rows, monkeypatch)
    all_time, fresh = out['summary']

    assert all_time['races'] == 2
    assert all_time['bets'] == 3
    assert all_time['races_won'] == 1
    staked = (0.02 + 0.01 + 0.03) * bank
    assert round(all_time['staked'], 6) == round(staked, 6)
    assert round(all_time['profit_sp'], 6) == round(0.02 * bank * 5.0 - staked, 6)
    assert round(all_time['profit_sized'], 6) == round(0.02 * bank * 6.0 - staked, 6)
    assert all_time['longest_losing_run'] == 1
    # Profit after race 1 is the peak; race 2's loss is the drawdown.
    assert round(all_time['max_drawdown'], 6) == round(0.03 * bank, 6)

    # Only race 2 is on or after the fresh-price start date.
    assert fresh['races'] == 1
    assert fresh['races_won'] == 0

    assert [m['month'] for m in out['months']] == ['2026-09', '2026-10']
    assert len(out['history']) == 2
    assert out['history'][-1]['cumulative'] == round(all_time['profit_sp'], 2)


def test_no_stakes_reads_as_empty_not_as_a_loss(monkeypatch):
    out = _results([], monkeypatch)
    assert out['summary'][0]['races'] == 0
    assert out['summary'][0]['roi_sp'] == 0.0
    assert out['months'] == []


def test_ml_data_page_shows_the_table():
    source = Path('app.py').read_text()
    start = source.index('def ml_data_analytics(')
    end = source.index('\n@app.route(', start)
    assert 'calculate_joint_kelly_results(' in source[start:end]
    template = Path('templates/ml_data.html').read_text()
    assert 'Live Kelly Stakes: Multi-Horse Results' in template
    assert 'jointKellyChart' in template


def _breakdown(out, key):
    return {row['label']: row for row in next(b for b in out['breakdowns'] if b['key'] == key)['rows']}


def test_breakdowns_split_the_same_bets(monkeypatch):
    start = datetime(2026, 10, 18, 5)
    rows = [
        # Race 1: model's top pick backed at $4 (edge 3), wins; 3rd pick unbacked.
        _row(1, start, 0.02, 1, 4.0, sized_price=4.0, edge=3.0, prob=0.4),
        _row(1, start, 0.01, 2, 12.0, sized_price=10.0, edge=12.0, prob=0.2),
        _row(1, start, 0.0, 3, 2.0, prob=0.3),
        # Race 2: one runner backed at $25 (edge 25), loses.
        _row(2, start, 0.03, 4, 30.0, sized_price=25.0, edge=25.0, prob=0.1),
        _row(2, start, 0.0, 1, 2.0, prob=0.5),
    ]
    out = _results(rows, monkeypatch)
    assert out['all_bets']['bets'] == 3

    edge = _breakdown(out, 'edge')
    assert edge['2-5 pts']['bets'] == 1 and edge['2-5 pts']['wins'] == 1
    assert edge['10-20 pts']['bets'] == 1 and edge['20+ pts']['bets'] == 1

    rank = _breakdown(out, 'rank')
    assert rank['Top pick']['bets'] == 1       # race 1's 0.4 runner
    assert rank['3rd pick']['bets'] == 1       # race 1's 0.2 runner
    assert rank['2nd pick']['bets'] == 1       # race 2's 0.1 runner

    backed = _breakdown(out, 'backed')
    assert backed['2 backed']['bets'] == 2 and backed['1 backed']['bets'] == 1

    price = _breakdown(out, 'price')
    assert set(price) == {'$3-$5', '$10-$20', '$20+'}

    move = _breakdown(out, 'move')
    assert move['Drifted 5%+']['bets'] == 2 and move['Steady']['bets'] == 1

    # Flat: +$30 on the winner, -$10 on each loser.
    assert out['all_bets']['flat_profit'] == 10.0
    assert out['all_bets']['luck_score'] is not None


def test_unstaked_runner_or_stake_after_the_jump_is_not_a_bet(monkeypatch):
    start = datetime(2026, 10, 18, 5)
    late = _row(1, start, 0.05, 1, 3.0)
    late[0].value_edge_captured_at = start + timedelta(minutes=1)
    out = _results([late, _row(1, start, 0.0, 2, 4.0)], monkeypatch)
    assert out['all_bets']['bets'] == 0
    assert out['summary'][0]['races'] == 0


def test_query_runs_on_a_real_database():
    """The subquery and joins must be valid SQL and pick the right rows."""
    from models import User, Meeting, Race, Horse, Prediction, Result
    db = appmod.db
    start = datetime(2026, 10, 18, 5)
    with appmod.app.app_context():
        db.create_all()
        user = User(username='jk_test_user', email='jk_test@example.com', password_hash='x')
        db.session.add(user)
        db.session.flush()
        meeting = Meeting(user_id=user.id, meeting_name='2026-10-18_JKTestPark',
                          uploaded_at=datetime(2026, 10, 17))
        db.session.add(meeting)
        db.session.flush()
        backed_race = Race(meeting_id=meeting.id, race_number=1, start_time_utc=start)
        other_race = Race(meeting_id=meeting.id, race_number=2, start_time_utc=start)
        db.session.add_all([backed_race, other_race])
        db.session.flush()
        for race, stakes in ((backed_race, (0.02, 0.0)), (other_race, (0.0, 0.0))):
            for i, stake in enumerate(stakes):
                horse = Horse(race_id=race.id, horse_name=f'JK {race.race_number}-{i}')
                db.session.add(horse)
                db.session.flush()
                db.session.add(Prediction(horse_id=horse.id, score=1.0, kelly_stake_pct=stake, ml_win_probability=0.3,
                                          value_edge_captured_at=start - timedelta(minutes=5)))
                db.session.add(Result(horse_id=horse.id, finish_position=i + 1, sp=3.0))
        db.session.commit()
        ids = {'meeting': meeting.id, 'user': user.id, 'races': [backed_race.id, other_race.id]}
        try:
            rows = appmod._load_joint_kelly_rows('JKTestPark')
            # Both runners of the race with a stake; nothing from the race without one.
            assert {r[2] for r in rows} == {backed_race.id}
            assert len(rows) == 2
        finally:
            # The test database is shared with other tests: leave it as found.
            db.session.rollback()
            horse_ids = [h.id for h in Horse.query.filter(Horse.race_id.in_(ids['races']))]
            Result.query.filter(Result.horse_id.in_(horse_ids)).delete(synchronize_session=False)
            Prediction.query.filter(Prediction.horse_id.in_(horse_ids)).delete(synchronize_session=False)
            Horse.query.filter(Horse.id.in_(horse_ids)).delete(synchronize_session=False)
            Race.query.filter(Race.id.in_(ids['races'])).delete(synchronize_session=False)
            Meeting.query.filter_by(id=ids['meeting']).delete()
            User.query.filter_by(id=ids['user']).delete()
            db.session.commit()
