-- Were any meetings loaded AFTER they ran? If so the saved form could hold the
-- race's own result ("last start" = today's race), and training would be
-- learning from the answer.

-- @upload_timing
-- Meetings by month: how many were saved a day or more after the race date.
select to_char(m.date, 'YYYY-MM') as month, count(*) as meetings,
       sum((m.uploaded_at::date > m.date)::int) as saved_after_race_day,
       round(avg(m.uploaded_at::date - m.date)::numeric, 1) as avg_days_after
from meetings m
where m.date is not null and m.uploaded_at is not null
group by 1 order by 1;

-- @last10_equals_result
-- Share of runners whose newest form figure equals where they finished in THIS
-- race. By chance that is roughly 10-13%. A much higher share for late-saved
-- meetings means the form file already held the result.
select (m.uploaded_at::date > m.date) as saved_after_race_day, count(*) as runners,
       round(100.0 * avg((right(rtrim(lower(h.csv_data ->> 'horse last10'), 'x'), 1)
             = case when r.finish_position >= 10 then '0' else r.finish_position::text end)::int), 1)
         as pct_newest_form_figure_equals_this_result
from horses h
join results r on r.horse_id = h.id
join races rc on rc.id = h.race_id
join meetings m on m.id = rc.meeting_id
where r.finish_position > 0 and m.date is not null and m.uploaded_at is not null
  and coalesce(h.csv_data ->> 'horse last10', '') <> ''
group by 1 order by 1;

-- @last10_equals_result_by_month
select to_char(m.date, 'YYYY-MM') as month, count(*) as runners,
       round(100.0 * avg((right(rtrim(lower(h.csv_data ->> 'horse last10'), 'x'), 1)
             = case when r.finish_position >= 10 then '0' else r.finish_position::text end)::int), 1)
         as pct_newest_form_figure_equals_this_result
from horses h
join results r on r.horse_id = h.id
join races rc on rc.id = h.race_id
join meetings m on m.id = rc.meeting_id
where r.finish_position > 0 and m.date is not null
  and coalesce(h.csv_data ->> 'horse last10', '') <> ''
group by 1 order by 1;

-- @last_start_on_or_after_race_day
-- Runners whose saved "last start" date is the race day itself or later.
with f as (
  select m.date as race_day, substring(h.csv_data ->> 'form meeting date' from '^\d{1,2}/\d{1,2}/\d{2,4}') as last_start
  from horses h
  join races rc on rc.id = h.race_id
  join meetings m on m.id = rc.meeting_id
  where m.date is not null)
select to_char(race_day, 'YYYY-MM') as month, count(*) as runners, count(last_start) as with_last_start_date,
       sum((to_date(last_start, case when length(split_part(last_start, '/', 3)) = 4 then 'DD/MM/YYYY' else 'DD/MM/YY' end)
            >= race_day)::int) as last_start_not_before_race
from f
group by 1 order by 1;
