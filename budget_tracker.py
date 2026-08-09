"""
budget_tracker.py
==================
"James & Julz Budget" — shared fortnightly household budget + credit card
payoff tracker. Restricted to exactly two accounts (Pardo & Julz); both read
and write the same rows, this is not scoped per-user like Bet Tracker.

Styled to match The Form Analyst's existing dark theme, following the
Bet Tracker module's patterns for auth gating and DB access.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import jsonify, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

from models import BudgetCategory, BudgetFortnight, BudgetEntry, DebtConfig, DebtTracker

logger = logging.getLogger(__name__)

# Household is based in Melbourne — "today" for fortnight purposes always means
# the Australia/Melbourne calendar date, regardless of what timezone the server runs in.
try:
    MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
except ZoneInfoNotFoundError:
    logger.warning("Timezone data for Australia/Melbourne is unavailable; falling back to UTC for Budget Tracker")
    MELBOURNE_TZ = timezone.utc


def melbourne_today() -> date:
    return datetime.now(MELBOURNE_TZ).date()

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

BUDGET_TRACKER_ALLOWED_EMAILS = {'j.partington13@hotmail.com', 'j.callegarivm@hotmail.com'}
JULZ_EMAIL = 'j.callegarivm@hotmail.com'


def is_budget_tracker_allowed(user) -> bool:
    """True if this user is one of the two accounts allowed on the Budget Tracker."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    email = (getattr(user, 'email', None) or '').strip().lower()
    return email in BUDGET_TRACKER_ALLOWED_EMAILS


