-- How have the AFL and UFC models actually gone? Settled picks only.

-- @afl_overall
select season, coalesce(selection_source, '?') as selection_source, count(*) as bets,
       round(100.0 * avg((result = 'win')::int), 1) as win_pct,
       round(avg(odds)::numeric, 2) as avg_odds,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round(sum(profit_units)::numeric, 1) as profit_units,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1, 2 order by 1, 2;

-- @afl_results_seen
select result, count(*) from afl_model_selections group by 1 order by 2 desc;

-- @afl_by_market
select market, line_type, count(*) as bets,
       round(100.0 * avg((result = 'win')::int), 1) as win_pct,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1, 2 order by 3 desc;

-- @afl_by_edge
-- If the edge number means anything, bigger edges should win more than the price says.
select case when edge < 2 then 'a <2' when edge < 5 then 'b 2-5' when edge < 10 then 'c 5-10'
            when edge < 20 then 'd 10-20' else 'e 20+' end as edge_band,
       count(*) as bets, round(avg(model_prob)::numeric, 1) as model_says_pct,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round(100.0 * avg((result = 'win')::int), 1) as actual_win_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1 order by 1;

-- @afl_by_confidence
select case when confidence_score < 50 then 'a <50' when confidence_score < 65 then 'b 50-65'
            when confidence_score < 80 then 'c 65-80' else 'd 80+' end as confidence_band,
       count(*) as bets, round(100.0 * avg((result = 'win')::int), 1) as win_pct,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1 order by 1;

-- @afl_by_odds
select case when odds < 1.5 then 'a <1.50' when odds < 1.8 then 'b 1.50-1.80' when odds < 2.0 then 'c 1.80-2.00'
            when odds < 2.5 then 'd 2.00-2.50' else 'e 2.50+' end as odds_band,
       count(*) as bets, round(100.0 * avg((result = 'win')::int), 1) as win_pct,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1 order by 1;

-- @afl_by_bookmaker
select bookmaker, count(*) as bets, round(100.0 * avg((result = 'win')::int), 1) as win_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1 order by 2 desc;

-- @afl_by_round
select season, round, count(*) as bets, round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1, 2 order by 1, 2;

-- @mma_accuracy
-- UFC: how often the predicted winner won, by how sure the model was.
select case when greatest(p.f1_win_probability, p.f2_win_probability) < 0.55 then 'a 50-55%'
            when greatest(p.f1_win_probability, p.f2_win_probability) < 0.65 then 'b 55-65%'
            when greatest(p.f1_win_probability, p.f2_win_probability) < 0.75 then 'c 65-75%'
            else 'd 75%+' end as model_confidence,
       count(*) as fights,
       round(100.0 * avg(greatest(p.f1_win_probability, p.f2_win_probability))::numeric, 1) as model_says_pct,
       round(100.0 * avg((p.predicted_winner = f.winner_name)::int), 1) as actual_win_pct
from mma_fights f
join mma_events e on e.id = f.event_id
join mma_predictions p on p.fight_id = f.id
where e.is_completed and f.winner_name is not null and p.f1_win_probability is not null
  and coalesce(p.model_version, 'catboost_v1') <> 'fallback_5050'
group by 1 order by 1;

-- @mma_span
select count(*) as scored_fights, min(e.date) as first_event, max(e.date) as last_event,
       sum((coalesce(p.model_version, 'catboost_v1') = 'fallback_5050')::int) as coin_flip_fallbacks
from mma_fights f
join mma_events e on e.id = f.event_id
join mma_predictions p on p.fight_id = f.id
where e.is_completed and f.winner_name is not null;

-- @mma_odds_kept
-- Are bookmaker odds kept after a fight? Needed to judge the model against the market.
select count(*) as odds_rows, count(distinct event_key) as events,
       min(commence_time) as first_fight, max(commence_time) as last_fight,
       sum((commence_time < now())::int) as rows_for_past_fights
from mma_fight_odds;

-- @mma_vs_market
-- Completed fights that still have odds: model's pick vs the favourite.
with o as (
  select lower(fighter_name) as fighter, lower(fighter_1_name) as a, lower(fighter_2_name) as b,
         commence_time::date as d, avg(odds) as odds
  from mma_fight_odds where odds > 1 group by 1, 2, 3, 4),
x as (
  select f.id, f.winner_name, p.predicted_winner, p.f1_win_probability as p1,
         o1.odds as odds1, o2.odds as odds2,
         (1 / o1.odds) / (1 / o1.odds + 1 / o2.odds) as mkt1
  from mma_fights f
  join mma_events e on e.id = f.event_id
  join mma_predictions p on p.fight_id = f.id
  join o o1 on o1.fighter = lower(f.fighter_1_name) and abs(o1.d - e.date) <= 1
  join o o2 on o2.fighter = lower(f.fighter_2_name) and abs(o2.d - e.date) <= 1
  where e.is_completed and f.winner_name is not null and p.f1_win_probability is not null
    and coalesce(p.model_version, 'catboost_v1') <> 'fallback_5050')
select count(*) as fights,
       round(100.0 * avg((predicted_winner = winner_name)::int), 1) as model_pick_wins_pct,
       round(100.0 * avg(((mkt1 >= 0.5) = (winner_name = (select fighter_1_name from mma_fights where id = x.id)))::int), 1) as favourite_wins_pct,
       round(avg(-ln(greatest(case when winner_name = (select fighter_1_name from mma_fights where id = x.id) then p1 else 1 - p1 end, 0.001)))::numeric, 4) as model_log_loss,
       round(avg(-ln(greatest(case when winner_name = (select fighter_1_name from mma_fights where id = x.id) then mkt1 else 1 - mkt1 end, 0.001)))::numeric, 4) as market_log_loss
from x;
