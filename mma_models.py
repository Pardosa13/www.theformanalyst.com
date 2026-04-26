"""
MMA Models - SQLAlchemy table definitions for The Form Analyst MMA feature.
These are ADDITIVE only - zero changes to existing models.py tables.
Import and call init_mma_tables(db) from app.py startup block.
"""

from datetime import datetime


def init_mma_tables(db):
    """
    Creates all MMA tables if they don't exist.
    Safe to call every startup - uses CREATE TABLE IF NOT EXISTS logic via db.create_all()
    with the models registered on the db instance passed in.
    """

    class MMAFighter(db.Model):
        __tablename__ = 'mma_fighters'
        __table_args__ = {'extend_existing': True}

        id = db.Column(db.String(64), primary_key=True)          # Fighter_Id from CSV
        full_name = db.Column(db.String(200), nullable=False)
        nickname = db.Column(db.String(200))
        height_cm = db.Column(db.Float)
        weight_lbs = db.Column(db.Float)
        reach_cm = db.Column(db.Float)
        stance = db.Column(db.String(50))
        wins = db.Column(db.Integer, default=0)
        losses = db.Column(db.Integer, default=0)
        draws = db.Column(db.Integer, default=0)
        has_belt = db.Column(db.Boolean, default=False)

        # Glicko-2 ratings (updated by weekly cron)
        glicko_rating = db.Column(db.Float, default=1500.0)
        glicko_rd = db.Column(db.Float, default=350.0)
        glicko_vol = db.Column(db.Float, default=0.06)
        glicko_updated_at = db.Column(db.DateTime)

        # EMA rolling stats (updated by weekly cron)
        ema_slpm = db.Column(db.Float, default=0.0)
        ema_sapm = db.Column(db.Float, default=0.0)
        ema_td_acc = db.Column(db.Float, default=0.4)
        ema_td_avg = db.Column(db.Float, default=1.0)
        ema_td_def = db.Column(db.Float, default=0.5)
        ema_kd_rate = db.Column(db.Float, default=0.2)
        ema_sub_rate = db.Column(db.Float, default=0.2)
        ema_ctrl_pct = db.Column(db.Float, default=10.0)
        ema_sig_str_acc = db.Column(db.Float, default=0.45)
        streak = db.Column(db.Integer, default=0)
        win_rate = db.Column(db.Float, default=0.5)
        total_fights = db.Column(db.Integer, default=0)
        recent_form = db.Column(db.String(20))  # e.g. "W-W-L-W"

        # ESPN profile + headshot (populated by mma_sync when ESPN URLs are available)
        espn_url = db.Column(db.String(500))
        headshot_url = db.Column(db.String(500))

        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class MMAEvent(db.Model):
        __tablename__ = 'mma_events'
        __table_args__ = {'extend_existing': True}

        id = db.Column(db.String(64), primary_key=True)           # ESPN event_id
        name = db.Column(db.String(300), nullable=False)
        date = db.Column(db.Date)
        location = db.Column(db.String(300))
        is_completed = db.Column(db.Boolean, default=False)
        espn_url = db.Column(db.String(500))

        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

        fights = db.relationship('MMAFight', backref='event', lazy=True,
                                 cascade='all, delete-orphan')

    class MMAFight(db.Model):
        __tablename__ = 'mma_fights'
        __table_args__ = {'extend_existing': True}

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        event_id = db.Column(db.String(64), db.ForeignKey('mma_events.id'), nullable=False)

        fighter_1_name = db.Column(db.String(200), nullable=False)
        fighter_2_name = db.Column(db.String(200), nullable=False)
        fighter_1_id = db.Column(db.String(64), db.ForeignKey('mma_fighters.id'))
        fighter_2_id = db.Column(db.String(64), db.ForeignKey('mma_fighters.id'))

        weight_class = db.Column(db.String(100))
        is_main_card = db.Column(db.Boolean, default=False)
        is_title_fight = db.Column(db.Boolean, default=False)

        # Result (filled after fight completes)
        winner_name = db.Column(db.String(200))
        method = db.Column(db.String(100))        # KO/TKO, SUB, DEC etc.
        round_ended = db.Column(db.Integer)
        time_ended = db.Column(db.String(20))

        # Scraped bio fallback (from ESPN, when fighter not in mma_fighters)
        f1_height = db.Column(db.String(20))
        f1_reach = db.Column(db.String(20))
        f1_stance = db.Column(db.String(50))
        f1_record = db.Column(db.String(30))
        f2_height = db.Column(db.String(20))
        f2_reach = db.Column(db.String(20))
        f2_stance = db.Column(db.String(50))
        f2_record = db.Column(db.String(30))

        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        prediction = db.relationship('MMAPrediction', backref='fight', uselist=False,
                                     cascade='all, delete-orphan')

    class MMAPrediction(db.Model):
        __tablename__ = 'mma_predictions'
        __table_args__ = {'extend_existing': True}

        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        fight_id = db.Column(db.Integer, db.ForeignKey('mma_fights.id'), nullable=False)

        predicted_winner = db.Column(db.String(200))
        f1_win_probability = db.Column(db.Float)   # 0.0 - 1.0
        f2_win_probability = db.Column(db.Float)
        confidence = db.Column(db.String(20))       # e.g. "62.4%"

        # Key factors (stored as JSON text)
        factors_json = db.Column(db.Text)

        # Model metadata
        model_version = db.Column(db.String(50), default='catboost_v1')
        generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Register all models then create tables
    db.create_all()

    return {
        'MMAFighter': MMAFighter,
        'MMAEvent': MMAEvent,
        'MMAFight': MMAFight,
        'MMAPrediction': MMAPrediction,
    }
