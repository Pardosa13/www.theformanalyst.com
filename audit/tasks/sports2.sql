-- Follow-up checks on the AFL and UFC models.

-- @mma_rating_age
-- When were fighter ratings last updated? They are only ever set by the seed import.
select date_trunc('month', glicko_updated_at)::date as updated_month, count(*) as fighters,
       sum((glicko_rating = 1500)::int) as still_default_1500
from mma_fighters group by 1 order by 1;

-- @mma_fighters_in_scored_fights
-- Fighters in fights the model scored: how many had only the default rating.
select count(*) as fighter_slots,
       sum((coalesce(m.glicko_rating, 1500) = 1500)::int) as default_rating
from (select fighter_1_id as fid from mma_fights f join mma_predictions p on p.fight_id = f.id
      union all select fighter_2_id from mma_fights f join mma_predictions p on p.fight_id = f.id) x
left join mma_fighters m on m.id = x.fid;

-- @afl_calibration
-- Does "model says X%" mean X%? Split by Over/Under.
select line_type,
       case when model_prob < 30 then 'a <30' when model_prob < 40 then 'b 30-40' when model_prob < 50 then 'c 40-50'
            when model_prob < 60 then 'd 50-60' when model_prob < 70 then 'e 60-70' else 'f 70+' end as model_band,
       count(*) as bets, round(avg(model_prob)::numeric, 1) as model_says_pct,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round(100.0 * avg((result = 'win')::int), 1) as actual_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1, 2 order by 1, 2;

-- @afl_lines_per_player
-- How many lines per player per game get saved. Picking the best of many lines
-- for the same player inflates the edge (the line where the model is most wrong wins).
select n_lines, count(*) as player_games, sum(bets) as bets,
       round((100.0 * sum(profit) / sum(bets))::numeric, 1) as roi_pct
from (select player_name, match_id, market, line_type, count(*) as bets, sum(profit_units) as profit,
             case when count(*) = 1 then '1' when count(*) <= 3 then '2-3' else '4+' end as n_lines
      from afl_model_selections
      where settled_at is not null and result in ('win', 'loss') and odds > 1
      group by 1, 2, 3, 4) g
group by 1 order by 1;

-- @afl_value_only
-- Only the picks the site actually calls "value".
select recommendation, line_type, count(*) as bets,
       round(100.0 * avg((result = 'win')::int), 1) as win_pct,
       round(100.0 * avg(1.0 / nullif(odds, 0))::numeric, 1) as price_says_pct,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1
group by 1, 2 order by 1, 2;

-- @afl_edge_by_half
-- Is the 10-20 edge band profitable in both halves of the season?
select case when round <= 14 then 'rounds <=14' else 'rounds 15+' end as half,
       case when edge < 5 then 'a <5' when edge < 10 then 'b 5-10' when edge < 20 then 'c 10-20' else 'd 20+' end as edge_band,
       line_type, count(*) as bets,
       round((100.0 * sum(profit_units) / count(*))::numeric, 1) as roi_pct
from afl_model_selections
where settled_at is not null and result in ('win', 'loss') and odds > 1 and round is not null
group by 1, 2, 3 order by 1, 2, 3;
