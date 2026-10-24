-- Core racing data checks. Every block is read-only.

-- @monthly_coverage
select to_char(m.date,'YYYY-MM') as month,
  count(distinct m.id) meetings, count(distinct rc.id) races, count(h.id) runners,
  count(*) filter (where h.is_scratched) scratched,
  count(distinct rc.id) filter (where exists(select 1 from results r2 join horses h2 on h2.id=r2.horse_id where h2.race_id=rc.id)) races_w_results,
  count(distinct rc.id) filter (where rc.results_incomplete) races_incomplete,
  count(r.id) results, count(r.sp) with_sp, count(r.bsp) with_bsp,
  count(p.id) preds, count(p.ml_win_probability) ml_prob,
  count(*) filter (where p.ml_scored_at < rc.start_time_utc) ml_forward,
  count(rc.start_time_utc) with_start_time
from meetings m join races rc on rc.meeting_id=m.id join horses h on h.race_id=rc.id
left join results r on r.horse_id=h.id left join predictions p on p.horse_id=h.id
group by 1 order by 1;

-- @meetings_no_date_or_track
select count(*) filter (where date is null) no_date, count(*) filter (where track is null or track='') no_track,
  count(*) filter (where auto_imported) auto, count(*) filter (where not coalesce(auto_imported,false)) manual,
  count(*) filter (where provisional) provisional, count(*) total from meetings;

-- @dup_meetings_same_track_date
select date, lower(track) track, count(*) n, string_agg(id::text, ',') ids
from meetings group by 1,2 having count(*)>1 order by n desc, date desc limit 50;

-- @dup_horse_in_race
select count(*) dup_groups, sum(n-1) extra_rows from (
  select race_id, lower(horse_name), count(*) n from horses group by 1,2 having count(*)>1) x;

-- @finish_position_dist
select finish_position, count(*), count(sp) with_sp, round((avg(sp)::numeric)::numeric,2) avg_sp
from results group by 1 order by 1;

-- @winners_per_race (complete races only)
select winners, count(*) races from (
  select rc.id, count(*) filter (where r.finish_position=1) winners
  from races rc join horses h on h.race_id=rc.id join results r on r.horse_id=h.id
  where not rc.results_incomplete group by rc.id) x group by 1 order by 1;

-- @runners_without_result_in_complete_races
select count(*) runners, count(distinct rc.id) races
from races rc join horses h on h.race_id=rc.id left join results r on r.horse_id=h.id
where not rc.results_incomplete and not h.is_scratched and r.id is null
  and exists (select 1 from horses h2 join results r2 on r2.horse_id=h2.id where h2.race_id=rc.id);

-- @sp_sanity
select count(*) filter (where sp is null and finish_position>0) ran_no_sp,
  count(*) filter (where sp<=1) sp_le_1, count(*) filter (where sp>500) sp_gt_500,
  count(*) filter (where bsp is not null and bsp<1.01) bsp_bad,
  percentile_cont(0.5) within group (order by sp) median_sp
from results;

-- @sp_overround_by_race (sum of 1/SP per complete race)
select width_bucket(book,0.8,1.6,8) bucket, round((min(book)::numeric)::numeric,3) lo, round((max(book)::numeric)::numeric,3) hi, count(*) races
from (select rc.id, sum(1.0/r.sp) book from races rc join horses h on h.race_id=rc.id join results r on r.horse_id=h.id
      where not rc.results_incomplete and r.finish_position>0 and r.sp>1 group by rc.id having count(*)>=4) x
group by 1 order by 1;

-- @bsp_overround_by_race
select width_bucket(book,0.8,1.3,10) bucket, round((min(book)::numeric)::numeric,3) lo, round((max(book)::numeric)::numeric,3) hi, count(*) races
from (select rc.id, sum(1.0/r.bsp) book, count(*) n, count(r.bsp) nb from races rc join horses h on h.race_id=rc.id join results r on r.horse_id=h.id
      where not rc.results_incomplete and r.finish_position>0 group by rc.id having count(*)=count(r.bsp) and count(*)>=4) x
group by 1 order by 1;

