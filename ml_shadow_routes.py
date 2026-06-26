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
from datetime import datetime

from flask import render_template, jsonify, request, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import text

log = logging.getLogger(__name__)


def _meeting_date_string(meeting):
    """Return YYYY-MM-DD for a meeting, using the date column or YYMMDD_Track name."""
    if getattr(meeting, 'date', None):
        return meeting.date.strftime('%Y-%m-%d')

    name = meeting.meeting_name or ''
    if '_' not in name:
        return None

    date_part = name.split('_', 1)[0]
    if len(date_part) != 6 or not date_part.isdigit():
        return None

    return f"20{date_part[:2]}-{date_part[2:4]}-{date_part[4:6]}"


def _normalise_runner_name(value):
    return ' '.join((value or '').strip().lower().split())


def _settle_meeting_results(db, meeting, recorded_by):
    """Fetch PuntingForm results for one meeting and upsert any runner results."""
    from models import Race, Result
    from puntingform_service import PuntingFormService

    if not meeting.puntingform_id:
        return {'meeting_id': meeting.id, 'meeting_name': meeting.meeting_name, 'status': 'skipped', 'reason': 'not a PuntingForm meeting'}

    date_str = _meeting_date_string(meeting)
    if not date_str:
        return {'meeting_id': meeting.id, 'meeting_name': meeting.meeting_name, 'status': 'skipped', 'reason': 'missing meeting date'}

    pf_service = PuntingFormService()
    response = pf_service.get_results(meeting.puntingform_id, date_str)
    if response.get('IsError'):
        return {'meeting_id': meeting.id, 'meeting_name': meeting.meeting_name, 'status': 'pending', 'reason': 'results not available'}

    races_results = response.get('RaceDetails') or response.get('Result') or []
    if not races_results:
        return {'meeting_id': meeting.id, 'meeting_name': meeting.meeting_name, 'status': 'pending', 'reason': 'no race results returned'}

    updated = created = matched = 0
    for race_result in races_results:
        race_num = race_result.get('RaceNumber') or race_result.get('RaceNo') or race_result.get('Race')
        race = Race.query.filter_by(meeting_id=meeting.id, race_number=race_num).first()
        if not race:
            continue

        horses_by_name = {_normalise_runner_name(h.horse_name): h for h in race.horses}
        for runner in race_result.get('Runners', []) or []:
            horse_name = runner.get('Name') or runner.get('Horse') or runner.get('RunnerName')
            horse = horses_by_name.get(_normalise_runner_name(horse_name))
            if not horse:
                continue

            finish_pos = runner.get('Position') or runner.get('FinishPosition') or runner.get('Place') or 0
            try:
                finish_pos = int(finish_pos or 0)
            except (TypeError, ValueError):
                finish_pos = 0
            if finish_pos > 4:
                finish_pos = 5

            sp = runner.get('Price_SP') or runner.get('SP') or runner.get('StartingPrice')
            try:
                sp = float(sp) if sp not in (None, '') else None
            except (TypeError, ValueError):
                sp = None

            matched += 1
            if horse.result:
                horse.result.finish_position = finish_pos
                horse.result.sp = sp
                horse.result.recorded_at = datetime.utcnow()
                horse.result.recorded_by = recorded_by
                updated += 1
            else:
                db.session.add(Result(
                    horse_id=horse.id,
                    finish_position=finish_pos,
                    sp=sp,
                    recorded_by=recorded_by,
                ))
                created += 1

    return {
        'meeting_id': meeting.id,
        'meeting_name': meeting.meeting_name,
        'status': 'settled' if matched else 'pending',
        'matched': matched,
        'created': created,
        'updated': updated,
    }



def _score_meeting_ml(db, meeting_id):
    """Generate and persist ML scores for one meeting, mirroring the manual button."""
    from ml_predict import predict_meeting
    from models import Prediction

    all_scores, _by_race = predict_meeting(meeting_id, db.session)
    if not all_scores:
        return {
            'success': False,
            'scored': 0,
            'reason': 'No scores generated — model may not be loaded or meeting has no active horses.',
        }

    updated = 0
    for horse_id, ml_score in all_scores.items():
        pred = Prediction.query.filter_by(horse_id=horse_id).first()
        if pred:
            pred.ml_score = ml_score
            updated += 1

    return {'success': True, 'scored': updated}


def _visible_ml_shadow_meetings_query():
    """Base query for meetings shown in the ML Shadow dropdown."""
    from models import Meeting

    return Meeting.query.order_by(Meeting.uploaded_at.desc()).limit(100)


