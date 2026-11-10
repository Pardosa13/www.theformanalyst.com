-- UFC events: which past events were never marked finished, and do their fights have winners?

-- @events
select e.id, e.name, e.date, e.is_completed,
       count(f.id) filter (where coalesce(f.is_active, true)) as active_fights,
       count(f.id) filter (where f.winner_name is not null) as with_winner,
       count(p.id) as with_prediction
from mma_events e
left join mma_fights f on f.event_id = e.id
left join mma_predictions p on p.fight_id = f.id
where e.date >= date '2026-04-01'
group by e.id, e.name, e.date, e.is_completed
order by e.date;
