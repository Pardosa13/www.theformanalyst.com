"""Race-level inputs: first starters, weight in lengths, and the likely pace.

Ideas from the reading list (Oct 2026):
- Peirson & Smith (2009): at the same odds, unraced horses win ~16% less often
  than raced ones, and ~34-41% less when most of the field is unraced. The gap
  is biggest at long odds. So the model gets a debut flag and the share of
  debutants in the race.
- Mordin, "Winning Without Thinking": the weight worth one length shrinks with
  distance, and weight only starts to slow a horse above ~53.5kg.
- Quirin speed points and pace handicapping: how many genuine leaders are in
  the race, and whether one horse is likely to lead unchallenged.
- Mordin: horses that change their usual running style rarely win.

Training (backtest.add_race_relative_features) and live scoring
(ml_predict.add_race_relative_features) both call add_race_shape_features on
the same columns, so the two always compute identical values.
"""
import numpy as np
import pandas as pd

# Column order is part of the training contract: appended after the
# race-relative columns, in this order, in training and live alike.
RACE_SHAPE_FEATURES = [
    'rs_is_debut',            # 1 if the horse has never raced
    'rs_debut_share',         # share of the field that has never raced
    'rs_debut_x_share',       # debut flag times debut share (the paper's strongest case)
    'rs_weight_lengths',      # weight above the race average, in lengths at today's distance
    'rs_weight_over_53_5',    # kg carried above 53.5kg (0 if at or below)
    'rs_n_speed',             # runners with 6+ speed points (regular early leaders)
    'rs_race_speed_total',    # total speed points in the race: likely pace pressure
    'rs_rival_speed_max',     # best speed points among this horse's rivals
    'rs_lone_speed',          # 1 if this horse has 6+ speed points and no rival has 4+
    'rs_style_change',        # predicted settle % today minus its usual settle % (negative = further forward)
]

# Mordin's kg-per-length scale. His table lists 0.5kg at 2000m, out of line
# with 1600m (1.0) and 2400m (0.65), so 2000m is interpolated instead.
KG_PER_LENGTH = ((1000, 1.75), (1200, 1.5), (1400, 1.25), (1600, 1.0), (2400, 0.65), (3200, 0.5))
WEIGHT_NEUTRAL_KG = 53.5
LEADER_POINTS = 6       # led or sat second in most of the last 3 runs
CHALLENGER_POINTS = 4


def kg_per_length(distance):
    """kg that costs one length at this distance (clamped to the table's ends)."""
    xs = [d for d, _ in KG_PER_LENGTH]
    ys = [k for _, k in KG_PER_LENGTH]
    return float(np.interp(distance, xs, ys))


def _col(temp, name):
    if name in temp.columns:
        return pd.to_numeric(temp[name], errors='coerce')
    return pd.Series(np.nan, index=temp.index)


def _rival_max(values):
    """For each runner, the highest value among the OTHER runners (NaN if none known)."""
    out = np.full(len(values), np.nan)
    known = ~np.isnan(values)
    for i in range(len(values)):
        others = known.copy()
        others[i] = False
        if others.any():
            out[i] = values[others].max()
    return out


def add_race_shape_features(temp, race_key=None):
    """Add RACE_SHAPE_FEATURES to a DataFrame of runners, in place.

    race_key: column naming each runner's race (training frames hold many
    races). None means the frame is one race (live scoring).
    Needs 'field_size' already set.
    """
    if temp.empty:
        for name in RACE_SHAPE_FEATURES:
            temp[name] = pd.Series(dtype=float)
        return temp
    groups = temp[race_key] if race_key else pd.Series(0, index=temp.index)

    career_runs = _col(temp, 'career_runs')
    is_debut = (career_runs == 0).astype(float).where(career_runs.notna())
    temp['rs_is_debut'] = is_debut
    temp['rs_debut_share'] = is_debut.groupby(groups).transform('mean')
    temp['rs_debut_x_share'] = temp['rs_is_debut'] * temp['rs_debut_share']

    weight = _col(temp, 'horse_weight')
    valid_weight = (weight >= 49) & (weight <= 65)
    # weight_vs_avg is race average minus this horse (positive = lighter), so
    # flip it: positive lengths = carrying more than the field.
    per_length = _col(temp, 'distance').fillna(1400).map(kg_per_length)
    temp['rs_weight_lengths'] = (-_col(temp, 'weight_vs_avg') / per_length).where(valid_weight)
    temp['rs_weight_over_53_5'] = (weight - WEIGHT_NEUTRAL_KG).clip(lower=0).where(valid_weight)

    points = _col(temp, 'ir_speed_points_l3')
    has_points = points.notna().groupby(groups).transform('any')
    temp['rs_n_speed'] = (points >= LEADER_POINTS).astype(float).groupby(groups).transform('sum').where(has_points)
    temp['rs_race_speed_total'] = points.groupby(groups).transform('sum').where(has_points)
    rival = pd.Series(np.nan, index=temp.index)
    for _, index in points.groupby(groups).groups.items():
        rival.loc[index] = _rival_max(points.loc[index].to_numpy(dtype=float))
    temp['rs_rival_speed_max'] = rival
    lone = ((points >= LEADER_POINTS) & (rival < CHALLENGER_POINTS)).astype(float)
    temp['rs_lone_speed'] = lone.where(points.notna() & rival.notna())

    # Today's speed-map settle position as a share of the field, on the same
    # 0 (leading) to 1 (last) scale as the past in-run average.
    field = _col(temp, 'field_size')
    settle_today = (_col(temp, 'sm_settle') - 1) / (field - 1).where(field > 1)
    temp['rs_style_change'] = settle_today.clip(0, 1) - _col(temp, 'ir_avg_settle_pct_l5')
    return temp
