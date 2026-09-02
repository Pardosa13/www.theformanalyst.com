/*
 * race-animation.js — oval track + three-phase race engine.
 *
 * Loaded only by templates/race-animations-predictions.html. Depends on
 * race-horse-art.js for the runners themselves.
 *
 * TRACK
 * A stadium oval: two straights joined by two turns, run anti-clockwise, with
 * the start/finish post at the end of the home (bottom) straight. Positions
 * come from sampling the real SVG path with getPointAtLength() rather than from
 * hand-rolled curve maths — the path is sampled once into a lookup table at
 * build time, so a 24-runner field costs a couple of array reads per horse per
 * frame instead of dozens of geometry calls.
 *
 * LANES
 * Every runner shares ONE reference path — the middle lane — and is pushed off
 * it perpendicularly by its barrier, barrier 1 nearest the rail. That offset is
 * geometrically the concentric lane (a constant normal offset from a straight
 * is a parallel straight, and from a circular arc is a concentric arc), so the
 * picture is identical to giving each runner its own path. What it fixes is the
 * parameterisation: with a path each, the same progress value put an outside
 * runner further round the turn than an inside one, because their paths are
 * different lengths and the straights are a different share of each. Sharing
 * one arc-length parameter means runners on the same progress are genuinely
 * abreast, on the same radial line, all the way round.
 *
 * The lane is not fixed. It starts as the barrier draw — lane 0 is barrier 1,
 * on the rail — and over the first tenth of the race the whole field slides
 * across into speed-map lanes instead: leaders innermost, then onpace, midfield
 * and backmarkers furthest out, each group ordered inside itself by its speed
 * rating. That is the crossing a real field makes in the first furlong, and it
 * leaves the runners in tidy grouped rows for the middle of the race.
 *
 * MOVEMENT
 * Every runner shares one pack curve pack(t) — 0 at the gates, 1 at the post,
 * with a burst of extra speed over the first 6% of the race so the jump reads
 * as an acceleration and not a rolling start. Each runner is then displaced
 * along the track from that pack by an offset, and it is the offset, never the
 * pack, that carries the story of the race:
 *
 *   GATE    t 0    -> 0.26   offsets ramp up from zero, so the field leaves the
 *                            barrier line together and strings out into its
 *                            speed-map order. A per-runner delay of a few
 *                            frames stops the launch looking synchronised.
 *   SETTLE  t 0.26 -> 0.65   offsets hold constant. Nobody passes anybody, and
 *                            nobody changes lanes. The only movement is a small
 *                            bob and a little lateral drift, so the field looks
 *                            alive but locked in its mapped positions.
 *   SPRINT  t 0.65 -> 1      offsets interpolate from the settled position to
 *                            the finishing position on a t^1.6 curve, so the
 *                            moves build through the straight. This is where a
 *                            backmarker with a big composite comes over the top
 *                            and a weak leader drops out of it.
 *
 * The pack rises far faster than any offset can fall, so no runner ever goes
 * backwards, and the sprint target is the composite finish order, so the result
 * always matches the ranking the API sent.
 *
 * FINISH SPACING
 * Real beaten margins are honest and unreadable: a two-length win is a handful
 * of pixels. Finishing offsets are therefore laid out with a MINIMUM gap between
 * adjacent placings, measured in horse body lengths and widest at the head of
 * the field, so first, second and third separate at a glance. The order is never
 * touched — only the spacing.
 *
 * ORIENTATION
 * Horses stay upright rather than rotating with the path, because a runner
 * rotated to follow the back straight would be drawn upside down. Instead the
 * art is scaled horizontally by cos(tangent): full width on the straights,
 * squashing towards the turn apex and coming back out mirrored, which reads as
 * a horse turning away from and back towards the camera — which is what
 * actually happens in race vision at the top of the bend.
 */
