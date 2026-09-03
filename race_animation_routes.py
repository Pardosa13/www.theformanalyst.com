"""
race_animation_routes.py — Flask blueprint for the Race Animations & Predictions page.

Registered from app.py the same way mma_routes / afl_routes are. Everything here
is additive: this module defines its own blueprint, its own URL prefix space and
its own template, and it reads existing tables without ever writing to them, so
no existing page, route or file changes behaviour because of it.

Routes:
  GET /race-animations-predictions                     -> the page
  GET /api/race-animation/meetings                     -> meetings for the dropdown
  GET /api/race-animation/meeting/<id>/races           -> races within a meeting
  GET /api/race-animation/race/<race_id>               -> the per-race payload
  GET /api/race-animation/race/<race_id>/silks         -> live silk artwork
  GET /api/race-animation/accuracy                     -> how a weighting has done
  GET /api/race-animation/tune                         -> let history pick a weighting
  GET /api/race-animation/race/<race_id>/calibrate     -> what would have picked THIS runner
  GET /api/race-animation/calibration-drift            -> where the missed winners point

The race payload accepts an optional custom weighting and normalisation, so the
page's sliders can ask the server for a different blend:

  /api/race-animation/race/123?w_speed_map=60&w_assessment=10&norm=rank

Any weight left out keeps its published default, and the components are
rescaled to sum to 100% before the blend runs.

WHAT THE REQUEST PATH IS ALLOWED TO WAIT FOR
Silks are decoration, they are fetched from Ladbrokes over the network, and for
any meeting older than today that call can never succeed. They used to sit
inside the race payload, so every page load paid for two HTTP requests before it
could answer — including for a meeting from last winter, where the answer was
always going to be nothing. They now have their own endpoint the page calls
afterwards, and the runners are drawn in their coded fallback colours until (and
unless) it answers.

Live prices are the one network call the payload still makes, and only for a
race that has not been run: there the market is real information the page needs
(it is a scoring component, and the whole value-and-stake column hangs off it),
and the call can genuinely succeed. A race that HAS been run prices itself off
its own recorded starting prices, which is both free and more correct than
anything a bookmaker would say about it today. `?prices=0` turns even that off.

Every query that walks a collection eager-loads it. The meeting list used to
count races one query at a time, and the race payload fetched each horse's
prediction and result separately — a 16-runner race was 30-odd round trips
where it should be three.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from flask import Blueprint, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from flask_login import login_required
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from models import Horse, Meeting, Race, StrikeRate
from race_animation_scoring import (
    COMPONENT_KEYS,
    CORE_COMPONENT_KEYS,
    PACE_CATEGORIES,
    PACE_LABELS,
    WEIGHTS,
    attach_market,
    build_composite_scores,
    draw_score,
    extract_best_adjusted_time,
    finish_margins,
    jockey_trainer_score,
    normalise_name,
    pace_category_for_settle,
    pace_fit_score,
    parse_distance_metres,
    race_pace_profile,
    race_shape,
    resolve_norm_method,
    resolve_weights,
    sample_finish_order,
    scoring_constants,
    sectional_rank_from_pfai,
    simulate_race,
    to_float,
    track_direction,
    weights_as_percentages,
)
from race_animation_tuning import (
    evaluate_weights,
    optimise_weights,
    prepare_records,
)
from race_animation_calibration import (
    calibration_drift,
    condition_group,
    solve_for_runner,
)

logger = logging.getLogger(__name__)

# Ladbrokes serves silks as one horizontal sprite strip, 32px per runner tile.
# Same geometry buildSilkStyle() uses in templates/view_meeting.html.
SILK_TILE_PX = 32

# Deterministic fallback silks, used when the Ladbrokes sprite is unavailable
# (no live market for the race, an old meeting, or the feed is down). These are
# only ever a fallback — when the real silk sprite is there, that is what the
# page draws, so the artwork matches the rest of the site.
FALLBACK_SILK_COLOURS = [
    '#e23b3b', '#2f6fd0', '#f2c53d', '#2fa860', '#8b4fc4', '#ef7f2e',
    '#25b4c4', '#d94f9a', '#5a6b8c', '#8f6b3a', '#c8d132', '#1f4f8f',
    '#b02a5b', '#3fae7a', '#e0632a', '#6c4fa8', '#357d9a', '#c9973a',
    '#4b8b3b', '#a33d3d', '#2c8fb0', '#7a5cc4', '#d7a32c', '#39a06a',
]
FALLBACK_SILK_PATTERNS = ['solid', 'stripes', 'halved', 'spots', 'sash', 'quartered', 'hoops', 'chevron']

def _load_json_column(value):
    """races.speed_maps_json / sectionals_json are written as both JSON and text."""
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _tab_number_from_csv(csv_data):
    """Saddlecloth number out of the stored CSV row. Mirrors app.py's helper."""
    if not isinstance(csv_data, dict):
        return None
    for key in ('horse number', 'tab number', 'tabNo', 'TabNo', 'horse_number'):
        value = csv_data.get(key)
        if value in (None, ''):
            continue
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    return None


