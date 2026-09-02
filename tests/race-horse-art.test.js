/*
 * Guards on the runner artwork's pure parts.
 *
 * Almost all of race-horse-art.js is DOM construction and cannot be reached
 * without a browser, but the two things that decide what a FIELD looks like —
 * which coat a horse gets and whether it has a blaze — are plain arithmetic on
 * the horse's name, and both have exactly one property that matters: the same
 * horse must look the same every time, and a field must not come out uniform.
 *
 * Run with `node --test tests/` or through tests/test_race_animation_engine.py,
 * which shells out to this file so it travels with the Python suite.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

/* Same trick as the engine suite: the file hangs itself off `window` and only
 * reaches for `document` inside its builders, so a sandbox with a global is
 * enough to get at the exported functions. */
function loadArt() {
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'static', 'js', 'race-horse-art.js'), 'utf8');
    const sandbox = { window: undefined, document: undefined, Math: Math, console: console };
    sandbox.self = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    return sandbox.RaceHorseArt;
}

const ART = loadArt();

const FIELD = [
    'Winx', 'Black Caviar', 'Makybe Diva', 'Sunline', 'Might And Power',
    'Northerly', 'Lonhro', 'So You Think', 'Chautauqua', 'Nature Strip',
    'Anamoe', 'Verry Elleegant', 'Gytrash', 'Zaaki', 'Incentivise',
    'Think It Over', 'Mr Brightside', 'Alligator Blood', 'Pride Of Jenni', 'Via Sistina'
];

test('every coat is a complete set of colours', () => {
    assert.ok(ART.COATS.length >= 8, 'a big field needs more than a handful of coats');
    for (const coat of ART.COATS) {
        for (const key of ['body', 'shade', 'mane', 'sock']) {
            assert.match(String(coat[key]), /^#[0-9a-f]{6,8}$/i,
                `coat.${key} should be a hex colour, got ${coat[key]}`);
        }
    }
});

test('a horse looks the same in every replay of the same race', () => {
    for (const name of FIELD) {
        assert.strictEqual(ART.hashString(name), ART.hashString(name));
        assert.strictEqual(ART.blazeFor(name), ART.blazeFor(name));
    }
});

test('a field is neither all blazed nor all plain', () => {
    const widths = FIELD.map(ART.blazeFor);
    const plain = widths.filter((w) => w === 0).length;
    assert.ok(plain > 0, 'every runner in a twenty-horse field had a blaze');
    assert.ok(plain < widths.length, 'no runner in a twenty-horse field had a blaze');
});

test('a blaze is never wider than the one the horse used to have', () => {
    // The old artwork drew one fixed stripe. That is now the widest a blaze
    // gets, so nothing can end up with a white face where it used to have a
    // marking down the middle of it.
    for (const name of FIELD) {
        const width = ART.blazeFor(name);
        assert.ok(width === 0 || (width >= 0.4 && width <= 1),
            `${name} got a blaze of ${width}`);
    }
});

test('the blaze path stays a closed stripe at any width', () => {
    for (const width of [0.45, 0.7, 1]) {
        const d = ART.blazePath(width);
        assert.match(d, /^M /, 'should start with a move');
        assert.match(d, /Z$/, 'should close, or it fills as an open shape');
        assert.ok(!/NaN|undefined/.test(d), `bad path data: ${d}`);
    }
    // Narrower really is narrower: the near edge sits closer to the far one.
    const nearEdgeAt = (w) => Number(ART.blazePath(w).match(/L ([\d.]+),/)[1]);
    assert.ok(nearEdgeAt(0.45) > nearEdgeAt(1),
        'a narrow blaze should leave its near edge closer to the far edge');
});

test('the silhouette the engine trails is real path data', () => {
    // The engine draws sprint trails as flat copies of these, so an empty or
    // malformed export would show up as invisible trails rather than an error.
    for (const d of [ART.BODY_PATH, ART.NECK_PATH]) {
        assert.match(String(d), /^M [\d.]+,[\d.]+/);
        assert.match(String(d), /Z$/);
    }
});
