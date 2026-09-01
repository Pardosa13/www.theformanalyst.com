"""Kelly stake must reach the page honestly, and be tracked as a staking plan.

Two defects motivated these tests, and they are unrelated to each other beyond
both being about `predictions.kelly_stake_pct`:

1. The ML meetings page could render "+9.6pp VALUE" in the Edge column beside a
   dash in the Kelly Stake column. The edge was being repainted from every live
   Ladbrokes poll while the stake was left at whatever the server rendered, so
   the two columns were reading two different prices. Worse, the dash was doing
   double duty: it meant both "no live price for this runner" and "priced, and
   the joint solve stakes nothing on it", which are opposite answers.

2. ML Data settled every tracked runner as a flat $10 win bet, so there was no
   way to tell whether sizing by `kelly_stake_pct` would have done better —
   the open question the Kelly stake exists to answer.
"""

import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as appmod
from model_classes import solve_joint_kelly

TEMPLATE = Path('templates/MLRaceMeetings.html').read_text(encoding='utf-8')
ML_DATA_TEMPLATE = Path('templates/ml_data.html').read_text(encoding='utf-8')

# Driver for the JS/Python solver parity check below. Kept as a list join so
# the JS never has to be embedded in a nested Python string literal.
NODE_PARITY_CHECK_JS = "const fs = require('fs');\neval(fs.readFileSync(process.argv[2], 'utf8'));\nconst cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));\nlet worst = 0, disagreements = 0;\nfor (const c of cases) {\n    const js = solveJointKelly(c.field.map(function (f) {\n        return { key: f[0], prob: f[1], odds: f[2] };\n    }));\n    const keys = new Set(Object.keys(js).concat(Object.keys(c.py)));\n    for (const k of keys) {\n        const a = js[k] || 0, b = c.py[k] || 0;\n        if ((a > 0) !== (b > 0)) disagreements++;\n        worst = Math.max(worst, Math.abs(a - b));\n    }\n}\nconsole.log(JSON.stringify({ disagreements: disagreements, worst: worst }));"


# ── 1. The Kelly Stake cell ─────────────────────────────────────────────────

def test_kelly_cell_distinguishes_no_bet_from_no_price():
    """A priced runner the solve declines says "No Bet"; only an unpriced one
    gets a dash. Collapsing both into a dash is what made a positive-edge
    runner look like a broken column."""
    cell = TEMPLATE[TEMPLATE.index('<td class="kelly-cell"'):]
    cell = cell[:cell.index('</td>')]

    assert "{% if horse.kelly_stake_pct and horse.kelly_stake_pct > 0 %}" in cell
    assert "{% elif horse.value_edge_pct is not none %}" in cell
    assert '>No Bet<' in cell
    # The dash survives, but only on the branch where nothing could be solved.
    assert cell.index('>No Bet<') < cell.index('kelly-unpriced')


def test_kelly_column_header_no_longer_claims_a_dash_means_a_short_price():
    header = TEMPLATE[TEMPLATE.index('>Kelly Stake</th>') - 1200:TEMPLATE.index('>Kelly Stake</th>')]
    assert 'the price is too short to be worth backing' not in header
    assert 'No Bet' in header


def test_poll_resolves_kelly_for_the_whole_race_not_just_the_edge():
    """The live poll must repaint the stake as well as the edge — they are two
    readings of the same price and cannot be struck at different ones."""
    assert 'function solveJointKelly(' in TEMPLATE
    assert 'function updateRaceKellyStakes(' in TEMPLATE
    # Collected per row, solved once per race: the stake for a runner depends
    # on its rivals, so a per-row pass cannot decide it.
    assert 'kellyEntries.push({' in TEMPLATE
    assert 'updateRaceKellyStakes(raceCard, kellyEntries);' in TEMPLATE
    # Solved after the row loop, not inside it.
    assert TEMPLATE.index('kellyEntries.push({') < TEMPLATE.index('updateRaceKellyStakes(raceCard, kellyEntries);')


