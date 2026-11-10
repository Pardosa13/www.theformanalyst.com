-- Is the stored starting price (results.sp) sound? Every stats page settles at it.

-- @book_by_month
-- Sum of 1/SP per race (100% = fair; Australian SP books usually 115-125%).
with r as (
  select rc.id, to_char(m.date, 'YYYY-MM') as month, count(*) as runners, count(res.sp) as priced,
         sum(1.0 / nullif(res.sp, 0)) as book, sum((res.finish_position = 1)::int) as winners,
         max(case when res.finish_position = 1 then res.sp end) as winner_sp,
         sum(1.0 / nullif(res.bsp, 0)) as bsp_book
  from races rc join meetings m on m.id = rc.meeting_id
  join horses h on h.race_id = rc.id join results res on res.horse_id = h.id
  where res.finish_position > 0 and not coalesce(rc.results_incomplete, false)
  group by 1, 2)
select month, count(*) as races,
       round(avg((priced = runners)::int) * 100, 1) as pct_races_all_priced,
       round(percentile_cont(0.5) within group (order by book)::numeric * 100, 1) as median_sp_book_pct,
       round(percentile_cont(0.5) within group (order by bsp_book)::numeric * 100, 1) as median_bsp_book_pct,
       sum((winner_sp is null)::int) as races_winner_has_no_sp
from r group by 1 order by 1;

-- @roi_all_runners
select to_char(m.date, 'YYYY-MM') as month, count(*) as runners,
       round(100.0 * avg(case when res.finish_position = 1 then res.sp - 1 else -1 end)::numeric, 1) as roi_sp,
       round(100.0 * avg(case when res.bsp is null then null when res.finish_position = 1 then (res.bsp - 1) * 0.92 else -1 end)::numeric, 1) as roi_bsp,
       round(avg(res.sp / nullif(res.bsp, 0))::numeric, 3) as avg_sp_over_bsp
from results res join horses h on h.id = res.horse_id join races rc on rc.id = h.race_id
join meetings m on m.id = rc.meeting_id
where res.finish_position > 0 and res.sp > 1 and not coalesce(rc.results_incomplete, false)
group by 1 order by 1;

-- @sp_vs_bsp_by_price
-- Winners only: SP against BSP by price band.
select case when res.bsp < 3 then 'a <3' when res.bsp < 6 then 'b 3-6' when res.bsp < 12 then 'c 6-12'
            when res.bsp < 30 then 'd 12-30' else 'e 30+' end as bsp_band,
       count(*) as runners, round(avg(res.sp / res.bsp)::numeric, 3) as avg_sp_over_bsp,
       round(percentile_cont(0.5) within group (order by res.sp / res.bsp)::numeric, 3) as median_sp_over_bsp
from results res join horses h on h.id = res.horse_id join races rc on rc.id = h.race_id
where res.finish_position > 0 and res.sp > 1 and res.bsp > 1 and not coalesce(rc.results_incomplete, false)
group by 1 order by 1;
