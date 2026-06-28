#!/usr/bin/env python3
"""AFL bet-quality / meta-model training and scoring.

This module is intentionally AFL-only. It does not import or mutate the horse
racing backtest, routes, templates, database logic, or model artifacts.

Usage:
  DATABASE_URL=postgresql://... python afl_backtest.py --schema-report
  DATABASE_URL=postgresql://... python afl_backtest.py --train
  DATABASE_URL=postgresql://... python afl_backtest.py --score-current

Railway setup:
  1. Attach the same Postgres DATABASE_URL used by the AFL app.
  2. Run schema/report first: python afl_backtest.py --schema-report
  3. Train after a round is settled: python afl_backtest.py --train
  4. Score current selections after odds syncs: python afl_backtest.py --score-current

Model artifacts:
  --train creates models/afl_bet_quality_model.pkl and
  models/afl_bet_quality_model_meta.json from live Railway Postgres data.
  Do not commit generated AFL model artifacts unless they were trained from
  live Railway data. Use a Railway volume mounted at models/ if the .pkl must
  persist across deployments; otherwise retrain on deploy or before scoring.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import joblib
    import numpy as np
    import pandas as pd
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import Engine
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder
except ModuleNotFoundError as _missing_dep:
    joblib = np = pd = None
    create_engine = inspect = text = None
    Engine = Any
    ColumnTransformer = HistGradientBoostingClassifier = RandomForestClassifier = None
    SimpleImputer = roc_auc_score = Pipeline = OneHotEncoder = None
    _MISSING_DEPENDENCY = _missing_dep
else:
    _MISSING_DEPENDENCY = None

LOG = logging.getLogger("afl_backtest")
MODEL_PATH = Path("models/afl_bet_quality_model.pkl")
META_PATH = Path("models/afl_bet_quality_model_meta.json")
SCORES_PATH = Path("models/afl_current_selection_scores.json")

TARGET_DEFINITION = "profit_units > 0 on settled afl_model_selections rows"
FORBIDDEN_FEATURES = {"actual_stat", "result", "profit_units", "settled_at", "hscore", "ascore", "hgoals", "hbehinds", "agoals", "abehinds", "margin", "winner", "winnerteamid"}
BASE_NUMERIC = ["line", "odds", "model_prediction", "model_prob", "implied_prob", "edge", "edge_pct", "season_avg", "last5_avg", "vs_opp_avg", "hist_pct", "confidence_score", "predicted_margin"]
BASE_CATEGORICAL = ["source", "selection_source", "season", "round", "home_team", "away_team", "team", "opponent", "market", "line_type", "bookmaker", "recommendation"]
TEAM_STATS = ["kicks", "marks", "handballs", "disposals", "effective_disposals", "disposal_efficiency_percentage", "goals", "behinds", "hitouts", "tackles", "rebounds", "inside_fifties", "clearances", "clangers", "free_kicks_for", "free_kicks_against", "contested_possessions", "uncontested_possessions", "contested_marks", "marks_inside_fifty", "one_percenters", "goal_assists", "time_on_ground_percentage", "afl_fantasy_score", "supercoach_score", "centre_clearances", "stoppage_clearances", "score_involvements", "metres_gained", "turnovers", "intercepts", "tackles_inside_fifty"]
DIFF_STATS = ["disposals", "kicks", "handballs", "marks", "tackles", "clearances", "inside_fifties", "goals", "behinds", "metres_gained", "score_involvements", "supercoach_score", "afl_fantasy_score", "turnovers", "intercepts"]
SAFE_GAME_CATEGORICAL = ["venue", "roundname"]
SAFE_PREFIXES = ("home_team_last5_", "away_team_last5_", "home_away_last5_", "standings_", "safe_market_")
MODEL_SCHEMA_VERSION = 1

FEATURE_AUDIT = {
    "afl_model_selections": {
        "used_numeric": BASE_NUMERIC[:-1],
        "used_categorical": BASE_CATEGORICAL,
        "target_only_or_excluded": ["actual_stat", "result", "profit_units", "settled_at"],
        "notes": "Primary row grain. created_at/commence_time are ordering/filter fields, not predictive features.",
    },
    "afl_match_predictions": {
        "used": ["predicted_margin"],
        "notes": "Existing AFL model output, joined by match_id as a pre-match meta feature.",
    },
    "afl_player_stats": {
        "used": TEAM_STATS,
        "excluded": ["match_home_team_score", "match_away_team_score", "match_margin", "match_winner", "brownlow_votes", "created_at", "player_headshot_url"],
        "notes": "Aggregated to team match totals and shifted before rolling so current-match stats cannot enter the target match.",
    },
    "afl_games": {
        "used": SAFE_GAME_CATEGORICAL,
        "outcome_only_or_excluded": ["hscore", "ascore", "hgoals", "hbehinds", "agoals", "abehinds", "margin", "winner", "winnerteamid", "complete"],
        "notes": "Only schedule/context columns are used as features; result columns remain settlement/backtest-only.",
    },
    "afl_match_markets": {
        "used": ["safe_market_snapshot_count", "safe_market_avg_odds", "safe_market_max_odds", "safe_market_min_hours_before_start"],
        "notes": "Only rows with fetched_at before commence_time are aggregated, preventing market lookahead.",
    },
    "afl_standings": {
        "used": ["rank", "pts", "wins", "losses", "percentage"],
        "notes": "Uses same-season standings from rounds strictly before the target selection round.",
    },
    "afl_player_props": {
        "used": [],
        "notes": "Not joined directly in V1 because afl_model_selections already snapshots prop line/odds/features at selection time.",
    },
    "afl_sync_log": {"used": [], "notes": "Operational logging only; not predictive."},
    "afl_team_logos": {"used": [], "notes": "UI only; not predictive."},
    "afl_tips": {"used": [], "notes": "No useful rows for V1."},
}


def db_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
    if not url:
        raise SystemExit("DATABASE_URL or SQLALCHEMY_DATABASE_URI must be set")
    return url.replace("postgres://", "postgresql://", 1)


def require_dependencies() -> None:
    if _MISSING_DEPENDENCY is not None:
        raise SystemExit(f"Missing Python dependency: {_MISSING_DEPENDENCY}. Install requirements.txt before running AFL ML jobs.")

def engine() -> Engine:
    require_dependencies()
    return create_engine(db_url(), pool_pre_ping=True)


def read_sql(e: Engine, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    return pd.read_sql_query(text(sql), e, params=params or {})


def schema_report(e: Engine) -> dict[str, Any]:
    insp = inspect(e)
    tables = [t for t in ["afl_games", "afl_match_markets", "afl_match_predictions", "afl_model_selections", "afl_player_stats", "afl_player_props", "afl_standings", "afl_sync_log", "afl_team_logos", "afl_tips"] if insp.has_table(t)]
    out: dict[str, Any] = {"tables": {}, "feature_audit": FEATURE_AUDIT}
    for t in tables:
        out["tables"][t] = {"columns": [c["name"] for c in insp.get_columns(t)]}
    out["joins"] = {
        "primary_training": "afl_model_selections is the row grain and joins to afl_match_predictions on match_id, to afl_games on match_id=id or event_id where available, and to player/team form by home_team/away_team plus commence_time/match date.",
        "market_safety": "afl_match_markets is only safe when fetched_at < commence_time; current implementation derives optional aggregate market context from safe snapshots only.",
        "player_stats_safety": "rolling form features are shifted by team and therefore use only prior match_date rows before each target selection commence_time.",
    }
    if "afl_model_selections" in tables:
        for col, key in [("market", "markets"), ("line_type", "line_types"), ("recommendation", "recommendations"), ("source", "sources"), ("selection_source", "selection_sources")]:
            out[key] = read_sql(e, f"SELECT {col}, COUNT(*) count FROM afl_model_selections GROUP BY {col} ORDER BY count DESC").to_dict("records")
        counts = read_sql(e, """
            SELECT
              COUNT(*) FILTER (WHERE settled_at IS NOT NULL AND result IS NOT NULL AND profit_units IS NOT NULL AND odds IS NOT NULL) AS settled_trainable,
              COUNT(*) FILTER (WHERE settled_at IS NULL AND odds IS NOT NULL) AS unsettled_scorable
            FROM afl_model_selections
        """).iloc[0].to_dict()
        out["row_counts"] = {k: int(v or 0) for k, v in counts.items()}
        out["market_trainable_counts"] = read_sql(e, """
            SELECT market, COUNT(*) count
            FROM afl_model_selections
            WHERE settled_at IS NOT NULL AND result IS NOT NULL AND profit_units IS NOT NULL AND odds IS NOT NULL
            GROUP BY market ORDER BY count DESC
        """).to_dict("records")
    return out


def load_selections(e: Engine, settled: bool) -> pd.DataFrame:
    if settled:
        where = "settled_at IS NOT NULL AND result IS NOT NULL AND profit_units IS NOT NULL AND odds IS NOT NULL"
    else:
        max_age_hours = int(os.getenv("AFL_ML_SCORE_MAX_AGE_HOURS", "12"))
        where = (
            "settled_at IS NULL AND odds IS NOT NULL "
            "AND COALESCE(result, 'pending') = 'pending' "
            "AND (commence_time IS NULL OR commence_time >= (NOW() - (:max_age_hours * INTERVAL '1 hour')))"
        )
        return read_sql(e, f"SELECT * FROM afl_model_selections WHERE {where}", {"max_age_hours": max_age_hours})
    return read_sql(e, f"SELECT * FROM afl_model_selections WHERE {where}")


def _normalise_team_series(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip().str.lower()


def _add_game_context(e: Engine, df: pd.DataFrame) -> pd.DataFrame:
    games = read_sql(e, "SELECT id AS match_id, venue, roundname FROM afl_games")
    if games.empty or "match_id" not in df:
        for col in SAFE_GAME_CATEGORICAL:
            if col not in df:
                df[col] = None
        return df
    return df.merge(games.drop_duplicates("match_id"), on="match_id", how="left")


def _add_safe_market_features(e: Engine, df: pd.DataFrame) -> pd.DataFrame:
    markets = read_sql(e, """
        SELECT event_id, bookmaker, market, line, odds, commence_time, fetched_at
        FROM afl_match_markets
        WHERE odds IS NOT NULL
          AND fetched_at IS NOT NULL
          AND commence_time IS NOT NULL
          AND fetched_at < commence_time
    """)
    if markets.empty:
        return df
    markets["line"] = pd.to_numeric(markets["line"], errors="coerce").round(3)
    markets["odds"] = pd.to_numeric(markets["odds"], errors="coerce")
    markets["commence_time"] = pd.to_datetime(markets["commence_time"], utc=True, errors="coerce")
    markets["fetched_at"] = pd.to_datetime(markets["fetched_at"], utc=True, errors="coerce")
    markets["hours_before_start"] = (markets["commence_time"] - markets["fetched_at"]).dt.total_seconds() / 3600.0
    agg = markets.groupby(["event_id", "bookmaker", "market", "line"], dropna=False).agg(
        safe_market_snapshot_count=("odds", "size"),
        safe_market_avg_odds=("odds", "mean"),
        safe_market_max_odds=("odds", "max"),
        safe_market_min_hours_before_start=("hours_before_start", "min"),
    ).reset_index()
    out = df.copy()
    out["line"] = pd.to_numeric(out["line"], errors="coerce").round(3)
    return out.merge(agg, on=["event_id", "bookmaker", "market", "line"], how="left")


def _add_safe_standings_features(e: Engine, df: pd.DataFrame) -> pd.DataFrame:
    standings = read_sql(e, """
        SELECT year AS season, round, team, rank, pts, wins, losses, percentage
        FROM afl_standings
        WHERE year IS NOT NULL AND round IS NOT NULL AND COALESCE(team, '') <> ''
    """)
    if standings.empty or not {"season", "round", "home_team", "away_team"}.issubset(df.columns):
        return df
    standings = standings.copy()
    standings["team_key"] = _normalise_team_series(standings["team"])
    standings["round"] = pd.to_numeric(standings["round"], errors="coerce")
    out = df.copy()
    out["round"] = pd.to_numeric(out["round"], errors="coerce")
    for side in ["home", "away"]:
        left = out[["id", "season", "round", f"{side}_team"]].copy()
        left["team_key"] = _normalise_team_series(left[f"{side}_team"])
        left = left.dropna(subset=["season", "round"]).sort_values("round")
        merged_parts = []
        for (season, team_key), group in left.groupby(["season", "team_key"], dropna=False):
            right = standings[(standings["season"] == season) & (standings["team_key"] == team_key)].sort_values("round")
            if right.empty:
                continue
            merged_parts.append(pd.merge_asof(group, right, on="round", direction="backward", allow_exact_matches=False))
        if not merged_parts:
            continue
        merged = pd.concat(merged_parts, ignore_index=True)
        cols = ["rank", "pts", "wins", "losses", "percentage"]
        rename = {c: f"standings_{side}_{c}" for c in cols}
        out = out.merge(merged[["id"] + cols].rename(columns=rename), on="id", how="left")
    for col in ["rank", "pts", "wins", "losses", "percentage"]:
        h, a = f"standings_home_{col}", f"standings_away_{col}"
        if h in out and a in out:
            out[f"standings_{col}_diff"] = out[h] - out[a]
    return out


def add_safe_features(e: Engine, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df = _add_game_context(e, df)
    df = _add_safe_market_features(e, df)
    df = _add_safe_standings_features(e, df)
    preds = read_sql(e, "SELECT match_id, predicted_margin FROM afl_match_predictions")
    if not preds.empty and "match_id" in df:
        df = df.merge(preds.drop_duplicates("match_id"), on="match_id", how="left")
    else:
        df["predicted_margin"] = np.nan

    stats = read_sql(e, f"""
        SELECT match_id, season, match_date, player_team, {', '.join(TEAM_STATS)}
        FROM afl_player_stats
        WHERE match_date IS NOT NULL AND COALESCE(player_team, '') <> ''
    """)
    if not stats.empty:
        stats["match_date"] = pd.to_datetime(stats["match_date"], utc=True, errors="coerce")
        agg = stats.groupby(["match_id", "season", "match_date", "player_team"], dropna=False)[TEAM_STATS].sum(min_count=1).reset_index()
        agg = agg.sort_values(["player_team", "match_date", "match_id"])
        for s in TEAM_STATS:
            agg[f"team_last5_{s}_avg"] = agg.groupby("player_team")[s].transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
        roll_cols = [f"team_last5_{s}_avg" for s in TEAM_STATS]
        form = agg[["player_team", "match_date"] + roll_cols].dropna(subset=["match_date"])
        targets = df[["id", "commence_time", "home_team", "away_team"]].copy()
        targets["commence_time"] = pd.to_datetime(targets["commence_time"], utc=True, errors="coerce")
        for side in ["home", "away"]:
            temp = targets[["id", "commence_time", f"{side}_team"]].rename(columns={f"{side}_team": "player_team"}).sort_values("commence_time")
            merged = pd.merge_asof(temp, form.sort_values("match_date"), left_on="commence_time", right_on="match_date", by="player_team", direction="backward", allow_exact_matches=False)
            rename = {c: f"{side}_{c}" for c in roll_cols}
            df = df.merge(merged[["id"] + roll_cols].rename(columns=rename), on="id", how="left")
        for s in DIFF_STATS:
            h, a = f"home_team_last5_{s}_avg", f"away_team_last5_{s}_avg"
            if h in df and a in df:
                df[f"home_away_last5_{s}_diff"] = df[h] - df[a]
    return df


def feature_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric = [c for c in BASE_NUMERIC if c in df.columns]
    numeric += [c for c in df.columns if c.startswith(SAFE_PREFIXES)]
    categorical = [c for c in BASE_CATEGORICAL + SAFE_GAME_CATEGORICAL if c in df.columns]
    numeric = [c for c in numeric if c not in FORBIDDEN_FEATURES]
    categorical = [c for c in categorical if c not in FORBIDDEN_FEATURES]
    return numeric, categorical


def bucket_perf(df: pd.DataFrame, group: str) -> list[dict[str, Any]]:
    if group not in df or df.empty:
        return []
    g = df.groupby(group, dropna=False).agg(selections=("profit_units", "size"), wins=("target", "sum"), profit_units=("profit_units", "sum"), avg_odds=("odds", "mean")).reset_index()
    g["strike_rate"] = (g["wins"] / g["selections"]).round(4)
    g["roi"] = (g["profit_units"] / g["selections"]).round(4)
    return g.replace({np.nan: None}).to_dict("records")


def summarise(df: pd.DataFrame, include_segments: bool = True) -> dict[str, Any]:
    if df.empty:
        return {"total_selections": 0}
    out = {
        "total_selections": int(len(df)),
        "strike_rate": round(float(df["target"].mean()), 4),
        "total_profit_units": round(float(df["profit_units"].sum()), 3),
        "roi": round(float(df["profit_units"].sum() / len(df)), 4),
        "average_odds": round(float(df["odds"].mean()), 3),
        "by_market": bucket_perf(df, "market"),
        "by_line_type": bucket_perf(df, "line_type"),
        "by_bookmaker": bucket_perf(df, "bookmaker"),
        "by_recommendation": bucket_perf(df, "recommendation"),
    }
    d = df.copy()
    d["confidence_bucket"] = pd.cut(pd.to_numeric(d.get("confidence_score"), errors="coerce"), bins=[-np.inf, 50, 65, 80, np.inf], labels=["<50", "50-65", "65-80", "80+"])
    d["edge_bucket"] = pd.cut(pd.to_numeric(d.get("edge"), errors="coerce"), bins=[-np.inf, 0, 2, 5, 10, np.inf], labels=["<=0", "0-2", "2-5", "5-10", "10+"])
    d["odds_bucket"] = pd.cut(pd.to_numeric(d.get("odds"), errors="coerce"), bins=[1, 1.5, 2, 3, np.inf], labels=["1.01-1.50", "1.51-2.00", "2.01-3.00", "3.01+"])
    out["by_confidence_bucket"] = bucket_perf(d, "confidence_bucket")
    out["by_edge_bucket"] = bucket_perf(d, "edge_bucket")
    out["by_odds_bucket"] = bucket_perf(d, "odds_bucket")
    if include_segments:
        lower_market = d["market"].fillna("").str.lower()
        line_type = d["line_type"].fillna("").str.lower()
        out["h2h_results"] = summarise(d[lower_market.str.contains("h2h|winner", regex=True)], include_segments=False) if lower_market.str.contains("h2h|winner", regex=True).any() else {"total_selections": 0}
        out["line_spread_results"] = summarise(d[lower_market.str.contains("spread|line", regex=True) | line_type.str.contains("spread|line")], include_segments=False) if len(d) else {"total_selections": 0}
        out["totals_over_under_results"] = summarise(d[lower_market.str.contains("total|over|under", regex=True) | line_type.isin(["over", "under"])], include_segments=False) if len(d) else {"total_selections": 0}
        out["player_props_results"] = summarise(d[lower_market.str.startswith("player_")], include_segments=False) if len(d) else {"total_selections": 0}
    return out


def threshold_search(test: pd.DataFrame) -> dict[str, Any]:
    best = {"threshold": 0.50, "profit_units": float("-inf"), "roi": 0, "selections": 0}
    for th in np.arange(0.35, 0.81, 0.01):
        picks = test[test["ml_bet_probability"] >= th]
        if len(picks) < max(10, int(len(test) * 0.02)):
            continue
        profit = float(picks["profit_units"].sum())
        if profit > best["profit_units"]:
            best = {"threshold": round(float(th), 2), "profit_units": round(profit, 3), "roi": round(profit / len(picks), 4), "selections": int(len(picks))}
    if best["profit_units"] == float("-inf"):
        best = {"threshold": 0.50, "profit_units": 0, "roi": 0, "selections": 0}
    return best


def train() -> dict[str, Any]:
    require_dependencies()
    e = engine()
    report = schema_report(e)
    LOG.info("Pre-implementation AFL schema/data report:\n%s", json.dumps(report, default=str, indent=2))
    df = add_safe_features(e, load_selections(e, settled=True))
    if len(df) < 50:
        raise SystemExit(f"Not enough settled AFL rows to train: {len(df)}")
    df["target"] = (pd.to_numeric(df["profit_units"], errors="coerce") > 0).astype(int)
    if df["target"].nunique() < 2:
        raise SystemExit("Not enough class diversity to train AFL bet-quality model: target has one class")
    df["_order_date"] = pd.to_datetime(df.get("commence_time"), utc=True, errors="coerce").fillna(pd.to_datetime(df.get("settled_at"), utc=True, errors="coerce")).fillna(pd.to_datetime(df.get("created_at"), utc=True, errors="coerce"))
    if df["_order_date"].isna().any():
        raise SystemExit("Cannot create chronological validation split: some rows have no usable date")
    df = df.sort_values(["_order_date", "season", "round", "id"])
    split_idx = max(1, int(len(df) * 0.80))
    train_df, test_df = df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()
    if test_df.empty or train_df["target"].nunique() < 2:
        raise SystemExit("Chronological split did not leave enough train/test class diversity")
    numeric, categorical = feature_columns(df)
    pre = ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), numeric),
        ("cat", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5))]), categorical),
    ])
    clf = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.04, l2_regularization=0.05, random_state=42)
    pipe = Pipeline([("preprocess", pre), ("model", clf)])
    try:
        pipe.fit(train_df[numeric + categorical], train_df["target"])
    except Exception as exc:
        LOG.warning("HistGradientBoosting failed (%s); falling back to RandomForest", exc)
        pipe = Pipeline([("preprocess", pre), ("model", RandomForestClassifier(n_estimators=300, min_samples_leaf=10, random_state=42, n_jobs=-1, class_weight="balanced_subsample"))])
        pipe.fit(train_df[numeric + categorical], train_df["target"])
    test_df["ml_bet_probability"] = pipe.predict_proba(test_df[numeric + categorical])[:, 1]
    test_df["ml_expected_value"] = test_df["ml_bet_probability"] * (pd.to_numeric(test_df["odds"], errors="coerce") - 1.0) - (1.0 - test_df["ml_bet_probability"])
    threshold = threshold_search(test_df)
    summary = summarise(test_df)
    summary["training_rows"] = int(len(train_df)); summary["test_rows"] = int(len(test_df))
    try:
        summary["roc_auc"] = round(float(roc_auc_score(test_df["target"], test_df["ml_bet_probability"])), 4)
    except Exception:
        summary["roc_auc"] = None
    summary["recommended_threshold"] = threshold
    artifact = {"schema_version": MODEL_SCHEMA_VERSION, "model": pipe, "numeric_features": numeric, "categorical_features": categorical, "feature_columns": numeric + categorical, "target_definition": TARGET_DEFINITION, "training_timestamp": datetime.now(timezone.utc).isoformat(), "training_row_count": int(len(df)), "validation_method": "time-based 80/20 split ordered by commence_time/settled_at/created_at", "validation_summary": summary, "schema_report": report, "feature_audit": FEATURE_AUDIT}
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists():
        backup = MODEL_PATH.with_suffix(f".{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.pkl")
        shutil.copy2(MODEL_PATH, backup)
    tmp_model = MODEL_PATH.with_suffix(".tmp.pkl")
    tmp_meta = META_PATH.with_suffix(".tmp.json")
    joblib.dump(artifact, tmp_model)
    tmp_meta.write_text(json.dumps({k: v for k, v in artifact.items() if k != "model"}, default=str, indent=2) + "\n")
    os.replace(tmp_model, MODEL_PATH)
    os.replace(tmp_meta, META_PATH)
    print(json.dumps(summary, default=str, indent=2))
    return summary


def validate_artifact(artifact: dict[str, Any]) -> None:
    required = {"schema_version", "model", "feature_columns", "target_definition", "training_timestamp", "validation_summary"}
    missing = sorted(required - set(artifact))
    if missing:
        raise SystemExit(f"Invalid AFL model artifact; missing keys: {missing}")
    if artifact.get("schema_version") != MODEL_SCHEMA_VERSION:
        raise SystemExit(f"Unsupported AFL model artifact schema_version={artifact.get('schema_version')}")
    if artifact.get("target_definition") != TARGET_DEFINITION:
        raise SystemExit("Invalid AFL model artifact; target definition does not match scorer")
    if not artifact.get("feature_columns"):
        raise SystemExit("Invalid AFL model artifact; feature_columns is empty")
    if not hasattr(artifact.get("model"), "predict_proba"):
        raise SystemExit("Invalid AFL model artifact; model does not support predict_proba")
    max_age_days = int(os.getenv("AFL_ML_MODEL_MAX_AGE_DAYS", "30"))
    trained_at = pd.to_datetime(artifact.get("training_timestamp"), utc=True, errors="coerce")
    if pd.isna(trained_at):
        raise SystemExit("Invalid AFL model artifact; training_timestamp is not parseable")
    age_days = (pd.Timestamp.now(tz="UTC") - trained_at).total_seconds() / 86400.0
    if age_days > max_age_days:
        raise SystemExit(
            f"AFL model artifact is stale ({age_days:.1f} days old; max {max_age_days}). "
            "Run python afl_backtest.py --train on Railway before scoring."
        )


def score_current() -> pd.DataFrame:
    require_dependencies()
    e = engine()
    if not MODEL_PATH.exists():
        raise SystemExit(f"AFL model artifact does not exist: {MODEL_PATH}. Run python afl_backtest.py --train on Railway first.")
    artifact = joblib.load(MODEL_PATH)
    validate_artifact(artifact)
    df = add_safe_features(e, load_selections(e, settled=False))
    if df.empty:
        SCORES_PATH.write_text("[]\n")
        print("[]")
        return df
    features = artifact["feature_columns"]
    for c in features:
        if c not in df:
            df[c] = np.nan
    prob = artifact["model"].predict_proba(df[features])[:, 1]
    threshold = float(artifact.get("validation_summary", {}).get("recommended_threshold", {}).get("threshold", 0.5))
    out_cols = ["id", "event_id", "match_id", "commence_time", "home_team", "away_team", "team", "opponent", "market", "line_type", "line", "odds", "bookmaker", "recommendation", "model_prob", "edge", "confidence_score"]
    out = df[[c for c in out_cols if c in df]].copy()
    out = out.rename(columns={"id": "selection_id", "recommendation": "existing_recommendation", "model_prob": "original_model_prob", "edge": "original_edge"})
    out["ml_bet_probability"] = np.round(prob, 4)
    out["ml_expected_value_score"] = np.round(prob * (pd.to_numeric(df["odds"], errors="coerce") - 1.0) - (1.0 - prob), 4)
    out["ml_recommendation"] = np.where(out["ml_bet_probability"] >= threshold, "BET", "PASS")
    out["threshold_used"] = threshold
    SCORES_PATH.write_text(out.replace({np.nan: None}).to_json(orient="records", date_format="iso", indent=2) + "\n")
    print(out.replace({np.nan: None}).to_json(orient="records", date_format="iso", indent=2))
    return out


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description="AFL bet-quality model trainer/scorer",
        epilog=(
            "Railway commands: python afl_backtest.py --schema-report | "
            "python afl_backtest.py --train | python afl_backtest.py --score-current. "
            "Mount a Railway volume at models/ if trained .pkl artifacts must "
            "persist between deploys; otherwise regenerate the model on Railway."
        ),
    )
    p.add_argument("--train", action="store_true")
    p.add_argument("--score-current", action="store_true")
    p.add_argument("--schema-report", action="store_true")
    args = p.parse_args()
    if args.schema_report:
        print(json.dumps(schema_report(engine()), default=str, indent=2))
    if args.train:
        train()
    if args.score_current:
        score_current()
    if not (args.schema_report or args.train or args.score_current):
        p.print_help()


if __name__ == "__main__":
    main()