(function (global) {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';

    // ── Phase boundaries, in normalised race time ──
    var T_GATE = 0.26;         // barriers -> settled into speed-map order
    var T_LANE_START = 0.05;   // the cross into speed-map lanes starts here...
    var T_LANE_END = 0.13;     // ...and finishes here: the first tenth of the race
    var T_SPRINT = 0.65;       // the settle phase ends and the run home begins
    var GATE_STAGGER = 0.012;  // biggest per-runner delay out of the gate

    // Lane order across the track once the field has settled, rail outwards.
    var PACE_LANE_ORDER = { leader: 0, onpace: 1, midfield: 2, back: 3 };

    // The settled field is strung out by walking the speed-map order and giving
    // each runner a gap back on the one in front, in HORSE BODY LENGTHS. Body
    // lengths rather than a share of the lap because the horses shrink as the
    // field grows, and the gaps have to shrink with them or a big field runs off
    // the back of the screen. The extra step at a group boundary is what makes
    // the leaders, the onpacers and the backmarkers read as separate bunches.
    var SETTLE_STEP_LENGTHS = 0.38;        // runner to runner inside a group
    var SETTLE_GROUP_STEP_LENGTHS = 0.90;  // extra, crossing into the next group
    var SETTLE_JITTER_LENGTHS = 0.22;      // deterministic per-runner untidiness

    // Hard cap on leader-to-tail at the settle, as a share of the race. This is
    // not a taste setting: the field has to reach its settled shape inside the
    // gate phase, so the rate the gaps open at is spread / gate length, and once
    // that passes the pack's own speed the tail runner would be going backwards
    // across the ground. Capping the spread keeps every runner moving forward
    // whatever the field size, and the same cap keeps a big field on screen.
    var SETTLE_MAX_SPREAD = 0.10;
    // Finish spacing. Display only: it never reorders anybody.
    var FINISH_MIN_GAP_HEAD = 0.80;   // body lengths between 1st and 2nd
    var FINISH_MIN_GAP_FLOOR = 0.35;  // ...and the floor further down the field
    var FINISH_GAP_DECAY = 0.90;      // per placing
    var TRUE_MARGIN_SCALE = 0.35;     // API lengths -> body lengths on screen
    var FINISH_MAX_SPREAD = 0.16;     // cap on 1st-to-last, as a share of the race

    // A body length as a share of the drawn icon: the art is drawn on a 100-unit
    // box with air around the horse, so the animal itself is ~70 of it.
    var BODY_OF_ICON = 0.70;

    // Icon size against field size. A match race gets big horses that can
    // actually be read; a 24-runner Cup gets small ones so the field still fits
    // one screen. Anything in between interpolates along the steps.
    var SIZE_STEPS = [[6, 80], [8, 72], [12, 58], [16, 46], [20, 37], [24, 32]];

    function clamp(value, low, high) { return value < low ? low : (value > high ? high : value); }

    function smoothstep(edge0, edge1, x) {
        if (edge1 - edge0 < 1e-9) return x < edge0 ? 0 : 1;
        var t = clamp((x - edge0) / (edge1 - edge0), 0, 1);
        return t * t * (3 - 2 * t);
    }

    /* Deterministic 0..1 out of a seed. The same horse jitters the same way
     * every time, so a replay is a replay of the same race and not a new one. */
    function hash01(seed) {
        var value = Math.sin(seed * 12.9898 + 78.233) * 43758.5453;
        return value - Math.floor(value);
    }

    function horseSizeForField(fieldSize) {
        if (fieldSize <= SIZE_STEPS[0][0]) return SIZE_STEPS[0][1];
        for (var i = 1; i < SIZE_STEPS.length; i++) {
            if (fieldSize <= SIZE_STEPS[i][0]) {
                var low = SIZE_STEPS[i - 1], high = SIZE_STEPS[i];
                var f = (fieldSize - low[0]) / (high[0] - low[0]);
                return low[1] + (high[1] - low[1]) * f;
            }
        }
        return SIZE_STEPS[SIZE_STEPS.length - 1][1];
    }

    // ── Monotone cubic interpolation (PCHIP) ──────────────────────────────
    /* Fritsch-Carlson slope limiting: where the data changes direction, or
     * where a plain cubic would overshoot, the slope is pulled back so the
     * curve can never rise above the next point and dip back. That is what
     * guarantees a runner's progress only ever increases. */
    function pchip(xs, ys) {
        var n = xs.length;
        var h = [], delta = [], m = [], i;
        for (i = 0; i < n - 1; i++) {
            h.push(xs[i + 1] - xs[i]);
            delta.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
        }
        m.push(delta[0]);
        for (i = 1; i < n - 1; i++) {
            if (delta[i - 1] * delta[i] <= 0) {
                m.push(0);
            } else {
                var w1 = 2 * h[i] + h[i - 1];
                var w2 = h[i] + 2 * h[i - 1];
                m.push((w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]));
            }
        }
        m.push(delta[n - 2]);

        return function (x) {
            if (x <= xs[0]) return ys[0];
            if (x >= xs[n - 1]) return ys[n - 1];
            var k = 0;
            while (k < n - 2 && x > xs[k + 1]) k++;
            var step = xs[k + 1] - xs[k];
            var t = (x - xs[k]) / step;
            var t2 = t * t, t3 = t2 * t;
            // Cubic Hermite basis.
            return (2 * t3 - 3 * t2 + 1) * ys[k]
                 + (t3 - 2 * t2 + t) * step * m[k]
                 + (-2 * t3 + 3 * t2) * ys[k + 1]
                 + (t3 - t2) * step * m[k + 1];
        };
    }

    function el(name, attrs) {
        var node = document.createElementNS(SVG_NS, name);
        if (attrs) {
            for (var key in attrs) {
                if (Object.prototype.hasOwnProperty.call(attrs, key) && attrs[key] != null) {
                    node.setAttribute(key, attrs[key]);
                }
            }
        }
        return node;
    }
    function append(parent, child) { parent.appendChild(child); return child; }

    // ── Track geometry ────────────────────────────────────────────────────
    var VIEW = { width: 1200, height: 700, cx: 600, cy: 350, straight: 300, outerRy: 272 };

    // The running surface is a fixed width whatever the field size — the lanes
    // pack tighter as runners are added rather than the track fattening up.
    // Letting the band grow with the field turned the infield into a sliver and
    // the whole oval into a green blob. It is as wide as it can be without
    // doing that, because every pixel of it is lane spacing, and lane spacing is
    // what stops a field standing in the barriers looking like one animal.
    var TRACK_BAND = 186;

    /* One lane, as a stadium path traced anti-clockwise from the winning post.
     *
     * The path deliberately STARTS at the post and ends back there, so a full
     * lap is exactly progress 0 -> 1 and the barriers sit on the winning line.
     * Order of travel: right-hand turn (up), back straight (right to left),
     * left-hand turn (down), home straight (left to right) into the post.
     */
    function lanePathData(radius) {
        var cx = VIEW.cx, cy = VIEW.cy, a = VIEW.straight;
        var r = radius;
        return 'M ' + (cx + a) + ',' + (cy + r) +
               ' A ' + r + ',' + r + ' 0 0 0 ' + (cx + a) + ',' + (cy - r) +
               ' L ' + (cx - a) + ',' + (cy - r) +
               ' A ' + r + ',' + r + ' 0 0 0 ' + (cx - a) + ',' + (cy + r) +
               ' Z';
    }

    /* Sample a lane path into a lookup table of {x, y, angle}.
     *
     * getPointAtLength is exact but not cheap, and a 24-runner field would call
     * it ~2,900 times a second. Sampling once at build time and interpolating
     * between samples gives the same picture for a fraction of the cost, and
     * the sample count scales with the path so the spacing stays under ~4px.
     */
    function sampleLane(path, radius) {
        var total = path.getTotalLength();
        // The lap is traced from the winning post, so the first segment is the
        // right-hand turn. Skipping it puts the barriers at the top of the back
        // straight and makes the race three-quarters of a lap, finishing at the
        // post. Both turns are circular (rx = ry), so the arc is exactly pi*r.
        var startAt = Math.PI * radius;
        var count = Math.max(360, Math.min(1400, Math.round(total / 3.5)));
        var xs = new Float32Array(count + 1);
        var ys = new Float32Array(count + 1);
        var angles = new Float32Array(count + 1);
        for (var i = 0; i <= count; i++) {
            var distance = (i / count) * total;
            var here = path.getPointAtLength(distance);
            var ahead = path.getPointAtLength(Math.min(total, distance + 2));
            var behind = path.getPointAtLength(Math.max(0, distance - 2));
            xs[i] = here.x;
            ys[i] = here.y;
            angles[i] = Math.atan2(ahead.y - behind.y, ahead.x - behind.x);
        }
        return {
            xs: xs, ys: ys, angles: angles, count: count, length: total,
            startFraction: startAt / total,
            raceLength: total - startAt
        };
    }

    /* Position at progress p (0..1) plus a perpendicular offset in px. */
    function samplePoint(table, progress, sideways) {
        var along = table.startFraction + clamp(progress, 0, 1) * (1 - table.startFraction);
        var scaled = along * table.count;
        var i = Math.min(table.count - 1, Math.floor(scaled));
        var frac = scaled - i;
        var x = table.xs[i] + (table.xs[i + 1] - table.xs[i]) * frac;
        var y = table.ys[i] + (table.ys[i + 1] - table.ys[i]) * frac;
        // Angles are interpolated through the shorter arc so the wrap from +pi
        // to -pi at the top of the turn does not spin a horse around.
        var a0 = table.angles[i], a1 = table.angles[i + 1];
        var delta = a1 - a0;
        while (delta > Math.PI) delta -= Math.PI * 2;
        while (delta < -Math.PI) delta += Math.PI * 2;
        var angle = a0 + delta * frac;
        if (sideways) {
            x += Math.sin(angle) * sideways;
            y += -Math.cos(angle) * sideways;
        }
        return { x: x, y: y, angle: angle };
    }

    // ── Movement ──────────────────────────────────────────────────────────
    /* The pack curve. Everybody runs on this; the offsets do the racing.
     *
     * PCHIP through (0,0), (0.06, 0.085), (1,1): monotone by construction, and
     * that middle point is the gate burst — 8.5% of the trip covered in the
     * first 6% of the clock, so the field visibly accelerates off the barriers
     * and then settles into an even gallop. */
    var packCurve = pchip([0, 0.06, 1], [0, 0.085, 1]);

    /* Settled gaps back from the leader, in progress units, for a field already
     * sorted into speed-map order (leaders first). Each runner is given a gap on
     * the one in front, with an extra step whenever the pace group changes, so
     * the bunches read as bunches. Capped by SETTLE_MAX_SPREAD. */
    function settleOffsets(order, bodyProgress) {
        var out = [0];
        var back = 0;
        for (var i = 1; i < order.length; i++) {
            var step = SETTLE_STEP_LENGTHS;
            if (order[i - 1].pace_category !== order[i].pace_category) step += SETTLE_GROUP_STEP_LENGTHS;
            step += hash01(i * 3.7 + 1) * SETTLE_JITTER_LENGTHS;
            back += step;
            out.push(-back * bodyProgress);
        }
        var spread = back * bodyProgress;
        if (spread > SETTLE_MAX_SPREAD) {
            var scale = SETTLE_MAX_SPREAD / spread;
            for (var k = 0; k < out.length; k++) out[k] *= scale;
        }
        return out;
    }

    /* Beaten margins -> on-screen finishing gaps, in pixels, cumulative from
     * the winner. `ranked` is the field in composite-rank order.
     *
     * Each gap is the true score-derived margin OR a minimum, whichever is
     * bigger, and the minimum decays down the field so the placings that matter
     * get the most air. The whole thing is then scaled to fit FINISH_MAX_SPREAD
     * so a 24-runner field cannot push its tail back onto the turn. */
    function finishGapsPx(ranked, bodyLengthPx, raceLengthPx) {
        var gaps = [0];
        var previous = ranked.length ? (ranked[0].beaten_margin || 0) : 0;
        for (var i = 1; i < ranked.length; i++) {
            var margin = ranked[i].beaten_margin || 0;
            var truth = Math.max(0, margin - previous) * TRUE_MARGIN_SCALE * bodyLengthPx;
            var floor = Math.max(FINISH_MIN_GAP_FLOOR,
                                 FINISH_MIN_GAP_HEAD * Math.pow(FINISH_GAP_DECAY, i - 1)) * bodyLengthPx;
            gaps.push(gaps[i - 1] + Math.max(truth, floor));
            previous = margin;
        }
        var total = gaps[gaps.length - 1];
        var cap = raceLengthPx * FINISH_MAX_SPREAD;
        if (total > cap && total > 0) {
            var scale = cap / total;
            for (var k = 0; k < gaps.length; k++) gaps[k] *= scale;
        }
        return gaps;
    }

    /* How far round the track a runner is at race time t (0..1). */
    function progressAt(entry, t) {
        // The gate delay is a genuine slow beginning — a horse held in the stalls
        // for a few frames — that washes out again by the time the field settles,
        // so it ruffles the jump without quietly reordering the settled field.
        var settled = smoothstep(0, T_GATE, t);
        var base = packCurve(t - entry.gateDelay * (1 - settled));

        var offset;
        if (t <= T_SPRINT) {
            // Gate then settle: the mapped gap fades in on a per-runner shape,
            // and every runner is on its mark by T_GATE, after which it holds.
            offset = entry.settleOffset * Math.pow(settled, entry.settleShape);
        } else {
            // Run home: settled position -> finishing position, accelerating.
            var u = (t - T_SPRINT) / (1 - T_SPRINT);
            offset = entry.settleOffset + (entry.finishOffset - entry.settleOffset) * Math.pow(u, 1.6);
        }

        // Cosmetic bob through the settle phase — enough to look alive, far too
        // small to take a place off anybody (the smallest settled gap between
        // two runners is wider than two horses' bobs put together).
        var alive = settled * (1 - smoothstep(T_SPRINT, T_SPRINT + 0.1, t));
        var bob = Math.sin(t * 9.1 + entry.jostleSeed) * entry.bobAmplitude * alive;

        return clamp(base + offset + bob, 0, 1);
    }

    /* Sideways position in px: the barrier lane out of the gates, crossing to
     * the speed-map lane over the first tenth of the race, then held. */
    function laneOffsetAt(entry, t) {
        var mix = smoothstep(T_LANE_START, T_LANE_END, t);
        return entry.barrierOffset + (entry.settleLaneOffset - entry.barrierOffset) * mix;
    }

    // ── Scenery ───────────────────────────────────────────────────────────
    function buildTrack(svg, innerRadius, outerRadius) {
        var defs = append(svg, el('defs'));

        var turf = append(defs, el('linearGradient', { id: 'ra-turf', x1: '0', y1: '0', x2: '0', y2: '1' }));
        append(turf, el('stop', { offset: '0', 'stop-color': '#22432a' }));
        append(turf, el('stop', { offset: '1', 'stop-color': '#2d5734' }));

        var infield = append(defs, el('linearGradient', { id: 'ra-infield', x1: '0', y1: '0', x2: '0', y2: '1' }));
        append(infield, el('stop', { offset: '0', 'stop-color': '#16281b' }));
        append(infield, el('stop', { offset: '1', 'stop-color': '#1b3421' }));

        var scenery = append(svg, el('g', { 'class': 'ra-scenery' }));

        // The running surface: one path holding the outer and inner outlines,
        // filled even-odd so the middle punches out as the infield.
        append(scenery, el('path', {
            d: lanePathData(outerRadius + 18) + ' ' + lanePathData(innerRadius - 18),
            'fill-rule': 'evenodd', fill: 'url(#ra-turf)'
        }));
        append(scenery, el('path', {
            d: lanePathData(innerRadius - 18), fill: 'url(#ra-infield)'
        }));
        append(scenery, el('path', {                        // inside running rail
            d: lanePathData(innerRadius - 18), fill: 'none',
            stroke: '#e8ecf2', 'stroke-width': 2.4, opacity: 0.85
        }));
        append(scenery, el('path', {                        // outside rail
            d: lanePathData(outerRadius + 18), fill: 'none',
            stroke: '#5d6b74', 'stroke-width': 2, opacity: 0.7
        }));

        // Winning post, at the end of the home straight where every lane path
        // both starts and finishes.
        var postX = VIEW.cx + VIEW.straight;
        append(scenery, el('line', {
            x1: postX, y1: VIEW.cy + innerRadius - 18, x2: postX, y2: VIEW.cy + outerRadius + 18,
            stroke: '#ffffff', 'stroke-width': 3, 'stroke-dasharray': '7 5', opacity: 0.9
        }));
        append(scenery, el('rect', {
            x: postX - 3.5, y: VIEW.cy + outerRadius + 10, width: 7, height: 34, rx: 2, fill: '#f4f6fa'
        }));
        var label = append(scenery, el('text', {
            x: postX + 14, y: VIEW.cy + outerRadius + 34,
            'font-size': 15, 'font-weight': '800', fill: '#f4f6fa', opacity: 0.85,
            'font-family': "'DM Mono', ui-monospace, monospace"
        }));
        label.textContent = 'START / FINISH';

    }

    /* The barrier stalls, at the top of the back straight where the race starts.
     *
     * Travel along that straight is right to left, so the gate line is vertical
     * and the stalls sit behind the field, off to its right. One stall per lane,
     * innermost stall = barrier 1, which is exactly where the runners line up.
     */
    function buildGate(parent, laneBase, laneGap, fieldSize, innerRadius, outerRadius) {
        var gateX = VIEW.cx + VIEW.straight;
        var gate = append(parent, el('g', { 'class': 'ra-gate' }));
        var stallDepth = clamp(laneGap * 1.7, 11, 22);

        // The line the field jumps from, right across the running surface.
        append(gate, el('line', {
            x1: gateX, y1: VIEW.cy - (outerRadius + 18), x2: gateX, y2: VIEW.cy - (innerRadius - 18),
            stroke: '#ffffff', 'stroke-width': 2.6, 'stroke-dasharray': '6 4', opacity: 0.8
        }));

        for (var lane = 0; lane < fieldSize; lane++) {
            var y = VIEW.cy - (laneBase + lane * laneGap);
            append(gate, el('rect', {
                x: gateX, y: y - laneGap * 0.46,
                width: stallDepth, height: Math.max(3, laneGap * 0.92),
                rx: 1.4, fill: 'rgba(10,12,18,0.72)',
                stroke: 'rgba(232,236,242,0.5)', 'stroke-width': 0.7
            }));
        }

        var label = append(gate, el('text', {
            x: gateX + stallDepth + 9, y: VIEW.cy - (outerRadius + 12),
            'font-size': 13, 'font-weight': '800', fill: '#f4f6fa', opacity: 0.75,
            'font-family': "'DM Mono', ui-monospace, monospace"
        }));
        label.textContent = 'BARRIERS';
    }

    // ── The controller ────────────────────────────────────────────────────
    /* options:
     *   svg           the <svg> to build into (cleared first)
     *   runners       the API payload's runners, already in composite-rank order
     *   duration      race length in seconds at speed 1 (default 15)
     *   showNames     draw name chips (auto-off for big fields)
     *   onTick        (raceTime01, liveOrder[]) each frame
     *   onFinish      (runners) when the winner hits the post
     *   onSelect      (runner|null) when a horse is tapped
     */
    function create(options) {
        options = options || {};
        var svg = options.svg;
        var runners = (options.runners || []).slice();
        var fieldSize = runners.length;
        if (!svg || !fieldSize) return null;

        while (svg.firstChild) svg.removeChild(svg.firstChild);
        svg.setAttribute('viewBox', '0 0 ' + VIEW.width + ' ' + VIEW.height);

        // Lane spacing falls away as the field grows, which is what keeps a
        // 24-runner race on one screen without scrolling. The icons are sized
        // off the field directly rather than off the lane gap, so a small field
        // gets genuinely big horses instead of merely wider lanes.
        var laneGap = Math.min(30, TRACK_BAND / (fieldSize + 0.6));
        var horseSize = horseSizeForField(fieldSize);
        var outerRadius = VIEW.outerRy;
        var innerRadius = outerRadius - TRACK_BAND;
        // Centre the lanes in the band so a small field runs down the middle of
        // the track instead of hugging the rail with dead turf outside it.
        var laneBase = innerRadius + (TRACK_BAND - (fieldSize - 1) * laneGap) / 2;
        var midLane = (fieldSize - 1) / 2;
        var referenceRadius = laneBase + midLane * laneGap;

        buildTrack(svg, innerRadius, outerRadius);
        buildGate(svg, laneBase, laneGap, fieldSize, innerRadius, outerRadius);

        var laneLayer = append(svg, el('g', { 'class': 'ra-lanes', opacity: 0.12 }));

        // The one path every runner is parameterised against. It is only ever
        // measured, never shown, so it lives outside the visible layers.
        var referencePath = append(laneLayer, el('path', {
            d: lanePathData(referenceRadius), fill: 'none', stroke: 'none'
        }));
        var table = sampleLane(referencePath, referenceRadius);
        referencePath.parentNode.removeChild(referencePath);

        var horseLayer = append(svg, el('g', { 'class': 'ra-horses' }));
        var labelLayer = append(svg, el('g', { 'class': 'ra-labels' }));

        // Runners always have an id from the API; the fallback only exists so a
        // hand-built payload cannot collide every runner onto one map key.
        function idOf(runner) {
            return runner.horse_id != null ? runner.horse_id : ('n' + runners.indexOf(runner));
        }

        var bodyLengthPx = horseSize * BODY_OF_ICON;
        var bodyProgress = bodyLengthPx / table.raceLength;

        // ── The settled shape, straight off the speed map ──────────────────
        // Rail outwards by pace group, and inside a group by speed rating. The
        // one order sets both the lane AND the gap back through the field, so
        // what the middle of the race shows is the speed map drawn on a track.
        var settleOrder = runners.slice().sort(function (a, b) {
            var pa = PACE_LANE_ORDER[a.pace_category] != null ? PACE_LANE_ORDER[a.pace_category] : 2;
            var pb = PACE_LANE_ORDER[b.pace_category] != null ? PACE_LANE_ORDER[b.pace_category] : 2;
            if (pa !== pb) return pa - pb;
            var sa = a.speed != null ? a.speed : -Infinity;
            var sb = b.speed != null ? b.speed : -Infinity;
            if (sa !== sb) return sb - sa;
            return (a.rank || 99) - (b.rank || 99);
        });

        var settleGaps = settleOffsets(settleOrder, bodyProgress);
        // The bob is cosmetic and must stay cosmetic: two neighbours bobbing in
        // opposite phase still have to keep their order, so cap the amplitude
        // against the tightest settled gap in this particular field.
        var tightestGap = Infinity;
        for (var g = 1; g < settleGaps.length; g++) {
            tightestGap = Math.min(tightestGap, settleGaps[g - 1] - settleGaps[g]);
        }
        var bobCeiling = isFinite(tightestGap) ? tightestGap * 0.35 : bodyProgress * 0.09;

        var settleLaneById = {}, settleOffsetById = {};
        settleOrder.forEach(function (runner, index) {
            settleLaneById[idOf(runner)] = index;
            settleOffsetById[idOf(runner)] = settleGaps[index];
        });

        // ── The finishing shape, straight off the composite ────────────────
        var rankOrder = runners.slice().sort(function (a, b) { return (a.rank || 99) - (b.rank || 99); });
        var finishGaps = finishGapsPx(rankOrder, bodyLengthPx, table.raceLength);
        var finishOffsetById = {};
        rankOrder.forEach(function (runner, index) {
            finishOffsetById[idOf(runner)] = -finishGaps[index] / table.raceLength;
        });

        var showNames = options.showNames != null ? options.showNames : fieldSize <= 11;
        // Chips shrink with the horses, and are staggered over three rows so
        // adjacent barriers do not stack their labels on top of each other when
        // the field bunches up.
        var chipScale = clamp(horseSize / 88, 0.42, 0.85);
        var chipRowHeight = 15 * chipScale;
        var entries = [];

        runners.forEach(function (runner, index) {
            var lane = runner.lane != null ? runner.lane : index;      // barrier draw
            var settleLane = settleLaneById[idOf(runner)];
            if (settleLane == null) settleLane = lane;
            // Positive offsets move towards the rail, so barrier 1 sits inside.
            var barrierOffset = (midLane - lane) * laneGap;
            var settleLaneOffset = (midLane - settleLane) * laneGap;
            append(laneLayer, el('path', {
                d: lanePathData(laneBase + lane * laneGap), fill: 'none',
                stroke: '#8fb79a', 'stroke-width': 0.8, 'stroke-dasharray': '3 7'
            }));

            var art = global.RaceHorseArt.create({
                seedName: runner.horse_name,
                name: runner.horse_name,
                number: runner.tab_number || runner.barrier || (index + 1),
                silk: runner.silk || {}
            });
            var holder = append(horseLayer, el('g', { 'class': 'ra-runner', 'data-horse-id': runner.horse_id }));
            holder.style.cursor = 'pointer';
            append(holder, art.node);

            var chip = append(labelLayer, el('g', {
                'class': 'ra-chip', 'data-horse-id': runner.horse_id,
                display: showNames ? 'inline' : 'none'
            }));
            // The chip is drawn at a fixed size then scaled with the horse, so
            // a name never ends up wider than the runner it belongs to.
            var chipInner = append(chip, el('g', { transform: 'scale(' + chipScale.toFixed(3) + ')' }));
            var chipBg = append(chipInner, el('rect', {
                x: -46, y: -9, width: 92, height: 13, rx: 6.5,
                fill: 'rgba(9,9,15,0.86)', stroke: 'rgba(255,255,255,0.14)', 'stroke-width': 0.7
            }));
            var chipText = append(chipInner, el('text', {
                x: 0, y: 0.6, 'text-anchor': 'middle', 'font-size': 9, 'font-weight': '700',
                fill: '#f0f0f5', 'font-family': "-apple-system, 'Segoe UI', system-ui, sans-serif"
            }));
            chipText.textContent = (runner.tab_number ? runner.tab_number + '. ' : '') + runner.horse_name;

            var seed = lane + 1 + index * 0.37;
            entries.push({
                runner: runner,
                lane: lane,
                settleLane: settleLane,
                barrierOffset: barrierOffset,
                settleLaneOffset: settleLaneOffset,
                // A few frames of delay each, so the jump is a ragged line of
                // horses and not one object leaving the gates.
                gateDelay: hash01(seed * 1.7) * GATE_STAGGER,
                settleShape: 0.88 + hash01(seed * 2.9) * 0.3,
                settleOffset: settleOffsetById[idOf(runner)] || 0,
                finishOffset: finishOffsetById[idOf(runner)] || 0,
                bobAmplitude: Math.min((0.05 + hash01(seed * 4.3) * 0.04) * bodyProgress, bobCeiling),
                art: art,
                holder: holder,
                chip: chip,
                chipBg: chipBg,
                gait: Math.random(),
                lastProgress: 0,
                jostleSeed: (lane * 1.7) + (index % 7)
            });
        });

        // Wider chips need the background sized to the text; measure once.
        entries.forEach(function (entry) {
            var text = entry.chip.querySelector('text');
            var width = 0;
            try { width = text.getComputedTextLength(); } catch (e) { width = 70; }
            var boxWidth = Math.max(30, width + 12);
            entry.chipBg.setAttribute('width', boxWidth);
            entry.chipBg.setAttribute('x', -boxWidth / 2);
        });

        // ── Frame loop ────────────────────────────────────────────────────
        var duration = options.duration || 15;
        var raceTime = 0;          // 0..1 through the race
        var playing = false;
        var speed = 1;
        var finished = false;
        var finishAnnounced = false;
        var selectedId = null;
        var lastFrame = 0;
        var rafHandle = null;
        var lastDepthKey = '';

        /* Lateral drift on top of the lane. Horses do not run down a painted
         * line, but through the settle phase they are meant to look locked, so
         * the drift is held to a shimmer there and only opens up once the
         * sprint starts and runners begin looking for room. Doing this sideways
         * rather than by nudging progress leaves the finish order untouched. */
        function drift(entry, t) {
            var alive = t < 0.06 ? t / 0.06 : 1;                     // none in the gates
            var room = 0.28 + 0.72 * smoothstep(T_SPRINT - 0.04, T_SPRINT + 0.16, t);
            var taper = t > 0.86 ? Math.max(0, 1 - (t - 0.86) / 0.14) : 1;
            var wave = Math.sin(t * 5.3 + entry.jostleSeed) * 0.62
                     + Math.sin(t * 2.1 + entry.jostleSeed * 2.3) * 0.38;
            return wave * laneGap * 0.42 * alive * room * taper;
        }

        function frame(now) {
            var delta = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 0;
            lastFrame = now;
            if (playing) {
                raceTime += (delta * speed) / duration;
                if (raceTime >= 1) { raceTime = 1; playing = false; finished = true; }
            }
            render(delta);
            // Announce the result once per running of the race, not once per
            // frame — and reset the flag on replay so a rerun announces again.
            if (finished && !finishAnnounced) {
                finishAnnounced = true;
                if (options.onFinish) options.onFinish(runners);
            }
            rafHandle = global.requestAnimationFrame(frame);
        }

        function render(delta) {
            var live = [];
            for (var i = 0; i < entries.length; i++) {
                var entry = entries[i];
                var progress = progressAt(entry, raceTime);
                // Belt and braces on top of PCHIP: a horse never goes backwards.
                if (progress < entry.lastProgress) progress = entry.lastProgress;
                var travelled = progress - entry.lastProgress;
                entry.lastProgress = progress;

                var point = samplePoint(table, progress,
                    laneOffsetAt(entry, raceTime) + drift(entry, raceTime));
                var scale = horseSize / 100;

                // Facing: cos(tangent) is +1 running right down the home
                // straight, -1 running left down the back straight, and passes
                // through 0 at the apex of a turn. Using it as the x-scale
                // keeps every horse upright, mirrors it automatically when the
                // direction of travel reverses, and foreshortens it round the
                // bend instead of popping between the two facings. The floor
                // stops a horse vanishing entirely at the apex.
                var facing = Math.cos(point.angle);
                var squash = Math.max(0.34, Math.abs(facing)) * (facing < 0 ? -1 : 1);
                // A little lean into the turn: zero on both straights, where
                // sin(tangent) is zero, and largest at the apex.
                var lean = Math.sin(point.angle) * 6;

                // Anchor mid-body, low, so the horse straddles its lane line.
                entry.holder.setAttribute('transform',
                    'translate(' + point.x.toFixed(2) + ',' + point.y.toFixed(2) + ') ' +
                    'rotate(' + lean.toFixed(2) + ') ' +
                    'scale(' + (scale * squash).toFixed(4) + ',' + scale.toFixed(4) + ') ' +
                    'translate(-50,-44)');

                // Legs cycle at the speed the horse is actually travelling, so
                // a fading runner visibly shortens stride while a finisher
                // winds up. One stride covers roughly two body lengths.
                if (delta > 0) {
                    var pixels = travelled * table.raceLength;
                    entry.gait = (entry.gait + pixels / (horseSize * 1.15)) % 1;
                }
                entry.art.setGait(entry.gait);

                var chipY = point.y - horseSize * 0.46 - (entry.settleLane % 3) * chipRowHeight;
                entry.chip.setAttribute('transform',
                    'translate(' + point.x.toFixed(2) + ',' + Math.max(12, chipY).toFixed(2) + ')');

                live.push({ entry: entry, progress: progress, y: point.y });
            }

            // Painter's order: whatever is lower on screen is nearer the camera,
            // so it is drawn last. Only touch the DOM when the order changes.
            live.sort(function (a, b) { return a.y - b.y; });
            var depthKey = '';
            for (var d = 0; d < live.length; d++) depthKey += live[d].entry.runner.horse_id + ',';
            if (depthKey !== lastDepthKey) {
                lastDepthKey = depthKey;
                for (var k = 0; k < live.length; k++) horseLayer.appendChild(live[k].entry.holder);
            }

            if (options.onTick) {
                var order = live.slice().sort(function (a, b) { return b.progress - a.progress; })
                    .map(function (item) { return item.entry.runner; });
                options.onTick(raceTime, order);
            }
        }

        // ── Interaction ───────────────────────────────────────────────────
        function applySelection() {
            entries.forEach(function (entry) {
                var isSelected = selectedId != null && entry.runner.horse_id === selectedId;
                entry.art.setDimmed(selectedId != null && !isSelected);
                entry.chip.setAttribute('display', (showNames || isSelected) ? 'inline' : 'none');
                entry.chipBg.setAttribute('stroke', isSelected ? '#667eea' : 'rgba(255,255,255,0.16)');
                entry.chipBg.setAttribute('stroke-width', isSelected ? 1.6 : 0.8);
            });
        }

        entries.forEach(function (entry) {
            entry.holder.addEventListener('click', function (event) {
                event.stopPropagation();
                selectedId = selectedId === entry.runner.horse_id ? null : entry.runner.horse_id;
                applySelection();
                if (options.onSelect) options.onSelect(selectedId ? entry.runner : null);
            });
        });
        svg.addEventListener('click', function () {
            if (selectedId != null) {
                selectedId = null;
                applySelection();
                if (options.onSelect) options.onSelect(null);
            }
        });

        render(0);
        rafHandle = global.requestAnimationFrame(frame);

        return {
            play: function () { if (!finished) playing = true; },
            pause: function () { playing = false; },
            toggle: function () { if (finished) { this.replay(); } else { playing = !playing; } return playing; },
            isPlaying: function () { return playing; },
            isFinished: function () { return finished; },
            replay: function () {
                raceTime = 0; finished = false; finishAnnounced = false; playing = true;
                entries.forEach(function (entry) { entry.lastProgress = 0; });
                lastDepthKey = '';
                render(0);
            },
            scrubTo: function (t) {
                playing = false;
                raceTime = clamp(t, 0, 1);
                finished = raceTime >= 1;
                finishAnnounced = finished;
                // The monotone clamp is relative to the last frame, so it has
                // to be released before jumping backwards through the race.
                entries.forEach(function (entry) { entry.lastProgress = 0; });
                render(0);
            },
            setSpeed: function (value) { speed = clamp(value, 0.25, 4); },
            getTime: function () { return raceTime; },
            setNamesVisible: function (visible) { showNames = !!visible; applySelection(); },
            select: function (horseId) {
                selectedId = horseId;
                applySelection();
            },
            destroy: function () {
                if (rafHandle) global.cancelAnimationFrame(rafHandle);
                entries.forEach(function (entry) { entry.art.destroy(); });
                while (svg.firstChild) svg.removeChild(svg.firstChild);
            }
        };
    }

    global.RaceAnimation = {
        create: create,
        pchip: pchip,
        packCurve: packCurve,
        progressAt: progressAt,
        settleOffsets: settleOffsets,
        laneOffsetAt: laneOffsetAt,
        finishGapsPx: finishGapsPx,
        horseSizeForField: horseSizeForField,
        lanePathData: lanePathData,
        VIEW: VIEW,
        T_GATE: T_GATE,
        T_SPRINT: T_SPRINT,
        T_LANE_START: T_LANE_START,
        T_LANE_END: T_LANE_END,
        GATE_STAGGER: GATE_STAGGER,
        BODY_OF_ICON: BODY_OF_ICON,
        PACE_LANE_ORDER: PACE_LANE_ORDER
    };
}(typeof window !== 'undefined' ? window : this));
