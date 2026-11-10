-- Where does the seeded UFC history end, so new ratings never count a fight twice?

-- @fights_by_source
select coalesce(f.card_source, '?') as card_source, count(*) as fights,
       min(e.date) as first_event, max(e.date) as last_event,
       sum((f.winner_name is not null)::int) as with_winner
from mma_fights f join mma_events e on e.id = f.event_id
where e.is_completed
group by 1 order by 3;

-- @fights_by_month_recent
select to_char(e.date, 'YYYY-MM') as month, coalesce(f.card_source, '?') as card_source, count(*) as fights
from mma_fights f join mma_events e on e.id = f.event_id
where e.is_completed and e.date >= date '2025-09-01'
group by 1, 2 order by 1, 2;

-- @fighter_created
select date_trunc('month', created_at)::date as created_month, count(*) as fighters,
       sum((glicko_rating = 1500)::int) as default_rating
from mma_fighters group by 1 order by 1;