def test_client_solver_uses_the_servers_staking_constants():
    """Hard-coding the multiplier or the cap in the template would let the
    displayed stake drift from the persisted one whenever either is tuned."""
    assert 'var KELLY_FRACTION_MULTIPLIER  = {{ kelly_fraction_multiplier|tojson }};' in TEMPLATE
    assert 'var KELLY_MAX_TOTAL_STAKE_PCT  = {{ kelly_max_total_stake_pct|tojson }};' in TEMPLATE

    route = appmod.APP_SOURCE_FOR_TESTS if hasattr(appmod, 'APP_SOURCE_FOR_TESTS') else Path('app.py').read_text(encoding='utf-8')
    route = route[route.index('def ml_view_meeting('):]
    route = route[:route.index('\nJURISDICTION_TRACKS')]
    assert 'kelly_fraction_multiplier=KELLY_FRACTION_MULTIPLIER' in route
    assert 'kelly_max_total_stake_pct=KELLY_MAX_TOTAL_STAKE_PCT' in route


def test_scratched_runners_are_kept_out_of_the_client_solve():
    """A dead runner left in the joint solve soaks up bankroll and shrinks
    every live stake beside it."""
    block = TEMPLATE[TEMPLATE.index('// ── 0b. KELLY STAKE INPUT'):]
    block = block[:block.index('kellyEntries.push({')]
    assert "row.classList.contains('horse-scratched')" in block


# ── The behaviour the cell is describing ────────────────────────────────────

def test_positive_edge_always_earns_a_stake_at_the_same_price():
    """The invariant that makes the reported screenshot a display bug and not a
    staking one.

    solve_joint_kelly's eligibility test is p*O > 1, which is precisely
    "edge > 0" at the same price. Its stakes-stay-positive check cannot then
    reject an eligible runner: for any included set, Q/(1-R) < 1 < p*O whenever
    the fair probabilities sum to at most 1 — which derive_ml_fair_probabilities
    guarantees, since each runner's probability is its share of the race. Nor
    can the reserve guard fire, because p_i > 1/O_i for every included runner
    makes R < sum(p) <= 1.

    So "+9.6pp edge, no stake" cannot come out of the solver. It can only mean
    the two cells were struck at different prices — which is exactly what the
    live poll was doing by repainting one and not the other.
    """
    random.seed(20260901)
    unstaked = 0
    races = 0
    for _ in range(20000):
        runners = random.choice([2, 4, 6, 8, 12])
        probabilities = [random.uniform(0.001, 1.0) for _ in range(runners)]
        total = sum(probabilities)
        probabilities = [p / total for p in probabilities]  # a real book: sums to 1
        odds = [round(random.uniform(1.02, 200.0), 2) for _ in range(runners)]
        field = list(zip(range(runners), probabilities, odds))

        positive_edge = [key for key, p, o in field if o > 1 and 0 < p < 1 and p * o > 1]
        if not positive_edge:
            continue
        races += 1
        stakes = solve_joint_kelly(field, 0.5, 0.20)
        unstaked += sum(1 for key in positive_edge if stakes.get(key, 0.0) <= 0)

    assert races > 1000, 'the sample never produced a value bet — check the generator'
    assert unstaked == 0


def test_no_bet_means_the_price_does_not_beat_the_model():
    """The cell copy has to say the true reason, not a plausible-sounding one.
    Since every positive edge is staked, a declined runner is always one whose
    price fails to beat the model's fair price."""
    # 1/2.00 = 50% implied against a 40% fair probability: negative edge.
    stakes = solve_joint_kelly([('short', 0.40, 2.00), ('value', 0.30, 5.00)], 1.0, None)
    assert stakes.get('short', 0.0) == 0.0
    assert stakes['value'] > 0

    cell = TEMPLATE[TEMPLATE.index('<td class="kelly-cell"'):]
    cell = cell[:cell.index('</td>')]
    assert 'the price does not beat what the model rates this runner' in cell
    assert 'better use of the same bankroll' not in cell


