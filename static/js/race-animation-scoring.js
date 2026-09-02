/*
 * race-animation-scoring.js — the browser's half of the composite maths.
 *
 * WHY THIS FILE EXISTS
 * The page re-blends the composite locally so the weight sliders respond
 * instantly instead of waiting on a round trip. That copy of the arithmetic
 * used to live inline in the template with its own hardcoded weights and
 * margin constants, which meant a change in race_animation_scoring.py silently
 * made the browser disagree with the server about who won.
 *
 * Now there are no hardcoded numbers here at all. Every constant is read from
 * the config the server injects (race_animation_scoring.scoring_constants()),
 * and tests/test_race_animation_parity.py runs this file in Node and the Python
 * module over the same fixture, and fails if they disagree by more than
 * rounding.
 *
 * WHAT IT DOES NOT DO
 * Normalisation. Turning a raw value into "where this runner sits in the field"
 * happens once on the server and is sent down with the payload, because it does
 * not depend on the weights — so re-blending never needs to redo it. That is
 * also what makes the sliders instant.
 *
 * Loads in a browser as window.RaceAnimationScoring, and in Node via require().
 */
(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.RaceAnimationScoring = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    /* Every function here takes the server's constants explicitly rather than
     * reading a global, so the parity test can drive it with the exact config
     * the Python side is using. */

    function keysOf(config) {
        return (config && config.component_keys) || [];
    }

    function num(value) {
        var parsed = Number(value);
        return isFinite(parsed) ? parsed : 0;
    }

    /* Slider percentages -> fractions summing to exactly 1.
     *
     * Port of resolve_weights(). Rescaling is what lets the sliders be dragged
     * freely without the composite drifting off the 0-100 scale the table and
     * the default blend are read on: only the proportions between the
     * components survive, so 60/20/20/20 and 30/10/10/10 are the same blend.
     *
     * A request with nothing in it carries no ordering information, so the
     * published default is handed back instead of a flat field. */
    function resolveWeights(percentages, config) {
        var keys = keysOf(config);
        var out = {};
        var total = 0;
        var i, key;

        for (i = 0; i < keys.length; i++) {
            var value = Math.max(0, num((percentages || {})[keys[i]]));
            out[keys[i]] = value;
            total += value;
        }

        if (total <= 1e-9) {
            var defaults = (config && config.default_weights) || {};
            var defaultTotal = 0;
            for (i = 0; i < keys.length; i++) defaultTotal += Math.max(0, num(defaults[keys[i]]));
            if (defaultTotal <= 1e-9) {
                for (i = 0; i < keys.length; i++) out[keys[i]] = 1 / keys.length;
                return out;
            }
            for (i = 0; i < keys.length; i++) {
                out[keys[i]] = Math.max(0, num(defaults[keys[i]])) / defaultTotal;
            }
            return out;
        }

        for (i = 0; i < keys.length; i++) {
            key = keys[i];
            out[key] = out[key] / total;
        }
        return out;
    }

    /* Re-blend every runner under a weighting. Mutates in place, the way the
     * Python side does, and returns the same array.
     *
     * The normalised values came off the server and are never touched here —
     * reweighting only ever changes how they are combined. */
    function applyWeights(runners, weights, config) {
        var keys = keysOf(config);
        (runners || []).forEach(function (runner) {
            var components = runner.components || {};
            var composite = 0;
            var availableWeighted = 0;
            var weighted = 0;
            keys.forEach(function (key) {
                var component = components[key];
                if (!component) return;
                var weight = num(weights[key]);
                var contribution = num(component.normalised) * weight;
                component.weight = weight;
                component.weight_pct = Math.round(weight * 1000) / 10;
                component.weighted = Math.round(contribution * 100) / 100;
                composite += contribution;
                if (weight > 0) {
                    weighted += 1;
                    if (component.available) availableWeighted += 1;
                }
            });
            runner.composite_score = Math.round(composite * 100) / 100;
            runner.components_available = availableWeighted;
            runner.components_weighted = weighted;
        });
        return runners;
    }

    /* Rank order, matching build_composite_scores() exactly: composite desc,
     * then assessment, then MAP, then barrier, then name — so a tie never
     * depends on the order the runners happened to arrive in. */
    function compareRunners(a, b) {
        var byComposite = num(b.composite_score) - num(a.composite_score);
        if (byComposite) return byComposite;
        var byAssessment = num(b.assessment_score) - num(a.assessment_score);
        if (byAssessment) return byAssessment;
        var byMap = num(b.map_value) - num(a.map_value);
        if (byMap) return byMap;
        var barrierA = a.barrier ? num(a.barrier) : 99;
        var barrierB = b.barrier ? num(b.barrier) : 99;
        if (barrierA !== barrierB) return barrierA - barrierB;
        return String(a.horse_name || '') < String(b.horse_name || '') ? -1 : 1;
    }

    /* Composite gaps -> cumulative beaten margins. Port of finish_margins(). */
    function assignMargins(ordered, config) {
        if (!ordered || !ordered.length) return ordered;
        var minMargin = num(config.margin_min);
        var maxMargin = num(config.margin_max);
        var maxTotal = num(config.margin_total);

        var drops = [];
        var biggest = 0;
        for (var i = 0; i < ordered.length - 1; i++) {
            var drop = Math.max(0, num(ordered[i].composite_score) - num(ordered[i + 1].composite_score));
            drops.push(drop);
            if (drop > biggest) biggest = drop;
        }

        var margins = [0];
        drops.forEach(function (drop) {
            var share = biggest > 1e-9 ? drop / biggest : 0;
            margins.push(margins[margins.length - 1] + minMargin + share * (maxMargin - minMargin));
        });

        var last = margins[margins.length - 1];
        var scale = last > maxTotal && last > 0 ? maxTotal / last : 1;
        ordered.forEach(function (runner, index) {
            runner.beaten_margin = Math.round(margins[index] * scale * 100) / 100;
        });
        return ordered;
    }

    /* Barrier draw, inside to outside; anything with no barrier goes to the
     * outside in rank order. Same rule the payload was built with. */
    function assignLanes(ordered) {
        var drawn = (ordered || []).filter(function (r) { return r.barrier; })
            .slice().sort(function (a, b) { return num(a.barrier) - num(b.barrier); });
        var undrawn = (ordered || []).filter(function (r) { return !r.barrier; });
        drawn.concat(undrawn).forEach(function (runner, lane) { runner.lane = lane; });
        return ordered;
    }

    /* Composite scores -> win probabilities summing to 1.
     * Port of win_probabilities(): Plackett-Luce strengths, exp(score / tau). */
    function winProbabilities(ordered, config) {
        if (!ordered || !ordered.length) return [];
        var tau = Math.max(1e-6, num(config.probability_temperature));
        var scores = ordered.map(function (r) { return num(r.composite_score); });
        var best = Math.max.apply(null, scores);
        var strengths = scores.map(function (score) { return Math.exp((score - best) / tau); });
        var total = strengths.reduce(function (sum, s) { return sum + s; }, 0);
        if (total <= 1e-12) {
            return ordered.map(function () { return 1 / ordered.length; });
        }
        return strengths.map(function (s) { return s / total; });
    }

    /* Model probability against a real price. Port of value_edge(). */
    function valueEdge(probability, price, config, marketProbability) {
        var p = Number(probability);
        var decimal = Number(price);
        var out = {
            price: isFinite(decimal) ? decimal : null,
            model_probability: isFinite(p) ? p : null,
            market_probability: isFinite(Number(marketProbability)) ? Number(marketProbability) : null,
            edge: null, edge_pct: null, expected_value: null,
            kelly_pct: null, is_value: false
        };
        if (!isFinite(p) || !isFinite(decimal) || decimal <= 1) return out;

        var implied = out.market_probability;
        if (implied === null) {
            implied = 1 / decimal;
            out.market_probability = implied;
        }

        out.edge = p - implied;
        out.edge_pct = Math.round((p - implied) * 10000) / 100;
        out.expected_value = Math.round((p * decimal - 1) * 10000) / 10000;

        var b = decimal - 1;
        var kelly = b > 1e-9 ? (b * p - (1 - p)) / b : 0;
        kelly = Math.max(0, kelly) * Math.max(0, num(config.kelly_fraction));
        out.kelly_pct = Math.round(kelly * 10000) / 100;
        out.is_value = out.edge >= num(config.min_value_edge) && out.expected_value > 0;
        return out;
    }

    /* Hang probability, fair price, edge and stake off each runner in place.
     * Port of attach_market(). Market probabilities, where the server worked
     * them out properly (Shin), come down on the runner as market_probability
     * and are reused rather than recomputed from the raw price. */
    function attachMarket(ordered, config) {
        if (!ordered || !ordered.length) return ordered;
        var probabilities = winProbabilities(ordered, config);
        ordered.forEach(function (runner, index) {
            var p = probabilities[index];
            runner.win_probability = Math.round(p * 1e6) / 1e6;
            runner.win_probability_pct = Math.round(p * 1000) / 10;
            runner.fair_odds = p > 1e-9 ? Math.round((1 / p) * 100) / 100 : null;
            runner.value = valueEdge(p, runner.price, config,
                runner.market_probability != null ? runner.market_probability : null);
        });
        return ordered;
    }

    /* The whole re-score, in the order the page needs it: blend, rank, margins,
     * lanes, prices. One call so the page cannot do half of it. */
    function rescore(runners, weightPercentages, config) {
        var weights = resolveWeights(weightPercentages, config);
        applyWeights(runners, weights, config);
        runners.sort(compareRunners);
        runners.forEach(function (runner, index) { runner.rank = index + 1; });
        assignMargins(runners, config);
        assignLanes(runners);
        attachMarket(runners, config);
        return { runners: runners, weights: weights };
    }

    return {
        resolveWeights: resolveWeights,
        applyWeights: applyWeights,
        compareRunners: compareRunners,
        assignMargins: assignMargins,
        assignLanes: assignLanes,
        winProbabilities: winProbabilities,
        valueEdge: valueEdge,
        attachMarket: attachMarket,
        rescore: rescore
    };
}));
