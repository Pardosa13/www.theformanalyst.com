/*
 * Guards on the race engine's movement maths.
 *
 * The engine had no tests at all: the scoring was well covered and the 900-odd
 * lines that turn a ranking into a race were not. These cover the properties
 * that make the animation trustworthy rather than merely pretty —
 *
 *   a runner never goes backwards,
 *   the field leaves the gate together and the winner reaches the post,
 *   the pace affects HOW a race is run and never WHO wins it,
 *   a big field still fits on the screen.
 *
 * Run with `node --test tests/` or through tests/test_race_animation_engine.py,
 * which shells out to exactly this file so it travels with the Python suite.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

/* The engine is a browser file: it hangs itself off `window` and reaches for
 * `document` inside its DOM functions. Loading it in a sandbox with a global
 * gives us the exported pure functions without needing a DOM at all — none of
 * the movement maths touches one. */
function loadEngine() {
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'static', 'js', 'race-animation.js'), 'utf8');
    const sandbox = { window: undefined, document: undefined, Math: Math, console: console };
    sandbox.self = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    return sandbox.RaceAnimation;
}

const RA = loadEngine();

/* A runner the way create() builds one, without the DOM half. */
function entry(overrides) {
    return Object.assign({
        gateDelay: 0,
        settleShape: 1,
        settleOffset: 0,
        finishOffset: 0,
        sprintExponent: 1.8,
        fade: 0,
        bobAmplitude: 0,
        jostleSeed: 1,
        barrierOffset: 0,
        settleLaneOffset: 0
    }, overrides || {});
}

function sample(fn, steps) {
    const out = [];
    for (let i = 0; i <= (steps || 400); i++) out.push(fn(i / (steps || 400)));
    return out;
}

// ── The one rule the whole thing rests on ────────────────────────────────
test('no runner ever goes backwards', () => {
    // A deliberately awkward set: a big settled deficit, a late sprint, a fade
    // riding on top, and a gate delay — every mechanism that pushes a runner
    // back, all at once.
    const runners = [
        entry({ settleOffset: -0.09, finishOffset: -0.0, sprintExponent: 2.5, fade: -0.01,
                gateDelay: 0.012, bobAmplitude: 0.0008 }),
        entry({ settleOffset: 0, finishOffset: -0.14, sprintExponent: 1.0, fade: 0.012,
                bobAmplitude: 0.0008 }),
        entry({ settleOffset: -0.04, finishOffset: -0.07, sprintExponent: 1.35, fade: 0.006 })
    ];

    for (const runner of runners) {
        const track = sample((t) => RA.progressAt(runner, t), 2000);
        for (let i = 1; i < track.length; i++) {
            assert.ok(track[i] >= track[i - 1] - 1e-9,
                `progress fell from ${track[i - 1]} to ${track[i]} at step ${i}`);
        }
    }
});

test('the field starts at the barrier and the winner reaches the post', () => {
    const winner = entry({ finishOffset: 0 });
    const tailender = entry({ settleOffset: -0.08, finishOffset: -0.15 });

    assert.strictEqual(RA.progressAt(winner, 0), 0);
    assert.strictEqual(RA.progressAt(tailender, 0), 0);
    assert.ok(Math.abs(RA.progressAt(winner, 1) - 1) < 1e-9,
        'the winner has to be exactly on the post at the end of the race');
    assert.ok(RA.progressAt(tailender, 1) < RA.progressAt(winner, 1),
        'a beaten runner has to still be behind at the post');
});

test('the gate delay has washed out by the time the field settles', () => {
    // A delay is a slow beginning, not a permanent handicap: two runners with
    // the same settled offset must be level again once they are settled.
    const quick = entry({ gateDelay: 0 });
    const slow = entry({ gateDelay: RA.GATE_STAGGER });

    assert.ok(RA.progressAt(quick, 0.02) > RA.progressAt(slow, 0.02),
        'the delay has to actually show at the jump');
    assert.ok(Math.abs(RA.progressAt(quick, RA.T_GATE) - RA.progressAt(slow, RA.T_GATE)) < 1e-6,
        'the delay must not survive into the settled field');
});