def is_julz(user) -> bool:
    """True if this is Julz's account — her nav is restricted to just this page."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    email = (getattr(user, 'email', None) or '').strip().lower()
    return email == JULZ_EMAIL


# ---------------------------------------------------------------------------
# Seed data — matches the household budget schema exactly on first load
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES = [
    ('Home & utilities', 'Mortgage & rent', 3007.50),
    ('Home & utilities', 'Body corporate fees', 308.33),
    ('Home & utilities', 'Council rates', 100.00),
    ('Home & utilities', 'Electricity', 19.56),
    ('Home & utilities', 'Internet', 27.69),
    ('Home & utilities', 'Pay TV', 23.08),
    ('Home & utilities', 'Mobile', 138.46),

    ('Insurance & financial', 'Car insurance', 58.67),
    ('Insurance & financial', 'Home & contents insurance', 69.23),
    ('Insurance & financial', 'Personal & life insurance', 115.38),
    ('Insurance & financial', 'Health insurance', 175.38),
    ('Insurance & financial', 'Credit card interest', 46.15),
    ('Insurance & financial', 'Paying off credit card', 1455.14),
    ('Insurance & financial', 'Charity donations', 23.08),
    ('Insurance & financial', 'Other', 23.08),

    ('Groceries', 'Supermarket', 600.00),
    ('Groceries', 'Butcher', 100.00),

    ('Personal & medical', 'Cosmetics & toiletries', 48.08),
    ('Personal & medical', 'Hair & beauty', 57.69),
    ('Personal & medical', 'Medicines & pharmacy', 96.15),
    ('Personal & medical', 'Glasses & eye care', 38.46),
    ('Personal & medical', 'Dental', 38.46),
    ('Personal & medical', 'Doctors & medical', 96.15),
    ('Personal & medical', 'Hobbies', 48.08),
    ('Personal & medical', 'Clothing & shoes', 48.08),
    ('Personal & medical', 'Jewellery & accessories', 19.23),
    ('Personal & medical', 'Computers & gadgets', 57.69),
    ('Personal & medical', 'Sports & gym', 23.08),

    ('Entertainment & eating out', 'Coffee & tea', 48.08),
    ('Entertainment & eating out', 'Lunches bought', 100.00),
    ('Entertainment & eating out', 'Take-away & snacks', 200.00),
    ('Entertainment & eating out', 'Cigarettes', 80.00),
    ('Entertainment & eating out', 'Drinks & alcohol', 50.00),
    ('Entertainment & eating out', 'Bars & clubs', 48.08),
    ('Entertainment & eating out', 'Restaurants', 48.08),
    ('Entertainment & eating out', 'Books', 3.85),
    ('Entertainment & eating out', 'Newspapers & magazines', 3.85),
    ('Entertainment & eating out', 'Movies & music', 9.23),
    ('Entertainment & eating out', 'Holidays', 192.31),
    ('Entertainment & eating out', 'Celebrations & gifts', 96.15),

    ('Transport & auto', 'Road tolls & parking', 69.23),
    ('Transport & auto', 'Fines', 0.00),
]

INCOME_SOURCES = [
    ('Your take-home pay', 2437.87),
    ("Partner's take-home pay", 4080.00),
    ('Bonuses / overtime', 57.69),
    ('Income from savings & investments', 1042.86),
    ('Other', 192.31),
]
TOTAL_INCOME = round(sum(amount for _, amount in INCOME_SOURCES), 2)

# First fortnight start (a Thursday, aligned to pay day). Later fortnights extend
# forward from here in 14-day blocks: #1 = Thu 30 Jul - Wed 12 Aug 2026, #2 = Thu 13 Aug -
# Wed 26 Aug 2026, and so on.
FORTNIGHT_ANCHOR_START = date(2026, 7, 30)

# How many fortnights of debt schedule to compute past the point the balance is expected to hit $0.
DEBT_SCHEDULE_MIN_HORIZON = 20
DEBT_SCHEDULE_MAX_HORIZON = 60


def _repair_fortnight_sequence(db):
    """Realign stored fortnights to FORTNIGHT_ANCHOR_START if it changed.

    Only deletes fortnights that have no logged spending against them — real
    household data is never discarded. ensure_fortnight_for_date() regenerates
    the sequence from the (possibly new) anchor on the next call.
    """
    fortnights = BudgetFortnight.query.order_by(BudgetFortnight.start_date.asc()).all()
    if not fortnights or fortnights[0].start_date == FORTNIGHT_ANCHOR_START:
        return

    fortnight_ids = [f.id for f in fortnights]
    has_data = db.session.query(BudgetEntry.id).filter(
        BudgetEntry.fortnight_id.in_(fortnight_ids)
    ).first() is not None
    if has_data:
        return

    for f in fortnights:
        db.session.delete(f)
    db.session.commit()


def seed_budget_tracker_defaults(db):
    """Idempotently create the default categories and debt config. Safe to call every startup."""
    _repair_fortnight_sequence(db)

    if BudgetCategory.query.first() is None:
        for order, (group_name, category_name, allowance) in enumerate(DEFAULT_CATEGORIES, start=1):
            db.session.add(BudgetCategory(
                group_name=group_name,
                category_name=category_name,
                default_allowance=allowance,
                sort_order=order,
                is_debt_payment=(category_name == 'Paying off credit card'),
            ))
        db.session.commit()

    if DebtConfig.query.get(1) is None:
        db.session.add(DebtConfig(
            id=1,
            starting_balance=23000.0,
            annual_interest_rate=20.0,
            planned_fortnightly_payment=1455.14,
        ))
        db.session.commit()


# ---------------------------------------------------------------------------
# Fortnight helpers
# ---------------------------------------------------------------------------

def ensure_fortnight_for_date(db, target_date):
    """Return the BudgetFortnight covering target_date, extending the sequence forward if needed."""
    fortnight = BudgetFortnight.query.filter(
        BudgetFortnight.start_date <= target_date,
        BudgetFortnight.end_date >= target_date,
    ).first()
    if fortnight:
        return fortnight

    last = BudgetFortnight.query.order_by(BudgetFortnight.fortnight_number.desc()).first()
    start = (last.end_date + timedelta(days=1)) if last else FORTNIGHT_ANCHOR_START
    number = (last.fortnight_number + 1) if last else 1

    created = None
    while created is None or created.end_date < target_date:
        end = start + timedelta(days=13)
        created = BudgetFortnight(fortnight_number=number, start_date=start, end_date=end)
        db.session.add(created)
        start = end + timedelta(days=1)
        number += 1
    db.session.commit()
    recompute_debt_schedule(db)
    return created


def get_current_fortnight(db):
    return ensure_fortnight_for_date(db, melbourne_today())


# ---------------------------------------------------------------------------
# Debt payoff schedule
# ---------------------------------------------------------------------------

def _debt_category():
    return BudgetCategory.query.filter_by(is_debt_payment=True).first()


def recompute_debt_schedule(db):
    """Rebuild the debt_tracker schedule from DebtConfig + any logged actual payments.

    Fortnights with a logged "Paying off credit card" entry use that actual figure;
    fortnights without one (including the future) fall back to the planned payment,
    so the schedule always reflects "the plan corrected by whatever's actually been paid".
    """
    config = DebtConfig.query.get(1)
    if not config:
        return

    debt_category = _debt_category()
    entries_by_fortnight_id = {}
    if debt_category:
        rows = BudgetEntry.query.filter_by(category_id=debt_category.id).all()
        entries_by_fortnight_id = {r.fortnight_id: r.actual_spend for r in rows}

    fortnights = BudgetFortnight.query.order_by(BudgetFortnight.fortnight_number.asc()).all()
    fortnight_id_by_number = {f.fortnight_number: f.id for f in fortnights}
    existing_count = fortnights[-1].fortnight_number if fortnights else 0

    period_rate = (config.annual_interest_rate / 100.0) / 26.0  # 26 fortnights/year

    DebtTracker.query.delete()

    balance = config.starting_balance
    payoff_fortnight = None
    n = 1
    while n <= DEBT_SCHEDULE_MAX_HORIZON:
        if balance <= 0.005:
            if payoff_fortnight is None:
                payoff_fortnight = n - 1
            # Keep a couple of trailing zero rows for context, then stop once we've also
            # covered every fortnight that actually exists.
            if n > max(existing_count, payoff_fortnight + 2, DEBT_SCHEDULE_MIN_HORIZON):
                break
            balance_start, interest, actual_paid, balance_end = 0.0, 0.0, 0.0, 0.0
        else:
            balance_start = balance
            interest = round(balance_start * period_rate, 2)
            fid = fortnight_id_by_number.get(n)
            actual = entries_by_fortnight_id.get(fid) if fid is not None else None
            actual_paid = actual if actual is not None else config.planned_fortnightly_payment
            balance_end = max(0.0, round(balance_start + interest - actual_paid, 2))

        db.session.add(DebtTracker(
            fortnight_number=n,
            starting_balance=config.starting_balance,
            interest_rate=config.annual_interest_rate,
            balance_start=round(balance_start, 2),
            interest=interest,
            planned_payment=config.planned_fortnightly_payment,
            actual_paid=round(actual_paid, 2),
            balance_end=balance_end,
        ))
        balance = balance_end
        n += 1

    db.session.commit()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_budget_tracker_routes(app, db):

    def _gate():
        if not is_budget_tracker_allowed(current_user):
            flash("Access denied. This page is restricted.", "danger")
            return redirect(url_for('history'))
        return None

    def _api_gate():
        if not is_budget_tracker_allowed(current_user):
            return jsonify({'error': 'Access denied'}), 403
        return None

    @app.before_request
    def _budget_tracker_julz_gate():
        if not current_user.is_authenticated or not is_julz(current_user):
            return None
        endpoint = request.endpoint or ''
        if endpoint in ('static', 'logout'):
            return None
        if endpoint.startswith('budget_tracker') or endpoint.startswith('api_budget_tracker'):
            return None
        if request.path.startswith('/api/'):
            return None
        return redirect(url_for('budget_tracker'))

    def _resolve_fortnight(fortnight_id):
        fortnight = BudgetFortnight.query.get(fortnight_id) if fortnight_id else None
        if not fortnight:
            fortnight = get_current_fortnight(db)
        return fortnight

    @app.route('/budget-tracker')
    @login_required
    def budget_tracker():
        gate = _gate()
        if gate:
            return gate
        seed_budget_tracker_defaults(db)
        fortnight = _resolve_fortnight(request.args.get('fortnight_id', type=int))
        fortnights = BudgetFortnight.query.order_by(BudgetFortnight.fortnight_number.desc()).all()
        return render_template(
            'budget_tracker.html',
            current_fortnight=fortnight,
            fortnights=fortnights,
        )

    @app.route('/api/budget-tracker/summary')
    @login_required
    def api_budget_tracker_summary():
        gate = _api_gate()
        if gate:
            return gate
        seed_budget_tracker_defaults(db)
        fortnight = _resolve_fortnight(request.args.get('fortnight_id', type=int))

        categories = BudgetCategory.query.order_by(BudgetCategory.sort_order.asc()).all()
        entries = {e.category_id: e for e in BudgetEntry.query.filter_by(fortnight_id=fortnight.id).all()}

        groups = OrderedDict()
        total_allowance = 0.0
        total_actual = 0.0
        for cat in categories:
            entry = entries.get(cat.id)
            actual = entry.actual_spend if entry else 0.0
            total_allowance += cat.default_allowance
            total_actual += actual

            group = groups.setdefault(cat.group_name, {
                'group_name': cat.group_name, 'allowance': 0.0, 'actual': 0.0, 'categories': [],
            })
            group['allowance'] += cat.default_allowance
            group['actual'] += actual
            group['categories'].append({
                'id': cat.id,
                'category_name': cat.category_name,
                'allowance': round(cat.default_allowance, 2),
                'actual': round(actual, 2),
                'updated_by': entry.updated_by if entry else None,
                'updated_at': entry.updated_at.isoformat() if entry and entry.updated_at else None,
            })

        for group in groups.values():
            group['allowance'] = round(group['allowance'], 2)
            group['actual'] = round(group['actual'], 2)

        return jsonify({
            'fortnight': {
                'id': fortnight.id,
                'fortnight_number': fortnight.fortnight_number,
                'start_date': fortnight.start_date.isoformat(),
                'end_date': fortnight.end_date.isoformat(),
            },
            'groups': list(groups.values()),
            'total_income': TOTAL_INCOME,
            'income_sources': [{'label': l, 'amount': a} for l, a in INCOME_SOURCES],
            'total_allowance': round(total_allowance, 2),
            'total_actual': round(total_actual, 2),
            'surplus_planned': round(TOTAL_INCOME - total_allowance, 2),
            'surplus_actual': round(TOTAL_INCOME - total_actual, 2),
        })

    @app.route('/api/budget-tracker/entries', methods=['POST'])
    @login_required
    def api_budget_tracker_entry_save():
        gate = _api_gate()
        if gate:
            return gate

        data = request.get_json(silent=True) or {}
        try:
            category_id = int(data.get('category_id'))
            actual_spend = float(data.get('actual_spend'))
        except (TypeError, ValueError):
            return jsonify({'error': 'category_id and actual_spend are required'}), 400
        if actual_spend < 0:
            return jsonify({'error': 'Actual spend cannot be negative'}), 400

        category = BudgetCategory.query.get(category_id)
        if not category:
            return jsonify({'error': 'Category not found'}), 404

        fortnight = _resolve_fortnight(data.get('fortnight_id'))

        entry = BudgetEntry.query.filter_by(fortnight_id=fortnight.id, category_id=category.id).first()
        if not entry:
            entry = BudgetEntry(fortnight_id=fortnight.id, category_id=category.id)
            db.session.add(entry)
        entry.actual_spend = round(actual_spend, 2)
        entry.updated_by = current_user.username
        entry.updated_at = datetime.utcnow()
        db.session.commit()

        if category.is_debt_payment:
            recompute_debt_schedule(db)

        return jsonify({
            'id': entry.id,
            'category_id': category.id,
            'fortnight_id': fortnight.id,
            'actual_spend': entry.actual_spend,
            'updated_by': entry.updated_by,
            'updated_at': entry.updated_at.isoformat(),
        })

    @app.route('/api/budget-tracker/fortnights')
    @login_required
    def api_budget_tracker_fortnights():
        gate = _api_gate()
        if gate:
            return gate
        seed_budget_tracker_defaults(db)
        get_current_fortnight(db)
        fortnights = BudgetFortnight.query.order_by(BudgetFortnight.fortnight_number.desc()).all()
        return jsonify([{
            'id': f.id,
            'fortnight_number': f.fortnight_number,
            'start_date': f.start_date.isoformat(),
            'end_date': f.end_date.isoformat(),
        } for f in fortnights])

    @app.route('/api/budget-tracker/chart/category-spend')
    @login_required
    def api_budget_tracker_chart_category():
        gate = _api_gate()
        if gate:
            return gate
        fortnight = _resolve_fortnight(request.args.get('fortnight_id', type=int))
        entries = (
            BudgetEntry.query
            .filter_by(fortnight_id=fortnight.id)
            .filter(BudgetEntry.actual_spend > 0)
            .all()
        )
        points = [{'label': e.category.category_name, 'value': round(e.actual_spend, 2)} for e in entries]
        points.sort(key=lambda p: p['value'], reverse=True)
        return jsonify({'points': points})

    @app.route('/api/budget-tracker/chart/trend')
    @login_required
    def api_budget_tracker_chart_trend():
        gate = _api_gate()
        if gate:
            return gate
        count = request.args.get('count', 6, type=int)
        get_current_fortnight(db)

        fortnights = BudgetFortnight.query.order_by(BudgetFortnight.fortnight_number.desc()).limit(count).all()
        fortnights.reverse()
        total_allowance = round(sum(c.default_allowance for c in BudgetCategory.query.all()), 2)

        points = []
        for f in fortnights:
            actual = (
                db.session.query(func.coalesce(func.sum(BudgetEntry.actual_spend), 0.0))
                .filter(BudgetEntry.fortnight_id == f.id)
                .scalar()
            )
            points.append({
                'label': f'#{f.fortnight_number}',
                'allowance': total_allowance,
                'actual': round(actual or 0.0, 2),
            })
        return jsonify({'points': points})

    @app.route('/api/budget-tracker/debt-schedule')
    @login_required
    def api_budget_tracker_debt_schedule():
        gate = _api_gate()
        if gate:
            return gate
        seed_budget_tracker_defaults(db)
        recompute_debt_schedule(db)

        rows = DebtTracker.query.order_by(DebtTracker.fortnight_number.asc()).all()
        config = DebtConfig.query.get(1)
        payoff_fortnight = next((r.fortnight_number for r in rows if r.balance_end <= 0.005), None)

        return jsonify({
            'config': {
                'starting_balance': config.starting_balance,
                'annual_interest_rate': config.annual_interest_rate,
                'planned_fortnightly_payment': config.planned_fortnightly_payment,
            } if config else None,
            'payoff_fortnight': payoff_fortnight,
            'rows': [{
                'fortnight_number': r.fortnight_number,
                'balance_start': r.balance_start,
                'interest': r.interest,
                'planned_payment': r.planned_payment,
                'actual_paid': r.actual_paid,
                'balance_end': r.balance_end,
            } for r in rows],
        })

    @app.route('/api/budget-tracker/debt-config', methods=['POST'])
    @login_required
    def api_budget_tracker_debt_config():
        gate = _api_gate()
        if gate:
            return gate

        data = request.get_json(silent=True) or {}
        config = DebtConfig.query.get(1)
        if not config:
            config = DebtConfig(id=1)
            db.session.add(config)

        try:
            if 'starting_balance' in data:
                config.starting_balance = float(data['starting_balance'])
            if 'annual_interest_rate' in data:
                config.annual_interest_rate = float(data['annual_interest_rate'])
            if 'planned_fortnightly_payment' in data:
                config.planned_fortnightly_payment = float(data['planned_fortnightly_payment'])
        except (TypeError, ValueError):
            return jsonify({'error': 'Values must be numbers'}), 400

        if config.starting_balance < 0 or config.annual_interest_rate < 0 or config.planned_fortnightly_payment < 0:
            return jsonify({'error': 'Values cannot be negative'}), 400

        config.updated_by = current_user.username
        config.updated_at = datetime.utcnow()
        db.session.commit()
        recompute_debt_schedule(db)
        return jsonify({'success': True})
