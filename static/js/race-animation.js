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
 * MOVEMENT
 * Each runner has a progress curve p(t), 0 at the barriers and ~1 at the post,
 * built as a monotone cubic (PCHIP) through four control points:
 *
 *   t=0.00  p=0            the barriers
 *   t=0.20  p=break        set by the speed map pace category — leaders clear
 *   t=0.62  p=midrace      order roughly holds, composite starts to leak in
 *   t=1.00  p=finish       set by composite rank and the score gaps
 *
 * PCHIP is the point here: it is monotone by construction, so no runner ever
 * slides backwards between control points however aggressive the run home is.
 * The last control point is the composite-score finish position, so the finish
 * order always matches the ranking from the API — the drama in between is
 * shaped by the first two control points, never by fudging the result.
 *
 * The three phases are deliberately mapped onto the three sections of track.
 * The race is three-quarters of a lap — it starts at the top of the back
 * straight, not at the post — so the break plays out along the back straight
 * where it can be seen, the midrace covers the turn, and the run home is the
 * whole of the home straight nearest the viewer. p≈0.645 at t=0.62 is exactly
 * the top of the home straight.
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
    var T_BREAK = 0.20;      // barriers -> settled
    var T_MIDRACE = 0.62;    // settled -> top of the home straight

    // How far up the track each pace category is when the field settles.
    // A leader is clear of a backmarker by ~7% of a lap out of the gates.
    var PACE_BREAK = { leader: 0.205, onpace: 0.184, midfield: 0.158, back: 0.134 };

    // At the top of the straight the order still mostly reflects the break, but
    // the composite has begun to tell — this is the 'minor jostling' phase, not
    // a reshuffle, so the mix leans hard on the break. The spread matters as
    // much as the mix: keep the field too tight here and the run home has no
    // ground for a closer to make up, so the surge reads as a drift instead of
    // a finish. 0.175 of a lap is roughly the eight-lengths-off-them a genuine
    // backmarker gives the leaders turning in.
    var MID_BASE = 0.685;
    var MID_SPREAD = 0.175;
    var MID_FROM_BREAK = 0.80;
    var MID_FROM_SCORE = 0.20;

    // One length as a fraction of the lap. A 20-length spread across the field
    // then covers ~4% of the circuit, which reads as a strung-out finish
    // without pushing the tail of the field back onto the turn.
    var LENGTH_FRACTION = 0.0021;

    function clamp(value, low, high) { return value < low ? low : (value > high ? high : value); }

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
    // the whole oval into a green blob.
    var TRACK_BAND = 150;

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

    // ── Progress curves ───────────────────────────────────────────────────
    /* Build one runner's p(t) from its pace category and composite rank.
     *
     * `spread01` is the runner's composite score placed on 0..1 against the
     * field (1 = the top-rated horse), so a race where everything is rated
     * within a point of everything else produces a blanket finish rather than
     * an artificial procession.
     */
    function buildProgressCurve(runner, spread01, fieldSize, seed) {
        var pace = PACE_BREAK[runner.pace_category] != null ? runner.pace_category : 'midfield';

        // Break: pace category sets it, with a small deterministic wobble so
        // two leaders out of adjacent barriers do not move as one object.
        var wobble = (Math.sin(seed * 12.9898) * 43758.5453) % 1;
        wobble = (wobble - Math.floor(wobble)) - 0.5;
        var breakProgress = PACE_BREAK[pace] + wobble * 0.018;

        // Settled: mostly still the break order, with the composite leaking in.
        var breakRank01 = (breakProgress - PACE_BREAK.back) / (PACE_BREAK.leader - PACE_BREAK.back);
        var midMix = MID_FROM_BREAK * breakRank01 + MID_FROM_SCORE * spread01;
        var midProgress = MID_BASE + (midMix - 0.5) * MID_SPREAD + wobble * 0.012;

        // Finish: locked to the composite rank via the beaten margin the API
        // derived from the score gaps. This is the only control point that
        // decides the result.
        var margin = runner.beaten_margin || 0;
        var finishProgress = 1 - margin * LENGTH_FRACTION;

        // Keep the control points strictly increasing so PCHIP stays sane even
        // if a huge field pushes the margins further than expected.
        midProgress = Math.max(midProgress, breakProgress + 0.05);
        finishProgress = Math.max(finishProgress, midProgress + 0.04);

        var curve = pchip([0, T_BREAK, T_MIDRACE, 1], [0, breakProgress, midProgress, finishProgress]);
        return {
            at: curve,
            breakProgress: breakProgress,
            midProgress: midProgress,
            finishProgress: finishProgress
        };
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

        // Lane spacing and horse size both fall away as the field grows, which
        // is what keeps a 24-runner race on one screen without scrolling.
        var laneGap = Math.min(18, TRACK_BAND / (fieldSize + 1));
        var horseSize = clamp(laneGap * 4.4, 40, 78);
        var outerRadius = VIEW.outerRy;
        var innerRadius = outerRadius - TRACK_BAND;
        // Centre the lanes in the band so a small field runs down the middle of
        // the track instead of hugging the rail with dead turf outside it.
        var laneBase = innerRadius + (TRACK_BAND - (fieldSize - 1) * laneGap) / 2;
        var midLane = (fieldSize - 1) / 2;
        var referenceRadius = laneBase + midLane * laneGap;

        buildTrack(svg, innerRadius, outerRadius);

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

        // Composite spread, for shaping how much the run home reshuffles.
        var scores = runners.map(function (r) { return r.composite_score || 0; });
        var bestScore = Math.max.apply(null, scores);
        var worstScore = Math.min.apply(null, scores);
        var scoreSpan = Math.max(1e-6, bestScore - worstScore);

        var showNames = options.showNames != null ? options.showNames : fieldSize <= 11;
        // Chips shrink with the horses, and are staggered over three rows so
        // adjacent barriers do not stack their labels on top of each other when
        // the field bunches up.
        var chipScale = clamp(horseSize / 88, 0.42, 0.85);
        var chipRowHeight = 15 * chipScale;
        var entries = [];

        runners.forEach(function (runner, index) {
            var lane = runner.lane != null ? runner.lane : index;
            // Positive offsets move towards the rail, so barrier 1 sits inside.
            var laneOffset = (midLane - lane) * laneGap;
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

            var spread01 = (runner.composite_score - worstScore) / scoreSpan;
            entries.push({
                runner: runner,
                lane: lane,
                laneOffset: laneOffset,
                art: art,
                holder: holder,
                chip: chip,
                chipBg: chipBg,
                curve: buildProgressCurve(runner, spread01, fieldSize, lane + 1 + runner.horse_id * 0.37),
                gait: Math.random(),
                lastProgress: 0,
                jostleSeed: (lane * 1.7) + (runner.horse_id % 7)
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

        /* Lateral drift. Horses do not run down a painted line: they shift
         * across each other for a run, most of it through the middle stages,
         * and straighten up under pressure in the last furlong. Doing this
         * sideways rather than by nudging progress keeps the finish order — and
         * PCHIP's monotonicity — completely untouched. */
        function jostle(entry, t) {
            var settle = t < 0.06 ? t / 0.06 : 1;                    // none in the gates
            var taper = t > 0.82 ? Math.max(0, 1 - (t - 0.82) / 0.18) : 1;
            var wave = Math.sin(t * 5.3 + entry.jostleSeed) * 0.62
                     + Math.sin(t * 2.1 + entry.jostleSeed * 2.3) * 0.38;
            return wave * laneGap * 0.42 * settle * taper;
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
                var progress = entry.curve.at(raceTime);
                // Belt and braces on top of PCHIP: a horse never goes backwards.
                if (progress < entry.lastProgress) progress = entry.lastProgress;
                var travelled = progress - entry.lastProgress;
                entry.lastProgress = progress;

                var point = samplePoint(table, progress, entry.laneOffset + jostle(entry, raceTime));
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

                var chipY = point.y - horseSize * 0.46 - (entry.lane % 3) * chipRowHeight;
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
        buildProgressCurve: buildProgressCurve,
        lanePathData: lanePathData,
        VIEW: VIEW,
        T_BREAK: T_BREAK,
        T_MIDRACE: T_MIDRACE,
        PACE_BREAK: PACE_BREAK
    };
}(typeof window !== 'undefined' ? window : this));