-- @market_baseline_by_sp_band (flat 1u win at SP and at BSP less 8% comm on profit)
select case when r.sp<2 then 'a <2' when r.sp<3 then 'b 2-3' when r.sp<4 then 'c 3-4' when r.sp<6 then 'd 4-6'
  when r.sp<10 then 'e 6-10' when r.sp<20 then 'f 10-20' when r.sp<50 then 'g 20-50' else 'h 50+' end band,
  count(*) bets, sum((r.finish_position=1)::int) wins,
  round((100.0*avg((r.finish_position=1)::int))::numeric,2) sr,
  round((100.0*avg(case when r.finish_position=1 then r.sp-1 else -1 end))::numeric,2) roi_sp,
  count(r.bsp) n_bsp,
  round((100.0*avg(case when r.bsp is null then null when r.finish_position=1 then (r.bsp-1)*0.92 else -1 end))::numeric,2) roi_bsp
from results r join horses h on h.id=r.horse_id join races rc on rc.id=h.race_id
where r.finish_position>0 and r.sp>1 and not rc.results_incomplete group by 1 order by 1;

-- @bsp_vs_sp_ratio_by_band
select case when r.sp<3 then 'a <3' when r.sp<6 then 'b 3-6' when r.sp<10 then 'c 6-10' when r.sp<20 then 'd 10-20' else 'e 20+' end band,
  count(*) n, round((avg(r.bsp/r.sp)::numeric)::numeric,3) avg_ratio, round(((percentile_cont(0.5) within group (order by r.bsp/r.sp))::numeric)::numeric,3) med_ratio
from results r where r.sp>1 and r.bsp>1 and r.finish_position>0 group by 1 order by 1;

-- @fav_by_rank_roi (market rank by SP within race)
with x as (select rc.id race_id, r.sp, r.bsp, r.finish_position, rank() over (partition by rc.id order by r.sp) rk
  from races rc join horses h on h.race_id=rc.id join results r on r.horse_id=h.id
  where not rc.results_incomplete and r.finish_position>0 and r.sp>1)
select rk, count(*) bets, round((100.0*avg((finish_position=1)::int))::numeric,2) sr,
  round((100.0*avg(case when finish_position=1 then sp-1 else -1 end))::numeric,2) roi_sp,
  round((100.0*avg(case when bsp is null then null when finish_position=1 then (bsp-1)*0.92 else -1 end))::numeric,2) roi_bsp
from x where rk<=8 group by 1 order by 1;

-- @analyzer_toppick_by_month (top `score` per race)
with x as (select m.date, rc.id race_id, r.sp, r.bsp, r.finish_position,
   row_number() over (partition by rc.id order by p.score desc, h.id) rk
  from meetings m join races rc on rc.meeting_id=m.id join horses h on h.race_id=rc.id
  join results r on r.horse_id=h.id join predictions p on p.horse_id=h.id
  where not rc.results_incomplete and r.finish_position>0 and r.sp>1)
select to_char(date,'YYYY-MM') as mon, count(*) bets, round((100.0*avg((finish_position=1)::int))::numeric,1) sr,
  round((avg(sp)::numeric)::numeric,2) avg_sp,
  round((100.0*avg(case when finish_position=1 then sp-1 else -1 end))::numeric,1) roi_sp,
  round((100.0*avg(case when bsp is null then null when finish_position=1 then (bsp-1)*0.92 else -1 end))::numeric,1) roi_bsp
from x where rk=1 group by 1 order by 1;

-- @ml_toppick_forward_by_month (top ml_win_probability per race, scored before start)
with x as (select m.date, rc.id race_id, r.sp, r.bsp, r.finish_position, p.ml_win_probability mp,
   row_number() over (partition by rc.id order by p.ml_win_probability desc nulls last, h.id) rk,
   bool_and(p.ml_scored_at < rc.start_time_utc) over (partition by rc.id) fwd
  from meetings m join races rc on rc.meeting_id=m.id join horses h on h.race_id=rc.id
  join results r on r.horse_id=h.id join predictions p on p.horse_id=h.id
  where not rc.results_incomplete and r.finish_position>0 and r.sp>1 and p.ml_win_probability is not null)
select to_char(date,'YYYY-MM') as mon, fwd, count(*) bets, round((100.0*avg((finish_position=1)::int))::numeric,1) sr,
  round((avg(sp)::numeric)::numeric,2) avg_sp, round((100*avg(mp)::numeric)::numeric,1) avg_ml_pct,
  round((100.0*avg(case when finish_position=1 then sp-1 else -1 end))::numeric,1) roi_sp,
  round((100.0*avg(case when bsp is null then null when finish_position=1 then (bsp-1)*0.92 else -1 end))::numeric,1) roi_bsp
from x where rk=1 group by 1,2 order by 1,2;

