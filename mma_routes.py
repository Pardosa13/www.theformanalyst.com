"""
mma_routes.py - Flask blueprint for the MMA page.
Registered in app.py exactly like afl_routes.py.

Routes:
  GET  /mma              -> mma.html (main page)
  GET  /api/mma/events   -> JSON: upcoming events + predictions
  GET  /api/mma/fighter/<name> -> JSON: fighter stats
"""

import json
import logging
from datetime import datetime, date

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

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

            result = []
            for ev in event_rows:
                event_id, ev_name, ev_date, ev_loc, ev_completed, ev_url = ev

                fights_sql = text("""
                    SELECT
                        f.id, f.fighter_1_name, f.fighter_2_name,
                        f.weight_class, f.is_main_card, f.is_title_fight,
                        f.winner_name, f.method, f.round_ended, f.time_ended,
                        f.f1_height, f.f1_reach, f.f1_stance, f.f1_record,
                        f.f2_height, f.f2_reach, f.f2_stance, f.f2_record,
                        p.predicted_winner, p.f1_win_probability,
                        p.f2_win_probability, p.confidence, p.factors_json,
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
                    ORDER BY f.is_main_card DESC, f.id ASC
                """)
                fight_rows = db.session.execute(fights_sql, {'eid': event_id}).fetchall()

                fights = []
                for fr in fight_rows:
                    (fight_id, f1, f2, wc, is_main, is_title,
                     winner, method, rnd, t_end,
                     f1h, f1r, f1s, f1rec,
                     f2h, f2r, f2s, f2rec,
                     pred_winner, f1_prob, f2_prob, conf, factors_json,
                     f1_headshot_url, f2_headshot_url) = fr

                    factors = {}
                    if factors_json:
                        try:
                            factors = json.loads(factors_json) if isinstance(factors_json, str) else factors_json
                        except Exception:
                            pass

                    fights.append({
                        'id': fight_id,
                        'fighter_1': f1,
                        'fighter_2': f2,
                        'weight_class': wc or '',
                        'is_main_card': bool(is_main),
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
                            'f1_prob': float(f1_prob) if f1_prob else 0.5,
                            'f2_prob': float(f2_prob) if f2_prob else 0.5,
                            'confidence': conf or '50.0%',
                            'factors': factors,
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
                return jsonify({'error': 'Fighter not found'}), 404

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

    app.register_blueprint(mma_bp)
    logger.info("✓ MMA routes registered")