def _speed_map_items(race):
    """{normalised runner name: speed map item} for one race.

    The stored shape is {'payLoad': [{'items': [...]}, ...]}; each item carries
    runnerName, tabNo, settle, speed, mapA2E, pfaiScore and jockeyA2E.
    """
    data = _load_json_column(race.speed_maps_json)
    if not isinstance(data, dict):
        return {}
    items = {}
    for entry in data.get('payLoad', []) or []:
        if not isinstance(entry, dict):
            continue
        for item in entry.get('items', []) or []:
            if not isinstance(item, dict):
                continue
            key = normalise_name(item.get('runnerName'))
            if key:
                items[key] = item
    return items


def _pfai_sectionals(race):
    """{normalised runner name: {last600_rank, last400_rank, last200_rank}}.

    Reads races.sectionals_json, the same store /api/race/<id>/pfai-sectionals
    serves from, and accepts the same alternate field spellings app.py accepts.
    """
    data = _load_json_column(race.sectionals_json)
    if not isinstance(data, dict):
        return {}

    def first_present(source, keys):
        for key in keys:
            if source.get(key) not in (None, ''):
                return source.get(key)
        return None

    out = {}
    for runner in data.get('payLoad', []) or []:
        if not isinstance(runner, dict):
            continue
        if runner.get('raceNo') is not None and to_float(runner.get('raceNo')) != float(race.race_number):
            continue
        key = normalise_name(runner.get('runnerName') or runner.get('name'))
        if not key:
            continue
        out[key] = {
            'last200_rank': first_present(runner, ['last200TimeRank', 'rank_200m', 'rank_200', 'pfai_200m_rank', 'sectional_200_rank']),
            'last400_rank': first_present(runner, ['last400TimeRank', 'rank_400m', 'rank_400', 'pfai_400m_rank', 'sectional_400_rank']),
            'last600_rank': first_present(runner, ['last600TimeRank', 'rank_600m', 'rank_600', 'pfai_600m_rank', 'last_600_rank', 'sectional_600_rank']),
        }
    return out