-- @ml_calibration_forward (bins of ml prob vs actual win rate, forward only)
select width_bucket(p.ml_win_probability,0,0.6,12) bin, count(*) n,
  round((100*avg(p.ml_win_probability)::numeric)::numeric,1) pred_pct, round((100.0*avg((r.finish_position=1)::int))::numeric,1) actual_pct,
  round((100*avg(1.0/r.sp)::numeric)::numeric,1) sp_implied_pct
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join results r on r.horse_id=h.id
where p.ml_win_probability is not null and p.ml_scored_at < rc.start_time_utc and r.finish_position>0 and r.sp>1 and not rc.results_incomplete
group by 1 order by 1;

-- @value_edge_snapshots (captured pre-race)
select case when value_edge_pct<5 then 'a <5' when value_edge_pct<10 then 'b 5-10' when value_edge_pct<20 then 'c 10-20'
   when value_edge_pct<40 then 'd 20-40' else 'e 40+' end edge, count(*) bets, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round((avg(p.value_edge_price)::numeric)::numeric,2) avg_price,
  round((100.0*avg(case when r.finish_position=1 then p.value_edge_price-1 else -1 end))::numeric,1) roi_at_capture,
  round((100.0*avg(case when r.finish_position=1 then r.sp-1 else -1 end))::numeric,1) roi_sp,
  round((100.0*avg(case when r.bsp is null then null when r.finish_position=1 then (r.bsp-1)*0.92 else -1 end))::numeric,1) roi_bsp
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join results r on r.horse_id=h.id
where p.value_edge_pct is not null and r.finish_position>0 and not rc.results_incomplete group by 1 order by 1;

-- @kelly_stakes
select count(*) bets, round((sum(p.kelly_stake_pct)::numeric)::numeric,3) total_stake,
  round((sum(p.kelly_stake_pct*case when r.finish_position=1 then r.sp-1 else -1 end)::numeric)::numeric,3) profit_sp,
  round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join results r on r.horse_id=h.id
where p.kelly_stake_pct>0 and r.finish_position>0 and r.sp>1 and not rc.results_incomplete;

-- @best_bets_by_route
select coalesce(p.best_bet_routes,'(flag only)') routes, count(*) bets, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round((avg(r.sp)::numeric)::numeric,2) avg_sp,
  round((100.0*avg(case when r.finish_position=1 then r.sp-1 else -1 end))::numeric,1) roi_sp,
  round((100.0*avg(case when r.bsp is null then null when r.finish_position=1 then (r.bsp-1)*0.92 else -1 end))::numeric,1) roi_bsp,
  min(m.date) first, max(m.date) last
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id join meetings m on m.id=rc.meeting_id
join results r on r.horse_id=h.id
where (p.best_bet_flagged_at is not null or p.best_bet_qualified_at is not null) and r.finish_position>0 and r.sp>1
group by 1 order by bets desc;

-- @ladbrokes_signals
select p.ladbrokes_signal_mask mask, count(*) bets, round((100.0*avg((r.finish_position=1)::int))::numeric,1) sr,
  round((avg(p.ladbrokes_signal_price)::numeric)::numeric,2) avg_price,
  round((100.0*avg(case when r.finish_position=1 then p.ladbrokes_signal_price-1 else -1 end))::numeric,1) roi_at_signal,
  round((100.0*avg(case when r.finish_position=1 then r.sp-1 else -1 end))::numeric,1) roi_sp
from predictions p join horses h on h.id=p.horse_id join results r on r.horse_id=h.id
where p.ladbrokes_signal_mask>0 and r.finish_position>0 and r.sp>1 group by 1 order by 1;

-- @leakage_pred_after_start (analyzer predictions written after race start)
select to_char(rc.start_time_utc,'YYYY-MM') as mon, count(*) preds,
  count(*) filter (where p.calculated_at > rc.start_time_utc) calc_after_start,
  count(*) filter (where p.ml_scored_at > rc.start_time_utc) ml_after_start,
  count(*) filter (where rc.scored_at > rc.start_time_utc) race_scored_after_start
from predictions p join horses h on h.id=p.horse_id join races rc on rc.id=h.race_id
where rc.start_time_utc is not null group by 1 order by 1;

-- @live_odds_volume
select to_char(captured_at,'YYYY-MM-DD') as dy, count(*) snaps, count(distinct race_id) races, count(distinct horse_id) horses
from live_odds_snapshots where captured_at > now() - interval '45 days' group by 1 order by 1;

