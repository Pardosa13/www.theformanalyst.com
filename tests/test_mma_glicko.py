import math

import mma_glicko


def test_matches_glickmans_worked_example_direction():
    # Equal fighters: winner goes up, loser down by the same amount, both less uncertain.
    w = mma_glicko.update(1500, 200, 0.06, 1500, 200, 1.0)
    l = mma_glicko.update(1500, 200, 0.06, 1500, 200, 0.0)
    assert w[0] > 1500 > l[0]
    # (plain arithmetic: another test file stubs numpy, which pytest.approx needs)
    assert abs((w[0] - 1500) - (1500 - l[0])) < 1e-6
    assert w[1] < 200 and l[1] < 200


def test_glickman_paper_example_single_game_scale():
    # Upset win over a much stronger opponent moves the rating far more than an expected win.
    upset = mma_glicko.update(1500, 100, 0.06, 1800, 50, 1.0)[0] - 1500
    expected = mma_glicko.update(1500, 100, 0.06, 1200, 50, 1.0)[0] - 1500
    assert upset > 3 * expected > 0


def test_fights_before_the_seed_cutoff_are_not_counted_again():
    ratings = {"a": (1600, 80, 0.06, None), "b": (1550, 90, 0.06, None)}
    fights = [("2026-03-01", "a", "b", 1.0), ("2026-06-01", "a", "b", 0.0)]
    changed = mma_glicko.plan_updates(fights, ratings, "2026-04-01")
    assert set(changed) == {"a", "b"}
    assert changed["a"][3].isoformat() == "2026-06-01"
    assert changed["a"][0] < 1600 and changed["b"][0] > 1550


def test_a_second_run_changes_nothing():
    ratings = {"a": (1600, 80, 0.06, None), "b": (1550, 90, 0.06, None)}
    fights = [("2026-06-01", "a", "b", 1.0)]
    first = mma_glicko.plan_updates(fights, ratings, "2026-04-01")
    again = {fid: (r, rd, v, last) for fid, (r, rd, v, last) in first.items()}
    assert mma_glicko.plan_updates(fights, again, "2026-04-01") == {}


def test_new_fighter_starts_at_default():
    changed = mma_glicko.plan_updates([("2026-06-01", "new", "b", 1.0)],
                                      {"b": (1700, 60, 0.06, None)}, "2026-04-01")
    assert changed["new"][0] > 1500
    assert math.isfinite(changed["new"][1]) and changed["new"][1] <= 350


class _Cur:
    def __init__(self, conn):
        self.conn = conn
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params=None):
        self.conn.sql.append((sql, params))
        self.last = sql
    def fetchone(self):
        return (self.conn.created,)
    def fetchall(self):
        if "FROM mma_fighters" in self.last:
            return [("a", 1600.0, 80.0, 0.06, None), ("b", 1550.0, 90.0, 0.06, None)]
        return [("2026-03-01", "a", "b", "A", "B", "A"), ("2026-06-01", "a", "b", "A", "B", "B")]


class _Conn:
    def __init__(self, created):
        self.created, self.sql, self.committed = created, [], False
    def cursor(self):
        return _Cur(self)
    def commit(self):
        self.committed = True


def test_default_cutoff_is_the_seed_date_and_only_later_fights_count(monkeypatch):
    from datetime import datetime
    monkeypatch.delenv("MMA_GLICKO_SEED_CUTOFF", raising=False)
    conn = _Conn(datetime(2026, 4, 10, 9, 0))
    assert mma_glicko.update_ratings(conn) == 2
    updates = [p for sql, p in conn.sql if sql.startswith("UPDATE")]
    assert all(str(p[3]) == "2026-06-01" for p in updates)
    a = next(p for p in updates if p[4] == "a")
    assert a[0] < 1600            # lost the June fight; the March one was already in the seed
    assert conn.committed


def test_no_seed_date_means_no_update(monkeypatch):
    monkeypatch.delenv("MMA_GLICKO_SEED_CUTOFF", raising=False)
    conn = _Conn(None)
    assert mma_glicko.update_ratings(conn) == 0
    assert not any(sql.startswith("UPDATE") for sql, _ in conn.sql)