def _fallback_silk(seed_name: str, tab_number):
    """A stable colour/pattern pair for a horse, from a hash of its name.

    Stable means the same horse draws the same silk every time the page is
    opened, which matters for recognising runners across replays.
    """
    key = normalise_name(seed_name) or str(tab_number or '')
    digest = 0
    for char in key:
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    primary = FALLBACK_SILK_COLOURS[digest % len(FALLBACK_SILK_COLOURS)]
    secondary = FALLBACK_SILK_COLOURS[(digest // 7 + 5) % len(FALLBACK_SILK_COLOURS)]
    if secondary == primary:
        secondary = FALLBACK_SILK_COLOURS[(digest // 7 + 11) % len(FALLBACK_SILK_COLOURS)]
    cap = FALLBACK_SILK_COLOURS[(digest // 53 + 3) % len(FALLBACK_SILK_COLOURS)]
    return {
        'primary': primary,
        'secondary': secondary,
        'cap': cap,
        'pattern': FALLBACK_SILK_PATTERNS[(digest // 17) % len(FALLBACK_SILK_PATTERNS)],
    }


def _ladbrokes_silks(meeting, race):
    """Best-effort live silk sprite for a race: {normalised name: runner_number}.

    Uses exactly the Ladbrokes lookup view_meeting.html uses (match the race to
    its event UUID, pull the event, take race.silk_url and each runner's
    runner_number). Purely decorative — any failure here degrades to the coded
    fallback silks and never fails the request.

    Called from its own endpoint rather than from the race payload, because it
    goes over the network and the payload must not wait on it.
    """
    empty = {'sprite_url': '', 'runner_numbers': {}}
    try:
        from ladbrokes import fetch_race_odds, match_race_uuid
    except Exception:
        return empty

    track = meeting.track or meeting.puntingform_id
    if not track and '_' in (meeting.meeting_name or ''):
        track = meeting.meeting_name.split('_', 1)[1]
    if not track:
        return empty

    if meeting.date:
        date_str = meeting.date.strftime('%Y-%m-%d')
    elif meeting.meeting_name and len(meeting.meeting_name) >= 6:
        prefix = meeting.meeting_name.split('_')[0]
        date_str = f"20{prefix[:2]}-{prefix[2:4]}-{prefix[4:6]}"
    else:
        return empty

    try:
        uuid = match_race_uuid(track, date_str, race.race_number)
        if not uuid:
            return empty
        payload = fetch_race_odds(uuid) or {}
        sprite_url = payload.get('silk_url') or ''
        numbers = {}
        prices = {}
        for key, runner in (payload.get('odds') or {}).items():
            name_key = normalise_name(runner.get('name') or key)
            number = to_float(runner.get('runner_number'))
            if number:
                numbers[name_key] = int(number)
            price = to_float(runner.get('win_odds') or runner.get('odds') or runner.get('price'))
            if price and price > 1.0:
                prices[name_key] = price
        return {'sprite_url': sprite_url, 'runner_numbers': numbers, 'prices': prices}
    except Exception as exc:
        logger.info("Race animation: no Ladbrokes silks for race %s (%s)", race.id, exc)
        return empty


def _live_prices(meeting, race):
    """Live win prices from Ladbrokes, best effort, {normalised name: price}.

    Only reached for a race with no starting prices recorded against it — a
    finished race prices itself off its own result, which is free and more
    correct than whatever the market says about it now.

    The Ladbrokes lookup returns the silks and the prices from one event fetch,
    so this shares that call rather than making a second one, and it is cached
    upstream (thirty seconds on odds, ten minutes on the meeting list).
    """
    try:
        return (_ladbrokes_silks(meeting, race) or {}).get('prices') or {}
    except Exception:
        return {}


def _weights_from_request(args) -> dict[str, float]:
    """Read w_<component> off a query string into a weight override dict.

    Only keys that are actually present are passed on; resolve_weights() fills
    the rest from the published defaults and rescales the lot to sum to 1.0.
    """
    overrides = {}
    for key in COMPONENT_KEYS:
        value = args.get('w_' + key)
        if value not in (None, ''):
            overrides[key] = value
    return overrides


# ── Strike rates ──────────────────────────────────────────────────────────
def _strike_rate_lookup(names, kind):
    """{normalised name: A/E ratio} for a batch of jockeys or trainers.

    One query for the whole race rather than one per runner. Prefers the last
    hundred rides, which is current form, and falls back to career where the
    recent figure is missing.
    """
    keys = {normalise_name(name) for name in names if name}
    keys.discard('')
    if not keys:
        return {}
    try:
        rows = (StrikeRate.query
                .filter(StrikeRate.type == kind)
                .filter(StrikeRate.normalised_name.in_(list(keys)))
                .all())
    except Exception as exc:
        logger.info("Race animation: strike rates unavailable for %s (%s)", kind, exc)
        return {}

    out = {}
    for row in rows:
        value = to_float(row.last100_actual_to_expected)
        if value is None:
            value = to_float(row.career_actual_to_expected)
        if value is None:
            continue
        key = normalise_name(row.normalised_name or row.name)
        # The feed can carry more than one row per person; keep the freshest,
        # which is what came back last given the table's updated_at index.
        out.setdefault(key, value)
    return out


# ── The payload ───────────────────────────────────────────────────────────
def _load_race(race_id):
    """One race with its horses, predictions and results already loaded.

    selectinload on the collection and joinedload on the one-to-ones turns what
    used to be one query per horse (twice over) into three queries flat.
    """
    return (Race.query
            .options(selectinload(Race.horses)
                     .joinedload(Horse.prediction),
                     selectinload(Race.horses)
                     .joinedload(Horse.result))
            .filter(Race.id == race_id)
            .first())


def build_race_payload(race, meeting, weights=None, norm_method=None,
                       include_prices: bool = True, simulate: bool = True) -> dict:
    """Assemble the full per-race JSON: runners, components, composite, rank.

    `weights` is an optional custom blend (anything resolve_weights() accepts —
    percentages or fractions, partial or complete). Left out, the page's
    published default split is used.
    """
    blend = resolve_weights(weights)
    method = resolve_norm_method(norm_method)
    speed_map = _speed_map_items(race)
    sectionals = _pfai_sectionals(race)

    live_runners = [h for h in race.horses if not h.is_scratched]
    field_size = len(live_runners)

    jockey_ae = _strike_rate_lookup([h.jockey for h in live_runners], 'jockey')
    trainer_ae = _strike_rate_lookup([h.trainer for h in live_runners], 'trainer')

    # Pace shape is a property of the whole field, so it has to be read before
    # any runner can be scored on how well the tempo suits it.
    pace_by_horse = {}
    for horse in live_runners:
        item = speed_map.get(normalise_name(horse.horse_name)) or {}
        pace_by_horse[horse.id] = pace_category_for_settle(item.get('settle'))
    profile = race_pace_profile(list(pace_by_horse.values()),
                               getattr(meeting, 'pace_bias', 0))
    rail = getattr(meeting, 'rail_position', 0) or 0

    # Starting prices where the race has been settled; the live market only for
    # a race that has not been run, and only when the caller wants it.
    has_results = any(h.result is not None for h in live_runners)
    live_prices = {}
    if include_prices and not has_results:
        live_prices = _live_prices(meeting, race)

    runners = []
    for horse in live_runners:
        key = normalise_name(horse.horse_name)
        csv_data = horse.csv_data if isinstance(horse.csv_data, dict) else {}
        map_item = speed_map.get(key) or {}
        prediction = horse.prediction
        result = horse.result

        # Overall race assessment: the ML score is the current head model, with
        # the PFAI blend score as the fallback for meetings analysed before the
        # ML scores existed (or where the model produced nothing).
        assessment = None
        assessment_source = None
        if prediction is not None:
            assessment = to_float(prediction.ml_score)
            assessment_source = 'ml_score'
            if assessment is None:
                assessment = to_float(prediction.score)
                assessment_source = 'pfai_score'
        if assessment is None:
            assessment_source = None

        best_recent = extract_best_adjusted_time(prediction.notes if prediction else None)

        tab_number = (
            to_float(map_item.get('tabNo'))
            or _tab_number_from_csv(csv_data)
        )
        tab_number = int(tab_number) if tab_number else None

        pace = pace_by_horse[horse.id]
        jockey_name = horse.jockey or (csv_data.get('horse jockey') if csv_data else '') or ''
        trainer_name = horse.trainer or ''

        price = None
        if result is not None and to_float(result.sp):
            price = to_float(result.sp)
        elif live_prices:
            price = live_prices.get(key)

        runners.append({
            'horse_id': horse.id,
            'horse_name': horse.horse_name,
            'barrier': to_float(horse.barrier),
            'tab_number': tab_number,
            'jockey': jockey_name,
            'trainer': trainer_name,

            # Raw component inputs, before any normalisation.
            'map_value': to_float(map_item.get('mapA2E')),
            'sectional_rank': sectional_rank_from_pfai(sectionals.get(key)),
            'adjusted_time': (best_recent or {}).get('adjusted_time'),
            'assessment_score': assessment,
            'assessment_source': assessment_source,
            'jockey_trainer_ae': jockey_trainer_score(
                jockey_ae.get(normalise_name(jockey_name)),
                trainer_ae.get(normalise_name(trainer_name))),
            'draw_value': draw_score(horse.barrier, field_size, rail),
            'pace_fit_value': pace_fit_score(pace, profile),
            'market_probability': None,     # filled in below, once the book is known

            # Supporting detail the table and the tap popup show.
            'settle': to_float(map_item.get('settle')),
            'speed': to_float(map_item.get('speed')),
            'pfai_score': to_float(map_item.get('pfaiScore')),
            'jockey_a2e': to_float(map_item.get('jockeyA2E')),
            'pace_category': pace,
            'pace_label': PACE_LABELS[pace],
            'sectional_ranks': sectionals.get(key) or {},
            'adjusted_time_detail': best_recent,
            'ml_score': to_float(prediction.ml_score) if prediction else None,
            'pfai_blend_score': to_float(prediction.score) if prediction else None,
            'price': price,

            # What actually happened, where it has been recorded.
            'result': None if result is None else {
                'finish_position': result.finish_position,
                'sp': to_float(result.sp),
                'won': result.finish_position == 1,
                'placed': result.finish_position in (1, 2, 3),
                'ran': result.finish_position > 0,
            },

            'silk': {
                'sprite_url': '',
                'runner_number': tab_number,
                'tile_px': SILK_TILE_PX,
                'tile_offset_px': -((tab_number - 1) * SILK_TILE_PX) if tab_number else 0,
                'fallback': _fallback_silk(horse.horse_name, tab_number),
            },
        })

    # The market component needs the whole book at once — one runner's fair
    # probability is not defined without its rivals — so it is filled in here,
    # after every price is known, and only then does the blend run.
    market_probs = _market_probabilities([r['price'] for r in runners])
    for runner, probability in zip(runners, market_probs):
        runner['market_probability'] = probability

    ordered = build_composite_scores(runners, blend, method)
    margins = finish_margins(ordered)
    for runner, margin in zip(ordered, margins):
        runner['beaten_margin'] = round(margin, 2)

    attach_market(ordered, [r['market_probability'] for r in ordered])

    # Barrier draw: inside to outside. Runners with no barrier recorded go to
    # the outside, in rank order, so the start line is always fully determined.
    with_barrier = sorted([r for r in ordered if r['barrier']], key=lambda r: r['barrier'])
    without_barrier = [r for r in ordered if not r['barrier']]
    for lane, runner in enumerate(with_barrier + without_barrier):
        runner['lane'] = lane

    pace_counts = {category: 0 for category in PACE_CATEGORIES}
    for runner in ordered:
        pace_counts[runner['pace_category']] += 1

    distance_m = parse_distance_metres(race.distance)
    shape = race_shape(distance_m)
    track_name = meeting.track or meeting.meeting_name or ''

    payload = {
        'success': True,
        'meeting': {
            'id': meeting.id,
            'name': meeting.meeting_name,
            'track': meeting.track or meeting.puntingform_id or '',
            'date': meeting.date.isoformat() if meeting.date else None,
            'rail_position': rail,
            'pace_bias': getattr(meeting, 'pace_bias', 0) or 0,
        },
        'race': {
            'id': race.id,
            'race_number': race.race_number,
            'distance': race.distance,
            'race_class': race.race_class,
            'track_condition': race.track_condition,
            'field_size': len(ordered),
            'has_speed_map': bool(speed_map),
            'has_sectionals': bool(sectionals),
            'has_prices': any(r.get('price') for r in ordered),
            'direction': track_direction(track_name),
            **shape,
        },
        'weights': weights_as_percentages(blend),
        'default_weights': weights_as_percentages(WEIGHTS),
        'weights_are_default': weights_as_percentages(blend) == weights_as_percentages(WEIGHTS),
        'norm_method': method,
        'pace': profile,
        'pace_counts': pace_counts,
        'runners': ordered,
        'result_summary': _result_summary(ordered),
    }

    if simulate:
        payload['simulation'] = simulate_race(ordered)
        payload['sample_order'] = [ordered[i]['horse_id']
                                   for i in sample_finish_order(ordered, seed=race.id or 1)]

    return payload


def _market_probabilities(prices):
    """Prices -> fair win probabilities with the bookmaker's margin removed.

    Uses the site's Shin correction where it is importable, because that is the
    correction the rest of the model is calibrated against and a raw 1/price
    book overstates every favourite. Falls back to the proportional strip if
    market_probability cannot be imported, which understates the edge slightly —
    the safe direction to be wrong in.
    """
    usable = [to_float(p) for p in prices]
    if not any(p and p > 1.0 for p in usable):
        return [None] * len(prices)

    priced = [p for p in usable if p and p > 1.0]
    try:
        from market_probability import fair_probabilities
        fair = fair_probabilities(priced)
    except Exception:
        raw = [1.0 / p for p in priced]
        total = sum(raw) or 1.0
        fair = [v / total for v in raw]

    out = []
    cursor = 0
    for price in usable:
        if price and price > 1.0 and cursor < len(fair):
            out.append(float(fair[cursor]))
            cursor += 1
        else:
            out.append(None)
    return out


def _result_summary(ordered):
    """Did the prediction get it right? None until the race has been settled."""
    results = [r for r in ordered if r.get('result') and r['result'].get('ran')]
    if not results:
        return {'has_results': False}

    winner = next((r for r in results if r['result']['finish_position'] == 1), None)
    top_pick = ordered[0] if ordered else None
    predicted_place_of_winner = None
    if winner is not None:
        predicted_place_of_winner = winner['rank']

    # How far out was each prediction, over the runners that actually finished
    # in a recorded position (1-4; the feed records everything else as 5).
    placed = [r for r in results if 1 <= r['result']['finish_position'] <= 4]
    error = None
    if placed:
        error = round(sum(abs(r['rank'] - r['result']['finish_position'])
                          for r in placed) / len(placed), 2)

    return {
        'has_results': True,
        'winner_name': winner['horse_name'] if winner else None,
        'winner_horse_id': winner['horse_id'] if winner else None,
        'winner_sp': winner['result']['sp'] if winner else None,
        'predicted_winner': top_pick['horse_name'] if top_pick else None,
        'predicted_winner_horse_id': top_pick['horse_id'] if top_pick else None,
        'found_winner': bool(winner and top_pick and winner['horse_id'] == top_pick['horse_id']),
        'winner_in_top3': bool(predicted_place_of_winner and predicted_place_of_winner <= 3),
        'predicted_place_of_winner': predicted_place_of_winner,
        'mean_placing_error': error,
        'settled_runners': len(results),
    }


# ── History, for the scoreboard and the tuner ─────────────────────────────
def _distance_band(distance_m) -> str:
    """Sprint / mile / staying, in the brackets the form talks in.

    Only used to split the calibration findings: barrier and pace matter in
    completely different amounts over 1000m and over 2400m, so lumping them
    together hides both.
    """
    if not distance_m:
        return 'unknown'
    if distance_m <= 1200:
        return 'sprint'
    if distance_m <= 1600:
        return 'mile'
    if distance_m <= 2000:
        return 'middle'
    return 'staying'


def _history_records(limit_races: int = 400, days: int | None = None):
    """Every settled race we can score, as plain dicts for the tuner.

    Deliberately capped: this walks whole meetings, and a tuning run has to
    finish inside a request. Newest first, then handed back oldest first so the
    walk-forward split runs forwards through time.
    """
    query = (Meeting.query
             .options(selectinload(Meeting.races)
                      .selectinload(Race.horses)
                      .joinedload(Horse.prediction),
                      selectinload(Meeting.races)
                      .selectinload(Race.horses)
                      .joinedload(Horse.result))
             .order_by(Meeting.date.desc().nulls_last(), Meeting.uploaded_at.desc()))

    if days:
        cutoff = date.today() - timedelta(days=int(days))
        query = query.filter(Meeting.date >= cutoff)

    records = []
    for meeting in query.limit(120).all():
        rail = getattr(meeting, 'rail_position', 0) or 0
        bias = getattr(meeting, 'pace_bias', 0) or 0
        for race in meeting.races:
            live = [h for h in race.horses if not h.is_scratched and h.result is not None]
            if len(live) < 4:
                continue
            if not any(h.result.finish_position == 1 for h in live):
                continue

            speed_map = _speed_map_items(race)
            sectionals = _pfai_sectionals(race)
            jockey_ae = _strike_rate_lookup([h.jockey for h in live], 'jockey')
            trainer_ae = _strike_rate_lookup([h.trainer for h in live], 'trainer')

            paces = {}
            for horse in live:
                item = speed_map.get(normalise_name(horse.horse_name)) or {}
                paces[horse.id] = pace_category_for_settle(item.get('settle'))
            profile = race_pace_profile(list(paces.values()), bias)

            prices = [to_float(h.result.sp) for h in live]
            market = _market_probabilities(prices)

            runners = []
            for horse, market_probability in zip(live, market):
                key = normalise_name(horse.horse_name)
                item = speed_map.get(key) or {}
                prediction = horse.prediction
                assessment = None
                if prediction is not None:
                    assessment = to_float(prediction.ml_score)
                    if assessment is None:
                        assessment = to_float(prediction.score)
                best_recent = extract_best_adjusted_time(prediction.notes if prediction else None)
                runners.append({
                    'map_value': to_float(item.get('mapA2E')),
                    'sectional_rank': sectional_rank_from_pfai(sectionals.get(key)),
                    'adjusted_time': (best_recent or {}).get('adjusted_time'),
                    'assessment_score': assessment,
                    'jockey_trainer_ae': jockey_trainer_score(
                        jockey_ae.get(normalise_name(horse.jockey or '')),
                        trainer_ae.get(normalise_name(horse.trainer or ''))),
                    'draw_value': draw_score(horse.barrier, len(live), rail),
                    'pace_fit_value': pace_fit_score(paces[horse.id], profile),
                    'market_probability': market_probability,
                    'finish_position': horse.result.finish_position,
                    'sp': to_float(horse.result.sp),
                })

            # Context is only carried for the calibration analysis, which splits
            # its findings by it — a wet Saturday and a dry Wednesday are not the
            # same question, and asking them together is how a real bias gets
            # averaged into nothing.
            distance_m = parse_distance_metres(race.distance)
            records.append({
                'race_id': race.id,
                'sort_key': meeting.date.isoformat() if meeting.date else str(race.id),
                'context': {
                    'track': meeting.track or meeting.meeting_name or '',
                    'date': meeting.date.isoformat() if meeting.date else None,
                    'track_condition': race.track_condition or '',
                    'condition': condition_group(race.track_condition),
                    'tempo': profile.get('shape') if isinstance(profile, dict) else None,
                    'distance_m': distance_m,
                    'trip': _distance_band(distance_m),
                    'field_size': len(live),
                },
                'runners': runners,
            })
            if len(records) >= limit_races:
                return records
    return records


def register_race_animation_routes(app, db):
    """Call from app.py after db.init_app(app), like register_mma_routes.

    The blueprint is built here rather than at module scope. Flask refuses to
    add a route to a blueprint that has already been registered, so a
    module-level one can be attached to exactly one app for the lifetime of the
    process — which is fine in production and makes the module impossible to
    test, because every test wants its own app and its own throwaway database.
    """
    race_animation_bp = Blueprint('race_animation', __name__)

    @race_animation_bp.route('/race-animations-predictions')
    @login_required
    def race_animations_predictions():
        # The page's copy of the blend arithmetic reads its constants from here
        # rather than carrying hardcoded duplicates that could drift.
        return render_template('race-animations-predictions.html',
                               scoring_config=scoring_constants())

    @race_animation_bp.route('/api/race-animation/meetings')
    @login_required
    def api_race_animation_meetings():
        """Meetings for the first dropdown, newest first."""
        try:
            limit = min(max(int(request.args.get('limit', 120)), 1), 500)
        except (TypeError, ValueError):
            limit = 120

        meetings = (Meeting.query
                    .order_by(Meeting.date.desc().nulls_last(), Meeting.uploaded_at.desc())
                    .limit(limit)
                    .all())

        # One grouped count for every meeting on the page, instead of one query
        # per meeting from len(m.races).
        ids = [m.id for m in meetings]
        counts = {}
        if ids:
            rows = (db.session.query(Race.meeting_id, func.count(Race.id))
                    .filter(Race.meeting_id.in_(ids))
                    .group_by(Race.meeting_id)
                    .all())
            counts = {meeting_id: count for meeting_id, count in rows}

        return jsonify({
            'success': True,
            'meetings': [{
                'id': m.id,
                'name': m.meeting_name,
                'track': m.track or m.puntingform_id or '',
                'date': m.date.isoformat() if m.date else None,
                'race_count': counts.get(m.id, 0),
            } for m in meetings],
        })

    @race_animation_bp.route('/api/race-animation/meeting/<int:meeting_id>/races')
    @login_required
    def api_race_animation_races(meeting_id):
        """Races within a meeting, for the second dropdown."""
        meeting = db.get_or_404(Meeting, meeting_id)
        races = (Race.query
                 .options(selectinload(Race.horses))
                 .filter_by(meeting_id=meeting.id)
                 .order_by(Race.race_number)
                 .all())
        return jsonify({
            'success': True,
            'meeting': {
                'id': meeting.id,
                'name': meeting.meeting_name,
                'track': meeting.track or meeting.puntingform_id or '',
                'date': meeting.date.isoformat() if meeting.date else None,
            },
            'races': [{
                'id': r.id,
                'race_number': r.race_number,
                'distance': r.distance,
                'race_class': r.race_class,
                'runner_count': sum(1 for h in r.horses if not h.is_scratched),
            } for r in races],
        })

    @race_animation_bp.route('/api/race-animation/race/<int:race_id>')
    @login_required
    def api_race_animation_race(race_id):
        """The composite-score payload the animation and the table both run off."""
        try:
            race = _load_race(race_id)
            if race is None:
                return jsonify({'success': False, 'error': 'Race not found'}), 404
            meeting = db.session.get(Meeting, race.meeting_id)
            if meeting is None:
                return jsonify({'success': False, 'error': 'Meeting not found for this race'}), 404
            payload = build_race_payload(
                race, meeting,
                weights=_weights_from_request(request.args),
                norm_method=request.args.get('norm'),
                include_prices=request.args.get('prices', '1') != '0',
            )
            if not payload['runners']:
                payload['success'] = False
                payload['error'] = 'No unscratched runners with data for this race'
                return jsonify(payload), 404
            return jsonify(payload)
        except HTTPException:
            # get_or_404 and friends abort with an HTTPException — let Flask
            # turn those into their real status rather than masking them as 500.
            raise
        except Exception as exc:
            logger.error("Race animation payload failed for race %s: %s", race_id, exc, exc_info=True)
            return jsonify({'success': False, 'error': 'Could not build race payload'}), 500

    @race_animation_bp.route('/api/race-animation/race/<int:race_id>/silks')
    @login_required
    def api_race_animation_silks(race_id):
        """Live silk artwork for a race, fetched separately from the payload.

        Decoration, over the network, and impossible for any meeting older than
        today. Keeping it out of the race payload is what stops every page load
        waiting on a bookmaker.
        """
        race = db.get_or_404(Race, race_id)
        meeting = db.session.get(Meeting, race.meeting_id)
        if meeting is None:
            return jsonify({'success': False, 'error': 'Meeting not found for this race'}), 404

        silks = _ladbrokes_silks(meeting, race)
        sprite_url = silks.get('sprite_url') or ''
        if not sprite_url:
            return jsonify({'success': True, 'has_silks': False, 'runners': []})

        runners = []
        for horse in race.horses:
            if horse.is_scratched:
                continue
            number = silks['runner_numbers'].get(normalise_name(horse.horse_name))
            if not number:
                continue
            runners.append({
                'horse_id': horse.id,
                'sprite_url': sprite_url,
                'runner_number': number,
                'tile_px': SILK_TILE_PX,
                'tile_offset_px': -((number - 1) * SILK_TILE_PX),
            })

        return jsonify({'success': True, 'has_silks': bool(runners), 'runners': runners})

    @race_animation_bp.route('/api/race-animation/accuracy')
    @login_required
    def api_race_animation_accuracy():
        """How a weighting has actually done, over settled races.

        The answer to "is this any good?". Scores the requested split and the
        published default over the same races so the two sit side by side.
        """
        try:
            weights = resolve_weights(_weights_from_request(request.args))
            method = resolve_norm_method(request.args.get('norm'))
            try:
                days = int(request.args.get('days')) if request.args.get('days') else None
            except (TypeError, ValueError):
                days = None

            records = _history_records(days=days)
            prepared = prepare_records(records, method)
            if not prepared:
                return jsonify({
                    'success': True, 'has_history': False,
                    'reason': 'No settled races with enough data to score yet.',
                })

            return jsonify({
                'success': True,
                'has_history': True,
                'races': len(prepared),
                'norm_method': method,
                'weights': weights_as_percentages(weights),
                'current': evaluate_weights(prepared, weights),
                'default': evaluate_weights(prepared, dict(WEIGHTS)),
            })
        except Exception as exc:
            logger.error("Race animation accuracy failed: %s", exc, exc_info=True)
            return jsonify({'success': False, 'error': 'Could not score the history'}), 500

    @race_animation_bp.route('/api/race-animation/tune')
    @login_required
    def api_race_animation_tune():
        """Let the history pick a weighting, and say what it is worth.

        Walk-forward throughout: the number reported is measured on races the
        search never saw. An in-sample figure would look wonderful and mean
        nothing.
        """
        try:
            criterion = request.args.get('criterion', 'log_loss')
            if criterion not in ('log_loss', 'strike_rate', 'top3_rate', 'roi'):
                criterion = 'log_loss'
            method = resolve_norm_method(request.args.get('norm'))
            scope = request.args.get('scope', 'core')
            search_keys = (list(CORE_COMPONENT_KEYS) if scope == 'core'
                           else list(COMPONENT_KEYS))
            try:
                days = int(request.args.get('days')) if request.args.get('days') else None
            except (TypeError, ValueError):
                days = None

            records = _history_records(days=days)
            prepared = prepare_records(records, method)
            outcome = optimise_weights(prepared, criterion=criterion,
                                       search_keys=search_keys)
            if not outcome.get('ok'):
                return jsonify({'success': True, 'ok': False,
                                'reason': outcome.get('reason'),
                                'races': outcome.get('races', 0)})

            outcome['success'] = True
            outcome['norm_method'] = method
            outcome['scope'] = scope
            outcome['weights_pct'] = weights_as_percentages(outcome['weights'])
            outcome['default_weights_pct'] = weights_as_percentages(dict(WEIGHTS))
            return jsonify(outcome)
        except Exception as exc:
            logger.error("Race animation tuning failed: %s", exc, exc_info=True)
            return jsonify({'success': False, 'error': 'Could not tune the weighting'}), 500

    @race_animation_bp.route('/api/race-animation/race/<int:race_id>/calibrate')
    @login_required
    def api_race_animation_calibrate(race_id):
        """What would the weighting have had to be for THIS runner to rate top?

        Defaults to the horse that actually won, which is the question worth
        asking after a race is beaten. `?horse_id=` asks it of any runner, and
        `?lock=market,draw` pins components where they are so the answer has to
        come out of the rest.

        The answer is a reading of the race, not a weighting to go and use — one
        race fitted after the fact will always look convincing. The drift
        endpoint below is where it turns into something measurable.
        """
        try:
            race = _load_race(race_id)
            if race is None:
                return jsonify({'success': False, 'error': 'Race not found'}), 404
            meeting = db.session.get(Meeting, race.meeting_id)
            if meeting is None:
                return jsonify({'success': False, 'error': 'Meeting not found for this race'}), 404

            weights = resolve_weights(_weights_from_request(request.args))
            method = resolve_norm_method(request.args.get('norm'))
            # simulate=False: the Monte Carlo is for the animation, and solving
            # a weighting has no use for it.
            payload = build_race_payload(race, meeting, weights=weights,
                                         norm_method=method, include_prices=False,
                                         simulate=False)
            runners = payload.get('runners') or []
            if not runners:
                return jsonify({'success': False,
                                'error': 'No unscratched runners with data for this race'}), 404

            summary = payload.get('result_summary') or {}
            requested = request.args.get('horse_id')
            try:
                target_id = int(requested) if requested else summary.get('winner_horse_id')
            except (TypeError, ValueError):
                target_id = summary.get('winner_horse_id')

            if not target_id:
                return jsonify({
                    'success': True, 'ok': False,
                    'reason': ('This race has no recorded winner yet, so there is '
                               'nothing to solve back from. Pick a runner to solve for.'),
                })

            index = next((i for i, r in enumerate(runners)
                          if r.get('horse_id') == target_id), None)
            if index is None:
                return jsonify({'success': False,
                                'error': 'That runner is not in this race'}), 404

            locked = [key.strip() for key in (request.args.get('lock') or '').split(',')
                      if key.strip() in COMPONENT_KEYS]

            matrix = [[(r.get('components', {}).get(key) or {}).get('normalised')
                       for key in COMPONENT_KEYS] for r in runners]
            outcome = solve_for_runner(
                matrix, index, weights, locked_keys=locked,
                labels=[r.get('horse_name') or '' for r in runners])

            target = runners[index]
            outcome['success'] = True
            outcome['norm_method'] = method
            outcome['race'] = {
                'id': race.id,
                'race_number': race.race_number,
                'track': meeting.track or meeting.meeting_name or '',
                'date': meeting.date.isoformat() if meeting.date else None,
                'track_condition': race.track_condition,
                'condition': condition_group(race.track_condition),
                'field_size': len(runners),
                'tempo': (payload.get('pace') or {}).get('shape'),
                'tempo_label': (payload.get('pace') or {}).get('shape_label'),
            }
            outcome['target'] = {
                'horse_id': target.get('horse_id'),
                'horse_name': target.get('horse_name'),
                'tab_number': target.get('tab_number'),
                'pace_label': target.get('pace_label'),
                'finish_position': (target.get('result') or {}).get('finish_position'),
                'won': bool((target.get('result') or {}).get('won')),
                'sp': (target.get('result') or {}).get('sp'),
                'is_actual_winner': target.get('horse_id') == summary.get('winner_horse_id'),
            }
            outcome['predicted_winner'] = {
                'horse_id': runners[0].get('horse_id'),
                'horse_name': runners[0].get('horse_name'),
                'finish_position': (runners[0].get('result') or {}).get('finish_position'),
            }
            return jsonify(outcome)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Race animation calibration failed for race %s: %s",
                         race_id, exc, exc_info=True)
            return jsonify({'success': False,
                            'error': 'Could not solve this race'}), 500

    @race_animation_bp.route('/api/race-animation/calibration-drift')
    @login_required
    def api_race_animation_calibration_drift():
        """Where every missed winner would have pulled the weighting.

        Solves the same question the per-race endpoint answers, over every
        settled race the current weighting got wrong, and reports where those
        answers point — split by track condition (`?group=condition`), tempo,
        trip or track.

        The headline number here is the holdout: a weighting fixed off the
        EARLIEST of those races and scored on the later ones it has never seen.
        The medians on their own are fitted to results already known and will
        always flatter themselves.
        """
        try:
            weights = resolve_weights(_weights_from_request(request.args))
            method = resolve_norm_method(request.args.get('norm'))
            group = request.args.get('group', 'condition')
            if group not in ('condition', 'tempo', 'trip', 'track'):
                group = 'condition'
            try:
                days = int(request.args.get('days')) if request.args.get('days') else None
            except (TypeError, ValueError):
                days = None

            records = _history_records(days=days)
            prepared = prepare_records(records, method)
            if not prepared:
                return jsonify({'success': True, 'ok': False,
                                'reason': 'No settled races with enough data to solve yet.'})

            outcome = calibration_drift(prepared, weights, group_by=group)
            outcome['success'] = True
            outcome['norm_method'] = method
            return jsonify(outcome)
        except Exception as exc:
            logger.error("Race animation calibration drift failed: %s", exc, exc_info=True)
            return jsonify({'success': False,
                            'error': 'Could not analyse the missed winners'}), 500

    app.register_blueprint(race_animation_bp)
    logger.info("✓ Race animation routes registered")