-- @betfair_match
select match_method, count(*) n, count(horse_id) matched, min(meeting_date), max(meeting_date) from betfair_prices group by 1 order by 2 desc;

-- @scratched_breakdown
select to_char(m.date,'YYYY-MM') as mon, count(*) runners,
  count(*) filter (where h.is_scratched) h_scr,
  count(*) filter (where p.notes='Scratched') p_scr_note,
  count(*) filter (where p.score=0) p_score0,
  count(*) filter (where r.finish_position=0) r_scr,
  count(*) filter (where p.notes='Scratched' and r.finish_position>0) note_scr_but_ran,
  count(*) filter (where p.notes='Scratched' and r.finish_position=1) note_scr_but_won
from meetings m join races rc on rc.meeting_id=m.id join horses h on h.race_id=rc.id
left join predictions p on p.horse_id=h.id left join results r on r.horse_id=h.id group by 1 order by 1;

-- @races_per_meeting_runners_per_race
select to_char(m.date,'YYYY-MM') as mon, round((avg(nr)::numeric)::numeric,1) avg_runners, max(nr) max_runners, count(*) races
from meetings m join (select race_id, count(*) nr from horses group by 1) x on true join races rc on rc.id=x.race_id and rc.meeting_id=m.id
group by 1 order by 1;

-- @tracks_top
select m.track, count(distinct m.id) meetings, count(distinct rc.id) races from meetings m join races rc on rc.meeting_id=m.id
group by 1 order by 2 desc limit 80;

-- @dup_groups_detail
with g as (select h.race_id, lower(trim(h.horse_name)) nm, count(*) n,
   count(*) filter (where not h.is_scratched) n_live,
   count(r.id) filter (where r.finish_position>0) n_ran,
   count(r.id) filter (where r.finish_position=0) n_r_scr,
   count(p.id) filter (where p.score<>0) n_scored,
   max(h.id) max_id, min(h.id) min_id,
   max(h.id) filter (where r.finish_position>0) ran_id
  from horses h left join results r on r.horse_id=h.id left join predictions p on p.horse_id=h.id
  group by 1,2 having count(*)>1)
select n, n_live, n_ran, n_scored, (ran_id=max_id) ran_is_newest, count(*) groups
from g group by 1,2,3,4,5 order by groups desc limit 40;

-- @dup_double_counted_in_training (same horse in same race with 2+ live rows that have a run result)
select count(*) groups, sum(k-1) extra from (
  select h.race_id, lower(trim(h.horse_name)), count(*) k
  from horses h join results r on r.horse_id=h.id join races rc on rc.id=h.race_id
  where not h.is_scratched and r.finish_position>0 and not rc.results_incomplete
  group by 1,2 having count(*)>1) x;

-- @scratch_flag_vs_result (horses flagged scratched but with a run result, and vice versa)
select h.is_scratched, (r.finish_position>0) ran, count(*) from horses h join results r on r.horse_id=h.id group by 1,2 order by 1,2;

-- @real_scratch_rate (one row per race+name; scratched only if no row ran)
with g as (select h.race_id, lower(trim(h.horse_name)) nm, bool_or(r.finish_position>0) ran, bool_and(h.is_scratched) all_scr
  from horses h left join results r on r.horse_id=h.id join races rc on rc.id=h.race_id where not rc.results_incomplete group by 1,2)
select count(*) unique_runners, count(*) filter (where ran) ran, count(*) filter (where not coalesce(ran,false)) not_ran,
  count(*) filter (where all_scr) all_rows_scr from g;

-- @races_field_size_dist (unique runners that ran, complete races)
select fs, count(*) races from (select h.race_id, count(distinct lower(h.horse_name)) fs from horses h join results r on r.horse_id=h.id
  join races rc on rc.id=h.race_id where r.finish_position>0 and not rc.results_incomplete group by 1) x group by 1 order by 1;

-- @zero_winner_races_sample
select rc.id, m.date, m.meeting_name, rc.race_number, count(*) runners, count(r.id) filter (where r.finish_position>0) ran,
  string_agg(distinct r.finish_position::text, ',') positions
from races rc join meetings m on m.id=rc.meeting_id join horses h on h.race_id=rc.id left join results r on r.horse_id=h.id
where not rc.results_incomplete group by 1,2,3,4 having count(*) filter (where r.finish_position=1)=0 and count(r.id)>0
order by m.date desc limit 25;
