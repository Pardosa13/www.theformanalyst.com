"""
bet_tracker.py
===============
Admin-only personal bet log ("Bet Tracker"). Logs bets James actually
places across all sports, and reports profit/ROI/progression over time
plus a full stats breakdown. Inspired by the Pardo app, styled to match
The Form Analyst's existing dark theme.

This is a private log of real-world staking — separate from, and does
not touch, the PPE scoring engine.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import datetime, timedelta

from flask import jsonify, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user

from models import Bet

SPORTS = ['Horse Racing', 'Greyhounds', 'AFL', 'MMA', 'Soccer', 'NFL', 'NBA', 'Other']
BOOKIES = ['Sportsbet', 'Betfair', 'Ladbrokes', 'Neds', 'Other']
RESULTS = ['pending', 'won', 'lost', 'refunded']

# Historical monthly net P/L totals carried over from Pardo (a previous
# 3rd-party bet tracker app), exactly as shown in its history screen.
# Pardo only exposed month-end totals, not individual bet records, so each
# month below is imported as a single tagged "summary" bet (see
# import_pardo_history()) rather than reconstructed bet-by-bet.
PARDO_MONTHLY_TOTALS = [
    (2024, 5, 54.64),
    (2024, 6, 834.76),
    (2024, 7, 359.00),
    (2024, 8, -142.80),
    (2024, 9, 2290.37),
    (2024, 10, -229.50),
    (2024, 11, -251.05),
    (2024, 12, 589.67),
    (2025, 1, -73.86),
    (2025, 2, 748.15),
    (2025, 3, 782.04),
    (2025, 4, 152.64),
    (2025, 5, -314.07),
    (2025, 6, -277.84),
    (2025, 7, -313.03),
    (2025, 8, 15.23),
    (2025, 9, 130.65),
    (2025, 10, 726.28),
    (2025, 11, 1024.07),
    (2025, 12, 95.94),
    (2026, 1, 313.74),
    (2026, 2, 213.58),
    (2026, 3, -954.27),
    (2026, 4, 152.90),
    (2026, 5, -50.42),
    (2026, 6, 32.28),
    (2026, 7, 34.83),
    (2026, 8, -59.70),
]


def _pardo_import_tag(year, month):
    return f"[pardo-import:{year:04d}-{month:02d}]"


def _pardo_month_bet(user_id, year, month, profit):
    last_day = calendar.monthrange(year, month)[1]
    date_placed = datetime(year, month, last_day, 12, 0, 0)
    month_name = date_placed.strftime('%B %Y')

    if profit >= 0:
        stake, odds, result = 1.0, round(1.0 + profit, 4), 'won'
        payout = round(stake * odds, 2)
    else:
        stake, odds, result = round(-profit, 2), 2.0, 'lost'
        payout = 0.0

    return Bet(
        user_id=user_id,
        date_placed=date_placed,
        sport='Other',
        selection=f'Pardo import — {month_name}',
        bookie='Other',
        stake=stake,
        odds=odds,
        result=result,
        payout=payout,
        notes=(
            f'Imported historical monthly total from Pardo (previous bet '
            f'tracker app). Net P/L for {month_name}: {profit:+.2f} AU$. '
            f'Not a real individual bet. {_pardo_import_tag(year, month)}'
        ),
    )


def import_pardo_history(db, user):
    """Import the Pardo monthly history for `user`, skipping months already
    imported (matched by the [pardo-import:YYYY-MM] tag in bet notes).
    Returns (created_count, skipped_count). Safe to call repeatedly."""
    created, skipped = 0, 0
    for year, month, profit in PARDO_MONTHLY_TOTALS:
        tag = _pardo_import_tag(year, month)
        exists = Bet.query.filter(
            Bet.user_id == user.id,
            Bet.notes.like(f'%{tag}%'),
        ).first()
        if exists:
            skipped += 1
            continue
        db.session.add(_pardo_month_bet(user.id, year, month, profit))
        created += 1
    db.session.commit()
    return created, skipped


def _admin_gate():
    """Return a redirect response if the current user isn't an admin, else None."""
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("history"))
    return None


