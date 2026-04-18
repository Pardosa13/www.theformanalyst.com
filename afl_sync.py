# afl_sync.py
from datetime import datetime
from afl_data import (
    fetch_squiggle_games,
    fetch_squiggle_standings,
    fetch_fryzigg_player_stats
)
from models import db, PlayerStat, Game, LadderSnapshot
import logging

logger = logging.getLogger(__name__)


def sync_afl_all(season: int):
    logger.info(f"Starting AFL sync for {season}")

    # 1. FIXTURES / RESULTS
    games = fetch_squiggle_games(season)

    for g in games:
        db.session.merge(Game(
            id=g["id"],
            season=season,
            round=g.get("round"),
            hteam=g.get("hteam"),
            ateam=g.get("ateam"),
            hscore=g.get("hscore"),
            ascore=g.get("ascore"),
            venue=g.get("venue"),
            date=g.get("date"),
        ))

    # 2. LADDER
    ladder = fetch_squiggle_standings(season)
    db.session.query(LadderSnapshot).filter_by(season=season).delete()

    for row in ladder:
        db.session.add(LadderSnapshot(
            season=season,
            team=row["team"],
            rank=row["rank"],
            wins=row["wins"],
            losses=row["losses"],
            percentage=row["percentage"],
            points=row["pts"],
        ))

    # 3. PLAYER STATS (HEAVY - cache carefully)
    player_stats = fetch_fryzigg_player_stats(season)

    for p in player_stats:
        db.session.add(PlayerStat(
            season=season,
            player_id=p["player_id"],
            player_name=f"{p['player_first_name']} {p['player_last_name']}",
            team=p["player_team"],
            disposals=p.get("disposals", 0),
            marks=p.get("marks", 0),
            kicks=p.get("kicks", 0),
            handballs=p.get("handballs", 0),
            goals=p.get("goals", 0),
            tackles=p.get("tackles", 0),
            match_date=p.get("match_date"),
        ))

    db.session.commit()
    logger.info("AFL sync complete")