// ── Pace: the manner of a race, never its result ─────────────────────────
test('the fade is worth nothing at the post', () => {
    // This is what lets the animation show a leader being run down without the
    // finishing order stopping matching the composite.
    const leader = entry({ fade: 0.02 });
    assert.strictEqual(RA.fadeAt(leader, 0), 0);
    assert.ok(Math.abs(RA.fadeAt(leader, 1)) < 1e-12);
    // ...and it genuinely does something in between.
    assert.ok(RA.fadeAt(leader, 0.3) > 0.01);
});

test('a fading leader still finishes exactly where the score put it', () => {
    const withFade = entry({ settleOffset: 0, finishOffset: -0.05, fade: 0.02 });
    const withoutFade = entry({ settleOffset: 0, finishOffset: -0.05, fade: 0 });

    assert.ok(Math.abs(RA.progressAt(withFade, 1) - RA.progressAt(withoutFade, 1)) < 1e-9,
        'the fade changed where a runner finished, which it must never do');
    // But it does change the picture through the straight.
    const middle = 0.65 + (1 - 0.65) * 0.3;
    assert.ok(RA.progressAt(withFade, middle) > RA.progressAt(withoutFade, middle));
});

test('each pace role runs its own home straight', () => {
    // A backmarker builds late; a leader is already rolling and simply holds.
    // Same start, same finish — different shape in between.
    const leader = entry({ settleOffset: 0, finishOffset: -0.06,
                           sprintExponent: RA.SPRINT_EXPONENT.leader });
    const backmarker = entry({ settleOffset: 0, finishOffset: -0.06,
                               sprintExponent: RA.SPRINT_EXPONENT.back });

    assert.ok(RA.SPRINT_EXPONENT.back > RA.SPRINT_EXPONENT.leader);
    const earlyStraight = RA.T_SPRINT + (1 - RA.T_SPRINT) * 0.25;
    // The backmarker has given away less of its deficit early on, because its
    // run comes later.
    assert.ok(RA.progressAt(backmarker, earlyStraight) > RA.progressAt(leader, earlyStraight));
    assert.ok(Math.abs(RA.progressAt(backmarker, 1) - RA.progressAt(leader, 1)) < 1e-9);
});

test('nobody moves in the settle phase', () => {
    // The middle of the race is meant to be locked: the speed map holds until
    // the corner. With the bob switched off, the gaps must not move at all.
    const runner = entry({ settleOffset: -0.05 });
    const settled = RA.progressAt(runner, RA.T_GATE) - RA.packCurve(RA.T_GATE);
    const later = RA.progressAt(runner, RA.T_SPRINT) - RA.packCurve(RA.T_SPRINT);
    assert.ok(Math.abs(settled - later) < 1e-9,
        'the gap back to the pack changed during the settle');
});

// ── Field shape ──────────────────────────────────────────────────────────
test('a big field still fits inside the settle cap', () => {
    for (const size of [4, 8, 16, 24]) {
        const order = [];
        for (let i = 0; i < size; i++) {
            order.push({ pace_category: i < size / 4 ? 'leader'
                : i < size / 2 ? 'onpace' : i < size * 0.75 ? 'midfield' : 'back' });
        }
        const gaps = RA.settleOffsets(order, RA.BODY_OF_ICON * RA.horseSizeForField(size) / 1800);
        assert.strictEqual(gaps[0], 0, 'the leader is the reference and sits at zero');
        // Strictly increasing deficits, and the tail is inside the cap.
        for (let i = 1; i < gaps.length; i++) {
            assert.ok(gaps[i] < gaps[i - 1], `runner ${i} was not behind runner ${i - 1}`);
        }
        assert.ok(Math.abs(gaps[gaps.length - 1]) <= 0.1 + 1e-9,
            `a field of ${size} spread ${Math.abs(gaps[gaps.length - 1])} of the race`);
    }
});

test('finishing gaps grow down the field and stay on the screen', () => {
    const ranked = [];
    for (let i = 0; i < 24; i++) ranked.push({ beaten_margin: i * 1.4 });
    const gaps = RA.finishGapsPx(ranked, 40, 2000);

    assert.strictEqual(gaps[0], 0);
    for (let i = 1; i < gaps.length; i++) {
        assert.ok(gaps[i] > gaps[i - 1], 'finishing gaps must be cumulative');
    }
    // A 24-runner field cannot push its tail back around the turn.
    assert.ok(gaps[gaps.length - 1] <= 2000 * 0.16 + 1e-6);
});

