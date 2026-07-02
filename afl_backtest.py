#!/usr/bin/env python3
"""AFL bet-quality / meta-model training and scoring.

This module is intentionally AFL-only. It does not import or mutate the horse
racing backtest, routes, templates, database logic, or model artifacts.

Usage:
  DATABASE_URL=postgresql://... python afl_backtest.py --schema-report
  DATABASE_URL=postgresql://... python afl_backtest.py --train
  DATABASE_URL=postgresql://... python afl_backtest.py --score-current
  DATABASE_URL=postgresql://... python afl_backtest.py --model-status

Railway setup:
  1. Attach the same Postgres DATABASE_URL used by the AFL app.
  2. Run schema/report first: python afl_backtest.py --schema-report
  3. Train after a round is settled: python afl_backtest.py --train
  4. Score current selections after odds syncs: python afl_backtest.py --score-current

Model artifacts:
  --train creates models/afl_bet_quality_model.pkl and
  models/afl_bet_quality_model_meta.json from live Railway Postgres data,
  then stores the .pkl bytes and metadata in Postgres table afl_ml_artifacts.
  The web app first tries the local .pkl and falls back to the active Postgres
  artifact so Railway cron and web services can share the trained model.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import importlib.util
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

REQUIRED_ML_DEPENDENCIES = {
    "joblib": "joblib",
    "numpy": "numpy",
    "pandas": "pandas",
    "scikit-learn": "sklearn",
}

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
ARTIFACT_MODEL_NAME = "afl_bet_quality_model"
ARTIFACT_VERSION = f"schema_v{MODEL_SCHEMA_VERSION}"

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


def missing_ml_dependencies() -> list[str]:
    missing = [package for package, module in REQUIRED_ML_DEPENDENCIES.items() if importlib.util.find_spec(module) is None]
    if _MISSING_DEPENDENCY is not None:
        missing_name = getattr(_MISSING_DEPENDENCY, "name", None)
        for package, module in REQUIRED_ML_DEPENDENCIES.items():
            if missing_name == module and package not in missing:
                missing.append(package)
    return missing


def ml_dependency_install_message(missing: list[str] | None = None) -> str:
    missing = missing or missing_ml_dependencies()
    missing_label = ", ".join(missing) if missing else str(_MISSING_DEPENDENCY)
    return (
        f"AFL ML runtime dependencies missing: {missing_label}. "
        "Install locally with: python -m pip install -r requirements.txt. "
        "On Railway, redeploy the AFL web/cron service so requirements.txt is installed. "
        "Required packages: joblib, scikit-learn, numpy, pandas."
    )


def require_dependencies() -> None:
    missing = missing_ml_dependencies()
    if _MISSING_DEPENDENCY is not None or missing:
        raise SystemExit(ml_dependency_install_message(missing))

def engine() -> Engine:
    require_dependencies()
    return create_engine(db_url(), pool_pre_ping=True)


def read_sql(e: Engine, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    return pd.read_sql_query(text(sql), e, params=params or {})


def json_safe(value: Any) -> Any:
    """Convert pandas/numpy/Decimal/NaN values to JSON-safe Python values."""
    if value is None:
        return None
    if pd is not None:
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
    if np is not None:
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            value = float(value)
        if isinstance(value, np.ndarray):
            return [json_safe(v) for v in value.tolist()]
    if isinstance(value, float):
        return value if np is None or np.isfinite(value) else None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return value


def df_to_safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    safe = df.replace([np.inf, -np.inf], np.nan).replace({np.nan: None})
    return json_safe(safe.to_dict(orient="records"))


def log_dataframe_diagnostics(label: str, df: pd.DataFrame, required: list[str] | None = None) -> dict[str, Any]:
    required = required or []
    missing = [c for c in required if c not in df.columns]
    LOG.info("AFL_PIPELINE_DIAG %s rows=%s cols=%s missing_required=%s", label, len(df), list(df.columns), missing)
    return {"rows": int(len(df)), "columns": list(df.columns), "missing_required_columns": missing}


def ensure_artifact_table(e: Engine) -> None:
    """Ensure the AFL-only ML artifact table exists in Postgres."""
    with e.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS afl_ml_artifacts (
                id SERIAL PRIMARY KEY,
                model_name TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                trained_at TIMESTAMP,
                artifact_bytes BYTEA NOT NULL,
                meta_json JSONB,
                is_active BOOLEAN DEFAULT FALSE
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_afl_ml_artifacts_active
            ON afl_ml_artifacts(model_name, is_active, created_at DESC)
        """))


