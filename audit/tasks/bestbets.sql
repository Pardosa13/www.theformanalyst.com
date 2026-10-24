-- @flagged_timing (best bet flagged before/after race start)
select (p.best_bet_flagged_at < rc.start_time_utc) flagged_pre_race, count(*) bets, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round((100.0*avg(case when r.finish_position=1 then r.sp-1 else -1 end))::numeric,1) roi_sp,
  count(r.bsp) n_bsp, round((100.0*avg(case when r.bsp is null then null when r.finish_position=1 then (r.bsp-1)*0.92 else -1 end))::numeric,1) roi_bsp
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join results r on r.horse_id=h.id
where p.best_bet_flagged_at is not null and r.finish_position>0 group by 1;

-- @flagged_by_month
select to_char(m.date,'YYYY-MM') as mon, count(*) bets, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round(avg(r.sp)::numeric,2) avg_sp, round((100.0*avg(case when r.finish_position=1 then r.sp-1 else -1 end))::numeric,1) roi_sp,
  count(r.bsp) n_bsp, round((100.0*avg(case when r.bsp is null then null when r.finish_position=1 then (r.bsp-1)*0.92 else -1 end))::numeric,1) roi_bsp,
  round((100.0*avg(case when r.bsp is null then null when r.finish_position=1 then r.sp-1 else -1 end))::numeric,1) roi_sp_same_rows
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join meetings m on m.id=rc.meeting_id join results r on r.horse_id=h.id
where p.best_bet_flagged_at is not null and r.finish_position>0 group by 1 order by 1;

-- @flagged_winners_sp_vs_bsp
select r.sp, r.bsp, m.meeting_name, rc.race_number, h.horse_name, p.best_bet_flagged_at, rc.start_time_utc
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join meetings m on m.id=rc.meeting_id join results r on r.horse_id=h.id
where p.best_bet_flagged_at is not null and r.finish_position=1 order by r.sp desc limit 25;

-- @sp_vs_bsp_winners_vs_losers (is SP biased by result?)
select (r.finish_position=1) won, count(*), round(avg(r.bsp/r.sp)::numeric,3) avg_bsp_over_sp, round((percentile_cont(0.5) within group (order by r.bsp/r.sp))::numeric,3) med
from results r where r.sp>1 and r.bsp>1 and r.finish_position>0 and r.sp<10 group by 1;

-- @sp_source_check (SP of winners vs Betfair implied)
select case when r.sp<3 then 'a<3' when r.sp<6 then 'b3-6' when r.sp<10 then 'c6-10' else 'd10+' end band, (r.finish_position=1) won, count(*),
  round(avg(r.bsp/r.sp)::numeric,3) ratio
from results r where r.sp>1 and r.bsp>1 and r.finish_position>0 group by 1,2 order by 1,2;