test('a dead-heat-tight field is still separated on screen', () => {
    // Every runner beaten the same distance: honest, and unreadable. The
    // minimum gap is what makes first, second and third tellable apart.
    const ranked = [{ beaten_margin: 0 }, { beaten_margin: 0 }, { beaten_margin: 0 }];
    const gaps = RA.finishGapsPx(ranked, 40, 2000);
    assert.ok(gaps[1] > 0);
    assert.ok(gaps[2] > gaps[1]);
});

test('horses shrink as the field grows, and never past the floor', () => {
    const sizes = [2, 6, 8, 12, 16, 20, 24, 30].map(RA.horseSizeForField);
    for (let i = 1; i < sizes.length; i++) {
        assert.ok(sizes[i] <= sizes[i - 1], 'a bigger field cannot get bigger horses');
    }
    assert.ok(sizes[sizes.length - 1] >= 30, 'horses must stay big enough to see');
});

// ── Lanes and the track ──────────────────────────────────────────────────
test('the field crosses from its barrier to its mapped lane, then holds', () => {
    const runner = entry({ barrierOffset: 40, settleLaneOffset: -20 });

    assert.strictEqual(RA.laneOffsetAt(runner, 0), 40, 'it jumps from its barrier');
    assert.strictEqual(RA.laneOffsetAt(runner, RA.T_LANE_END), -20, 'and reaches its lane');
    assert.strictEqual(RA.laneOffsetAt(runner, 0.9), -20, 'and stays there');
    // The crossing is gradual, not a jump.
    const halfway = RA.laneOffsetAt(runner, (RA.T_LANE_START + RA.T_LANE_END) / 2);
    assert.ok(halfway < 40 && halfway > -20);
});

test('the two directions of travel are genuinely different tracks', () => {
    const anticlockwise = RA.lanePathData(200, false);
    const clockwise = RA.lanePathData(200, true);

    assert.notStrictEqual(anticlockwise, clockwise);
    // Anti-clockwise starts at the right-hand end of the home straight, and
    // clockwise at the left — that is where each one's winning post is.
    assert.ok(anticlockwise.startsWith('M 900,'), anticlockwise.slice(0, 24));
    assert.ok(clockwise.startsWith('M 300,'), clockwise.slice(0, 24));
    // And the arcs are swept the other way round.
    assert.ok(anticlockwise.includes('0 0 0'));
    assert.ok(clockwise.includes('0 0 1'));
});

// ── The interpolator underneath it all ───────────────────────────────────
test('the pack curve is monotone and covers the whole trip', () => {
    const curve = sample(RA.packCurve, 1000);
    assert.ok(Math.abs(curve[0]) < 1e-12);
    assert.ok(Math.abs(curve[curve.length - 1] - 1) < 1e-12);
    for (let i = 1; i < curve.length; i++) {
        assert.ok(curve[i] >= curve[i - 1] - 1e-12, 'the pack curve went backwards');
    }
    // The gate burst: more ground covered in the first 6% than an even gallop.
    assert.ok(RA.packCurve(0.06) > 0.06);
});

test('pchip never overshoots the points it was given', () => {
    // The property the whole no-going-backwards guarantee depends on.
    const curve = RA.pchip([0, 0.3, 0.5, 1], [0, 0.8, 0.82, 1]);
    for (let i = 0; i <= 1000; i++) {
        const value = curve(i / 1000);
        assert.ok(value >= -1e-9 && value <= 1 + 1e-9, `pchip left its range at ${value}`);
    }
    for (let i = 1; i <= 1000; i++) {
        assert.ok(curve(i / 1000) >= curve((i - 1) / 1000) - 1e-9);
    }
});

// ── Reduced motion ───────────────────────────────────────────────────────
test('reduced motion is detected without a matchMedia to ask', () => {
    // The sandbox has no matchMedia at all. It must answer false rather than
    // throwing, or the page cannot be built in any environment lacking one.
    assert.strictEqual(RA.prefersReducedMotion(), false);
});
