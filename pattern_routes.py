"""Pattern Finder page (admin only).

Register in app.py with:
    from pattern_routes import register_pattern_routes
    register_pattern_routes(app, db)

Adds:
    GET  /pattern-finder       proven patterns, live record, today's matches, last run
    POST /pattern-finder/run   start a run in the background
"""
import json
import logging
import subprocess
import sys
import threading

from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required

log = logging.getLogger(__name__)

_run_lock = threading.Lock()


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def register_pattern_routes(app, db):
    from models import PatternFinderRun, PatternRule

    @app.route('/pattern-finder')
    @login_required
    def pattern_finder_page():
        if not current_user.is_admin:
            flash("Access denied. Admin privileges required.", "danger")
            return redirect(url_for('history'))
        import pattern_finder as pf

        run = report = None
        rules = []
        error = None
        try:
            run = PatternFinderRun.query.order_by(PatternFinderRun.id.desc()).first()
            report = _loads(run.report_json, {}) if run else None
            for rule in PatternRule.query.order_by(PatternRule.proven_at.desc()).all():
                conditions = _loads(rule.conditions_json, [])
                rules.append({
                    'id': rule.id, 'label': rule.label, 'status': rule.status,
                    'conditions': conditions,
                    'jump': [pf.condition_info(k)[0] for k in conditions if pf.condition_info(k)[2]],
                    'proven_at': rule.proven_at, 'proven_through': rule.proven_through,
                    'retired_at': rule.retired_at, 'retire_reason': rule.retire_reason,
                    'history': _loads(rule.history_json, {}),
                    'live': _loads(rule.live_json, {}),
                })
        except Exception as exc:   # tables not created yet, bad row...
            log.warning("Pattern Finder page could not load: %s", exc)
            db.session.rollback()
            error = str(exc)

        proven = [r for r in rules if r['status'] == 'proven']
        retired = [r for r in rules if r['status'] == 'retired']
        matches = []
        if proven:
            try:
                matches = pf.todays_matches(db.session.connection(), proven)
            except Exception as exc:
                log.warning("Pattern Finder today's matches failed: %s", exc)
                db.session.rollback()

        luck = (report or {}).get('luck') or []
        luck_summary = None
        if luck:
            luck_summary = {
                'runs': len(luck),
                'passed_search': round(sum(x['passed_search'] for x in luck) / len(luck), 1),
                'proven': round(sum(x['proven'] for x in luck) / len(luck), 1),
            }
        return render_template(
            'pattern_finder.html', run=run, report=report, proven=proven, retired=retired,
            matches=matches, luck_summary=luck_summary, running=_run_lock.locked(), error=error,
        )

    @app.route('/pattern-finder/run', methods=['POST'])
    @login_required
    def pattern_finder_run():
        if not current_user.is_admin:
            flash("Access denied. Admin privileges required.", "danger")
            return redirect(url_for('history'))
        if not _run_lock.acquire(blocking=False):
            flash('The Pattern Finder is already running.', 'info')
            return redirect(url_for('pattern_finder_page'))

        def work():
            try:
                subprocess.run([sys.executable, 'pattern_finder.py'], timeout=3600, check=True)
            except Exception as exc:
                log.error("Manual Pattern Finder run failed: %s", exc)
            finally:
                _run_lock.release()

        threading.Thread(target=work, daemon=True).start()
        flash('Pattern Finder started. Refresh in a few minutes.', 'info')
        return redirect(url_for('pattern_finder_page'))