def _ml_scored_meeting_ids(db):
    """Return meeting ids with at least one persisted ML score."""
    rows = db.session.execute(text("""
        SELECT DISTINCT rc.meeting_id
        FROM predictions p
        JOIN horses h ON h.id = p.horse_id
        JOIN races rc ON rc.id = h.race_id
        WHERE p.ml_score IS NOT NULL
    """)).fetchall()
    return {r[0] for r in rows}

def _unsettled_puntingform_meetings_sql():
    """Return SQL for PuntingForm meetings that still have active runners without results.

    This intentionally does not require generated ML scores: the ML Shadow
    dashboard can settle results before or after a meeting is scored.
    """
    return """
                SELECT DISTINCT m.*
                FROM meetings m
                JOIN races rc ON rc.meeting_id = m.id
                JOIN horses h ON h.race_id = rc.id
                LEFT JOIN results r ON r.horse_id = h.id
                WHERE COALESCE(h.is_scratched, FALSE) = FALSE
                  AND r.id IS NULL
                  AND m.puntingform_id IS NOT NULL
                ORDER BY m.uploaded_at DESC
            """


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

        meetings_raw = _visible_ml_shadow_meetings_query().all()
        scored_ids = _ml_scored_meeting_ids(db)

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
            score_result = _score_meeting_ml(db, meeting_id)

            if not score_result['success']:
                return jsonify({'success': False, 'error': score_result['reason']})

            updated = score_result['scored']
            db.session.commit()
            log.info(f"ML shadow: scored {updated} horses for meeting {meeting_id}")
            return jsonify({'success': True, 'scored': updated})

        except Exception as e:
            db.session.rollback()
            log.error(f"ML shadow score failed: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500


    @app.route('/api/ml-shadow/score-visible', methods=['POST'])
    @login_required
    def ml_shadow_score_visible():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            meetings = _visible_ml_shadow_meetings_query().all()
            scored_ids = _ml_scored_meeting_ids(db)
            details = []
            generated = 0
            skipped = 0

            for meeting in meetings:
                if meeting.id in scored_ids:
                    skipped += 1
                    details.append({
                        'meeting_id': meeting.id,
                        'meeting_name': meeting.meeting_name,
                        'status': 'skipped',
                        'reason': 'already scored',
                    })
                    continue

                try:
                    score_result = _score_meeting_ml(db, meeting.id)
                    if score_result.get('success'):
                        generated += 1
                        details.append({
                            'meeting_id': meeting.id,
                            'meeting_name': meeting.meeting_name,
                            'status': 'generated',
                            'scored': score_result.get('scored', 0),
                        })
                    else:
                        details.append({
                            'meeting_id': meeting.id,
                            'meeting_name': meeting.meeting_name,
                            'status': 'error',
                            'reason': score_result.get('reason', 'No scores generated'),
                        })
                except Exception as exc:
                    log.warning("ML shadow bulk score failed for meeting %s: %s", meeting.id, exc, exc_info=True)
                    details.append({
                        'meeting_id': meeting.id,
                        'meeting_name': meeting.meeting_name,
                        'status': 'error',
                        'reason': str(exc),
                    })

            db.session.commit()

            checked = len(meetings)
            summary = f"Checked {checked} meetings. Generated ML scores for {generated} meetings. Skipped {skipped} already scored."
            return jsonify({
                'success': True,
                'meetings_checked': checked,
                'meetings_generated': generated,
                'meetings_skipped': skipped,
                'summary': summary,
                'details': details,
            })

        except Exception as e:
            db.session.rollback()
            log.error(f"ML shadow bulk score failed: {e}", exc_info=True)
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

    @app.route('/api/ml-shadow/settle-all', methods=['POST'])
    @login_required
    def ml_shadow_settle_all():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            from models import Meeting

            unsettled_meetings = Meeting.query.from_statement(
                text(_unsettled_puntingform_meetings_sql())
            ).all()

            details = []
            for meeting in unsettled_meetings:
                try:
                    score_result = _score_meeting_ml(db, meeting.id)
                    settle_result = _settle_meeting_results(db, meeting, current_user.id)
                    settle_result['ml_scored'] = score_result.get('scored', 0)
                    if not score_result.get('success'):
                        settle_result['ml_score_warning'] = score_result.get('reason')
                    details.append(settle_result)
                except Exception as exc:
                    log.warning("ML shadow settle failed for meeting %s: %s", meeting.id, exc, exc_info=True)
                    details.append({
                        'meeting_id': meeting.id,
                        'meeting_name': meeting.meeting_name,
                        'status': 'error',
                        'reason': str(exc),
                    })

            db.session.commit()

            settled = sum(1 for d in details if d.get('status') == 'settled')
            created = sum(d.get('created', 0) for d in details)
            updated = sum(d.get('updated', 0) for d in details)
            ml_scored = sum(d.get('ml_scored', 0) for d in details)
            ml_warnings = [d for d in details if d.get('ml_score_warning')]
            return jsonify({
                'success': True,
                'meetings_checked': len(details),
                'meetings_settled': settled,
                'results_created': created,
                'results_updated': updated,
                'ml_scores_generated': ml_scored,
                'ml_score_warnings': len(ml_warnings),
                'details': details,
            })

        except Exception as e:
            db.session.rollback()
            log.error(f"ML shadow settle-all failed: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/ml-shadow/global-stats')
    @login_required
    def ml_shadow_global_stats():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            rows = db.session.execute(text("""
                SELECT
                    rc.id AS race_id,
                    m.date AS meeting_date,
                    m.uploaded_at AS meeting_uploaded_at,
                    rc.race_number,
                    MAX(r.recorded_at) OVER (PARTITION BY rc.id) AS latest_result_at,
                    p.score AS analyzer_score,
                    p.ml_score,
                    h.id AS horse_id,
                    r.finish_position,
                    r.sp
                FROM predictions p
                JOIN horses h ON h.id = p.horse_id
                JOIN races rc ON rc.id = h.race_id
                JOIN meetings m ON m.id = rc.meeting_id
                JOIN results r ON r.horse_id = h.id
                WHERE p.ml_score IS NOT NULL
                  AND COALESCE(h.is_scratched, FALSE) = FALSE
                  AND r.finish_position IS NOT NULL
                  AND r.finish_position > 0
                  AND r.sp IS NOT NULL
            """)).fetchall()

            from collections import defaultdict
            races = defaultdict(list)
            for row in rows:
                races[row.race_id].append(row)

            STAKE = 10.0
            race_results = []
            for race_id, horses in races.items():
                if not horses:
                    continue
                top_a = max(horses, key=lambda x: x.analyzer_score or 0)
                top_m = max(horses, key=lambda x: x.ml_score or 0)

                a_won = top_a.finish_position == 1
                m_won = top_m.finish_position == 1
                a_profit = (top_a.sp * STAKE - STAKE) if (a_won and top_a.sp) else -STAKE
                m_profit = (top_m.sp * STAKE - STAKE) if (m_won and top_m.sp) else -STAKE

                sort_value = top_m.meeting_date or top_m.latest_result_at or top_m.meeting_uploaded_at
                sort_key = sort_value.isoformat() if sort_value else ''
                race_results.append({
                    'race_id': race_id,
                    'race_number': top_m.race_number or 0,
                    'sort_key': sort_key,
                    'meeting_uploaded_at': top_m.meeting_uploaded_at,
                    'latest_result_at': top_m.latest_result_at,
                    'a_won': a_won,
                    'm_won': m_won,
                    'a_profit': a_profit,
                    'm_profit': m_profit,
                    'agree': top_a.horse_id == top_m.horse_id,
                })

            race_results.sort(
                key=lambda r: (
                    bool(r['sort_key']),
                    r['sort_key'],
                    r['race_number'],
                    r['race_id'],
                ),
                reverse=True,
            )

            def summarise(sample):
                n = len(sample)
                a_wins = sum(1 for r in sample if r['a_won'])
                m_wins = sum(1 for r in sample if r['m_won'])
                a_profit = sum(r['a_profit'] for r in sample)
                m_profit = sum(r['m_profit'] for r in sample)
                agree = sum(1 for r in sample if r['agree'])
                ml_total_return = m_profit / STAKE + n
                ml_total_profit = ml_total_return - n
                return {
                    'races': n,
                    'a_wins': a_wins,
                    'a_roi': round(a_profit / (n * STAKE) * 100, 1) if n else None,
                    'm_wins': m_wins,
                    'm_roi': round(ml_total_profit / n * 100, 2) if n else None,
                    'agree_rate': round(agree / n * 100, 1) if n else None,
                    'ml_performance': {
                        'selections': n,
                        'wins': m_wins,
                        'strike_rate': round(m_wins / n * 100, 2) if n else None,
                        'total_stake': n,
                        'total_return': round(ml_total_return, 2),
                        'total_profit': round(ml_total_profit, 2),
                        'roi': round(ml_total_profit / n * 100, 2) if n else None,
                    },
                }

            n = len(race_results)
            payload = summarise(race_results)
            payload['windows'] = [
                {'label': f'Last {limit:,} races', 'limit': limit, **summarise(race_results[:limit])}
                for limit in (50, 100, 200, 500, 1000, 2000, 3000, 4000)
                if n >= limit
            ]
            if n and (not payload['windows'] or payload['windows'][-1]['races'] != n):
                payload['windows'].append({'label': 'All ML races', 'limit': 'all', **summarise(race_results)})
            return jsonify(payload)

        except Exception as e:
            log.error(f"Global stats failed: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    log.info("✓ ML shadow routes registered at /ml-shadow")