def _artifact_meta(artifact: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in artifact.items() if k != "model"}


def save_artifact_to_postgres(e: Engine, artifact_bytes: bytes, meta: dict[str, Any]) -> int:
    ensure_artifact_table(e)
    trained_at = meta.get("training_timestamp")
    LOG.info(
        "Saving AFL ML model artifact to Postgres table afl_ml_artifacts (model_name=%s version=%s bytes=%s trained_at=%s)",
        ARTIFACT_MODEL_NAME,
        ARTIFACT_VERSION,
        len(artifact_bytes),
        trained_at,
    )
    with e.begin() as conn:
        conn.execute(
            text("UPDATE afl_ml_artifacts SET is_active = FALSE WHERE model_name = :model_name AND is_active = TRUE"),
            {"model_name": ARTIFACT_MODEL_NAME},
        )
        artifact_id = conn.execute(
            text("""
                INSERT INTO afl_ml_artifacts
                    (model_name, version, trained_at, artifact_bytes, meta_json, is_active)
                VALUES
                    (:model_name, :version, :trained_at, :artifact_bytes, CAST(:meta_json AS JSONB), TRUE)
                RETURNING id
            """),
            {
                "model_name": ARTIFACT_MODEL_NAME,
                "version": ARTIFACT_VERSION,
                "trained_at": trained_at,
                "artifact_bytes": artifact_bytes,
                "meta_json": json.dumps(meta, default=str),
            },
        ).scalar_one()
    LOG.info("Saved active AFL ML model artifact to Postgres (id=%s)", artifact_id)
    return int(artifact_id)


def load_active_artifact_from_postgres(e: Engine) -> dict[str, Any] | None:
    ensure_artifact_table(e)
    LOG.info("Loading active AFL ML model artifact from Postgres table afl_ml_artifacts")
    with e.begin() as conn:
        row = conn.execute(
            text("""
                SELECT id, version, created_at, trained_at, artifact_bytes, meta_json
                FROM afl_ml_artifacts
                WHERE model_name = :model_name AND is_active = TRUE
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            """),
            {"model_name": ARTIFACT_MODEL_NAME},
        ).mappings().first()
    if not row:
        LOG.warning("No active AFL ML model artifact found in Postgres")
        return None
    LOG.info(
        "Loaded active AFL ML model artifact metadata from Postgres (id=%s version=%s bytes=%s trained_at=%s)",
        row["id"], row["version"], len(row["artifact_bytes"] or b""), row["trained_at"],
    )
    import io
    artifact = joblib.load(io.BytesIO(bytes(row["artifact_bytes"])))
    return artifact


