"""Live pre-race odds ingestion: what gets stored, and what deliberately isn't.

The network and the database are the two parts of odds_ingest.py that cannot
be exercised here, so the matching and filtering rules that decide what ends up
in live_odds_snapshots are pure functions and are tested directly.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import odds_ingest
from ladbrokes import normalize_runner_name


CAPTURED_AT = datetime(2026, 8, 30, 4, 30, tzinfo=timezone.utc)


def _race(**overrides):
    race = {
        'race_id': 101,
        'race_number': 4,
        'meeting_id': 11,
        'meeting_name': '260830_Flemington',
        'track': 'Flemington',
        'runners': {
            normalize_runner_name('Winx'): 1,
            normalize_runner_name("Black Caviar"): 2,
            normalize_runner_name("Might And Power"): 3,
        },
    }
    race.update(overrides)
    return race


def _payload(status='open', runners=None):
    return {
        'status': status,
        'odds': runners if runners is not None else {
            normalize_runner_name('Winx'): {'name': 'Winx', 'win': 2.4, 'place': 1.3},
            normalize_runner_name('Black Caviar'): {'name': 'Black Caviar', 'win': 3.8, 'place': 1.6},
            normalize_runner_name('Might And Power'): {'name': 'Might And Power', 'win': 9.0, 'place': 2.5},
        },
    }


class TestTrackFromMeetingName:
    def test_strips_the_yymmdd_prefix(self):
        assert odds_ingest._track_from_meeting_name('251128_Mt Gambier') == 'Mt Gambier'

    def test_a_name_without_a_prefix_is_the_track(self):
        assert odds_ingest._track_from_meeting_name('Flemington') == 'Flemington'

    def test_empty_is_none(self):
        assert odds_ingest._track_from_meeting_name('') is None
        assert odds_ingest._track_from_meeting_name(None) is None


class TestRaceIsInWindow:
    NOW = datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc)

    def test_a_race_about_to_jump_is_polled(self):
        assert odds_ingest.race_is_in_window(self.NOW + timedelta(minutes=10), self.NOW, 6, 5)

    def test_a_race_tomorrow_is_not_polled_yet(self):
        assert not odds_ingest.race_is_in_window(self.NOW + timedelta(hours=20), self.NOW, 6, 5)

    def test_a_race_just_past_its_start_is_still_polled(self):
        # Races run late; the closed-market status is the authoritative stop
        # signal, not the scheduled time.
        assert odds_ingest.race_is_in_window(self.NOW - timedelta(minutes=2), self.NOW, 6, 5)

    def test_a_long_finished_race_is_not_polled(self):
        assert not odds_ingest.race_is_in_window(self.NOW - timedelta(hours=2), self.NOW, 6, 5)

    def test_an_unknown_start_time_is_polled(self):
        # Far more likely a feed quirk than a reason to skip a race that may be
        # about to run.
        assert odds_ingest.race_is_in_window(None, self.NOW, 6, 5)


class TestSnapshotRows:
    def test_one_row_per_matched_runner(self):
        rows, diagnostics = odds_ingest.snapshot_rows_for_race(_race(), _payload(), CAPTURED_AT)
        assert len(rows) == 3
        assert diagnostics == []
        assert {row['horse_id'] for row in rows} == {1, 2, 3}
        assert all(row['race_id'] == 101 for row in rows)
        assert all(row['source'] == 'ladbrokes' for row in rows)
        assert all(row['captured_at'] == CAPTURED_AT for row in rows)

    def test_prices_and_place_prices_are_carried(self):
        rows, _ = odds_ingest.snapshot_rows_for_race(_race(), _payload(), CAPTURED_AT)
        by_horse = {row['horse_id']: row for row in rows}
        assert by_horse[1]['odds'] == pytest.approx(2.4)
        assert by_horse[1]['place_odds'] == pytest.approx(1.3)

    def test_a_closed_market_stores_nothing(self):
        for status in ('final', 'resulted', 'abandoned', 'closed', 'interim'):
            rows, diagnostics = odds_ingest.snapshot_rows_for_race(
                _race(), _payload(status=status), CAPTURED_AT
            )
            assert rows == []
            assert 'market closed' in diagnostics[0]

    def test_a_suspended_market_is_still_captured(self):
        # Suspended is temporary — the price is real and the race has not run.
        rows, _ = odds_ingest.snapshot_rows_for_race(
            _race(), _payload(status='suspended'), CAPTURED_AT
        )
        assert len(rows) == 3

    def test_an_unmatched_runner_is_reported_not_stored(self):
        payload = _payload()
        payload['odds'][normalize_runner_name('Phar Lap')] = {'name': 'Phar Lap', 'win': 5.0}
        rows, diagnostics = odds_ingest.snapshot_rows_for_race(_race(), payload, CAPTURED_AT)
        assert len(rows) == 3
        assert any('Phar Lap' in d for d in diagnostics)

    def test_a_scratched_runner_is_recorded_as_scratched(self):
        payload = _payload()
        payload['odds'][normalize_runner_name('Might And Power')]['is_scratched'] = True
        rows, _ = odds_ingest.snapshot_rows_for_race(_race(), payload, CAPTURED_AT)
        scratched = [row for row in rows if row['horse_id'] == 3]
        assert scratched and scratched[0]['is_scratched'] is True

    def test_a_partial_feed_is_not_stored_as_a_book(self):
        """One price is not a market.

        Storing it would create exactly the single-runner 'book' that
        market_probability.fair_probabilities has to defend against, and it is
        the same defect book_quality.py gates broken historical races on.
        """
        payload = _payload(runners={
            normalize_runner_name('Winx'): {'name': 'Winx', 'win': 2.4},
            normalize_runner_name('Black Caviar'): {'name': 'Black Caviar', 'win': None},
        })
        rows, diagnostics = odds_ingest.snapshot_rows_for_race(_race(), payload, CAPTURED_AT)
        assert rows == []
        assert any('not a book' in d for d in diagnostics)

    def test_unavailable_runners_do_not_count_toward_the_book(self):
        payload = _payload()
        for name in (normalize_runner_name('Black Caviar'), normalize_runner_name('Might And Power')):
            payload['odds'][name]['is_available'] = False
        rows, diagnostics = odds_ingest.snapshot_rows_for_race(_race(), payload, CAPTURED_AT)
        assert rows == []
        assert any('not a book' in d for d in diagnostics)

    def test_nonsense_prices_are_dropped_without_losing_the_runner(self):
        # A runner is still recorded (it was in the race) but with no price:
        # '1.00' is not a price and 'SP' is not a number. The rest of the book
        # still has two real prices, so the snapshot is stored.
        race = _race()
        race['runners'][normalize_runner_name('Sunline')] = 4
        payload = _payload()
        payload['odds'][normalize_runner_name('Sunline')] = {'name': 'Sunline', 'win': 6.0}
        payload['odds'][normalize_runner_name('Winx')]['win'] = '1.00'
        payload['odds'][normalize_runner_name('Black Caviar')]['win'] = 'SP'
        rows, _ = odds_ingest.snapshot_rows_for_race(race, payload, CAPTURED_AT)
        by_horse = {row['horse_id']: row for row in rows}
        assert by_horse[1]['odds'] is None
        assert by_horse[2]['odds'] is None
        assert by_horse[4]['odds'] == pytest.approx(6.0)

    def test_an_empty_payload_is_not_an_error(self):
        assert odds_ingest.snapshot_rows_for_race(_race(), {}, CAPTURED_AT)[0] == []
        assert odds_ingest.snapshot_rows_for_race(_race(), None, CAPTURED_AT)[0] == []


class TestCoercePrice:
    @pytest.mark.parametrize('value,expected', [
        (2.5, 2.5), ('3.40', 3.4), (1.0, None), (0.5, None), (-2.0, None),
        (None, None), ('', None), ('SP', None),
    ])
    def test_only_real_decimal_prices_survive(self, value, expected):
        assert odds_ingest._coerce_price(value) == expected


class TestLiveOddsFreshness:
    """ml_predict only scores off a price that is still the market."""

    class _Session:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, *_args, **_kwargs):
            rows = self.rows

            class Result:
                def mappings(self):
                    class M:
                        def all(inner):
                            return rows
                    return M()
            return Result()

    class _Race:
        def __init__(self, race_id):
            self.id = race_id

    def _load(self, rows):
        import ml_predict
        return ml_predict._load_latest_live_odds_for_meeting(
            [self._Race(101)], self._Session(rows)
        )

    def _row(self, horse_id, odds, age_seconds, is_scratched=False):
        return {
            'horse_id': horse_id, 'odds': odds, 'source': 'ladbrokes',
            'captured_at': datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
            'is_scratched': is_scratched,
        }

    def test_a_fresh_price_is_used(self):
        loaded = self._load([self._row(1, 3.5, 30)])
        assert loaded[1]['odds'] == pytest.approx(3.5)

    def test_a_stale_price_is_dropped(self):
        assert self._load([self._row(1, 3.5, 60 * 60)]) == {}

    def test_a_scratched_snapshot_is_dropped(self):
        assert self._load([self._row(1, 3.5, 30, is_scratched=True)]) == {}

    def test_a_priceless_snapshot_is_dropped(self):
        assert self._load([self._row(1, None, 30)]) == {}

    def test_no_races_needs_no_query(self):
        import ml_predict

        def explode(*_args, **_kwargs):
            raise AssertionError("should not have queried")

        session = self._Session([])
        session.execute = explode
        assert ml_predict._load_latest_live_odds_for_meeting([], session) == {}

    def test_a_missing_table_degrades_to_no_live_odds(self):
        import ml_predict

        class Broken:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError('relation "live_odds_snapshots" does not exist')

        assert ml_predict._load_latest_live_odds_for_meeting([self._Race(101)], Broken()) == {}


class TestBoundedLoop:
    """`--duration-seconds` exists so a scheduler that can only fire every
    quarter hour can still poll every three minutes: one run holds the loop
    for its window and then exits on its own. Killing the process instead
    would work, but a SIGKILL and a crash look identical in the logs, which is
    the wrong thing for the job whose silence took live picks down."""

    def _run(self, monkeypatch, argv, sleeps_before_stop=10):
        passes = []
        slept = []

        monkeypatch.setattr(odds_ingest, 'get_engine', lambda: object())
        monkeypatch.setattr(odds_ingest, 'ensure_tables', lambda _engine: None)
        monkeypatch.setattr(
            odds_ingest, 'run_once',
            lambda *args, **kwargs: passes.append(1),
        )

        # A monotonic clock that only advances when the loop sleeps, so the
        # window is exercised deterministically instead of in real time.
        now = {'t': 0.0}
        monkeypatch.setattr(odds_ingest.time, 'monotonic', lambda: now['t'])

        def fake_sleep(seconds):
            slept.append(seconds)
            now['t'] += seconds
            if len(slept) > sleeps_before_stop:
                raise AssertionError("loop did not stop at its deadline")

        monkeypatch.setattr(odds_ingest.time, 'sleep', fake_sleep)
        assert odds_ingest.main(argv) == 0
        return passes, slept

    def test_the_loop_exits_after_its_window(self, monkeypatch):
        passes, slept = self._run(
            monkeypatch,
            ['--loop', '--interval', '180', '--duration-seconds', '780'],
        )
        # 780s / 180s leaves room for the pass at t=0 and three more sleeps;
        # the fifth would run past the deadline, so the loop stops instead.
        assert len(passes) == 5
        assert slept == [180.0, 180.0, 180.0, 180.0]

    def test_a_window_shorter_than_one_interval_still_polls_once(self, monkeypatch):
        """A misconfigured window must not turn into a job that stores
        nothing — the pass runs before the deadline is ever consulted."""
        passes, slept = self._run(
            monkeypatch,
            ['--loop', '--interval', '180', '--duration-seconds', '10'],
        )
        assert len(passes) == 1
        assert slept == []

    def test_ensure_tables_runs_before_any_polling(self, monkeypatch):
        """The table is created by this job and nothing else — no migration
        owns live_odds_snapshots — so a single one-shot run against a database
        that has never seen it must leave it there."""
        order = []
        monkeypatch.setattr(odds_ingest, 'get_engine', lambda: object())
        monkeypatch.setattr(odds_ingest, 'ensure_tables',
                            lambda _engine: order.append('ensure_tables'))
        monkeypatch.setattr(odds_ingest, 'run_once',
                            lambda *a, **kw: order.append('run_once'))

        assert odds_ingest.main([]) == 0
        assert order == ['ensure_tables', 'run_once']