def test_client_solver_agrees_with_the_server_solver_exactly():
    """The JS in the template is a hand port of model_classes.solve_joint_kelly.

    Two implementations of one formula drift, and when they do the page shows a
    stake the database never agreed to. So they are checked against each other
    over random fields rather than trusted to stay in step — and the JS is read
    out of the template itself, so the test cannot pass against a copy that is
    no longer what ships.
    """
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is not available to run the template JS')

    start = TEMPLATE.index('var KELLY_FRACTION_MULTIPLIER')
    solver_js = TEMPLATE[start:TEMPLATE.index('function renderKellyCell(')]
    solver_js = (solver_js
                 .replace('{{ kelly_fraction_multiplier|tojson }}', '0.5')
                 .replace('{{ kelly_max_total_stake_pct|tojson }}', '0.2'))

    random.seed(1234)
    cases = []
    for _ in range(500):
        runners = random.choice([2, 4, 6, 8, 12, 16])
        probabilities = [random.uniform(0.001, 1.0) for _ in range(runners)]
        total = sum(probabilities)
        probabilities = [p / total for p in probabilities]
        odds = [round(random.uniform(1.02, 250.0), 2) for _ in range(runners)]
        field = [(str(i), probabilities[i], odds[i]) for i in range(runners)]
        cases.append({
            'field': [[key, prob, price] for key, prob, price in field],
            'py': solve_joint_kelly(field, 0.5, 0.20),
        })

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, 'kelly.js').write_text(solver_js, encoding='utf-8')
        Path(tmp, 'cases.json').write_text(json.dumps(cases), encoding='utf-8')
        Path(tmp, 'check.js').write_text(NODE_PARITY_CHECK_JS, encoding='utf-8')
        out = subprocess.run(
            [node, str(Path(tmp, 'check.js')), str(Path(tmp, 'kelly.js')), str(Path(tmp, 'cases.json'))],
            capture_output=True, text=True, timeout=180,
        )

    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    # Which runners get backed must match exactly — that split is the Back /
    # No Bet the column renders.
    assert result['disagreements'] == 0
    # And the sizes must agree to floating-point noise.
    assert result['worst'] < 1e-12, result


# ── 2. Kelly-staked cohort tracking in ML Data ──────────────────────────────

def _row(edge, kelly, finish, sp):
    return (
        SimpleNamespace(value_edge_pct=edge, kelly_stake_pct=kelly),
        SimpleNamespace(finish_position=finish, sp=sp),
    )


