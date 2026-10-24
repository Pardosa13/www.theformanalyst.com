-- @backtest_runs_recent
select id, started_at::date, status, total_races, total_horses, round(baseline_roi::numeric,2) base_roi, round(grid_search_best_roi::numeric,3) gs_roi,
  round(grid_search_best_sr::numeric,1) gs_sr, round(jockey_snapshot_coverage_pct::numeric,1) jock_cov, left(notes,80)
from backtest_runs order by id desc limit 30;

-- @competition_columns
select column_name, data_type from information_schema.columns where table_name='backtest_model_competition' order by ordinal_position;

-- @best_model_history
select id, run_id, run_date, model_type, left(model_name,40) model, is_active, round(validation_roi::numeric,2) roi, round(validation_strike_rate::numeric,1) sr,
  validation_bets bets, round(validation_profit_units::numeric,1) profit, expected_feature_count feats, scoring_formula_version, promoted_at::date, deactivated_at::date
from backtest_best_model order by id;

-- @promotions
select * from backtest_model_promotions order by 1 desc limit 30;

-- @pattern_finder_runs
select * from pattern_finder_runs order by 1 desc limit 10;

-- @feature_importance_latest
select * from backtest_feature_importance where run_id=(select max(run_id) from backtest_feature_importance) order by 3 desc nulls last limit 60;

-- @ml_alerts
select * from ml_pipeline_alerts order by 1 desc limit 20;

-- @calibration_runs
select * from calibration_runs order by 1 desc limit 10;

-- @feature_ablation_runs
select id, left(row_to_json(f)::text, 600) from feature_ablation_runs f order by id desc limit 10;

-- @backtest_data_repairs
select * from backtest_data_repairs order by 1 desc limit 10;

-- @match_rate_history
select * from backtest_match_rate_history order by 1 desc limit 15;

-- @user_bets_summary
select sport, bookie, count(*), round(sum(stake)::numeric,0) staked, round(sum(payout)::numeric,0) returned,
  round((100*(sum(payout)-sum(stake))/nullif(sum(stake),0))::numeric,1) roi_pct, min(date_placed)::date, max(date_placed)::date
from bets group by 1,2 order by 3 desc;

-- @user_bets_by_month
select to_char(date_placed,'YYYY-MM') as mon, count(*), round(sum(stake)::numeric,0) staked, round((sum(payout)-sum(stake))::numeric,0) profit
from bets group by 1 order by 1;