def _serialize(bet: Bet) -> dict:
    return {
        'id': bet.id,
        'date_placed': bet.date_placed.isoformat() if bet.date_placed else None,
        'sport': bet.sport,
        'event_track': bet.event_track,
        'selection': bet.selection,
        'bookie': bet.bookie,
        'stake': bet.stake,
        'odds': bet.odds,
        'result': bet.result,
        'payout': bet.payout,
        'profit': bet.profit,
        'notes': bet.notes,
    }


def _range_cutoff(range_key):
    now = datetime.utcnow()
    if range_key == '1d':
        return now - timedelta(days=1)
    if range_key == '1w':
        return now - timedelta(weeks=1)
    if range_key == '1m':
        return now - timedelta(days=30)
    if range_key == '1y':
        return now - timedelta(days=365)
    return None  # 'all'


def _auto_payout(bet: Bet) -> float:
    """Auto-calculate payout from stake/odds when it isn't supplied explicitly."""
    if bet.result == 'won':
        return round(bet.stake * bet.odds, 2)
    if bet.result == 'refunded':
        return round(bet.stake, 2)
    return 0.0


def _compute_stats(bets, starting_bankroll=0.0):
    settled = [b for b in bets if b.result in ('won', 'lost')]
    won = [b for b in bets if b.result == 'won']
    lost = [b for b in bets if b.result == 'lost']
    refunded = [b for b in bets if b.result == 'refunded']
    pending = [b for b in bets if b.result == 'pending']

    total_staked = sum(b.stake for b in settled)
    total_profit = sum(b.profit for b in bets)
    roi = (total_profit / total_staked * 100) if total_staked else 0.0
    progression = (total_profit / starting_bankroll * 100) if starting_bankroll else 0.0

    # Longest win/loss streaks, in chronological order of settled bets
    streak_bets = sorted(settled, key=lambda b: b.date_placed or datetime.min)
    longest_win_streak = longest_loss_streak = 0
    cur_win = cur_loss = 0
    for b in streak_bets:
        if b.result == 'won':
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        longest_win_streak = max(longest_win_streak, cur_win)
        longest_loss_streak = max(longest_loss_streak, cur_loss)

    # Drawdown: biggest peak-to-trough dip in cumulative profit
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for b in sorted(bets, key=lambda b: b.date_placed or datetime.min):
        cumulative += b.profit
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return {
        'bets': len(bets),
        'settled_bets': len(settled),
        'won': len(won),
        'lost': len(lost),
        'refunded': len(refunded),
        'pending': len(pending),
        'profit': round(total_profit, 2),
        'roi': round(roi, 2),
        'progression': round(progression, 2),
        'total_staked': round(total_staked, 2),
        'win_pct': round((len(won) / len(settled) * 100), 2) if settled else 0.0,
        'biggest_win': round(max((b.profit for b in won), default=0.0), 2),
        'biggest_loss': round(min((b.profit for b in lost), default=0.0), 2),
        'average_stake': round((sum(b.stake for b in bets) / len(bets)), 2) if bets else 0.0,
        'average_odds': round((sum(b.odds for b in bets) / len(bets)), 2) if bets else 0.0,
        'max_stake': round(max((b.stake for b in bets), default=0.0), 2),
        'longest_win_streak': longest_win_streak,
        'longest_loss_streak': longest_loss_streak,
        'drawdown': round(max_drawdown, 2),
        'starting_bankroll': round(starting_bankroll, 2),
        'current_bankroll': round(starting_bankroll + total_profit, 2),
    }