def load_model_artifact(e: Engine | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if MODEL_PATH.exists():
        LOG.info("Loading AFL ML model artifact from local filesystem: %s", MODEL_PATH)
        return joblib.load(MODEL_PATH), "local"
    LOG.warning("Local AFL ML model artifact missing at %s; trying Postgres", MODEL_PATH)
    e = e or engine()
    artifact = load_active_artifact_from_postgres(e)
    if artifact is not None:
        return artifact, "postgres"
    return None, None


def model_status(emit: bool = True) -> dict[str, Any]:
    require_dependencies()
    status: dict[str, Any] = {
        "expected_model_name": ARTIFACT_MODEL_NAME,
        "local_model": {"path": str(MODEL_PATH), "exists": MODEL_PATH.exists()},
        "postgres_active_model": {"exists": False},
        "model_source": "local" if MODEL_PATH.exists() else "missing",
    }
    if MODEL_PATH.exists():
        status["local_model"]["bytes"] = MODEL_PATH.stat().st_size
    e = engine()
    try:
        ensure_artifact_table(e)
        with e.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT id, model_name, version, created_at, trained_at,
                           OCTET_LENGTH(artifact_bytes) AS bytes,
                           (artifact_bytes IS NOT NULL) AS artifact_bytes_not_null,
                           is_active
                    FROM afl_ml_artifacts
                    WHERE model_name = :model_name AND is_active = TRUE
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                """),
                {"model_name": ARTIFACT_MODEL_NAME},
            ).mappings().first()
        if row:
            status["postgres_active_model"] = {"exists": True, **dict(row)}
            if not MODEL_PATH.exists():
                status["model_source"] = "postgres"
    except Exception as exc:
        LOG.exception("AFL ML model-status Postgres check failed")
        status["postgres_active_model"] = {"exists": False, "error": str(exc)}
    if emit:
        print(json.dumps(json_safe(status), default=str, indent=2, allow_nan=False))
    return status


def schema_report(e: Engine) -> dict[str, Any]:
    insp = inspect(e)
    tables = [t for t in ["afl_games", "afl_match_markets", "afl_match_predictions", "afl_model_selections", "afl_player_stats", "afl_player_props", "afl_standings", "afl_sync_log", "afl_ml_artifacts", "afl_team_logos", "afl_tips"] if insp.has_table(t)]
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


def _normalise_merge_key(values: pd.Series) -> pd.Series:
    """Return a stable string key for joins where DB drivers may disagree on int/float dtypes.

    PostgreSQL integer columns can arrive as int64 in one dataframe and float64 in
    another when NULLs are present. Pandas refuses to merge those directly, so we
    normalise integer-like values to the same string representation before joins.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    text = values.astype("string").str.strip()
    integer_like = numeric.notna() & np.isclose(numeric, np.floor(numeric))
    normalised = text.copy()
    normalised.loc[integer_like] = numeric.loc[integer_like].astype("Int64").astype("string")
    normalised = normalised.mask(text.isna() | text.eq("") | text.str.lower().isin(["nan", "none", "<na>"]))
    return normalised

def _normalise_team_series(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip().str.lower()


def _log_pre_merge(label: str, left: pd.DataFrame, right: pd.DataFrame, on: list[str]) -> None:
    """Log dtype, null count, and sample values for merge keys on both sides before a merge."""
    for col in on:
        left_dtype = str(left[col].dtype) if col in left.columns else "MISSING"
        right_dtype = str(right[col].dtype) if col in right.columns else "MISSING"
        left_nulls = int(left[col].isna().sum()) if col in left.columns else -1
        right_nulls = int(right[col].isna().sum()) if col in right.columns else -1
        left_sample = left[col].dropna().head(3).tolist() if col in left.columns else []
        right_sample = right[col].dropna().head(3).tolist() if col in right.columns else []
        LOG.info(
            "AFL_MERGE_PRE %s col=%s left_dtype=%s right_dtype=%s "
            "left_nulls=%s right_nulls=%s left_sample=%s right_sample=%s",
            label, col, left_dtype, right_dtype,
            left_nulls, right_nulls, left_sample, right_sample,
        )


def _add_game_context(e: Engine, df: pd.DataFrame) -> pd.DataFrame:
    games = read_sql(e, "SELECT id AS match_id, venue, roundname FROM afl_games")
    if games.empty or "match_id" not in df:
        for col in SAFE_GAME_CATEGORICAL:
            if col not in df:
                df[col] = None
        return df
    out = df.copy()
    games = games.copy()
    out["__match_id_key"] = _normalise_merge_key(out["match_id"])
    games["__match_id_key"] = _normalise_merge_key(games["match_id"])
    _log_pre_merge("game_context", out, games.drop_duplicates("__match_id_key"), ["__match_id_key"])
    return out.merge(
        games.drop_duplicates("__match_id_key").drop(columns=["match_id"]),
        on="__match_id_key",
        how="left",
    ).drop(columns=["__match_id_key"], errors="ignore")


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
    # Normalize event_id to a stable string key to avoid float64/int64 dtype
    # mismatch between afl_model_selections and afl_match_markets.
    out["__event_id_key"] = _normalise_merge_key(out["event_id"])
    agg["__event_id_key"] = _normalise_merge_key(agg["event_id"])
    merge_cols = ["__event_id_key", "bookmaker", "market", "line"]
    _log_pre_merge("market_features", out, agg, merge_cols)
    try:
        result = out.merge(
            agg.drop(columns=["event_id"]),
            on=merge_cols,
            how="left",
        ).drop(columns=["__event_id_key"], errors="ignore")
    except Exception as exc:
        def _col_info(frame: pd.DataFrame, cols: list[str]) -> dict:
            return {c: {"dtype": str(frame[c].dtype), "nulls": int(frame[c].isna().sum())} for c in cols if c in frame.columns}
        left_info = _col_info(out, merge_cols)
        right_info = _col_info(agg, merge_cols)
        LOG.error(
            "AFL_MERGE_FAILED market_features error=%s left_cols=%s right_cols=%s",
            exc, left_info, right_info,
        )
        left_eid_dtype = out["event_id"].dtype if "event_id" in out.columns else "MISSING"
        right_eid_dtype = agg["event_id"].dtype if "event_id" in agg.columns else "MISSING"
        raise RuntimeError(
            f"AFL market features merge failed: {exc}. "
            f"Left event_id dtype={left_eid_dtype}, Right event_id dtype={right_eid_dtype}, "
            f"merge_cols={merge_cols}"
        ) from exc
    return result


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
            right = standings[(standings["season"] == season) & (standings["team_key"] == team_key)].copy()
            group = group.copy()
            group["round"] = pd.to_numeric(group["round"], errors="coerce")
            right["round"] = pd.to_numeric(right["round"], errors="coerce")
            group = group.dropna(subset=["round"]).copy()
            right = right.dropna(subset=["round"]).copy()
            if group.empty or right.empty:
                continue
            group["round"] = group["round"].astype("float64")
            right["round"] = right["round"].astype("float64")
            group = group.sort_values("round")
            right = right.sort_values("round")
            _log_pre_merge("standings_asof", group, right, ["round"])
            merged_parts.append(pd.merge_asof(group, right, on="round", direction="backward", allow_exact_matches=False))
        if not merged_parts:
            continue
        merged = pd.concat(merged_parts, ignore_index=True)
        cols = ["rank", "pts", "wins", "losses", "percentage"]
        rename = {c: f"standings_{side}_{c}" for c in cols}
        out["__selection_id_key"] = _normalise_merge_key(out["id"])
        feature_rows = merged[["id"] + cols].rename(columns=rename).copy()
        feature_rows["__selection_id_key"] = _normalise_merge_key(feature_rows["id"])
        _log_pre_merge(f"standings_{side}", out, feature_rows, ["__selection_id_key"])
        out = out.merge(
            feature_rows.drop(columns=["id"]),
            on="__selection_id_key",
            how="left",
        ).drop(columns=["__selection_id_key"], errors="ignore")
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
        preds = preds.copy()
        df["__match_id_key"] = _normalise_merge_key(df["match_id"])
        preds["__match_id_key"] = _normalise_merge_key(preds["match_id"])
        _log_pre_merge("predictions", df, preds.drop_duplicates("__match_id_key"), ["__match_id_key"])
        df = df.merge(
            preds.drop_duplicates("__match_id_key").drop(columns=["match_id"]),
            on="__match_id_key",
            how="left",
        ).drop(columns=["__match_id_key"], errors="ignore")
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
            df["__selection_id_key"] = _normalise_merge_key(df["id"])
            feature_rows = merged[["id"] + roll_cols].rename(columns=rename).copy()
            feature_rows["__selection_id_key"] = _normalise_merge_key(feature_rows["id"])
            _log_pre_merge(f"player_stats_{side}", df, feature_rows, ["__selection_id_key"])
            df = df.merge(
                feature_rows.drop(columns=["id"]),
                on="__selection_id_key",
                how="left",
            ).drop(columns=["__selection_id_key"], errors="ignore")
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
    LOG.info("AFL_TRAIN_START model_name=%s local_path=%s", ARTIFACT_MODEL_NAME, MODEL_PATH)
    LOG.info("Pre-implementation AFL schema/data report:\n%s", json.dumps(report, default=str, indent=2))
    raw_df = load_selections(e, settled=True)
    LOG.info("AFL_TRAIN_ROWS_LOADED rows=%s", len(raw_df))
    log_dataframe_diagnostics("settled_loaded", raw_df, ["profit_units", "result", "odds", "settled_at"])
    df = add_safe_features(e, raw_df)
    log_dataframe_diagnostics("settled_with_features", df)
    if len(df) < 50:
        raise SystemExit(f"Not enough settled AFL rows to train: {len(df)}")
    df["target"] = (pd.to_numeric(df["profit_units"], errors="coerce") > 0).astype(int)
    LOG.info("AFL_PIPELINE_DIAG target_distribution=%s", df["target"].value_counts(dropna=False).to_dict())
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
    LOG.info("AFL_PIPELINE_DIAG feature_matrix rows=%s numeric=%s categorical=%s shape=(%s,%s)", len(df), numeric, categorical, len(df), len(numeric) + len(categorical))
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
    meta = _artifact_meta(artifact)
    tmp_meta.write_text(json.dumps(meta, default=str, indent=2) + "\n")
    os.replace(tmp_model, MODEL_PATH)
    os.replace(tmp_meta, META_PATH)
    LOG.info("Saved AFL ML model artifact to local filesystem: %s bytes=%s", MODEL_PATH, MODEL_PATH.stat().st_size)
    LOG.info("AFL_TRAIN_MODEL_SAVED_LOCAL path=%s bytes=%s", MODEL_PATH, MODEL_PATH.stat().st_size)
    LOG.info("Saved AFL ML model metadata to local filesystem: %s", META_PATH)
    artifact_id = save_artifact_to_postgres(e, MODEL_PATH.read_bytes(), meta)
    summary["postgres_artifact_id"] = artifact_id
    LOG.info("AFL_TRAIN_MODEL_SAVED_POSTGRES artifact_id=%s model_name=%s", artifact_id, ARTIFACT_MODEL_NAME)
    LOG.info("AFL_TRAIN_END status=ok artifact_id=%s", artifact_id)
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


def score_current(emit: bool = True) -> pd.DataFrame:
    require_dependencies()
    e = engine()
    artifact, artifact_source = load_model_artifact(e)
    if artifact is None:
        raise SystemExit(f"AFL model artifact does not exist locally at {MODEL_PATH} and no active Postgres artifact was found. Run python afl_backtest.py --train on Railway first.")
    LOG.info("Using AFL ML model artifact source=%s", artifact_source)
    validate_artifact(artifact)
    raw_df = load_selections(e, settled=False)
    log_dataframe_diagnostics("current_loaded", raw_df, ["odds", "result"])
    df = add_safe_features(e, raw_df)
    log_dataframe_diagnostics("current_with_features", df)
    if df.empty:
        SCORES_PATH.write_text("[]\n")
        if emit:
            print("[]")
        return df
    features = artifact["feature_columns"]
    for c in features:
        if c not in df:
            df[c] = np.nan
    prob = artifact["model"].predict_proba(df[features])[:, 1]
    threshold = float(artifact.get("validation_summary", {}).get("recommended_threshold", {}).get("threshold", 0.5))
    out_cols = ["id", "event_id", "match_id", "commence_time", "home_team", "away_team", "player_name", "team", "opponent", "market", "line_type", "line", "odds", "bookmaker", "recommendation", "model_prob", "edge", "confidence_score"]
    out = df[[c for c in out_cols if c in df]].copy()
    out = out.rename(columns={"id": "selection_id", "recommendation": "existing_recommendation", "model_prob": "original_model_prob", "edge": "original_edge"})
    out["ml_bet_probability"] = np.round(prob, 4)
    out["ml_expected_value_score"] = np.round(prob * (pd.to_numeric(df["odds"], errors="coerce") - 1.0) - (1.0 - prob), 4)
    out["ml_recommendation"] = np.where(out["ml_bet_probability"] >= threshold, "BET", "PASS")
    out["threshold_used"] = threshold
    safe_rows = df_to_safe_records(out)
    SCORES_PATH.write_text(json.dumps(safe_rows, default=str, indent=2, allow_nan=False) + "\n")
    if emit:
        print(json.dumps(safe_rows, default=str, indent=2, allow_nan=False))
    LOG.info("AFL_PIPELINE_DIAG current_scored rows=%s response_keys=%s", len(safe_rows), list(safe_rows[0].keys()) if safe_rows else [])
    return out



def debug_pipeline() -> dict[str, Any]:
    """Print an evidence-first AFL ML/backtest health report and exit 0."""
    out: dict[str, Any] = {"ok": True, "database": {}, "counts": {}, "ml": {}}
    if _MISSING_DEPENDENCY is not None:
        out["ok"] = False
        out["dependency_error"] = ml_dependency_install_message()
        out["ml"] = {
            "local_model": {"path": str(MODEL_PATH), "exists": MODEL_PATH.exists()},
            "postgres_active_model": {"exists": False, "error": "dependencies unavailable"},
        }
        print(json.dumps(json_safe(out), indent=2, default=str, allow_nan=False))
        return out
    try:
        e = engine()
        with e.connect() as conn:
            conn.execute(text("SELECT 1"))
        out["database"] = {"connected": True}
        LOG.info("AFL_CRON_DB_CONNECTED debug_pipeline=1")
    except BaseException as exc:
        out["ok"] = False
        out["database"] = {"connected": False, "error": str(exc)}
        out["ml"] = {
            "local_model": {"path": str(MODEL_PATH), "exists": MODEL_PATH.exists()},
            "postgres_active_model": {"exists": False, "error": "database unavailable"},
        }
        print(json.dumps(json_safe(out), indent=2, default=str, allow_nan=False))
        return out

    tables = ["afl_games", "afl_player_stats", "afl_player_props", "afl_match_markets", "afl_model_selections", "afl_ml_artifacts"]
    with e.begin() as conn:
        for table in tables:
            try:
                out["counts"][table] = int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
            except Exception as exc:
                out["counts"][table] = {"error": str(exc)}
        try:
            out["counts"]["completed_games"] = int(conn.execute(text("SELECT COUNT(*) FROM afl_games WHERE complete = 100 OR complete = TRUE OR winner IS NOT NULL OR (hscore IS NOT NULL AND ascore IS NOT NULL)")).scalar() or 0)
        except Exception as exc:
            out["counts"]["completed_games"] = {"error": str(exc)}

    raw = load_selections(e, settled=True)
    raw_diag = log_dataframe_diagnostics("debug_settled_loaded", raw, ["profit_units", "result", "odds", "settled_at"])
    featured = add_safe_features(e, raw)
    numeric, categorical = feature_columns(featured)
    target = (pd.to_numeric(featured.get("profit_units"), errors="coerce") > 0).astype(int) if not featured.empty and "profit_units" in featured else pd.Series(dtype=int)
    status = model_status(emit=False)
    out.update({
        "rows_usable_for_ml": int(len(featured)),
        "missing_required_columns": raw_diag["missing_required_columns"],
        "feature_columns_used": numeric + categorical,
        "target_distribution": {str(k): int(v) for k, v in target.value_counts(dropna=False).to_dict().items()},
        "ml": status,
    })
    try:
        scored = score_current(emit=False)
        out["sample_5_scored_selections"] = df_to_safe_records(scored.head(5))
    except Exception as exc:
        out["sample_5_scored_selections"] = []
        out["scoring_error"] = str(exc)
    print(json.dumps(json_safe(out), indent=2, default=str, allow_nan=False))
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
    p.add_argument("--model-status", action="store_true")
    p.add_argument("--debug-pipeline", action="store_true")
    args = p.parse_args()
    if args.schema_report:
        print(json.dumps(schema_report(engine()), default=str, indent=2))
    if args.train:
        train()
    if args.score_current:
        score_current()
    if args.model_status:
        model_status()
    if args.debug_pipeline:
        debug_pipeline()
    if not (args.schema_report or args.train or args.score_current or args.model_status or args.debug_pipeline):
        p.print_help()


if __name__ == "__main__":
    main()
