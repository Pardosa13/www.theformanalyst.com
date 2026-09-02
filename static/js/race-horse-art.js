/*
 * race-horse-art.js — code-drawn horse + jockey for the Race Animations page.
 *
 * Nothing on the site depends on this file; it is only loaded by
 * templates/race-animations-predictions.html.
 *
 * WHY THIS IS DRAWN IN CODE
 * Every runner needs its own silk colours, its own silk pattern and its own
 * saddlecloth number, and those change from race to race. A fixed image cannot
 * do that, so the horse is built once as an articulated vector rig and then
 * recoloured per runner from the race payload.
 *
 * THE RIG
 * One right-facing side-view horse in a 0 0 100 62 viewBox, ground line at
 * y = 58. The body, neck and tail are static paths. Each leg is two hinged
 * segments — upper rotates about the hip/shoulder, lower rotates about the
 * hock/knee and hangs off the end of the upper — so a gallop is a matter of
 * driving eight angles rather than swapping eight full drawings.
 *
 * THE GAIT
 * GAIT_FORE / GAIT_HIND below are four hand-set keyframes of a gallop cycle
 * (gather, reach, plant, drive). setGait(phase) samples them with a smoothstep
 * between adjacent frames, so the four authored poses give a continuous cycle
 * instead of a four-frame flicker. The near-side pair leads the off-side pair
 * slightly, and the hindquarters lead the forequarters, which is what makes it
 * read as a gallop rather than a trot.
 *
 * SILKS
 * Two sources, in priority order:
 *   1. The Ladbrokes silk sprite the rest of the site already uses — the same
 *      strip URL and 32px-per-runner tile offset as buildSilkStyle() in
 *      view_meeting.html — cropped to the jockey's body with a nested <svg>
 *      viewBox and clipped to the torso outline.
 *   2. A coded pattern (solid / stripes / hoops / halved / quartered / spots /
 *      sash / chevron) in the runner's fallback colours, drawn underneath the
 *      sprite so there is always something visible if the sprite 404s.
 */
