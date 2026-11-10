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

-- @fight_cards_new_order
-- The 10 events the Fight Cards tab will show with the date-based order.
SELECT id, name, date, (is_completed OR date < CURRENT_DATE - 1) AS shown_as_completed
FROM mma_events
ORDER BY
    CASE WHEN date >= CURRENT_DATE - 1 THEN 0 ELSE 1 END,
    CASE WHEN date >= CURRENT_DATE - 1 THEN date END ASC,
    date DESC
LIMIT 10;

-- @missing_results_backlog
-- What the sync will re-read on its next run (newest first, up to 30).
SELECT e.id, e.name, e.date, e.is_completed, (e.espn_url IS NOT NULL AND e.espn_url <> '') AS has_link
FROM mma_events e
WHERE e.date < CURRENT_DATE
  AND (COALESCE(e.is_completed, FALSE) = FALSE
       OR EXISTS (SELECT 1 FROM mma_fights f WHERE f.event_id = e.id AND COALESCE(f.is_active, TRUE) AND f.winner_name IS NULL))
ORDER BY e.date DESC;
