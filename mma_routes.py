"""
mma_routes.py - Flask blueprint for the MMA page.
Registered in app.py exactly like afl_routes.py.

Routes:
  GET  /mma              -> mma.html (main page)
  GET  /api/mma/events   -> JSON: upcoming events + predictions
  GET  /api/mma/fighter/<name> -> JSON: fighter stats
  GET  /api/mma/edge-finder    -> JSON: value bets based on model vs bookmaker odds
  POST /api/mma/sync/odds      -> Trigger manual odds refresh from The Odds API
"""

import json
import logging
from datetime import datetime, date

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from mma_data import normalise_name

logger = logging.getLogger(__name__)

mma_bp = Blueprint('mma', __name__)


def register_mma_routes(app, db):
    """
    Call this from app.py after db.init_app(app).
    Mirrors the register_afl_routes(app, db) pattern exactly.
    """

    @mma_bp.route('/mma')
    @login_required
    def mma_hub():
        return render_template('mma.html')

    @mma_bp.route('/api/mma/events')
    @login_required
    def api_mma_events():
        """
        Returns upcoming events with fights and predictions.
        Queries directly from Postgres via raw SQL to avoid
        importing mma_models classes (they're registered dynamically).
        """
        try:
            from sqlalchemy import text

            # Fetch upcoming + most recent completed event
            events_sql = text("""
                SELECT id, name, date, location, is_completed, espn_url
                FROM mma_events
                ORDER BY
                    CASE WHEN is_completed = FALSE THEN 0 ELSE 1 END,
                    date ASC
                LIMIT 10
            """)
            event_rows = db.session.execute(events_sql).fetchall()

            headshot_rows = db.session.execute(text("""
                SELECT id, full_name, headshot_url
                FROM mma_fighters
                WHERE NULLIF(TRIM(headshot_url), '') IS NOT NULL
            """)).fetchall()
            headshots_by_id = {}
            headshots_by_norm = {}
            headshots_by_last_name = {}
            for fighter_id, fighter_name, headshot_url in headshot_rows:
                if fighter_id:
                    headshots_by_id[str(fighter_id)] = headshot_url
                norm = normalise_name(fighter_name)
                if norm and norm not in headshots_by_norm:
                    headshots_by_norm[norm] = headshot_url
                last_name = norm.split()[-1] if norm else ""
                if last_name:
                    headshots_by_last_name.setdefault(last_name, set()).add(headshot_url)

            def resolve_headshot(fighter_name, current_headshot, fighter_id=None):
                current_headshot = (current_headshot or '').strip()
                if current_headshot:
                    return current_headshot
                if fighter_id and str(fighter_id) in headshots_by_id:
                    return headshots_by_id[str(fighter_id)]
                if not fighter_name:
                    return None

                norm_fighter_name = normalise_name(fighter_name)
                direct = headshots_by_norm.get(norm_fighter_name)
                if direct:
                    return direct

                # ESPN/Odds API names can differ from the seeded fighter table
                # by middle names, suffixes, punctuation, or accents. Prefer a
                # unique containment match before falling back to a unique last
                # name match to avoid assigning the wrong fighter's headshot.
                containment_matches = [
                    url for norm, url in headshots_by_norm.items()
                    if norm_fighter_name and (
                        norm_fighter_name in norm or norm in norm_fighter_name
                    )
                ]
                if len(set(containment_matches)) == 1:
                    return containment_matches[0]

                last_name = norm_fighter_name.split()[-1] if norm_fighter_name else ""
                matched = list(headshots_by_last_name.get(last_name, set()))
                return matched[0] if len(matched) == 1 else None

            result = []
            for ev in event_rows:
                event_id, ev_name, ev_date, ev_loc, ev_completed, ev_url = ev

                fights_sql = text("""
                    SELECT
                        f.id, f.bout_uid, f.status, f.fighter_1_id, f.fighter_2_id,
                        f.fighter_1_name, f.fighter_2_name,
                        f.weight_class, f.is_main_card, f.card_section, f.bout_order, f.is_title_fight,
                        f.winner_name, f.method, f.round_ended, f.time_ended,
                        f.f1_height, f.f1_reach, f.f1_stance, f.f1_record,
                        f.f2_height, f.f2_reach, f.f2_stance, f.f2_record,
                        p.predicted_winner, p.f1_win_probability,
                        p.f2_win_probability, p.confidence, p.factors_json, p.model_version,
                        COALESCE(
                            mf1.headshot_url,
                            (SELECT headshot_url FROM mma_fighters
                             WHERE LOWER(full_name) = LOWER(f.fighter_1_name)
                             LIMIT 1)
                        ) AS f1_headshot_url,
                        COALESCE(
                            mf2.headshot_url,
                            (SELECT headshot_url FROM mma_fighters
                             WHERE LOWER(full_name) = LOWER(f.fighter_2_name)
                             LIMIT 1)
                        ) AS f2_headshot_url
                    FROM mma_fights f
                    LEFT JOIN mma_predictions p ON p.fight_id = f.id
                    LEFT JOIN mma_fighters mf1 ON mf1.id = f.fighter_1_id
                    LEFT JOIN mma_fighters mf2 ON mf2.id = f.fighter_2_id
                    WHERE f.event_id = :eid
                      AND COALESCE(f.is_active, TRUE) = TRUE
                      AND COALESCE(f.status, 'confirmed') IN ('confirmed', 'completed')
                      AND NULLIF(TRIM(f.fighter_1_name), '') IS NOT NULL
                      AND NULLIF(TRIM(f.fighter_2_name), '') IS NOT NULL
                      AND LOWER(TRIM(f.fighter_1_name)) NOT IN ('tba', 'tbd', 'opponent tba', 'opponent tbd')
                      AND LOWER(TRIM(f.fighter_2_name)) NOT IN ('tba', 'tbd', 'opponent tba', 'opponent tbd')
                    ORDER BY
                        CASE COALESCE(f.card_section, CASE WHEN f.is_main_card THEN 'main_card' ELSE 'prelims' END)
                            WHEN 'main_card' THEN 1
                            WHEN 'prelims' THEN 2
                            WHEN 'early_prelims' THEN 3
                            ELSE 4
                        END ASC,
                        f.bout_order DESC NULLS LAST,
                        f.id ASC
                """)
                fight_rows = db.session.execute(fights_sql, {'eid': event_id}).fetchall()

                fights = []
                for fr in fight_rows:
                    (fight_id, bout_uid, status, f1_id, f2_id, f1, f2, wc, is_main, card_section, bout_order, is_title,
                     winner, method, rnd, t_end,
                     f1h, f1r, f1s, f1rec,
                     f2h, f2r, f2s, f2rec,
                     pred_winner, f1_prob, f2_prob, conf, factors_json, model_version,
                     f1_headshot_url, f2_headshot_url) = fr

                    f1_headshot_url = resolve_headshot(f1, f1_headshot_url, f1_id)
                    f2_headshot_url = resolve_headshot(f2, f2_headshot_url, f2_id)

                    factors = {}
                    if factors_json:
                        try:
                            factors = json.loads(factors_json) if isinstance(factors_json, str) else factors_json
                        except Exception:
                            pass

                    fights.append({
                        'id': fight_id,
                        'bout_uid': bout_uid,
                        'status': status,
                        'fighter_1': f1,
                        'fighter_2': f2,
                        'weight_class': wc or '',
                        'is_main_card': bool(is_main),
                        'card_section': card_section or ('main_card' if is_main else 'prelims'),
                        'bout_order': bout_order,
                        'is_title_fight': bool(is_title),
                        'result': {
                            'winner': winner,
                            'method': method,
                            'round': rnd,
                            'time': t_end,
                        } if winner else None,
                        'f1_stats': {
                            'Height': f1h, 'Reach': f1r,
                            'Stance': f1s, 'Record': f1rec,
                        },
                        'f2_stats': {
                            'Height': f2h, 'Reach': f2r,
                            'Stance': f2s, 'Record': f2rec,
                        },
                        'f1_headshot_url': f1_headshot_url or None,
                        'f2_headshot_url': f2_headshot_url or None,
                        'prediction': {
                            'winner': pred_winner,
                            'f1_prob': float(f1_prob),
                            'f2_prob': float(f2_prob),
                            'confidence': conf or '50.0%',
                            'factors': factors,
                            'model_version': model_version or 'catboost_v1',
                            'is_fallback': model_version == 'fallback_5050',
                        } if pred_winner else None,
                    })

                result.append({
                    'event_id': event_id,
                    'event_name': ev_name,
                    'date': ev_date.isoformat() if ev_date else None,
                    'location': ev_loc or '',
                    'is_completed': bool(ev_completed),
                    'url': ev_url or '',
                    'fights': fights,
                })

            return jsonify(result)

        except Exception as e:
            logger.error(f"MMA events API error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @mma_bp.route('/api/mma/fighter/<path:fighter_name>')
    @login_required
    def api_mma_fighter(fighter_name):
        """Return stats for a single fighter by name (case-insensitive)."""
        try:
            from sqlalchemy import text
            sql = text("""
                SELECT id, full_name, nickname, height_cm, weight_lbs, reach_cm,
                       stance, wins, losses, draws,
                       glicko_rating, glicko_rd,
                       ema_slpm, ema_sapm, ema_td_avg, ema_td_def,
                       ema_kd_rate, ema_sub_rate, ema_ctrl_pct,
                       streak, win_rate, total_fights, recent_form,
                       headshot_url
                FROM mma_fighters
                WHERE LOWER(full_name) = LOWER(:name)
                LIMIT 1
            """)
            row = db.session.execute(sql, {'name': fighter_name}).fetchone()
            if not row:
                rows = db.session.execute(text("""
                    SELECT id, full_name, nickname, height_cm, weight_lbs, reach_cm,
                           stance, wins, losses, draws,
                           glicko_rating, glicko_rd,
                           ema_slpm, ema_sapm, ema_td_avg, ema_td_def,
                           ema_kd_rate, ema_sub_rate, ema_ctrl_pct,
                           streak, win_rate, total_fights, recent_form,
                           headshot_url
                    FROM mma_fighters
                """)).fetchall()
                target = normalise_name(fighter_name)
                matches = [r for r in rows if normalise_name(r[1]) == target]
                if not matches:
                    matches = [
                        r for r in rows
                        if target and (
                            target in normalise_name(r[1])
                            or normalise_name(r[1]) in target
                        )
                    ]
                if len(matches) != 1:
                    return jsonify({'error': 'Fighter not found'}), 404
                row = matches[0]

            return jsonify({
                'id': row[0], 'name': row[1], 'nickname': row[2],
                'height_cm': row[3], 'weight_lbs': row[4], 'reach_cm': row[5],
                'stance': row[6], 'wins': row[7], 'losses': row[8], 'draws': row[9],
                'glicko_rating': round(row[10]) if row[10] else 1500,
                'glicko_rd': round(row[11]) if row[11] else 350,
                'stats': {
                    'slpm': round(row[12], 2) if row[12] else 0,
                    'sapm': round(row[13], 2) if row[13] else 0,
                    'td_avg': round(row[14], 2) if row[14] else 0,
                    'td_def': round(row[15], 2) if row[15] else 0,
                    'kd_rate': round(row[16], 2) if row[16] else 0,
                    'sub_rate': round(row[17], 2) if row[17] else 0,
                    'ctrl_pct': round(row[18], 1) if row[18] else 0,
                },
                'streak': row[19], 'win_rate': round(row[20], 3) if row[20] else 0,
                'total_fights': row[21], 'recent_form': row[22],
                'headshot_url': row[23] or None,
            })
        except Exception as e:
            logger.error(f"Fighter API error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @mma_bp.route('/api/mma/edge-finder')
    @login_required
    def api_mma_edge_finder():
        """
        Returns edge bets for upcoming UFC fights.

        For each fight that has a real model prediction AND bookmaker odds in
        mma_fight_odds, calculates:
            edge = (model_win_prob − devigged_bookmaker_implied_prob) × 100

        The implied probability is devigged (normalised against the same
        bookmaker's two-way overround) whenever that bookmaker also quotes
        the opponent; otherwise it falls back to the raw 1/odds price and
        `devigged` is reported as false so the UI can flag it.

        Query params:
            min_edge  float  minimum |edge| to include (default 2.0)
        """
        try:
            from sqlalchemy import text
            from mma_data import calculate_mma_edge, names_match, normalise_name, pairs_match

            # Clamp min_edge to a sensible range
            min_edge = max(0.0, min(100.0, request.args.get('min_edge', 2.0, type=float)))

            # ── 1. Fetch upcoming fights with real (non-fallback) predictions ──
            # model_version = 'fallback_5050' means the CatBoost model was
            # unavailable and predict_fight() wrote a bare coin-flip — any
            # "edge" computed against that would just reflect the bookmaker's
            # own skew/vig, not genuine model insight, so it's excluded here.
            fights_sql = text("""
                SELECT
                    f.id, f.fighter_1_name, f.fighter_2_name,
                    f.weight_class, f.is_main_card, f.card_section, f.bout_order, f.is_title_fight,
                    e.id  AS event_id,
                    e.name AS event_name, e.date AS event_date,
                    p.predicted_winner,
                    p.f1_win_probability, p.f2_win_probability, p.confidence
                FROM mma_fights f
                JOIN mma_events e ON e.id = f.event_id
                LEFT JOIN mma_predictions p ON p.fight_id = f.id
                WHERE e.is_completed = FALSE
                  AND COALESCE(f.is_active, TRUE) = TRUE
                  AND COALESCE(f.status, 'confirmed') = 'confirmed'
                  AND p.predicted_winner IS NOT NULL
                  AND p.f1_win_probability IS NOT NULL
                  AND p.f2_win_probability IS NOT NULL
                  AND COALESCE(p.model_version, 'catboost_v1') <> 'fallback_5050'
                ORDER BY
                    e.date ASC,
                    CASE COALESCE(f.card_section, CASE WHEN f.is_main_card THEN 'main_card' ELSE 'prelims' END)
                        WHEN 'main_card' THEN 1
                        WHEN 'prelims' THEN 2
                        WHEN 'early_prelims' THEN 3
                        ELSE 4
                    END ASC,
                    f.bout_order DESC NULLS LAST,
                    f.id ASC
            """)
            fight_rows = db.session.execute(fights_sql).fetchall()

            if not fight_rows:
                return jsonify({
                    'bets': [],
                    'message': (
                        'No upcoming fights with predictions found. '
                        'Run the weekly MMA sync to generate predictions.'
                    ),
                })

            # ── 2. Fetch recent h2h odds ────────────────────────────────────────
            # A bookmaker that stops quoting a fight leaves its last-known row
            # in place (upsert only updates rows it sees again), so without a
            # recency filter a stale line from weeks ago could still win
            # "best odds" and dominate the edge calculation. Anchor the
            # window to the most recent successful fetch rather than to
            # wall-clock time, since odds only refresh on the weekly cron or
            # a manual "Refresh Odds" click.
            odds_sql = text("""
                WITH latest AS (SELECT MAX(fetched_at) AS ts FROM mma_fight_odds)
                SELECT o.event_key, o.fighter_1_name, o.fighter_2_name,
                       o.commence_time, o.bookmaker, o.fighter_name, o.odds
                FROM mma_fight_odds o, latest
                WHERE latest.ts IS NOT NULL
                  AND o.fetched_at >= latest.ts - INTERVAL '24 hours'
                ORDER BY o.fetched_at DESC
            """)
            odds_rows = db.session.execute(odds_sql).fetchall()

            # Index: normalised_fighter_name → list of odds rows
            odds_by_fighter: dict[str, list] = {}
            for row in odds_rows:
                key = normalise_name(row.fighter_name)
                odds_by_fighter.setdefault(key, []).append(row)

            # ── 3. Build edge bets ────────────────────────────────────────────
            bets = []

            for fr in fight_rows:
                (fight_id, f1, f2, wc, is_main, card_section, bout_order, is_title,
                 event_id, event_name, event_date,
                 pred_winner, f1_prob, f2_prob, confidence) = fr

                if f1_prob is None or f2_prob is None:
                    continue

                # Gather every odds row whose (fighter_1, fighter_2) pair matches
                # this fight once, rather than re-scanning + re-logging per
                # fighter — the old version logged an INFO line per
                # fighter × odds-key × row combination on every request.
                fight_odds_rows = []
                logged_keys = set()
                for norm_key, odds_list in odds_by_fighter.items():
                    if not (names_match(f1, norm_key) or names_match(f2, norm_key)):
                        continue
                    for orow in odds_list:
                        if not pairs_match(f1, f2, orow.fighter_1_name, orow.fighter_2_name):
                            continue
                        fight_odds_rows.append(orow)
                        log_key = (orow.bookmaker, orow.fighter_name)
                        if log_key not in logged_keys:
                            logged_keys.add(log_key)
                            logger.debug(
                                "ODDS_MATCH event_id=%s espn_pair=%s|%s bookmaker=%s fighter=%s odds=%s",
                                event_id, f1, f2, orow.bookmaker, orow.fighter_name, orow.odds,
                            )

                if not fight_odds_rows:
                    continue

                # (bookmaker, normalised fighter) → best odds that bookmaker
                # offers for that fighter, used to devig against the SAME
                # book's opposite-side price (cross-book prices don't share a
                # common overround, so they can't be used to devig each other).
                odds_by_bookmaker_fighter: dict[tuple, float] = {}
                for orow in fight_odds_rows:
                    key = (orow.bookmaker, normalise_name(orow.fighter_name))
                    if key not in odds_by_bookmaker_fighter or (orow.odds or 0) > odds_by_bookmaker_fighter[key]:
                        odds_by_bookmaker_fighter[key] = orow.odds

                for fighter_name, model_prob, opponent_name, opponent_prob in [
                    (f1, float(f1_prob), f2, float(f2_prob)),
                    (f2, float(f2_prob), f1, float(f1_prob)),
                ]:
                    candidates = [orow for orow in fight_odds_rows if names_match(fighter_name, orow.fighter_name)]
                    if not candidates:
                        continue

                    best_row = max(candidates, key=lambda r: r.odds or 0)
                    best_odds = best_row.odds
                    best_bookmaker = best_row.bookmaker

                    if not best_odds or best_odds <= 1.0:
                        continue

                    opponent_odds = odds_by_bookmaker_fighter.get(
                        (best_bookmaker, normalise_name(opponent_name))
                    )

                    edge_data = calculate_mma_edge(
                        model_prob=model_prob,
                        odds=best_odds,
                        opponent_odds=opponent_odds,
                    )
                    edge = edge_data['edge']

                    if abs(edge) < min_edge:
                        continue

                    bets.append({
                        'fight_id': fight_id,
                        'event_id': event_id,
                        'event_name': event_name,
                        'event_date': event_date.isoformat() if event_date else None,
                        'fighter': fighter_name,
                        'opponent': opponent_name,
                        'weight_class': wc or '',
                        'is_main_card': bool(is_main),
                        'card_section': card_section or ('main_card' if is_main else 'prelims'),
                        'bout_order': bout_order,
                        'is_title_fight': bool(is_title),
                        'predicted_winner': pred_winner,
                        'confidence': confidence or '',
                        'model_prob': edge_data['model_prob'],
                        'opponent_model_prob': round(opponent_prob * 100.0, 1),
                        'implied_prob': edge_data['implied_prob'],
                        'odds': edge_data['odds'],
                        'bookmaker': best_bookmaker or '',
                        'devigged': edge_data['devigged'],
                        'edge': edge_data['edge'],
                        'edge_pct': edge_data['edge_pct'],
                        'recommendation': edge_data['recommendation'],
                    })

            # Keep only the best edge per fight-fighter pair and sort
            bets.sort(key=lambda x: abs(x.get('edge', 0)), reverse=True)

            odds_count_sql = text("SELECT COUNT(*) FROM mma_fight_odds")
            total_odds = db.session.execute(odds_count_sql).scalar() or 0

            return jsonify({
                'bets': bets,
                'count': len(bets),
                'min_edge': min_edge,
                'total_odds_rows': total_odds,
                'message': None if bets else (
                    'No value bets found. Odds may not yet be loaded — '
                    'use "Refresh Odds" to fetch from The Odds API, '
                    'or lower the minimum edge threshold.'
                ),
            })

        except Exception as e:
            logger.error("MMA edge-finder API error: %s", e, exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500

    @mma_bp.route('/api/mma/sync/odds', methods=['POST'])
    @login_required
    def api_mma_sync_odds():
        """Manually trigger a UFC odds refresh from The Odds API."""
        try:
            from mma_data import fetch_mma_fight_odds, get_odds_api_key
            from mma_models import upsert_mma_fight_odds
            from sqlalchemy import text

            api_key = get_odds_api_key()
            if not api_key:
                return jsonify({
                    'status': 'error',
                    'message': 'ODDS_API_KEY not configured in environment variables.',
                }), 400

            rows = fetch_mma_fight_odds(api_key=api_key)
            count = upsert_mma_fight_odds(db, rows)

            return jsonify({
                'status': 'ok',
                'rows_synced': count,
            })

        except Exception as e:
            logger.error("MMA odds sync error: %s", e, exc_info=True)
            return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

    app.register_blueprint(mma_bp)
    logger.info("✓ MMA routes registered")
