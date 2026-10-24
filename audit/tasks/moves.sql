-- Late market moves, Ladbrokes fixed odds (live data starts 15 Oct).

-- @moves_by_bucket
with s as (
  select o.race_id, o.horse_id, o.odds, o.captured_at, rc.start_time_utc st
  from live_odds_snapshots o join races rc on rc.id=o.race_id
  where o.odds>1 and rc.start_time_utc is not null and o.captured_at < rc.start_time_utc and o.captured_at > rc.start_time_utc - interval '6 hours'),
e as (select distinct on (race_id,horse_id) race_id,horse_id,odds early from s where captured_at <= st - interval '30 minutes' order by race_id,horse_id,captured_at desc),
l as (select distinct on (race_id,horse_id) race_id,horse_id,odds late, captured_at from s where captured_at >= st - interval '5 minutes' order by race_id,horse_id,captured_at desc),
x as (select e.race_id,e.horse_id,early,late,(late/early) mv, r.finish_position fin, r.sp
  from e join l using(race_id,horse_id) join results r on r.horse_id=e.horse_id join races rc on rc.id=e.race_id
  where r.finish_position>0 and not rc.results_incomplete)
select case when mv<0.7 then 'a big steam <0.7' when mv<0.85 then 'b steam 0.7-0.85' when mv<0.97 then 'c firm 0.85-0.97'
  when mv<=1.03 then 'd steady' when mv<=1.2 then 'e drift 1.03-1.2' when mv<=1.5 then 'f drift 1.2-1.5' else 'g big drift 1.5+' end bucket,
  count(*) n, count(distinct race_id) races, round((100.0*avg((fin=1)::int))::numeric,1) sr,
  round((100*avg(1/late))::numeric,1) late_implied, round((100*avg(1/early))::numeric,1) early_implied,
  round((100.0*avg(case when fin=1 then early-1 else -1 end))::numeric,1) roi_early,
  round((100.0*avg(case when fin=1 then late-1 else -1 end))::numeric,1) roi_late,
  round((100.0*avg(case when fin=1 and sp>1 then sp-1 else -1 end))::numeric,1) roi_sp
from x group by 1 order by 1;

-- @early_vs_late_overall
with s as (
  select o.race_id, o.horse_id, o.odds, o.captured_at, rc.start_time_utc st
  from live_odds_snapshots o join races rc on rc.id=o.race_id
  where o.odds>1 and rc.start_time_utc is not null and o.captured_at < rc.start_time_utc and o.captured_at > rc.start_time_utc - interval '6 hours'),
e as (select distinct on (race_id,horse_id) race_id,horse_id,odds early from s where captured_at <= st - interval '60 minutes' order by race_id,horse_id,captured_at desc),
l as (select distinct on (race_id,horse_id) race_id,horse_id,odds late from s where captured_at >= st - interval '5 minutes' order by race_id,horse_id,captured_at desc)
select count(*) n, count(distinct e.race_id) races,
  round((100.0*avg(case when r.finish_position=1 then early-1 else -1 end))::numeric,1) roi_early_60m,
  round((100.0*avg(case when r.finish_position=1 then late-1 else -1 end))::numeric,1) roi_late,
  round((100.0*avg(case when r.finish_position=1 and r.sp>1 then r.sp-1 else -1 end))::numeric,1) roi_sp,
  round((100.0*avg(case when r.finish_position=1 then greatest(early,late)-1 else -1 end))::numeric,1) roi_best_of_two
from e join l using(race_id,horse_id) join results r on r.horse_id=e.horse_id join races rc on rc.id=e.race_id
where r.finish_position>0 and not rc.results_incomplete;

-- @fav_early_vs_late
with s as (
  select o.race_id, o.horse_id, o.odds, o.captured_at, rc.start_time_utc st
  from live_odds_snapshots o join races rc on rc.id=o.race_id
  where o.odds>1 and rc.start_time_utc is not null and o.captured_at < rc.start_time_utc and o.captured_at > rc.start_time_utc - interval '6 hours'),
l as (select distinct on (race_id,horse_id) race_id,horse_id,odds late from s where captured_at >= st - interval '5 minutes' order by race_id,horse_id,captured_at desc),
k as (select l.*, rank() over (partition by race_id order by late) rk from l)
select rk, count(*) n, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round((100.0*avg(case when r.finish_position=1 then late-1 else -1 end))::numeric,1) roi_late,
  round((100.0*avg(case when r.finish_position=1 and r.sp>1 then r.sp-1 else -1 end))::numeric,1) roi_sp
from k join results r on r.horse_id=k.horse_id join races rc on rc.id=k.race_id
where r.finish_position>0 and not rc.results_incomplete and rk<=5 group by 1 order by 1;

-- @ml_toppick_live_price (forward ML top pick, settled at last pre-race Ladbrokes price)
with s as (
  select o.race_id, o.horse_id, o.odds, o.captured_at, rc.start_time_utc st
  from live_odds_snapshots o join races rc on rc.id=o.race_id
  where o.odds>1 and rc.start_time_utc is not null and o.captured_at < rc.start_time_utc),
l as (select distinct on (race_id,horse_id) race_id,horse_id,odds late from s order by race_id,horse_id,captured_at desc),
m as (select h.race_id, p.horse_id, p.ml_win_probability mp, row_number() over (partition by h.race_id order by p.ml_win_probability desc) rk
  from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id
  where p.ml_win_probability is not null and p.ml_scored_at < rc.start_time_utc and not h.is_scratched)
select (m.mp*l.late > 1) model_says_value, count(*) n, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round(avg(l.late)::numeric,2) avg_price, round((100.0*avg(case when r.finish_position=1 then l.late-1 else -1 end))::numeric,1) roi_live
from m join l on l.horse_id=m.horse_id join results r on r.horse_id=m.horse_id join races rc on rc.id=m.race_id
where m.rk=1 and r.finish_position>0 and not rc.results_incomplete group by 1;