def _performance(rows, monkeypatch):
    """Run calculate_value_edge_performance over a fixed row set.

    The function's own query is replaced rather than mocked at the DB layer, so
    the summarising — the part under test — runs exactly as it does in the app.
    """
    class _Query:
        def join(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def order_by(self, *a, **k): return self
        def all(self): return rows

    monkeypatch.setattr(appmod.db.session, 'query', lambda *a, **k: _Query())
    return appmod.calculate_value_edge_performance()


def test_kelly_cohort_sits_beside_the_flat_cohort_over_identical_rows(monkeypatch):
    rows = [
        _row(25.0, 0.04, 1, 6.0),   # backed, won
        _row(22.0, 0.02, 4, 3.0),   # backed, lost
        _row(21.0, 0.0, 1, 2.0),    # positive edge, solver declined — winner Kelly skipped
        _row(7.0, 0.01, 5, 9.0),    # backed, lost
        _row(-3.0, 0.0, 2, 12.0),   # negative edge, no stake
    ]
    perf = _performance(rows, monkeypatch)

    flat = perf['overall']
    kelly = perf['overall']['kelly']
    bankroll = perf['kelly_bankroll']

    # The flat cohort is unchanged: every measured runner is a $10 bet.
    assert flat['bets'] == 5
    assert flat['total_staked'] == 50.0

    # Kelly bets only what the solver staked, and says how many it declined.
    assert kelly['bets'] == 3
    assert kelly['no_bet'] == 2
    assert kelly['wins'] == 1
    assert round(kelly['total_staked'], 6) == round((0.04 + 0.02 + 0.01) * bankroll, 6)
    assert round(kelly['total_return'], 6) == round(0.04 * bankroll * 6.0, 6)
    assert round(kelly['profit'], 6) == round(kelly['total_return'] - kelly['total_staked'], 6)
    assert round(kelly['roi'], 6) == round(kelly['profit'] / kelly['total_staked'] * 100, 6)
    assert round(kelly['avg_stake'], 6) == round(kelly['total_staked'] / 3, 6)


def test_a_declined_runner_is_a_no_bet_not_a_losing_bet(monkeypatch):
    """A zero stake must not be settled as a bet that lost — that would make
    Kelly look worse than it is, and the comparison worthless."""
    rows = [_row(30.0, 0.0, 5, 8.0), _row(30.0, 0.05, 1, 4.0)]
    kelly = _performance(rows, monkeypatch)['overall']['kelly']
    assert kelly['bets'] == 1
    assert kelly['wins'] == 1
    assert kelly['strike_rate'] == 100.0


def test_every_bucket_carries_its_own_kelly_numbers(monkeypatch):
    rows = [_row(25.0, 0.04, 1, 6.0), _row(7.0, 0.01, 5, 9.0), _row(-3.0, 0.0, 2, 12.0)]
    perf = _performance(rows, monkeypatch)

    by_key = {bucket['key']: bucket for bucket in perf['buckets']}
    assert set(by_key) == {'below_0', '0_5', '5_10', '10_15', '15_20', '20_plus'}
    for bucket in perf['buckets']:
        assert 'kelly' in bucket, bucket['key']

    assert by_key['20_plus']['kelly']['bets'] == 1
    assert by_key['5_10']['kelly']['bets'] == 1
    # The control group staked nothing, so it has no Kelly bets to compare.
    assert by_key['below_0']['kelly']['bets'] == 0
    assert by_key['below_0']['kelly']['roi'] == 0.0

    assert perf['at_best_bets_threshold']['kelly']['bets'] == 1
    assert perf['kelly_rows_staked'] == 2


def test_empty_kelly_cohort_is_reported_as_staking_not_as_a_loss(monkeypatch):
    """Nothing staked must not read as a 0% ROI result — it means the solver
    declined everything, which says nothing about how those runners ran."""
    rows = [_row(25.0, 0.0, 1, 6.0), _row(7.0, None, 5, 9.0)]
    perf = _performance(rows, monkeypatch)
    assert perf['kelly_rows_staked'] == 0
    assert perf['overall']['kelly']['bets'] == 0
    assert perf['overall']['kelly']['total_staked'] == 0.0
    assert perf['overall']['kelly']['roi'] == 0.0
    assert perf['overall']['bets'] == 2  # the flat cohort still tracks them


def test_sizing_gain_holds_selection_constant(monkeypatch):
    """Kelly's ROI must be compared against the SAME bets flat-staked, not
    against the flat cohort — that cohort also carries every runner Kelly
    declined, so the difference would mix selection in with sizing and answer a
    question nobody asked."""
    rows = [
        _row(30.0, 0.05, 1, 4.0),    # Kelly backed it, it won
        _row(6.0, 0.01, 5, 9.0),     # Kelly backed it, it lost
        _row(-20.0, 0.0, 1, 1.5),    # Kelly declined a winner — flat cohort only
    ]
    kelly = _performance(rows, monkeypatch)['overall']['kelly']

    # The baseline covers Kelly's two bets, not the cohort's three.
    assert kelly['bets'] == 2
    assert kelly['flat_staked_same_bets'] == 20.0
    # $10 x 2 staked, one winner at $4.00 -> $40 back -> +100% flat.
    assert round(kelly['flat_roi_same_bets'], 6) == 100.0

    # Kelly put 5% on the winner and 1% on the loser, so it beat flat here.
    assert kelly['roi'] > kelly['flat_roi_same_bets']


def test_ml_data_shows_both_tables_and_the_sizing_comparison():
    assert 'Flat $10 vs Kelly Staking' in ML_DATA_TEMPLATE
    assert 'Same bets, sized by Kelly' in ML_DATA_TEMPLATE
    assert 'value_edge_performance.kelly_bankroll' in ML_DATA_TEMPLATE
    assert 'kelly_rows_staked' in ML_DATA_TEMPLATE
    # The comparison itself, not two tables the reader has to diff by eye — and
    # against the same-selection baseline, not the flat cohort's own ROI.
    assert 'cohort.kelly.roi - cohort.kelly.flat_roi_same_bets' in ML_DATA_TEMPLATE
    assert 'cohort.kelly.roi - cohort.roi' not in ML_DATA_TEMPLATE
    assert 'Sizing Gain' in ML_DATA_TEMPLATE
    # The flat table is still there, unreplaced.
    assert 'All Value Edge Bets' in ML_DATA_TEMPLATE
    assert 'value_edge_performance.overall.total_staked' in ML_DATA_TEMPLATE
    # And it does not repeat the reason for No Bet that cannot actually happen.
    assert 'a rival in the same race outranks' not in ML_DATA_TEMPLATE
