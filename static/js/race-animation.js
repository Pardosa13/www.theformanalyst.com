/*
 * race-animation.js — oval track + three-phase race engine.
 *
 * Loaded only by templates/race-animations-predictions.html. Depends on
 * race-horse-art.js for the runners themselves.
 *
 * TRACK
 * A stadium oval: two straights joined by two turns, with the start/finish post
 * at the end of the home straight. Positions come from sampling the real SVG
 * path with getPointAtLength() rather than from hand-rolled curve maths — the
 * path is sampled once into a lookup table at build time, so a 24-runner field
 * costs a couple of array reads per horse per frame instead of dozens of
 * geometry calls.
 *
 * The direction of travel and how much of the lap the race covers both come
 * from the payload. Australian fields run clockwise in New South Wales and
 * Queensland and anti-clockwise everywhere else, and a 1000m dash starts a long
 * way closer to the post than a 2400m staying race. Drawing every race as the
 * same three-quarter lap made a sprint and a Cup look identical.
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
 *   SPRINT  t 0.65 -> 1      offsets move from the settled position to the
 *                            finishing position, on a curve whose shape is the
 *                            runner's own — see PACE below.
 *
 * The pack rises far faster than any offset can fall, so no runner ever goes
 * backwards, and the sprint target is the composite finish order, so the result
 * always matches the ranking the API sent.
 *
 * PACE
 * The run home used to be one shared t^1.6 for everybody, which meant the speed
 * map decided the middle of the race and nothing else — a leader who got hunted
 * and a backmarker who got a dream run both arrived exactly where their score
 * said, in exactly the same manner. Now each runner sprints on its own curve,
 * taken from its map role and the tempo of the race:
 *
 *   a leader in a soft race holds on (early, flat curve)
 *   a leader in a speed duel kicks clear and is swallowed (a fade transient)
 *   a backmarker sprints late and hard (a steep curve)
 *
 * The curve and the transient both land on exactly the finishing offset at the
 * post, so the story is in HOW a runner gets there, never in where it ends up.
 * Pace changes the result through the score instead — pace fit is a scoring
 * component now, so a hot tempo genuinely reorders the field before the
 * animation ever runs.
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
 *
 * PRESENTATION
 * Everything above decides where a horse is. A separate, strictly one-way
 * layer decides how the picture of it looks: ground shadows, a softened
 * background once the race is on, a camera that tightens onto the leading pack
 * in the straight, sprint trails, a held frame and a flash at the post, a
 * vignette, and a sound bed. None of it can move a runner or change a placing
 * — it is all read off raceTime and the positions the engine has already
 * worked out — and all of it is either dropped or frozen under reduced motion.
 *
 * FRAME LOOP
 * The loop only runs while there is something to animate. It used to fire sixty
 * times a second forever — paused, finished, tab in the background — which is a
 * fan spinning for nothing. Now pausing stops it, finishing stops it, and
 * hiding the tab stops it; anything that changes the picture asks for a single
 * frame instead.
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

    // How each pace role runs its home straight. The exponent shapes the curve
    // from the settled position to the finishing one: 1 is a runner already
    // rolling that simply holds its ground, and anything above builds later and
    // harder. A backmarker's whole race is the last furlong, so it gets the
    // steepest curve on the track.
    var SPRINT_EXPONENT = { leader: 1.0, onpace: 1.35, midfield: 1.8, back: 2.5 };

    // The fade. In a speed duel the leaders go too hard, get clear, and stop.
    // This is that transient: a bump forward early in the straight that decays
    // to nothing by the post, so the finishing order is untouched and only the
    // manner of it changes. Scaled by the race's pace pressure, so a soft lead
    // produces none of it at all.
    var FADE_ROLE = { leader: 1.0, onpace: 0.55, midfield: 0.0, back: -0.35 };
    var FADE_STRENGTH = 0.55;   // in body lengths, at full pressure

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

    // ── Presentation. None of this touches where a horse is. ──
    var FINISH_HOLD_MS = 800;    // the picture is held at the post before the
                                 // result is announced, so the winner is seen
                                 // to win rather than the page jumping to text
    var CAMERA_TIGHTEST = 0.78;  // viewBox scale by the time the leader arrives
    var CAMERA_RATE = 0.055;     // how fast the camera chases its target, per frame
    var TRAIL_SPACING = [0.17, 0.36];   // trail copies, in icon widths behind
    var TRAIL_OPACITY = [0.20, 0.10];   // ...and how solid each one is
    var BUNCH_LENGTHS = 4;       // within this many body lengths of the leader
                                 // counts as part of the leading bunch, which
                                 // is what the hoofbeat bed is scaled by

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

    /* Does this viewer want less movement? Checked once per build rather than
     * per frame, and overridable, because the page also exposes it as a toggle. */
    function prefersReducedMotion() {
        try {
            return !!(global.matchMedia &&
                      global.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (error) {
            return false;
        }
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

    /* One lane, as a stadium path traced in the direction of travel.
     *
     * The path deliberately STARTS at the winning post and ends back there, so
     * a full lap is exactly progress 0 -> 1 and any fraction of a lap can be
     * measured back from the post.
     *
     * Anti-clockwise: post at the right-hand end of the home straight; the lap
     * runs right turn (up), back straight (right to left), left turn (down),
     * home straight (left to right) into the post.
     *
     * Clockwise mirrors all of that: the post sits at the left-hand end of the
     * home straight and the lap runs the other way round, which is what a field
     * at Randwick or Eagle Farm actually does.
     */
    function lanePathData(radius, clockwise) {
        var cx = VIEW.cx, cy = VIEW.cy, a = VIEW.straight;
        var r = radius;
        if (clockwise) {
            return 'M ' + (cx - a) + ',' + (cy + r) +
                   ' A ' + r + ',' + r + ' 0 0 1 ' + (cx - a) + ',' + (cy - r) +
                   ' L ' + (cx + a) + ',' + (cy - r) +
                   ' A ' + r + ',' + r + ' 0 0 1 ' + (cx + a) + ',' + (cy + r) +
                   ' Z';
        }
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
     *
     * `lapFraction` is how much of the circuit this race covers, so the start
     * point is that far back from the post. A 1200m race over a nominal 1800m
     * circuit starts two thirds of the way round; a staying race starts at the
     * post and runs the full lap.
     */
    function sampleLane(path, lapFraction) {
        var total = path.getTotalLength();
        var fraction = clamp(lapFraction == null ? 0.75 : lapFraction, 0.08, 1);
        var startAt = total * (1 - fraction);
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

    /* Position at progress p (0..1) plus a perpendicular offset in px.
     *
     * `laneSign` flips which side of the path counts as "towards the rail",
     * because the infield is on the runners' left going anti-clockwise and on
     * their right going the other way. */
    function samplePoint(table, progress, sideways, laneSign) {
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
            var offset = sideways * (laneSign == null ? 1 : laneSign);
            x += Math.sin(angle) * offset;
            y += -Math.cos(angle) * offset;
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

    /* The fade transient, in progress units.
     *
     * A hump through the middle of the straight: zero when the sprint starts,
     * biggest around a third of the way home, and back to zero at the post. A
     * leader with a positive fade goes clear and is then reeled in; a
     * backmarker's small negative one has it drop out the back before it
     * launches. It cannot change the result because it is worth nothing at the
     * line — the whole point is that it changes the manner, not the placing. */
    function fadeAt(entry, u) {
        if (!entry.fade) return 0;
        // sin(pi * u^0.7) peaks early in the straight rather than halfway.
        return entry.fade * Math.sin(Math.PI * Math.pow(clamp(u, 0, 1), 0.7));
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
            // Run home. The curve from settled to finishing position is the
            // runner's own — see SPRINT_EXPONENT — and the fade rides on top of
            // it. Both are exact at u = 1, so the finishing order is whatever
            // the composite said and nothing here can move it.
            var u = (t - T_SPRINT) / (1 - T_SPRINT);
            var eased = Math.pow(u, entry.sprintExponent);
            offset = entry.settleOffset + (entry.finishOffset - entry.settleOffset) * eased;
            offset += fadeAt(entry, u);
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
    /* The crowd on the outer rail of the home straight.
     *
     * Deliberately a silhouette and nothing more: banked rows of heads under a
     * roof line, all of it darker than the turf so it reads as depth behind the
     * runners rather than as something to look at. Built once at track time —
     * it never moves, so it never costs a frame. */
    function buildGrandstand(parent) {
        var stand = append(parent, el('g', { 'class': 'ra-stand' }));
        var left = VIEW.cx - VIEW.straight - 62;
        var right = VIEW.cx + VIEW.straight + 62;
        var top = VIEW.cy + VIEW.outerRy + 24;

        append(stand, el('rect', {                          // terracing
            x: left, y: top, width: right - left, height: VIEW.height - top,
            rx: 6, fill: '#111820', opacity: 0.95
        }));
        append(stand, el('path', {                          // roof over the front of it
            d: 'M ' + (left - 12) + ',' + top + ' L ' + (right + 12) + ',' + top +
               ' L ' + (right - 2) + ',' + (top - 10) + ' L ' + (left + 2) + ',' + (top - 10) + ' Z',
            fill: '#1c2530'
        }));
        append(stand, el('line', {                          // the lip catching the light
            x1: left - 12, y1: top, x2: right + 12, y2: top,
            stroke: '#3d4a58', 'stroke-width': 1.4, opacity: 0.8
        }));

        // Three banked rows of heads, spaced closer than they are wide so they
        // overlap into a mass rather than reading as a row of dots. Only the
        // height, the size and the shade wobble, which is all it needs at this
        // size and keeps the whole crowd to one cheap deterministic pass.
        for (var row = 0; row < 3; row++) {
            var y = top + 11 + row * 11;
            for (var x = left + 6; x < right - 4; x += 6.2) {
                var wobble = hash01(x * 0.31 + row * 7.7);
                append(stand, el('circle', {
                    cx: x + wobble * 1.8, cy: y - wobble * 2.2, r: 3.7 + wobble * 1.1,
                    fill: ['#2c3540', '#37414d', '#424d5a'][row],
                    opacity: 0.62 + wobble * 0.3
                }));
            }
        }
        return stand;
    }

    function buildTrack(svg, innerRadius, outerRadius, clockwise, post) {
        var defs = append(svg, el('defs'));

        var turf = append(defs, el('linearGradient', { id: 'ra-turf', x1: '0', y1: '0', x2: '0', y2: '1' }));
        append(turf, el('stop', { offset: '0', 'stop-color': '#22432a' }));
        append(turf, el('stop', { offset: '1', 'stop-color': '#2d5734' }));

        var infield = append(defs, el('linearGradient', { id: 'ra-infield', x1: '0', y1: '0', x2: '0', y2: '1' }));
        append(infield, el('stop', { offset: '0', 'stop-color': '#16281b' }));
        append(infield, el('stop', { offset: '1', 'stop-color': '#1b3421' }));

        // The ground the oval sits on. Flat black behind the track made the
        // whole thing look like a diagram; a cool wash at the top falling to
        // near-black at the bottom gives it somewhere to be.
        var sky = append(defs, el('linearGradient', { id: 'ra-sky', x1: '0', y1: '0', x2: '0', y2: '1' }));
        append(sky, el('stop', { offset: '0', 'stop-color': '#16232e' }));
        append(sky, el('stop', { offset: '0.55', 'stop-color': '#0f1a18' }));
        append(sky, el('stop', { offset: '1', 'stop-color': '#0a1210' }));

        /* Depth of field, in two strengths.
         *
         * A blurred background is not free: the browser rasterises the filtered
         * group once and reuses it, which costs nothing while the frame is
         * still — but the camera moves the frame every frame down the straight,
         * and re-blurring the whole track sixty times a second took a
         * 24-runner field from 60fps to 50. So the blur belongs to the wide,
         * static shot, and the run home gets the desaturation alone. Nothing is
         * lost: by then the camera crop and the vignette are doing the job the
         * blur was doing, on the part of the picture the eye is actually on. */
        var soften = append(defs, el('filter', {
            id: 'ra-soften', x: '-6%', y: '-6%', width: '112%', height: '112%',
            'color-interpolation-filters': 'sRGB'
        }));
        append(soften, el('feGaussianBlur', { stdDeviation: '1.25', result: 'ra-blur' }));
        append(soften, el('feColorMatrix', { 'in': 'ra-blur', type: 'saturate', values: '0.74' }));

        var softenLite = append(defs, el('filter', {
            id: 'ra-soften-lite', x: '-2%', y: '-2%', width: '104%', height: '104%',
            'color-interpolation-filters': 'sRGB'
        }));
        append(softenLite, el('feColorMatrix', { type: 'saturate', values: '0.74' }));

        // The edges of the frame, darkened, so the eye goes to the pack rather
        // than to the empty turf in the corners.
        var vignette = append(defs, el('radialGradient', {
            id: 'ra-vignette', cx: '0.5', cy: '0.52', r: '0.72'
        }));
        append(vignette, el('stop', { offset: '0.5', 'stop-color': '#000000', 'stop-opacity': '0' }));
        append(vignette, el('stop', { offset: '0.82', 'stop-color': '#000000', 'stop-opacity': '0.22' }));
        append(vignette, el('stop', { offset: '1', 'stop-color': '#000000', 'stop-opacity': '0.55' }));

        // The whiteout on the line as the winner goes past.
        var flash = append(defs, el('radialGradient', {
            id: 'ra-flash', cx: '0.5', cy: '0.5', r: '0.5'
        }));
        append(flash, el('stop', { offset: '0', 'stop-color': '#ffffff', 'stop-opacity': '0.92' }));
        append(flash, el('stop', { offset: '0.45', 'stop-color': '#fff6d8', 'stop-opacity': '0.42' }));
        append(flash, el('stop', { offset: '1', 'stop-color': '#fff6d8', 'stop-opacity': '0' }));

        var scenery = append(svg, el('g', { 'class': 'ra-scenery' }));

        append(scenery, el('rect', {
            x: 0, y: 0, width: VIEW.width, height: VIEW.height, fill: 'url(#ra-sky)'
        }));
        buildGrandstand(scenery);

        // The running surface: one path holding the outer and inner outlines,
        // filled even-odd so the middle punches out as the infield.
        append(scenery, el('path', {
            d: lanePathData(outerRadius + 18, clockwise) + ' ' + lanePathData(innerRadius - 18, clockwise),
            'fill-rule': 'evenodd', fill: 'url(#ra-turf)'
        }));
        append(scenery, el('path', {
            d: lanePathData(innerRadius - 18, clockwise), fill: 'url(#ra-infield)'
        }));
        append(scenery, el('path', {                        // inside running rail
            d: lanePathData(innerRadius - 18, clockwise), fill: 'none',
            stroke: '#e8ecf2', 'stroke-width': 2.4, opacity: 0.85
        }));
        append(scenery, el('path', {                        // outside rail
            d: lanePathData(outerRadius + 18, clockwise), fill: 'none',
            stroke: '#5d6b74', 'stroke-width': 2, opacity: 0.7
        }));

        // Winning post, wherever the lane path both starts and finishes.
        var postX = post.x;
        append(scenery, el('line', {
            x1: postX, y1: VIEW.cy + innerRadius - 18, x2: postX, y2: VIEW.cy + outerRadius + 18,
            stroke: '#ffffff', 'stroke-width': 3, 'stroke-dasharray': '7 5', opacity: 0.9
        }));
        append(scenery, el('rect', {
            x: postX - 3.5, y: VIEW.cy + outerRadius + 10, width: 7, height: 34, rx: 2, fill: '#f4f6fa'
        }));
        var label = append(scenery, el('text', {
            x: postX + (clockwise ? -14 : 14), y: VIEW.cy + outerRadius + 34,
            'text-anchor': clockwise ? 'end' : 'start',
            'font-size': 15, 'font-weight': '800', fill: '#f4f6fa', opacity: 0.85,
            'font-family': "'DM Mono', ui-monospace, monospace"
        }));
        label.textContent = 'FINISH';

        return scenery;
    }

    /* The barrier stalls, drawn wherever this race actually starts.
     *
     * The start point moves with the distance now, so the gate is placed off
     * the sampled path rather than assumed to be at the top of the back
     * straight: one stall per lane along the track's normal at progress zero,
     * innermost stall = barrier 1, which is exactly where the runners line up.
     */
    function buildGate(parent, table, laneGap, fieldSize, laneSign) {
        var gate = append(parent, el('g', { 'class': 'ra-gate' }));
        var stallDepth = clamp(laneGap * 1.7, 11, 22);
        var midLane = (fieldSize - 1) / 2;

        // Where the field jumps from, and which way the line of stalls runs.
        var innerEdge = samplePoint(table, 0, (midLane * laneGap) + laneGap, laneSign);
        var outerEdge = samplePoint(table, 0, -(midLane * laneGap) - laneGap, laneSign);

        append(gate, el('line', {
            x1: innerEdge.x, y1: innerEdge.y, x2: outerEdge.x, y2: outerEdge.y,
            stroke: '#ffffff', 'stroke-width': 2.6, 'stroke-dasharray': '6 4', opacity: 0.8
        }));

        for (var lane = 0; lane < fieldSize; lane++) {
            // Lane 0 is barrier 1, on the rail: the same positive-is-inside
            // convention the runners themselves use.
            var at = samplePoint(table, 0, (midLane - lane) * laneGap, laneSign);
            // The stall faces the way the field will run. samplePoint already
            // carries the track's tangent there — deriving it from a second
            // sample "just behind" would not work, because progress is clamped
            // at zero and both samples would land on the same point.
            var angle = at.angle * 180 / Math.PI;
            append(gate, el('rect', {
                x: -stallDepth, y: -Math.max(3, laneGap * 0.92) / 2,
                width: stallDepth, height: Math.max(3, laneGap * 0.92),
                rx: 1.4, fill: 'rgba(10,12,18,0.72)',
                stroke: 'rgba(232,236,242,0.5)', 'stroke-width': 0.7,
                transform: 'translate(' + at.x.toFixed(2) + ',' + at.y.toFixed(2) + ') ' +
                           'rotate(' + angle.toFixed(2) + ')'
            }));
        }

        var label = append(gate, el('text', {
            x: outerEdge.x, y: outerEdge.y - 10, 'text-anchor': 'middle',
            'font-size': 13, 'font-weight': '800', fill: '#f4f6fa', opacity: 0.75,
            'font-family': "'DM Mono', ui-monospace, monospace"
        }));
        label.textContent = 'START';
        return gate;
    }

    // ── The controller ────────────────────────────────────────────────────
    /* options:
     *   svg           the <svg> to build into (cleared first)
     *   runners       the API payload's runners, already in composite-rank order
     *   duration      race length in seconds at speed 1 (default 15)
     *   direction     'clockwise' | 'anticlockwise'
     *   lapFraction   how much of the circuit this race covers (0..1)
     *   pace          the payload's pace profile ({pressure, shape, ...})
     *   reducedMotion suppress the cosmetic bob and drift, and never autoplay
     *   audio         optional sound module — see race-animation-audio.js. Any
     *                 of play/pause/stop/update/finish it does not implement is
     *                 simply not called, and one that throws is dropped
     *   showNames     draw name chips (auto-off for big fields)
     *   showResults   put the actual finishing position on each chip
     *   onTick        (raceTime01, liveOrder[]) each frame
     *   onFinish      (runners) when the winner hits the post
     *   onSelect      (runner|null) when a horse is tapped
     *   onPlayState   (isPlaying) whenever the loop starts or stops
     */
    function create(options) {
        options = options || {};
        var svg = options.svg;
        var runners = (options.runners || []).slice();
        var fieldSize = runners.length;
        if (!svg || !fieldSize) return null;

        while (svg.firstChild) svg.removeChild(svg.firstChild);
        svg.setAttribute('viewBox', '0 0 ' + VIEW.width + ' ' + VIEW.height);

        var clockwise = options.direction === 'clockwise';
        // Going the other way round, the infield — and therefore the rail — is
        // on the runners' other side, so the lane offsets flip with it.
        var laneSign = clockwise ? -1 : 1;
        var reducedMotion = options.reducedMotion != null
            ? !!options.reducedMotion : prefersReducedMotion();

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

        // Build the reference path first: the post and the barrier line are
        // both read off it, so the scenery has to follow the geometry rather
        // than assume it.
        var measureLayer = append(svg, el('g', { 'class': 'ra-measure', opacity: 0 }));
        var referencePath = append(measureLayer, el('path', {
            d: lanePathData(referenceRadius, clockwise), fill: 'none', stroke: 'none'
        }));
        var table = sampleLane(referencePath, options.lapFraction);
        measureLayer.parentNode.removeChild(measureLayer);

        var post = samplePoint(table, 1, 0, laneSign);
        var scenery = buildTrack(svg, innerRadius, outerRadius, clockwise, post);

        var laneLayer = append(svg, el('g', { 'class': 'ra-lanes', opacity: 0.12 }));
        var horseLayer = append(svg, el('g', { 'class': 'ra-horses' }));
        // Shadows and sprint trails belong under every runner, not just under
        // their own. They live in the first child of the horse layer, and the
        // depth sort only ever appendChild()s holders, so this stays first.
        var underLayer = append(horseLayer, el('g', { 'class': 'ra-under' }));
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

        // How hard the tempo is, 0 (soft lead) to 1 (speed duel). Drives the
        // fade: nobody fades in a race with one leader out in front on its own.
        var pressure = clamp(Number((options.pace || {}).pressure) || 0, 0, 1);

        var showNames = options.showNames != null ? options.showNames : fieldSize <= 11;
        var showResults = !!options.showResults;
        // Chips shrink with the horses, and are staggered over three rows so
        // adjacent barriers do not stack their labels on top of each other when
        // the field bunches up.
        var chipScale = clamp(horseSize / 88, 0.42, 0.85);
        var chipRowHeight = 15 * chipScale;
        // Only big fields bunch hard enough to need separating, and a small
        // field looks wrong with it, so the nudge fades in from about nine
        // runners and is worth a third of a lane at twenty.
        var zJitterRange = laneGap * 0.34 * smoothstep(9, 16, fieldSize);
        var entries = [];

        runners.forEach(function (runner, index) {
            var lane = runner.lane != null ? runner.lane : index;      // barrier draw
            var settleLane = settleLaneById[idOf(runner)];
            if (settleLane == null) settleLane = lane;
            // Positive offsets move towards the rail, so barrier 1 sits inside.
            var barrierOffset = (midLane - lane) * laneGap;
            var settleLaneOffset = (midLane - settleLane) * laneGap;
            append(laneLayer, el('path', {
                d: lanePathData(laneBase + lane * laneGap, clockwise), fill: 'none',
                stroke: '#8fb79a', 'stroke-width': 0.8, 'stroke-dasharray': '3 7'
            }));

            var art = global.RaceHorseArt.create({
                seedName: runner.horse_name,
                name: runner.horse_name,
                number: runner.tab_number || runner.barrier || (index + 1),
                silk: runner.silk || {},
                reducedMotion: reducedMotion
            });

            // The horse's own shadow on the ground. Drawn flat and separately
            // rather than inside the runner's group, because a shadow that
            // rotated with the body through the turn would read as painted on
            // the horse instead of lying under it.
            var shadow = append(underLayer, el('ellipse', {
                cx: 0, cy: 0, rx: horseSize * 0.30, ry: horseSize * 0.075,
                fill: '#000000', opacity: 0.26
            }));

            // Two flat copies of the runner's outline, trailed behind it in the
            // straight. Copies of the SILHOUETTE, not of the rig: cloning the
            // articulated horse would triple the cost of a big field to draw a
            // shape that is 20% opaque and eight pixels long.
            var trails = [];
            if (!reducedMotion && global.RaceHorseArt.BODY_PATH) {
                var silhouette = global.RaceHorseArt.BODY_PATH + ' ' + global.RaceHorseArt.NECK_PATH;
                for (var tI = 0; tI < TRAIL_SPACING.length; tI++) {
                    trails.push(append(underLayer, el('path', {
                        d: silhouette, fill: (art.coat && art.coat.body) || '#5b3a22',
                        opacity: 0, display: 'none'
                    })));
                }
            }

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

            var pace = runner.pace_category || 'midfield';
            var seed = lane + 1 + index * 0.37;
            var entry = {
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
                // The run home, shaped by the runner's own map role.
                sprintExponent: SPRINT_EXPONENT[pace] != null ? SPRINT_EXPONENT[pace] : 1.8,
                // The fade, scaled by the tempo. Zero in a soft race whatever
                // the role, because there is nothing to be cooked by.
                fade: (FADE_ROLE[pace] || 0) * pressure * FADE_STRENGTH * bodyProgress,
                bobAmplitude: reducedMotion ? 0
                    : Math.min((0.05 + hash01(seed * 4.3) * 0.04) * bodyProgress, bobCeiling),
                // A fixed few pixels up or down at draw time so two runners on
                // the same stride in a twenty-runner field do not sit exactly
                // on top of each other. It is added to the drawn y and nothing
                // else — not to the lane, not to progress — for the same reason
                // the bob is capped: readability must not become a result.
                zJitter: (hash01(seed * 6.7) - 0.5) * zJitterRange,
                art: art,
                holder: holder,
                shadow: shadow,
                trails: trails,
                chip: chip,
                chipBg: chipBg,
                chipText: chipText,
                gait: hash01(seed * 5.1),
                lastProgress: 0,
                jostleSeed: (lane * 1.7) + (index % 7)
            };
            entries.push(entry);
            setChipText(entry);
        });

        /* What the chip says. The saddlecloth and the name always; the actual
         * finishing position too, once the race has been settled and the page
         * has asked for it — that is the whole point of showing a replay of a
         * race we already know the answer to. */
        function setChipText(entry) {
            var runner = entry.runner;
            var text = (runner.tab_number ? runner.tab_number + '. ' : '') + runner.horse_name;
            if (showResults && runner.result && runner.result.ran) {
                var finish = runner.result.finish_position;
                text += finish === 1 ? '  ★ WON'
                      : (finish <= 4 ? '  ' + finish + (finish === 2 ? 'nd' : finish === 3 ? 'rd' : 'th')
                                     : '  unpl');
            }
            entry.chipText.textContent = text;
        }

        function measureChips() {
            entries.forEach(function (entry) {
                var width = 0;
                try { width = entry.chipText.getComputedTextLength(); } catch (e) { width = 70; }
                var boxWidth = Math.max(30, width + 12);
                entry.chipBg.setAttribute('width', boxWidth);
                entry.chipBg.setAttribute('x', -boxWidth / 2);
            });
        }
        measureChips();

        buildGate(svg, table, laneGap, fieldSize, laneSign);

        // ── Overlays, above everything and deaf to the mouse ───────────────
        // pointer-events matters: the <svg> carries the click that clears a
        // selection and each runner carries its own, and a sheet of glass over
        // the top would swallow both.
        var overlay = append(svg, el('g', { 'class': 'ra-overlay' }));
        overlay.style.pointerEvents = 'none';

        // The whiteout at the post. Sized off the track band so it covers the
        // width the field can cross the line on, whatever the field size.
        var flashSpan = TRACK_BAND + 120;
        var finishFlash = append(overlay, el('rect', {
            x: post.x - flashSpan / 2, y: VIEW.cy + innerRadius - 60,
            width: flashSpan, height: TRACK_BAND + 120,
            fill: 'url(#ra-flash)', opacity: 0
        }));
        append(overlay, el('rect', {
            x: 0, y: 0, width: VIEW.width, height: VIEW.height, fill: 'url(#ra-vignette)'
        }));

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
        var destroyed = false;

        // ── Presentation state ─────────────────────────────────────────────
        var audio = options.audio || null;
        var holdUntil = 0;         // the post-race freeze ends at this timestamp
        var softening = '';        // which softening filter the scenery is on
        var lastViewBox = '';
        var cameraGoal = null;     // where the last render wanted the camera
        // The camera, as a viewBox. Kept as numbers so it can be eased rather
        // than snapped, and reset to the full frame on anything that restarts
        // the race.
        var fullView = [0, 0, VIEW.width, VIEW.height];
        var camera = fullView.slice();

        function callAudio(name, a, b) {
            if (!audio || typeof audio[name] !== 'function') return;
            // A sound module that throws must never take the race down with it.
            try { audio[name](a, b); } catch (error) { audio = null; }
        }

        /* Push the background back for the duration of the race, so the
         * runners sit in front of the track instead of in it. Full blur while
         * the camera is still, desaturation alone once it starts moving — see
         * the ra-soften defs for why. Toggled on a state flag rather than set
         * every frame: it is one attribute, but it is an attribute that makes
         * the browser rebuild a filter. */
        function setSoftened(on) {
            var want = '';
            if (on && !reducedMotion) want = raceTime > T_SPRINT ? 'ra-soften-lite' : 'ra-soften';
            if (want === softening) return;
            softening = want;
            if (want) scenery.setAttribute('filter', 'url(#' + want + ')');
            else scenery.removeAttribute('filter');
        }

        /* Where the camera wants to be this frame.
         *
         * Full frame for the whole race until the run home, then tightening on
         * the leader through the straight. Clamped inside the full view so a
         * leader near the edge pans the frame rather than showing the void
         * outside it. */
        function cameraTarget(leadX, leadY) {
            if (reducedMotion || raceTime <= T_SPRINT || leadX == null) return fullView;
            var u = (raceTime - T_SPRINT) / (1 - T_SPRINT);
            var zoom = 1 - (1 - CAMERA_TIGHTEST) * smoothstep(0, 0.8, u);
            var w = VIEW.width * zoom, h = VIEW.height * zoom;
            var x = clamp(leadX - w / 2, 0, VIEW.width - w);
            var y = clamp(leadY - h / 2, 0, VIEW.height - h);
            return [x, y, w, h];
        }

        function applyCamera(target, snap) {
            for (var i = 0; i < 4; i++) {
                camera[i] = snap ? target[i] : camera[i] + (target[i] - camera[i]) * CAMERA_RATE;
            }
            var next = camera[0].toFixed(1) + ' ' + camera[1].toFixed(1) + ' ' +
                       camera[2].toFixed(1) + ' ' + camera[3].toFixed(1);
            if (next === lastViewBox) return;
            lastViewBox = next;
            svg.setAttribute('viewBox', next);
        }

        /* Back to the start: full frame, sharp background, no flash. Called by
         * everything that puts the race back to a time it has already run. */
        function resetPresentation() {
            applyCamera(fullView, true);
            setSoftened(false);
            finishFlash.setAttribute('opacity', 0);
            holdUntil = 0;
        }

        /* Lateral drift on top of the lane. Horses do not run down a painted
         * line, but through the settle phase they are meant to look locked, so
         * the drift is held to a shimmer there and only opens up once the
         * sprint starts and runners begin looking for room. Doing this sideways
         * rather than by nudging progress leaves the finish order untouched. */
        function drift(entry, t) {
            if (reducedMotion) return 0;
            var alive = t < 0.06 ? t / 0.06 : 1;                     // none in the gates
            var room = 0.28 + 0.72 * smoothstep(T_SPRINT - 0.04, T_SPRINT + 0.16, t);
            var taper = t > 0.86 ? Math.max(0, 1 - (t - 0.86) / 0.14) : 1;
            var wave = Math.sin(t * 5.3 + entry.jostleSeed) * 0.62
                     + Math.sin(t * 2.1 + entry.jostleSeed * 2.3) * 0.38;
            return wave * laneGap * 0.42 * alive * room * taper;
        }

        /* Ask for one frame. The loop is not a heartbeat — it exists only while
         * the race is running, or for the single frame something else needs to
         * redraw. Everything that changes the picture calls this. */
        function requestFrame() {
            if (destroyed || rafHandle != null) return;
            rafHandle = global.requestAnimationFrame(frame);
        }

        function stopLoop() {
            if (rafHandle != null) {
                global.cancelAnimationFrame(rafHandle);
                rafHandle = null;
            }
            lastFrame = 0;
        }

        function setPlaying(value) {
            var next = !!value;
            if (next === playing) return;
            playing = next;
            if (playing) {
                requestFrame();
                callAudio('play', raceTime);
            } else {
                callAudio('pause');
            }
            if (options.onPlayState) options.onPlayState(playing);
        }

        function frame(now) {
            rafHandle = null;
            if (destroyed) return;

            var delta = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 0;
            lastFrame = now;

            if (playing) {
                raceTime += (delta * speed) / duration;
                if (raceTime >= 1) {
                    raceTime = 1;
                    playing = false;
                    finished = true;
                    callAudio('finish');
                    // Hold the picture at the post for a beat before the page
                    // is told who won, so the finish is watched rather than
                    // read. Reduced motion gets the result immediately.
                    if (!reducedMotion) holdUntil = now + FINISH_HOLD_MS;
                }
            }
            render(delta);

            /* The freeze. Not a second clock: raceTime is already pinned at 1
             * and nothing moves, the loop simply stays awake long enough to
             * fade the flash out. A hidden tab gets no frames anyway, so it
             * drops the hold and announces rather than stalling on the result
             * until somebody comes back to the tab. */
            var holding = holdUntil > now && !document.hidden;
            if (holdUntil) {
                var left = holding ? (holdUntil - now) / FINISH_HOLD_MS : 0;
                finishFlash.setAttribute('opacity', (left * left).toFixed(3));
                if (!holding) holdUntil = 0;
            }

            // Announce the result once per running of the race, not once per
            // frame — and reset the flag on replay so a rerun announces again.
            if (finished && !finishAnnounced && !holding) {
                finishAnnounced = true;
                if (options.onFinish) options.onFinish(runners);
            }
            if (playing || holding) {
                requestFrame();
            } else {
                // Nothing is moving any more, so nothing needs a next frame.
                lastFrame = 0;
                if (options.onPlayState) options.onPlayState(false);
            }
        }

        function render(delta) {
            var live = [];
            // How far into the run home the field is: 0 for the whole first two
            // thirds of the race, then 0..1. The art layer takes it as the one
            // signal for how hard a horse is being asked — sweat, ears, the
            // rider's seat and the whip all hang off it.
            var sprintU = raceTime > T_SPRINT
                ? clamp((raceTime - T_SPRINT) / (1 - T_SPRINT), 0, 1) : 0;
            var trailShow = !reducedMotion && sprintU > 0;
            var trailFade = smoothstep(0, 0.18, sprintU);
            var leadProgress = -1, leadX = null, leadY = 0;

            for (var i = 0; i < entries.length; i++) {
                var entry = entries[i];
                var progress = progressAt(entry, raceTime);
                // Belt and braces on top of PCHIP: a horse never goes backwards.
                if (progress < entry.lastProgress) progress = entry.lastProgress;
                var travelled = progress - entry.lastProgress;
                entry.lastProgress = progress;

                var point = samplePoint(table, progress,
                    laneOffsetAt(entry, raceTime) + drift(entry, raceTime), laneSign);
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
                // Drawn position only. See entry.zJitter.
                var drawY = point.y + entry.zJitter;

                // Anchor mid-body, low, so the horse straddles its lane line.
                var pose = 'rotate(' + lean.toFixed(2) + ') ' +
                    'scale(' + (scale * squash).toFixed(4) + ',' + scale.toFixed(4) + ') ' +
                    'translate(-50,-44)';
                entry.holder.setAttribute('transform',
                    'translate(' + point.x.toFixed(2) + ',' + drawY.toFixed(2) + ') ' + pose);

                // The shadow lies on the ground: it takes the turn's
                // foreshortening and only a fraction of the lean, so it stays
                // put under the horse instead of tipping with it.
                entry.shadow.setAttribute('transform',
                    'translate(' + point.x.toFixed(2) + ',' + (drawY + horseSize * 0.17).toFixed(2) + ') ' +
                    'rotate(' + (lean * 0.35).toFixed(2) + ') ' +
                    'scale(' + Math.max(0.4, Math.abs(squash)).toFixed(3) + ',1)');

                // Trails, only down the straight, laid backwards along the
                // track's own tangent so they follow the line the horse ran
                // rather than pointing off it through the turn.
                for (var t = 0; t < entry.trails.length; t++) {
                    var trail = entry.trails[t];
                    if (!trailShow) {
                        if (trail.getAttribute('display') !== 'none') trail.setAttribute('display', 'none');
                        continue;
                    }
                    // The tangent already points the way this runner is
                    // travelling, so backwards is simply minus it — no need to
                    // know which straight the horse is on.
                    var back = TRAIL_SPACING[t] * horseSize;
                    trail.setAttribute('display', 'inline');
                    trail.setAttribute('opacity', (TRAIL_OPACITY[t] * trailFade).toFixed(3));
                    trail.setAttribute('transform',
                        'translate(' + (point.x - Math.cos(point.angle) * back).toFixed(2) +
                        ',' + (drawY - Math.sin(point.angle) * back).toFixed(2) + ') ' + pose);
                }

                // Legs cycle at the speed the horse is actually travelling, so
                // a fading runner visibly shortens stride while a finisher
                // winds up. One stride covers roughly two body lengths.
                if (delta > 0) {
                    var pixels = travelled * table.raceLength;
                    entry.gait = (entry.gait + pixels / (horseSize * 1.15)) % 1;
                }
                // Phase before stride: the stride reads the effort the phase
                // has just set, so setting it the other way round would leave
                // the whip and the nostrils a frame behind the race.
                entry.art.setPhase(raceTime, sprintU, lean);
                entry.art.setGait(entry.gait);

                var chipY = drawY - horseSize * 0.46 - (entry.settleLane % 3) * chipRowHeight;
                entry.chip.setAttribute('transform',
                    'translate(' + point.x.toFixed(2) + ',' + Math.max(12, chipY).toFixed(2) + ')');

                if (progress > leadProgress) {
                    leadProgress = progress;
                    leadX = point.x;
                    leadY = drawY;
                }
                live.push({ entry: entry, progress: progress, y: drawY });
            }

            // ── The picture around the runners ────────────────────────────
            setSoftened(raceTime > 0);
            cameraGoal = cameraTarget(leadX, leadY);
            applyCamera(cameraGoal, raceTime === 0);

            if (audio) {
                // How much of the field is still in touch with the leader. A
                // strung-out procession and a wall of horses coming to the line
                // should not sound the same, and the depth-sorted positions the
                // renderer has just worked out are already the answer.
                var bunched = 0;
                for (var b = 0; b < live.length; b++) {
                    if (leadProgress - live[b].progress <= bodyProgress * BUNCH_LENGTHS) bunched++;
                }
                callAudio('update', raceTime, bunched / Math.max(1, fieldSize));
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

        // Every listener added here is remembered so destroy() can take it off
        // again. The background click lives on the <svg>, which OUTLIVES this
        // engine — the page keeps one canvas and rebuilds the race into it — so
        // a listener left behind would stack up one per weight change, each
        // holding a dead race alive.
        var teardown = [];
        function listen(node, type, handler, capture) {
            node.addEventListener(type, handler, capture);
            teardown.push(function () { node.removeEventListener(type, handler, capture); });
        }

        entries.forEach(function (entry) {
            listen(entry.holder, 'click', function (event) {
                event.stopPropagation();
                selectedId = selectedId === entry.runner.horse_id ? null : entry.runner.horse_id;
                applySelection();
                if (options.onSelect) options.onSelect(selectedId ? entry.runner : null);
            });
        });

        listen(svg, 'click', function () {
            if (selectedId != null) {
                selectedId = null;
                applySelection();
                if (options.onSelect) options.onSelect(null);
            }
        });

        // A race running in a tab nobody is looking at is pure waste, and the
        // browser throttles the frames anyway, which makes the replay stutter
        // when the tab comes back. Pause instead.
        listen(document, 'visibilitychange', function () {
            if (document.hidden && playing) setPlaying(false);
        });

        render(0);

        return {
            play: function () { if (!finished) setPlaying(true); },
            pause: function () { setPlaying(false); },
            toggle: function () {
                if (finished) { this.replay(); return true; }
                setPlaying(!playing);
                return playing;
            },
            isPlaying: function () { return playing; },
            isFinished: function () { return finished; },
            replay: function () {
                raceTime = 0; finished = false; finishAnnounced = false;
                entries.forEach(function (entry) { entry.lastProgress = 0; });
                lastDepthKey = '';
                resetPresentation();
                render(0);
                setPlaying(true);
            },
            scrubTo: function (t) {
                setPlaying(false);
                raceTime = clamp(t, 0, 1);
                finished = raceTime >= 1;
                finishAnnounced = finished;
                // The monotone clamp is relative to the last frame, so it has
                // to be released before jumping backwards through the race.
                entries.forEach(function (entry) { entry.lastProgress = 0; });
                // Scrubbing is not watching: land on the frame asked for with
                // the camera already where that moment of the race puts it,
                // rather than easing towards it over the next second.
                resetPresentation();
                render(0);
                applyCamera(cameraGoal, true);
            },
            setSpeed: function (value) { speed = clamp(value, 0.25, 4); },
            getTime: function () { return raceTime; },
            getDuration: function () { return duration; },
            setNamesVisible: function (visible) {
                showNames = !!visible;
                applySelection();
            },
            setResultsVisible: function (visible) {
                showResults = !!visible;
                entries.forEach(setChipText);
                measureChips();
            },
            /* Repaint every runner's silk once the live artwork has arrived.
             * The payload no longer waits on the bookmaker, so the race is
             * already on screen in its coded colours when this lands. */
            applySilks: function (byHorseId) {
                entries.forEach(function (entry) {
                    var silk = byHorseId[entry.runner.horse_id];
                    if (!silk) return;
                    var merged = {};
                    var key;
                    for (key in (entry.runner.silk || {})) merged[key] = entry.runner.silk[key];
                    for (key in silk) merged[key] = silk[key];
                    entry.runner.silk = merged;
                    if (entry.art.setSilk) entry.art.setSilk(merged);
                });
                render(0);
            },
            select: function (horseId) {
                selectedId = horseId;
                applySelection();
            },
            destroy: function () {
                destroyed = true;
                stopLoop();
                callAudio('stop');
                teardown.forEach(function (off) { off(); });
                teardown.length = 0;
                entries.forEach(function (entry) { entry.art.destroy(); });
                while (svg.firstChild) svg.removeChild(svg.firstChild);
                // Hand the canvas back the way it was found. The page keeps one
                // <svg> and rebuilds into it, so a viewBox left zoomed on the
                // last finish would be the frame the next race started in.
                svg.setAttribute('viewBox', '0 0 ' + VIEW.width + ' ' + VIEW.height);
            }
        };
    }

    global.RaceAnimation = {
        create: create,
        pchip: pchip,
        packCurve: packCurve,
        progressAt: progressAt,
        fadeAt: fadeAt,
        settleOffsets: settleOffsets,
        laneOffsetAt: laneOffsetAt,
        finishGapsPx: finishGapsPx,
        horseSizeForField: horseSizeForField,
        lanePathData: lanePathData,
        prefersReducedMotion: prefersReducedMotion,
        VIEW: VIEW,
        T_GATE: T_GATE,
        T_SPRINT: T_SPRINT,
        T_LANE_START: T_LANE_START,
        T_LANE_END: T_LANE_END,
        GATE_STAGGER: GATE_STAGGER,
        BODY_OF_ICON: BODY_OF_ICON,
        FINISH_HOLD_MS: FINISH_HOLD_MS,
        CAMERA_TIGHTEST: CAMERA_TIGHTEST,
        PACE_LANE_ORDER: PACE_LANE_ORDER,
        SPRINT_EXPONENT: SPRINT_EXPONENT,
        FADE_ROLE: FADE_ROLE
    };
}(typeof window !== 'undefined' ? window : this));