(function (global) {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var XLINK_NS = 'http://www.w3.org/1999/xlink';

    var VIEWBOX = { width: 100, height: 66, ground: 60 };

    // Joint anchors in viewBox units. Proportioned off a thoroughbred rather
    // than guessed: girth depth (withers y=14 to belly y=34) is a shade under
    // the elbow-to-ground drop (30), and the barrel is about 2.4 girths long,
    // which is what stops the horse reading as a barrel on stumps.
    var HIP = { x: 33, y: 30 };        // hind leg pivot (stifle)
    var SHOULDER = { x: 62, y: 30 };   // fore leg pivot (elbow)
    var UPPER_LEN = 15;                // hip -> hock, shoulder -> knee
    var LANE_SPREAD = 3.2;             // near/off leg separation, for depth

    var uid = 0;

    // ── Gait keyframes ────────────────────────────────────────────────────
    // [upperAngle, lowerAngle] in degrees. Positive swings the limb backwards
    // (a point below the pivot rotates towards -x), negative reaches forward.
    var GAIT_FORE = [
        [-28, -62],   // 0 gather: folded up under the chest
        [-36, -14],   // 1 reach:  unfolding out in front
        [6, -2],      // 2 plant:  vertical, taking the weight
        [34, 6]       // 3 drive:  trailing behind, pushing off
    ];
    var GAIT_HIND = [
        [38, -16],    // 0 trail:  extended out behind
        [8, -46],     // 1 fold:   coming under the body, hock closed
        [-24, -26],   // 2 reach:  planted well forward under the barrel
        [12, -2]      // 3 drive:  straightening out behind
    ];

    var NEAR_LEAD = 0.13;   // near-side legs lead the off-side pair
    var HIND_LEAD = 0.42;   // hindquarters lead the forequarters

    function smoothstep(t) { return t * t * (3 - 2 * t); }

    function sampleCycle(frames, phase) {
        var n = frames.length;
        var scaled = (((phase % 1) + 1) % 1) * n;
        var i = Math.floor(scaled);
        var blend = smoothstep(scaled - i);
        var a = frames[i % n];
        var b = frames[(i + 1) % n];
        return [a[0] + (b[0] - a[0]) * blend, a[1] + (b[1] - a[1]) * blend];
    }

    // ── Small DOM helpers ─────────────────────────────────────────────────
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

    /* Pick black or white text for a given background so the saddlecloth
     * number stays readable on both a pale yellow and a navy silk. */
    function readableInk(hex) {
        var value = String(hex || '').replace('#', '');
        if (value.length === 3) {
            value = value[0] + value[0] + value[1] + value[1] + value[2] + value[2];
        }
        if (value.length !== 6) return '#ffffff';
        var r = parseInt(value.slice(0, 2), 16);
        var g = parseInt(value.slice(2, 4), 16);
        var b = parseInt(value.slice(4, 6), 16);
        // Rec. 709 luma; 0.6 is where mid-tones stop carrying white text.
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.6 ? '#12121a' : '#ffffff';
    }

    function toRgb(hex) {
        var value = String(hex || '#888888').replace('#', '');
        if (value.length === 3) {
            value = value[0] + value[0] + value[1] + value[1] + value[2] + value[2];
        }
        if (value.length !== 6) return [136, 136, 136];
        return [parseInt(value.slice(0, 2), 16), parseInt(value.slice(2, 4), 16), parseInt(value.slice(4, 6), 16)];
    }

    /* Blend two colours. Used for the off-side legs: pulling the coat colour
     * partway towards a neutral slate darkens a grey and lifts a black, so the
     * far legs always separate from the body whatever the horse's colour is —
     * simply darkening them loses the legs entirely on a black horse. */
    function mix(hexA, hexB, t) {
        var a = toRgb(hexA), b = toRgb(hexB), out = '#';
        for (var i = 0; i < 3; i++) {
            var channel = Math.round(a[i] + (b[i] - a[i]) * t);
            out += ('0' + Math.min(255, Math.max(0, channel)).toString(16)).slice(-2);
        }
        return out;
    }

    function shade(hex, amount) {
        var value = String(hex || '#888888').replace('#', '');
        if (value.length === 3) {
            value = value[0] + value[0] + value[1] + value[1] + value[2] + value[2];
        }
        if (value.length !== 6) return hex;
        var out = '#';
        for (var i = 0; i < 3; i++) {
            var channel = parseInt(value.substr(i * 2, 2), 16);
            channel = Math.round(Math.min(255, Math.max(0, channel + amount * 255)));
            out += ('0' + channel.toString(16)).slice(-2);
        }
        return out;
    }

    // ── Coat colours ──────────────────────────────────────────────────────
    // Purely cosmetic variety so a field is not twenty identical brown horses.
    // Silk colour, not coat colour, is what identifies a runner.
    var COATS = [
        { body: '#5b3a22', shade: '#41291630', mane: '#241408', sock: '#efe6d8' }, // bay
        { body: '#7d4520', shade: '#5c301230', mane: '#4a2409', sock: '#efe6d8' }, // chestnut
        { body: '#8d8d97', shade: '#66666f30', mane: '#d8d8de', sock: '#f4f4f8' }, // grey
        { body: '#332e2c', shade: '#1c191830', mane: '#151212', sock: '#e8e2d8' }, // black
        { body: '#6b4a30', shade: '#4b331e30', mane: '#33200f', sock: '#efe6d8' }, // brown
        { body: '#a4795a', shade: '#7c563a30', mane: '#e9d7c2', sock: '#f4ece0' }  // dun
    ];

    function hashString(text) {
        var digest = 0;
        var value = String(text || '');
        for (var i = 0; i < value.length; i++) {
            digest = (digest * 131 + value.charCodeAt(i)) >>> 0;
        }
        return digest;
    }

    // ── Silk pattern fills ────────────────────────────────────────────────
    /* Builds the paint for the jockey's body/cap. Returns a fill string; any
     * <pattern>/<linearGradient> it needs is appended to `defs`. */
    function buildSilkPaint(defs, id, silk, kind) {
        var primary = (silk && silk.primary) || '#cccccc';
        var secondary = (silk && silk.secondary) || '#ffffff';
        var pattern = (silk && silk.pattern) || 'solid';
        var patternId = id + '-' + kind + '-fill';

        // The cap is small; patterning it just turns to mush at race scale, so
        // it stays a flat colour and only the body carries the pattern.
        if (kind === 'cap' || pattern === 'solid') {
            return kind === 'cap' ? ((silk && silk.cap) || primary) : primary;
        }

        if (pattern === 'halved' || pattern === 'quartered') {
            // Hard-stop gradients give clean straight divisions without needing
            // to know the exact shape of the torso outline.
            var gradient = el('linearGradient', {
                id: patternId,
                x1: pattern === 'halved' ? '0' : '0',
                y1: pattern === 'halved' ? '0' : '0',
                x2: pattern === 'halved' ? '1' : '1',
                y2: pattern === 'halved' ? '0' : '1'
            });
            append(gradient, el('stop', { offset: '0', 'stop-color': primary }));
            append(gradient, el('stop', { offset: '0.5', 'stop-color': primary }));
            append(gradient, el('stop', { offset: '0.5', 'stop-color': secondary }));
            append(gradient, el('stop', { offset: '1', 'stop-color': secondary }));
            append(defs, gradient);
            return 'url(#' + patternId + ')';
        }

        var tile = el('pattern', {
            id: patternId,
            patternUnits: 'userSpaceOnUse',
            width: 6,
            height: 6,
            patternTransform: pattern === 'chevron' ? 'rotate(45)' : (pattern === 'sash' ? 'rotate(-38)' : null)
        });
        append(tile, el('rect', { x: 0, y: 0, width: 6, height: 6, fill: primary }));

        if (pattern === 'stripes' || pattern === 'chevron') {
            append(tile, el('rect', { x: 0, y: 0, width: 3, height: 6, fill: secondary }));
        } else if (pattern === 'hoops') {
            append(tile, el('rect', { x: 0, y: 0, width: 6, height: 3, fill: secondary }));
        } else if (pattern === 'sash') {
            append(tile, el('rect', { x: 0, y: 0, width: 6, height: 2.6, fill: secondary }));
        } else if (pattern === 'spots') {
            append(tile, el('circle', { cx: 1.6, cy: 1.6, r: 1.25, fill: secondary }));
            append(tile, el('circle', { cx: 4.6, cy: 4.6, r: 1.25, fill: secondary }));
        }
        append(defs, tile);
        return 'url(#' + patternId + ')';
    }

    // ── Leg construction ──────────────────────────────────────────────────
    /* One leg = <g at pivot, rotates upper> ( upper path
     *              + <g translated to the joint, rotates lower> ( cannon + hoof ) )
     * Returns the two <g> nodes so setGait can drive them directly without
     * re-querying the DOM every frame. */
    function buildLeg(parent, pivot, isHind, coat, depth) {
        var upperGroup = append(parent, el('g', {
            transform: 'translate(' + pivot.x + ',' + pivot.y + ') rotate(0)'
        }));

        var body = depth === 'off' ? mix(shade(coat.body, -0.1), '#454552', 0.45) : coat.body;
        var hoofInk = depth === 'off' ? '#3a3a46' : '#22222a';

        append(upperGroup, el('path', {
            d: isHind
                // Gaskin: heavy muscle at the stifle, tapering into the hock.
                ? 'M -6,-6 C 1.5,-8.6 7,-3 6.8,3.6 L 3.4,15 L -3.2,15 C -5.8,8 -7,-1 -6,-6 Z'
                // Forearm: narrower, straighter.
                : 'M -4,-4.2 C 1.2,-5.6 5,-2 4.8,3 L 3,15 L -2.6,15 C -4,8 -4.8,1 -4,-4.2 Z',
            fill: body
        }));

        var lowerGroup = append(upperGroup, el('g', {
            transform: 'translate(0.2,' + UPPER_LEN + ') rotate(0)'
        }));
        // Cannon bone.
        append(lowerGroup, el('path', {
            d: 'M -1.9,0 L 1.9,0 L 1.4,12 L -1.4,12 Z',
            fill: body
        }));
        // Fetlock + hoof.
        append(lowerGroup, el('path', {
            d: 'M -2.1,11.6 C -0.5,11 0.7,11 2.2,11.6 L 2.5,15.4 L -2.4,15.4 Z',
            fill: hoofInk
        }));
        return { upper: upperGroup, lower: lowerGroup };
    }

    // ── Static outlines ───────────────────────────────────────────────────
    // Thoroughbred side view: high croup, dipped back, deep girth, tucked flank.
    // Point of buttock x=26, point of shoulder x=74, topline y~14, belly y~34.
    var BODY_PATH =
        'M 26,26 C 24,18 30,13.4 37,14 C 44,14 47,17 52,18 ' +
        'C 57,18.4 61,14.4 66,14.4 C 71,15.4 74,20 74.6,26 ' +
        'C 75,31 72,34.2 68,34.6 C 60,35.6 52,35 45,33.6 ' +
        'C 38,32.6 30,31 26,26 Z';

    // Neck sweeps up and forward off the withers; head drops to the muzzle.
    var NECK_PATH =
        'M 63,20 C 66,13 71,8 78,4.6 C 81,3 84.6,3 86.6,5.6 ' +
        'C 88.6,8 90.6,11 91.6,14 C 92.6,16.6 90.6,18.6 87.6,18 ' +
        'C 84.6,17.4 82.6,15 80.6,12.6 C 77,11.6 73,13.6 69.6,17.6 ' +
        'C 67,19.6 65,21.6 63,23 Z';

    var TAIL_PATH =
        'M 28,19 C 20,18.6 13,22 8,30 C 11.6,26.6 15,25.6 18,27 ' +
        'C 13.6,31 10,37 9.6,44 C 14,35.6 21,28 29,25.6 Z';

    // The jockey: seat on the saddle at x~46, shoulders driven forward to x~66.
    // This outline is the silk — it is what the pattern or sprite fills.
    var JOCKEY_TORSO_PATH =
        'M 47,21.6 C 46.6,15.4 50,10.4 55.4,7.8 C 59,6 62.8,5.6 64.8,7.4 ' +
        'C 66.6,9.2 66.4,12 64.4,13.8 C 60.4,15.2 56,17 52.4,19.2 ' +
        'C 50.4,20.6 48.4,21.6 47,21.6 Z';

    /* Build one horse. Returns a handle:
     *   .node            the root <g> — position/scale this from outside
     *   .setGait(phase)  advance the gallop cycle, phase in 0..1
     *   .setDimmed(on)   fade a runner back when another one is selected
     *   .setSilk(info)   drop real silk artwork on after the horse is drawn
     *   .destroy()       drop the node
     */
    function create(options) {
        options = options || {};
        var id = 'rha' + (++uid);
        var silk = options.silk || {};
        var fallback = silk.fallback || {};
        var number = options.number != null ? String(options.number) : '';
        var seed = hashString(options.seedName || options.name || id);
        var coat = COATS[seed % COATS.length];
        var useSprite = !!(silk.sprite_url && silk.runner_number);

        var root = el('g', { 'class': 'rha-horse', 'data-rha-id': id });
        var defs = append(root, el('defs'));
        var bob = append(root, el('g', { 'class': 'rha-bob' }));

        var bodyPaint = buildSilkPaint(defs, id, fallback, 'body');
        var capPaint = buildSilkPaint(defs, id, fallback, 'cap');
        var clothColour = fallback.primary || '#cccccc';
        var clothInk = readableInk(clothColour);

        // ── Off-side legs, drawn first so the body occludes them ──
        var offHind = buildLeg(bob, { x: HIP.x - LANE_SPREAD, y: HIP.y }, true, coat, 'off');
        var offFore = buildLeg(bob, { x: SHOULDER.x - LANE_SPREAD, y: SHOULDER.y }, false, coat, 'off');

        // ── Tail, body, neck ──
        var tail = append(bob, el('g', { transform: 'translate(28,19) rotate(0) translate(-28,-19)' }));
        append(tail, el('path', { d: TAIL_PATH, fill: coat.mane }));

        append(bob, el('path', { d: BODY_PATH, fill: coat.body }));
        // Girth shadow — one soft dark wash along the underline gives the flat
        // silhouette enough form to read at 30px without any real shading.
        append(bob, el('path', {
            d: 'M 32,29 C 42,34 56,36 69,33.6 C 63,36.6 44,36.6 32,29 Z',
            fill: coat.shade
        }));

        var neck = append(bob, el('g', { transform: 'translate(65,17) rotate(0) translate(-65,-17)' }));
        append(neck, el('path', { d: NECK_PATH, fill: coat.body }));
        append(neck, el('path', {                                   // mane along the crest
            d: 'M 64,19.4 C 67,12.6 72,7.6 79,4.2 C 80.6,3.4 82.2,3 83.6,3.4 ' +
               'C 79.6,6 73,10.6 68.6,16.6 C 67,18.4 65.4,19.6 64,19.4 Z',
            fill: coat.mane
        }));
        append(neck, el('path', { d: 'M 83.6,4 L 85.4,0 L 87.6,4.6 Z', fill: coat.mane }));  // ear
        append(neck, el('circle', { cx: 86.4, cy: 9.2, r: 1.2, fill: '#12121a' }));          // eye
        append(neck, el('path', {                                   // nostril
            d: 'M 89.4,15 C 90.8,15.2 91.6,16 91.2,17',
            stroke: '#12121a', 'stroke-width': 0.7, fill: 'none'
        }));
        append(neck, el('path', {                                   // blaze down the face
            d: 'M 84.6,7 C 87,9.4 89.2,12.2 90.6,15.2 L 88.6,15.8 C 87,12.8 85,10 82.8,8 Z',
            fill: coat.sock, opacity: 0.7
        }));

        // ── Saddlecloth: this is where the number goes, as it does in real life ──
        append(bob, el('path', {
            // Cut to sit on the topline rather than hover above it as a plain
            // rectangle would, and drape down over the barrel.
            d: 'M 34.6,15.4 C 39.4,14.2 44.6,16 48.6,17.4 L 47.8,28.6 ' +
               'C 43,29.8 38,28.8 33.6,26.6 Z',
            fill: clothColour, stroke: shade(clothColour, -0.34), 'stroke-width': 0.7
        }));
        var numberText = append(bob, el('text', {
            x: 41, y: 25.4, 'text-anchor': 'middle',
            'font-size': 9.4, 'font-weight': '800',
            'font-family': "'DM Mono', ui-monospace, monospace",
            fill: clothInk, 'class': 'rha-number'
        }));
        numberText.textContent = number;
        // Saddle flap, between the cloth and the rider.
        append(bob, el('path', {
            d: 'M 48,16.6 C 52.6,16.2 55.6,17 56.2,19.2 C 56.6,21.6 55,24.2 52.2,25 ' +
               'C 49.6,25.6 48,24 47.8,21 Z',
            fill: '#4a3524'
        }));

        // ── Jockey ──
        var jockey = append(bob, el('g', { 'class': 'rha-jockey' }));
        // Breeches and boot go down first so the torso overlaps them at the hip.
        append(jockey, el('path', {                                 // thigh -> knee
            d: 'M 48.8,18.8 C 52.8,18.2 56.2,18.8 58,20.6',
            stroke: '#eceff5', 'stroke-width': 4.8, 'stroke-linecap': 'round', fill: 'none'
        }));
        append(jockey, el('path', {                                 // knee -> ankle
            d: 'M 58,20.6 C 57.6,23.4 56,25.4 53.8,26.4',
            stroke: '#eceff5', 'stroke-width': 4.2, 'stroke-linecap': 'round', fill: 'none'
        }));
        append(jockey, el('path', {                                 // boot in the iron
            d: 'M 54.6,25.2 C 53.6,27.4 52.6,28.8 51.6,29.6',
            stroke: '#16161d', 'stroke-width': 3.6, 'stroke-linecap': 'round', fill: 'none'
        }));
        append(jockey, el('path', {                                 // stirrup leather + iron
            d: 'M 51.8,19.4 L 50.8,29.4 M 49.4,29.6 L 52.8,29.6',
            stroke: '#9aa0ae', 'stroke-width': 0.8, fill: 'none'
        }));

        // Torso — the silk. Coded pattern underneath, sprite over the top.
        append(jockey, el('path', { d: JOCKEY_TORSO_PATH, fill: bodyPaint }));

        /* Crop the runner's tile out of the silk sprite strip and clip it to
         * the torso outline.
         *
         * Broken out of the build so it can also run LATER: the race payload no
         * longer waits on the bookmaker for artwork, so a runner is drawn in
         * its coded colours first and the real silk is dropped on top if and
         * when the separate silks request answers. Calling this again replaces
         * whatever is there, so a second answer cannot stack two strips up. */
        var spriteFrame = null;
        function mountSprite(info) {
            if (spriteFrame && spriteFrame.parentNode) {
                spriteFrame.parentNode.removeChild(spriteFrame);
            }
            spriteFrame = null;
            if (!info || !info.sprite_url || !info.runner_number) return;

            if (!defs.querySelector('#' + id + '-silkclip')) {
                var clip = append(defs, el('clipPath', { id: id + '-silkclip' }));
                append(clip, el('path', { d: JOCKEY_TORSO_PATH }));
            }
            var tile = info.tile_px || 32;
            var frame = append(jockey, el('svg', {
                x: 46.4, y: 5.2, width: 20.6, height: 17,
                viewBox: ((info.runner_number - 1) * tile) + ' 0 ' + tile + ' ' + tile,
                preserveAspectRatio: 'none',
                'clip-path': 'url(#' + id + '-silkclip)'
            }));
            var image = append(frame, el('image', { x: 0, y: 0 }));
            image.setAttributeNS(XLINK_NS, 'xlink:href', info.sprite_url);
            image.setAttribute('href', info.sprite_url);
            // If the strip fails to load, the coded pattern under it is already
            // drawn, so just remove the empty frame rather than leaving a hole.
            image.addEventListener('error', function () {
                if (frame.parentNode) frame.parentNode.removeChild(frame);
                if (spriteFrame === frame) spriteFrame = null;
            });
            spriteFrame = frame;
        }
        if (useSprite) mountSprite(silk);
        append(jockey, el('path', {                                 // arm reaching to the reins
            d: 'M 63.4,11.2 C 67.6,12.2 71.6,13.8 74.6,15.8',
            stroke: shade(fallback.primary || '#cccccc', -0.12),
            'stroke-width': 3.4, 'stroke-linecap': 'round', fill: 'none'
        }));
        append(jockey, el('path', {                                 // reins to the bit
            d: 'M 75,16 C 79,16.2 83.4,16.2 87.4,16.2',
            stroke: '#2b2b36', 'stroke-width': 0.8, fill: 'none'
        }));
        append(jockey, el('circle', { cx: 74.8, cy: 16, r: 1.8, fill: '#16161d' }));    // glove
        append(jockey, el('path', {                                 // face below the peak
            d: 'M 67.6,9.6 C 70,9.2 71.6,10.2 71.8,11.8 C 72,13.4 70.2,14.2 68.6,13.6 Z',
            fill: '#d8a882'
        }));
        append(jockey, el('circle', { cx: 66.4, cy: 7.6, r: 4.1, fill: capPaint }));    // helmet
        append(jockey, el('path', {                                 // helmet peak
            d: 'M 69.2,4.8 C 72,4.6 74,5.8 74.2,7.4 L 70,8.6 Z',
            fill: shade((fallback.cap || fallback.primary || '#cccccc'), -0.22)
        }));
        append(jockey, el('rect', {                                 // goggles
            x: 69, y: 8.8, width: 3.4, height: 2.1, rx: 1, fill: '#2b2b36'
        }));

        // ── Near-side legs, in front of everything ──
        var nearHind = buildLeg(bob, { x: HIP.x + LANE_SPREAD, y: HIP.y }, true, coat, 'near');
        var nearFore = buildLeg(bob, { x: SHOULDER.x + LANE_SPREAD, y: SHOULDER.y }, false, coat, 'near');

        var legs = [
            { rig: offHind, frames: GAIT_HIND, offset: HIND_LEAD + NEAR_LEAD },
            { rig: nearHind, frames: GAIT_HIND, offset: HIND_LEAD },
            { rig: offFore, frames: GAIT_FORE, offset: NEAR_LEAD },
            { rig: nearFore, frames: GAIT_FORE, offset: 0 }
        ];
        var pivots = [
            { x: HIP.x - LANE_SPREAD, y: HIP.y },
            { x: HIP.x + LANE_SPREAD, y: HIP.y },
            { x: SHOULDER.x - LANE_SPREAD, y: SHOULDER.y },
            { x: SHOULDER.x + LANE_SPREAD, y: SHOULDER.y }
        ];

        var handle = {
            node: root,
            id: id,
            coat: coat,

            /* phase 0..1 through one gallop stride. Also drives the body bob,
             * the neck reach and the tail sway, which all run off the same
             * cycle so the whole animal moves as one thing. */
            setGait: function (phase) {
                for (var i = 0; i < legs.length; i++) {
                    var leg = legs[i];
                    var angles = sampleCycle(leg.frames, phase + leg.offset);
                    leg.rig.upper.setAttribute('transform',
                        'translate(' + pivots[i].x + ',' + pivots[i].y + ') rotate(' + angles[0].toFixed(2) + ')');
                    leg.rig.lower.setAttribute('transform',
                        'translate(0.2,' + UPPER_LEN + ') rotate(' + angles[1].toFixed(2) + ')');
                }
                var cycle = phase * Math.PI * 2;
                // Two bounces per stride: the gallop's two suspension moments.
                var lift = Math.sin(cycle * 2) * 1.5;
                bob.setAttribute('transform', 'translate(0,' + lift.toFixed(2) + ')');
                // The head and neck reach forward as the forelegs extend.
                neck.setAttribute('transform',
                    'translate(65,17) rotate(' + (Math.sin(cycle + 0.6) * 4.5).toFixed(2) + ') translate(-65,-17)');
                tail.setAttribute('transform',
                    'translate(28,19) rotate(' + (Math.sin(cycle * 0.5 + 1.2) * 7).toFixed(2) + ') translate(-28,-19)');
            },

            setDimmed: function (dimmed) {
                root.setAttribute('opacity', dimmed ? '0.28' : '1');
            },

            setNumberVisible: function (visible) {
                numberText.setAttribute('display', visible ? 'inline' : 'none');
            },

            /* Swap in real silk artwork after the fact. See mountSprite(). */
            setSilk: function (info) {
                mountSprite(info);
            },

            destroy: function () {
                if (root.parentNode) root.parentNode.removeChild(root);
            }
        };

        handle.setGait(0);
        return handle;
    }

    global.RaceHorseArt = {
        create: create,
        VIEWBOX: VIEWBOX,
        COATS: COATS,
        readableInk: readableInk,
        shade: shade,
        mix: mix,
        hashString: hashString,
        GAIT_FORE: GAIT_FORE,
        GAIT_HIND: GAIT_HIND
    };
}(typeof window !== 'undefined' ? window : this));
