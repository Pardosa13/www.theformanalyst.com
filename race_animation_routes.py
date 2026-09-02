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

The race payload accepts an optional custom weighting, so the page's sliders can
ask the server for a different blend:

  /api/race-animation/race/123?w_speed_map=60&w_sectional=20&w_adjusted_time=10&w_assessment=10

Any weight left out keeps its published default, and the four are rescaled to
sum to 100% before the blend runs.
"""

from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from flask_login import login_required

from models import Meeting, Race
from race_animation_scoring import (
    COMPONENT_KEYS,
    PACE_CATEGORIES,
    PACE_LABELS,
    WEIGHTS,
    build_composite_scores,
    extract_best_adjusted_time,
    finish_margins,
    normalise_name,
    pace_category_for_settle,
    resolve_weights,
    sectional_rank_from_pfai,
    to_float,
    weights_as_percentages,
)

logger = logging.getLogger(__name__)

race_animation_bp = Blueprint('race_animation', __name__)

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
        if not sprite_url:
            return empty
        numbers = {}
        for key, runner in (payload.get('odds') or {}).items():
            number = to_float(runner.get('runner_number'))
            if number:
                numbers[normalise_name(runner.get('name') or key)] = int(number)
        return {'sprite_url': sprite_url, 'runner_numbers': numbers}
    except Exception as exc:
        logger.info("Race animation: no Ladbrokes silks for race %s (%s)", race.id, exc)
        return empty


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


def build_race_payload(race, meeting, include_silks: bool = True, weights=None) -> dict:
    """Assemble the full per-race JSON: runners, components, composite, rank.

    `weights` is an optional custom blend (anything resolve_weights() accepts —
    percentages or fractions, partial or complete). Left out, the page's
    published default split is used.
    """
    blend = resolve_weights(weights)
    speed_map = _speed_map_items(race)
    sectionals = _pfai_sectionals(race)
    silks = _ladbrokes_silks(meeting, race) if include_silks else {'sprite_url': '', 'runner_numbers': {}}

    runners = []
    for horse in race.horses:
        if horse.is_scratched:
            continue

        key = normalise_name(horse.horse_name)
        csv_data = horse.csv_data if isinstance(horse.csv_data, dict) else {}
        map_item = speed_map.get(key) or {}
        prediction = horse.prediction

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
            or silks['runner_numbers'].get(key)
        )
        tab_number = int(tab_number) if tab_number else None

        runner_number = silks['runner_numbers'].get(key) or tab_number
        pace = pace_category_for_settle(map_item.get('settle'))

        runners.append({
            'horse_id': horse.id,
            'horse_name': horse.horse_name,
            'barrier': to_float(horse.barrier),
            'tab_number': tab_number,
            'jockey': horse.jockey or (csv_data.get('horse jockey') if csv_data else '') or '',
            'trainer': horse.trainer or '',

            # Raw component inputs, before any normalisation.
            'map_value': to_float(map_item.get('mapA2E')),
            'sectional_rank': sectional_rank_from_pfai(sectionals.get(key)),
            'adjusted_time': (best_recent or {}).get('adjusted_time'),
            'assessment_score': assessment,
            'assessment_source': assessment_source,

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

            'silk': {
                'sprite_url': silks['sprite_url'],
                'runner_number': runner_number,
                'tile_px': SILK_TILE_PX,
                # Byte offset into the sprite strip, matching view_meeting.html.
                'tile_offset_px': -((runner_number - 1) * SILK_TILE_PX) if runner_number else 0,
                'fallback': _fallback_silk(horse.horse_name, tab_number),
            },
        })

    ordered = build_composite_scores(runners, blend)
    margins = finish_margins(ordered)
    for runner, margin in zip(ordered, margins):
        runner['beaten_margin'] = round(margin, 2)

    # Barrier draw: inside to outside. Runners with no barrier recorded go to
    # the outside, in rank order, so the start line is always fully determined.
    with_barrier = sorted([r for r in ordered if r['barrier']], key=lambda r: r['barrier'])
    without_barrier = [r for r in ordered if not r['barrier']]
    for lane, runner in enumerate(with_barrier + without_barrier):
        runner['lane'] = lane

    pace_counts = {category: 0 for category in PACE_CATEGORIES}
    for runner in ordered:
        pace_counts[runner['pace_category']] += 1

    return {
        'success': True,
        'meeting': {
            'id': meeting.id,
            'name': meeting.meeting_name,
            'track': meeting.track or meeting.puntingform_id or '',
            'date': meeting.date.isoformat() if meeting.date else None,
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
            'has_silks': bool(silks['sprite_url']),
        },
        'weights': weights_as_percentages(blend),
        'default_weights': weights_as_percentages(WEIGHTS),
        'weights_are_default': weights_as_percentages(blend) == weights_as_percentages(WEIGHTS),
        'pace_counts': pace_counts,
        'runners': ordered,
    }


def register_race_animation_routes(app, db):
    """Call from app.py after db.init_app(app), like register_mma_routes."""

    @race_animation_bp.route('/race-animations-predictions')
    @login_required
    def race_animations_predictions():
        return render_template('race-animations-predictions.html')

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
        return jsonify({
            'success': True,
            'meetings': [{
                'id': m.id,
                'name': m.meeting_name,
                'track': m.track or m.puntingform_id or '',
                'date': m.date.isoformat() if m.date else None,
                'race_count': len(m.races),
            } for m in meetings],
        })

    @race_animation_bp.route('/api/race-animation/meeting/<int:meeting_id>/races')
    @login_required
    def api_race_animation_races(meeting_id):
        """Races within a meeting, for the second dropdown."""
        meeting = Meeting.query.get_or_404(meeting_id)
        races = (Race.query
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
            race = Race.query.get_or_404(race_id)
            meeting = Meeting.query.get(race.meeting_id)
            if meeting is None:
                return jsonify({'success': False, 'error': 'Meeting not found for this race'}), 404
            include_silks = request.args.get('silks', '1') != '0'
            payload = build_race_payload(
                race, meeting,
                include_silks=include_silks,
                weights=_weights_from_request(request.args),
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

    app.register_blueprint(race_animation_bp)
    logger.info("✓ Race animation routes registered")
