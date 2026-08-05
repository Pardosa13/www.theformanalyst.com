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

from functools import wraps
from collections import defaultdict
from datetime import datetime, timedelta

from flask import jsonify, render_template, request, redirect, url_for
from flask_login import login_required, current_user

from models import Bet

SPORTS = ['Horse Racing', 'Greyhounds', 'AFL', 'MMA', 'Soccer', 'NFL', 'NBA', 'Other']
BOOKIES = ['Sportsbet', 'Betfair', 'Ladbrokes', 'Neds', 'Other']
RESULTS = ['pending', 'won', 'lost', 'refunded']


def bet_tracker_admin_required(view):
    """Restrict a Bet Tracker route to admins only.

    Non-admins get a plain 403 (JSON routes) or a silent redirect home
    (page routes) — no flash message, no indication of why, so the gate
    doesn't leak that the feature exists to accounts that shouldn't see it.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden'}), 403
            return redirect(url_for('history'))
        return view(*args, **kwargs)
    return wrapped


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
    @bet_tracker_admin_required
    def bet_tracker():
        return render_template('bet_tracker.html', sports=SPORTS, bookies=BOOKIES, results=RESULTS)

    @app.route('/bet-tracker/history')
    @login_required
    @bet_tracker_admin_required
    def bet_tracker_history():
        bets = Bet.query.filter_by(user_id=current_user.id).order_by(Bet.date_placed.desc()).all()

        grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for b in bets:
            dp = b.date_placed or datetime.utcnow()
            month_key = dp.strftime('%B %Y')
            week_key = f"Week {dp.isocalendar()[1]}"
            day_key = dp.strftime('%A %d %b')
            grouped[month_key][week_key][day_key].append(b)

        return render_template(
            'bet_tracker_history.html',
            grouped=grouped,
            sports=SPORTS,
            bookies=BOOKIES,
            results=RESULTS,
        )

    @app.route('/bet-tracker/stats')
    @login_required
    @bet_tracker_admin_required
    def bet_tracker_stats():
        bets = Bet.query.filter_by(user_id=current_user.id).all()
        stats = _compute_stats(bets, starting_bankroll=current_user.bet_tracker_starting_bankroll or 0.0)
        return render_template('bet_tracker_stats.html', stats=stats)

    # ------------------------------------------------------------------
    # JSON API
    # ------------------------------------------------------------------

    @app.route('/api/bet-tracker/bets', methods=['GET', 'POST'])
    @login_required
    @bet_tracker_admin_required
    def api_bet_tracker_bets():
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
    @bet_tracker_admin_required
    def api_bet_tracker_bet_detail(bet_id):
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
    @bet_tracker_admin_required
    def api_bet_tracker_chart():
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

    @app.route('/api/bet-tracker/summary')
    @login_required
    @bet_tracker_admin_required
    def api_bet_tracker_summary():
        range_key = request.args.get('range', 'all')
        cutoff = _range_cutoff(range_key)

        query = Bet.query.filter_by(user_id=current_user.id)
        if cutoff:
            query = query.filter(Bet.date_placed >= cutoff)
        bets = query.all()

        return jsonify(_compute_stats(bets, starting_bankroll=current_user.bet_tracker_starting_bankroll or 0.0))

    @app.route('/api/bet-tracker/bankroll', methods=['POST'])
    @login_required
    @bet_tracker_admin_required
    def api_bet_tracker_bankroll():
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