def register_bet_tracker_routes(app, db):

    @app.route('/bet-tracker')
    @login_required
    def bet_tracker():
        gate = _admin_gate()
        if gate:
            return gate
        return render_template('bet_tracker.html', sports=SPORTS, bookies=BOOKIES, results=RESULTS)

    @app.route('/bet-tracker/history')
    @login_required
    def bet_tracker_history():
        gate = _admin_gate()
        if gate:
            return gate

        bets = Bet.query.filter_by(user_id=current_user.id).order_by(Bet.date_placed.desc()).all()

        grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        month_totals = defaultdict(float)
        for b in bets:
            dp = b.date_placed or datetime.utcnow()
            month_key = dp.strftime('%B %Y')
            week_key = f"Week {dp.isocalendar()[1]}"
            day_key = dp.strftime('%A %d %b')
            grouped[month_key][week_key][day_key].append(b)
            month_totals[month_key] += b.profit

        return render_template(
            'bet_tracker_history.html',
            grouped=grouped,
            month_totals=month_totals,
            sports=SPORTS,
            bookies=BOOKIES,
            results=RESULTS,
        )

    @app.route('/bet-tracker/import-pardo-history', methods=['POST'])
    @login_required
    def bet_tracker_import_pardo_history():
        gate = _admin_gate()
        if gate:
            return gate

        created, skipped = import_pardo_history(db, current_user)
        if created:
            flash(f"Imported {created} month(s) of Pardo history "
                  f"({skipped} already present).", "success")
        else:
            flash("Pardo history already imported — nothing new to add.", "info")
        return redirect(url_for('bet_tracker_history'))

    @app.route('/bet-tracker/stats')
    @login_required
    def bet_tracker_stats():
        gate = _admin_gate()
        if gate:
            return gate

        bets = Bet.query.filter_by(user_id=current_user.id).all()
        stats = _compute_stats(bets, starting_bankroll=current_user.bet_tracker_starting_bankroll or 0.0)
        return render_template('bet_tracker_stats.html', stats=stats)

    # ------------------------------------------------------------------
    # JSON API
    # ------------------------------------------------------------------

    @app.route('/api/bet-tracker/bets', methods=['GET', 'POST'])
    @login_required
    def api_bet_tracker_bets():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403

        if request.method == 'GET':
            bets = Bet.query.filter_by(user_id=current_user.id).order_by(Bet.date_placed.desc()).all()
            return jsonify([_serialize(b) for b in bets])

        data = request.get_json(silent=True) or {}
        try:
            stake = float(data.get('stake'))
            odds = float(data.get('odds'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Stake and odds must be numbers'}), 400

        selection = (data.get('selection') or '').strip()
        if not selection:
            return jsonify({'error': 'Selection is required'}), 400
        if stake <= 0 or odds <= 0:
            return jsonify({'error': 'Stake and odds must be greater than zero'}), 400

        result = data.get('result') or 'pending'
        if result not in RESULTS:
            return jsonify({'error': 'Invalid result'}), 400

        sport = data.get('sport') or 'Horse Racing'
        if sport not in SPORTS:
            sport = 'Other'

        date_placed = datetime.utcnow()
        if data.get('date_placed'):
            try:
                date_placed = datetime.fromisoformat(data['date_placed'])
            except ValueError:
                pass

        bet = Bet(
            user_id=current_user.id,
            date_placed=date_placed,
            sport=sport,
            event_track=(data.get('event_track') or '').strip() or None,
            selection=selection,
            bookie=data.get('bookie') or None,
            stake=stake,
            odds=odds,
            result=result,
            notes=(data.get('notes') or '').strip() or None,
        )
        bet.payout = _auto_payout(bet)

        db.session.add(bet)
        db.session.commit()
        return jsonify(_serialize(bet)), 201

    @app.route('/api/bet-tracker/bets/<int:bet_id>', methods=['PUT', 'DELETE'])
    @login_required
    def api_bet_tracker_bet_detail(bet_id):
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403

        bet = Bet.query.filter_by(id=bet_id, user_id=current_user.id).first()
        if not bet:
            return jsonify({'error': 'Bet not found'}), 404

        if request.method == 'DELETE':
            db.session.delete(bet)
            db.session.commit()
            return jsonify({'success': True})

        data = request.get_json(silent=True) or {}
        try:
            if 'stake' in data:
                bet.stake = float(data['stake'])
            if 'odds' in data:
                bet.odds = float(data['odds'])
        except (TypeError, ValueError):
            return jsonify({'error': 'Stake and odds must be numbers'}), 400

        if bet.stake <= 0 or bet.odds <= 0:
            return jsonify({'error': 'Stake and odds must be greater than zero'}), 400

        if 'sport' in data:
            bet.sport = data['sport'] if data['sport'] in SPORTS else bet.sport
        if 'event_track' in data:
            bet.event_track = (data['event_track'] or '').strip() or None
        if 'selection' in data:
            selection = (data['selection'] or '').strip()
            if not selection:
                return jsonify({'error': 'Selection is required'}), 400
            bet.selection = selection
        if 'bookie' in data:
            bet.bookie = data['bookie'] or None
        if 'result' in data:
            if data['result'] not in RESULTS:
                return jsonify({'error': 'Invalid result'}), 400
            bet.result = data['result']
        if 'notes' in data:
            bet.notes = (data['notes'] or '').strip() or None
        if 'date_placed' in data and data['date_placed']:
            try:
                bet.date_placed = datetime.fromisoformat(data['date_placed'])
            except ValueError:
                pass

        if data.get('payout') not in (None, ''):
            try:
                bet.payout = float(data['payout'])
            except (TypeError, ValueError):
                return jsonify({'error': 'Payout must be a number'}), 400
        else:
            bet.payout = _auto_payout(bet)

        db.session.commit()
        return jsonify(_serialize(bet))

    @app.route('/api/bet-tracker/chart')
    @login_required
    def api_bet_tracker_chart():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403

        range_key = request.args.get('range', 'all')
        cutoff = _range_cutoff(range_key)

        query = Bet.query.filter_by(user_id=current_user.id).filter(Bet.result.in_(['won', 'lost']))
        if cutoff:
            query = query.filter(Bet.date_placed >= cutoff)
        bets = query.order_by(Bet.date_placed.asc()).all()

        cumulative = 0.0
        points = []
        for b in bets:
            cumulative += b.profit
            points.append({'x': b.date_placed.isoformat() if b.date_placed else None, 'y': round(cumulative, 2)})

        return jsonify({'points': points})

    @app.route('/api/bet-tracker/monthly')
    @login_required
    def api_bet_tracker_monthly():
        """Net profit/loss per calendar month, newest month first.

        The dashboard renders one row per month with the total coloured green
        or red, so months with no bets are simply absent rather than shown as
        a 0.00 row — an empty month is not a break-even month.
        """
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403

        range_key = request.args.get('range', 'all')
        cutoff = _range_cutoff(range_key)

        query = Bet.query.filter_by(user_id=current_user.id)
        if cutoff:
            query = query.filter(Bet.date_placed >= cutoff)
        bets = query.all()

        totals = defaultdict(float)
        for b in bets:
            dp = b.date_placed or datetime.utcnow()
            totals[(dp.year, dp.month)] += b.profit

        months = [
            {
                'year': year,
                'month': month,
                'label': f"{calendar.month_name[month]} {year}",
                'profit': round(total, 2),
            }
            for (year, month), total in sorted(totals.items(), reverse=True)
        ]
        return jsonify({'months': months})

    @app.route('/api/bet-tracker/summary')
    @login_required
    def api_bet_tracker_summary():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403

        range_key = request.args.get('range', 'all')
        cutoff = _range_cutoff(range_key)

        query = Bet.query.filter_by(user_id=current_user.id)
        if cutoff:
            query = query.filter(Bet.date_placed >= cutoff)
        bets = query.all()

        return jsonify(_compute_stats(bets, starting_bankroll=current_user.bet_tracker_starting_bankroll or 0.0))

    @app.route('/api/bet-tracker/bankroll', methods=['POST'])
    @login_required
    def api_bet_tracker_bankroll():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403

        data = request.get_json(silent=True) or {}
        try:
            starting_bankroll = float(data.get('starting_bankroll'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Starting bankroll must be a number'}), 400
        if starting_bankroll < 0:
            return jsonify({'error': 'Starting bankroll cannot be negative'}), 400

        current_user.bet_tracker_starting_bankroll = starting_bankroll
        db.session.commit()
        return jsonify({'starting_bankroll': starting_bankroll})
