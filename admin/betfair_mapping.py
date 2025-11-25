"""
Minimal admin blueprint to list unmapped races and allow one-click mapping.
Register the blueprint with your app:
    from admin.betfair_mapping import bp as betfair_bp
    app.register_blueprint(betfair_bp)
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
import logging
from sqlalchemy import create_engine, MetaData, select
import os

bp = Blueprint('betfair_admin', __name__, url_prefix='/admin')
logger = logging.getLogger('betfair_admin')

SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
engine = create_engine(SQLALCHEMY_DATABASE_URI) if SQLALCHEMY_DATABASE_URI else None
metadata = MetaData()
if engine:
    metadata.reflect(bind=engine)

race_table = metadata.tables.get('races') or metadata.tables.get('race')
horse_table = metadata.tables.get('horses') or metadata.tables.get('horse')

@bp.route('/betfair-mapping', methods=['GET', 'POST'])
def mapping_index():
    if not engine:
        return "DB not configured", 500
    if request.method == 'POST':
        race_id = request.form.get('race_id')
        market_id = request.form.get('market_id')
        if not race_id or not market_id:
            flash("Missing race_id or market_id")
            return redirect(url_for('.mapping_index'))
        with engine.begin() as conn:
            try:
                stmt = race_table.update().where(race_table.c.id == race_id).values(market_id=market_id)
                conn.execute(stmt)
                flash("Mapped race {} to market {}".format(race_id, market_id))
            except Exception:
                logger.exception("Failed to update race mapping")
                flash("Failed to update mapping")
        return redirect(url_for('.mapping_index'))
    rows = []
    with engine.connect() as conn:
        s = select([race_table]).where(race_table.c.market_id == None)
        rows = conn.execute(s).fetchall()
    return render_template('admin/betfair_mapping.html', races=rows)
