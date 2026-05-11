"""
ml_shadow_routes.py  — Shadow ML scoring routes.

Register in app.py with:
    from ml_shadow_routes import register_ml_shadow_routes
    register_ml_shadow_routes(app, db)

Adds:
    GET  /ml-shadow                              — comparison dashboard
    POST /api/ml-shadow/score/<meeting_id>       — trigger ML scoring for a meeting
    GET  /api/ml-shadow/results/<meeting_id>     — JSON results for a meeting
    GET  /api/ml-shadow/global-stats             — aggregate ROI stats
"""

import logging
from flask import render_template, jsonify, request, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import text

log = logging.getLogger(__name__)


def register_ml_shadow_routes(app, db):
    """Call this from app.py to add ML shadow routes."""

    with app.app_context():
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS ml_score FLOAT"
                ))
                conn.commit()
            log.info("✓ ml_score column ready on predictions table")
        except Exception as e:
            log.warning(f"ml_score migration: {e}")

    @app.route('/ml-shadow')
    @login_required
    def ml_shadow():
        if not current_user.is_admin:
            return redirect(url_for('history'))

        from models import Meeting

        meetings_raw = Meeting.query.order_by(Meeting.uploaded_at.desc()).limit(100).all()

        rows = db.session.execute(text("""
            SELECT DISTINCT rc.meeting_id
            FROM predictions p
            JOIN horses h ON h.id = p.horse_id
            JOIN races rc ON rc.id = h.race_id
            WHERE p.ml_score IS NOT NULL
        """)).fetchall()
        scored_ids = {r[0] for r in rows}

        meetings = []
        for m in meetings_raw:
            meetings.append({
                'id': m.id,
                'meeting_name': m.meeting_name,
                'has_ml': m.id in scored_ids,
            })

        selected_id = request.args.get('meeting_id', type=int)
        return render_template('ml_shadow.html',
                               meetings=meetings,
                               selected_id=selected_id)

    @app.route('/api/ml-shadow/score/<int:meeting_id>', methods=['POST'])
    @login_required
    def ml_shadow_score(meeting_id):
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            from ml_predict import predict_meeting
            from models import Prediction

            all_scores, by_race = predict_meeting(meeting_id, db.session)

            if not all_scores:
                return jsonify({'success': False, 'error': 'No scores generated — model may not be loaded or meeting has no active horses.'})

            updated = 0
            for horse_id, ml_score in all_scores.items():
                pred = Prediction.query.filter_by(horse_id=horse_id).first()
                if pred:
                    pred.ml_score = ml_score
                    updated += 1

            db.session.commit()
            log.info(f"ML shadow: scored {updated} horses for meeting {meeting_id}")
            return jsonify({'success': True, 'scored': updated})

        except Exception as e:
            db.session.rollback()
            log.error(f"ML shadow score failed: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/ml-shadow/results/<int:meeting_id>')
    @login_required
    def ml_shadow_results(meeting_id):
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            from models import Meeting, Race

            meeting = Meeting.query.get_or_404(meeting_id)
            races   = Race.query.filter_by(meeting_id=meeting_id).order_by(Race.race_number).all()

            races_out = []
            for race in races:
                horses_out = []
                for horse in race.horses:
                    if horse.is_scratched:
                        continue
                    pred   = horse.prediction
                    result = horse.result

                    horses_out.append({
                        'horse_id':        horse.id,
                        'horse_name':      horse.horse_name,
                        'barrier':         horse.barrier,
                        'jockey':          horse.jockey,
                        'analyzer_score':  round(pred.score, 2) if pred else None,
                        'ml_score':        round(pred.ml_score, 2) if (pred and pred.ml_score is not None) else None,
                        'finish_position': result.finish_position if result else 0,
                        'sp':              result.sp if result else None,
                    })

                if horses_out:
                    races_out.append({
                        'race_number':     race.race_number,
                        'distance':        race.distance,
                        'track_condition': race.track_condition,
                        'race_class':      race.race_class,
                        'horses':          horses_out,
                    })

            return jsonify({'races': races_out, 'meeting_name': meeting.meeting_name})

        except Exception as e:
            log.error(f"ML shadow results failed: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/ml-shadow/global-stats')
    @login_required
    def ml_shadow_global_stats():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            rows = db.session.execute(text("""
                SELECT
                    rc.id AS race_id,
                    p.score AS analyzer_score,
                    p.ml_score,
                    h.id AS horse_id,
                    r.finish_position,
                    r.sp
                FROM predictions p
                JOIN horses h ON h.id = p.horse_id
                JOIN races rc ON rc.id = h.race_id
                LEFT JOIN results r ON r.horse_id = h.id
                WHERE p.ml_score IS NOT NULL
                  AND h.is_scratched = FALSE
                  AND r.finish_position > 0
            """)).fetchall()

            from collections import defaultdict
            races = defaultdict(list)
            for row in rows:
                races[row.race_id].append(row)

            a_wins = m_wins = races_with_result = 0
            a_profit = m_profit = 0.0
            agree = 0
            STAKE = 10.0

            for race_id, horses in races.items():
                if not horses:
                    continue
                top_a = max(horses, key=lambda x: x.analyzer_score or 0)
                top_m = max(horses, key=lambda x: x.ml_score or 0)
                races_with_result += 1

                a_won = top_a.finish_position == 1
                m_won = top_m.finish_position == 1

                a_profit += (top_a.sp * STAKE - STAKE) if (a_won and top_a.sp) else -STAKE
                m_profit += (top_m.sp * STAKE - STAKE) if (m_won and top_m.sp) else -STAKE

                if a_won: a_wins += 1
                if m_won: m_wins += 1
                if top_a.horse_id == top_m.horse_id: agree += 1

            n = races_with_result
            return jsonify({
                'races':      n,
                'a_wins':     a_wins,
                'a_roi':      round(a_profit / (n * STAKE) * 100, 1) if n else None,
                'm_wins':     m_wins,
                'm_roi':      round(m_profit / (n * STAKE) * 100, 1) if n else None,
                'agree_rate': round(agree / n * 100, 1) if n else None,
            })

        except Exception as e:
            log.error(f"Global stats failed: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    log.info("✓ ML shadow routes registered at /ml-shadow")
