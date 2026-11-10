-- Old vs new AFL predicted margin against what actually happened.

-- @compare
with new_view as (
    WITH team_match_stats AS (
        SELECT
            ps.match_date,
            CASE LOWER(TRIM(ps.player_team))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(ps.player_team)) END AS team_key,
            (
                -- Scoring
                6.00 * SUM(COALESCE(ps.goals, 0)) +
                1.00 * SUM(COALESCE(ps.behinds, 0)) +
                0.75 * SUM(COALESCE(ps.score_involvements, 0)) +
                -- Attack
                0.90 * SUM(COALESCE(ps.inside_fifties, 0)) +
                1.50 * SUM(COALESCE(ps.marks_inside_fifty, 0)) +
                -- Clearances
                1.15 * SUM(COALESCE(ps.clearances, 0)) +
                1.50 * SUM(COALESCE(ps.centre_clearances, 0)) +
                1.20 * SUM(COALESCE(ps.stoppage_clearances, 0)) +
                -- Contested possession
                1.30 * SUM(COALESCE(ps.contested_possessions, 0)) +
                0.25 * SUM(COALESCE(ps.uncontested_possessions, 0)) +
                1.00 * SUM(COALESCE(ps.contested_marks, 0)) +
                -- Ball use / territory
                0.18 * SUM(COALESCE(ps.disposals, 0)) +
                0.40 * SUM(COALESCE(ps.effective_disposals, 0)) +
                0.10 * SUM(COALESCE(ps.disposal_efficiency_percentage, 0)) +
                0.04 * SUM(COALESCE(ps.metres_gained, 0)) +
                0.20 * SUM(COALESCE(ps.hitouts, 0)) +
                -- Defence
                0.45 * SUM(COALESCE(ps.intercepts, 0)) +
                0.40 * SUM(COALESCE(ps.rebounds, 0)) +
                0.50 * SUM(COALESCE(ps.tackles, 0)) +
                0.70 * SUM(COALESCE(ps.tackles_inside_fifty, 0)) +
                0.25 * SUM(COALESCE(ps.one_percenters, 0)) +
                0.35 * SUM(COALESCE(ps.free_kicks_for, 0)) -
                -- Negative
                0.75 * SUM(COALESCE(ps.turnovers, 0)) -
                0.65 * SUM(COALESCE(ps.clangers, 0)) -
                0.35 * SUM(COALESCE(ps.free_kicks_against, 0))
            ) AS team_rating
        FROM afl_player_stats ps
        WHERE COALESCE(ps.player_team, '') <> ''
        GROUP BY ps.match_date, CASE LOWER(TRIM(ps.player_team))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(ps.player_team)) END
    ),
    team_rolling AS (
        SELECT match_date, team_key,
               AVG(team_rating) OVER (PARTITION BY team_key ORDER BY match_date
                                      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS rolling_avg_5
        FROM team_match_stats
    )
    SELECT
        g.id AS match_id,
        g.hteam AS home_team,
        g.ateam AS away_team,
        home.rolling_avg_5 - away.rolling_avg_5 AS predicted_margin
    FROM afl_games g
    LEFT JOIN LATERAL (
        SELECT tr.rolling_avg_5 FROM team_rolling tr
        WHERE tr.team_key = CASE LOWER(TRIM(g.hteam))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(g.hteam)) END AND tr.match_date < g.date::DATE
        ORDER BY tr.match_date DESC LIMIT 1
    ) home ON TRUE
    LEFT JOIN LATERAL (
        SELECT tr.rolling_avg_5 FROM team_rolling tr
        WHERE tr.team_key = CASE LOWER(TRIM(g.ateam))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(g.ateam)) END AND tr.match_date < g.date::DATE
        ORDER BY tr.match_date DESC LIMIT 1
    ) away ON TRUE

)
select extract(year from g.date)::int as season, count(*) as games,
       round(corr(old.predicted_margin, g.hscore - g.ascore)::numeric, 3) as old_corr_with_result,
       round(corr(n.predicted_margin, g.hscore - g.ascore)::numeric, 3) as new_corr_with_result,
       round(100.0 * avg(((old.predicted_margin > 0) = (g.hscore > g.ascore))::int), 1) as old_tips_right_pct,
       round(100.0 * avg(((n.predicted_margin > 0) = (g.hscore > g.ascore))::int), 1) as new_tips_right_pct,
       sum((n.predicted_margin is null)::int) as new_missing
from afl_games g
left join afl_match_predictions old on old.match_id = g.id
left join new_view n on n.match_id = g.id
where g.complete = 100 and g.hscore is not null and g.hscore <> g.ascore
group by 1 order by 1;

-- @upcoming
with new_view as (
    WITH team_match_stats AS (
        SELECT
            ps.match_date,
            CASE LOWER(TRIM(ps.player_team))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(ps.player_team)) END AS team_key,
            (
                -- Scoring
                6.00 * SUM(COALESCE(ps.goals, 0)) +
                1.00 * SUM(COALESCE(ps.behinds, 0)) +
                0.75 * SUM(COALESCE(ps.score_involvements, 0)) +
                -- Attack
                0.90 * SUM(COALESCE(ps.inside_fifties, 0)) +
                1.50 * SUM(COALESCE(ps.marks_inside_fifty, 0)) +
                -- Clearances
                1.15 * SUM(COALESCE(ps.clearances, 0)) +
                1.50 * SUM(COALESCE(ps.centre_clearances, 0)) +
                1.20 * SUM(COALESCE(ps.stoppage_clearances, 0)) +
                -- Contested possession
                1.30 * SUM(COALESCE(ps.contested_possessions, 0)) +
                0.25 * SUM(COALESCE(ps.uncontested_possessions, 0)) +
                1.00 * SUM(COALESCE(ps.contested_marks, 0)) +
                -- Ball use / territory
                0.18 * SUM(COALESCE(ps.disposals, 0)) +
                0.40 * SUM(COALESCE(ps.effective_disposals, 0)) +
                0.10 * SUM(COALESCE(ps.disposal_efficiency_percentage, 0)) +
                0.04 * SUM(COALESCE(ps.metres_gained, 0)) +
                0.20 * SUM(COALESCE(ps.hitouts, 0)) +
                -- Defence
                0.45 * SUM(COALESCE(ps.intercepts, 0)) +
                0.40 * SUM(COALESCE(ps.rebounds, 0)) +
                0.50 * SUM(COALESCE(ps.tackles, 0)) +
                0.70 * SUM(COALESCE(ps.tackles_inside_fifty, 0)) +
                0.25 * SUM(COALESCE(ps.one_percenters, 0)) +
                0.35 * SUM(COALESCE(ps.free_kicks_for, 0)) -
                -- Negative
                0.75 * SUM(COALESCE(ps.turnovers, 0)) -
                0.65 * SUM(COALESCE(ps.clangers, 0)) -
                0.35 * SUM(COALESCE(ps.free_kicks_against, 0))
            ) AS team_rating
        FROM afl_player_stats ps
        WHERE COALESCE(ps.player_team, '') <> ''
        GROUP BY ps.match_date, CASE LOWER(TRIM(ps.player_team))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(ps.player_team)) END
    ),
    team_rolling AS (
        SELECT match_date, team_key,
               AVG(team_rating) OVER (PARTITION BY team_key ORDER BY match_date
                                      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS rolling_avg_5
        FROM team_match_stats
    )
    SELECT
        g.id AS match_id,
        g.hteam AS home_team,
        g.ateam AS away_team,
        home.rolling_avg_5 - away.rolling_avg_5 AS predicted_margin
    FROM afl_games g
    LEFT JOIN LATERAL (
        SELECT tr.rolling_avg_5 FROM team_rolling tr
        WHERE tr.team_key = CASE LOWER(TRIM(g.hteam))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(g.hteam)) END AND tr.match_date < g.date::DATE
        ORDER BY tr.match_date DESC LIMIT 1
    ) home ON TRUE
    LEFT JOIN LATERAL (
        SELECT tr.rolling_avg_5 FROM team_rolling tr
        WHERE tr.team_key = CASE LOWER(TRIM(g.ateam))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(g.ateam)) END AND tr.match_date < g.date::DATE
        ORDER BY tr.match_date DESC LIMIT 1
    ) away ON TRUE

)
select count(*) as upcoming_games, sum((n.predicted_margin is not null)::int) as with_new_margin,
       sum((old.predicted_margin is not null)::int) as with_old_margin
from afl_games g
left join afl_match_predictions old on old.match_id = g.id
left join new_view n on n.match_id = g.id
where coalesce(g.complete, 0) < 100 and g.date >= now() - interval '400 days';

-- @h2h_vs_market
-- Games with a pre-start head-to-head price: the bookies' favourite vs the model's tip,
-- and what backing the model's side whenever it disagreed with the favourite returned.
with new_view as (
    WITH team_match_stats AS (
        SELECT
            ps.match_date,
            CASE LOWER(TRIM(ps.player_team))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(ps.player_team)) END AS team_key,
            (
                -- Scoring
                6.00 * SUM(COALESCE(ps.goals, 0)) +
                1.00 * SUM(COALESCE(ps.behinds, 0)) +
                0.75 * SUM(COALESCE(ps.score_involvements, 0)) +
                -- Attack
                0.90 * SUM(COALESCE(ps.inside_fifties, 0)) +
                1.50 * SUM(COALESCE(ps.marks_inside_fifty, 0)) +
                -- Clearances
                1.15 * SUM(COALESCE(ps.clearances, 0)) +
                1.50 * SUM(COALESCE(ps.centre_clearances, 0)) +
                1.20 * SUM(COALESCE(ps.stoppage_clearances, 0)) +
                -- Contested possession
                1.30 * SUM(COALESCE(ps.contested_possessions, 0)) +
                0.25 * SUM(COALESCE(ps.uncontested_possessions, 0)) +
                1.00 * SUM(COALESCE(ps.contested_marks, 0)) +
                -- Ball use / territory
                0.18 * SUM(COALESCE(ps.disposals, 0)) +
                0.40 * SUM(COALESCE(ps.effective_disposals, 0)) +
                0.10 * SUM(COALESCE(ps.disposal_efficiency_percentage, 0)) +
                0.04 * SUM(COALESCE(ps.metres_gained, 0)) +
                0.20 * SUM(COALESCE(ps.hitouts, 0)) +
                -- Defence
                0.45 * SUM(COALESCE(ps.intercepts, 0)) +
                0.40 * SUM(COALESCE(ps.rebounds, 0)) +
                0.50 * SUM(COALESCE(ps.tackles, 0)) +
                0.70 * SUM(COALESCE(ps.tackles_inside_fifty, 0)) +
                0.25 * SUM(COALESCE(ps.one_percenters, 0)) +
                0.35 * SUM(COALESCE(ps.free_kicks_for, 0)) -
                -- Negative
                0.75 * SUM(COALESCE(ps.turnovers, 0)) -
                0.65 * SUM(COALESCE(ps.clangers, 0)) -
                0.35 * SUM(COALESCE(ps.free_kicks_against, 0))
            ) AS team_rating
        FROM afl_player_stats ps
        WHERE COALESCE(ps.player_team, '') <> ''
        GROUP BY ps.match_date, CASE LOWER(TRIM(ps.player_team))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(ps.player_team)) END
    ),
    team_rolling AS (
        SELECT match_date, team_key,
               AVG(team_rating) OVER (PARTITION BY team_key ORDER BY match_date
                                      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS rolling_avg_5
        FROM team_match_stats
    )
    SELECT
        g.id AS match_id,
        g.hteam AS home_team,
        g.ateam AS away_team,
        home.rolling_avg_5 - away.rolling_avg_5 AS predicted_margin
    FROM afl_games g
    LEFT JOIN LATERAL (
        SELECT tr.rolling_avg_5 FROM team_rolling tr
        WHERE tr.team_key = CASE LOWER(TRIM(g.hteam))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(g.hteam)) END AND tr.match_date < g.date::DATE
        ORDER BY tr.match_date DESC LIMIT 1
    ) home ON TRUE
    LEFT JOIN LATERAL (
        SELECT tr.rolling_avg_5 FROM team_rolling tr
        WHERE tr.team_key = CASE LOWER(TRIM(g.ateam))
            WHEN 'west coast eagles' THEN 'west coast'
            WHEN 'greater western sydney' THEN 'gws giants'
            WHEN 'greater western sydney giants' THEN 'gws giants'
            WHEN 'gws' THEN 'gws giants'
            WHEN 'footscray' THEN 'western bulldogs'
            WHEN 'brisbane' THEN 'brisbane lions'
            WHEN 'adelaide crows' THEN 'adelaide'
            WHEN 'carlton blues' THEN 'carlton'
            WHEN 'collingwood magpies' THEN 'collingwood'
            WHEN 'essendon bombers' THEN 'essendon'
            WHEN 'fremantle dockers' THEN 'fremantle'
            WHEN 'geelong cats' THEN 'geelong'
            WHEN 'gold coast suns' THEN 'gold coast'
            WHEN 'hawthorn hawks' THEN 'hawthorn'
            WHEN 'melbourne demons' THEN 'melbourne'
            WHEN 'north melbourne kangaroos' THEN 'north melbourne'
            WHEN 'port adelaide power' THEN 'port adelaide'
            WHEN 'richmond tigers' THEN 'richmond'
            WHEN 'st kilda saints' THEN 'st kilda'
            WHEN 'sydney swans' THEN 'sydney'
            WHEN 'western bulldogs bulldogs' THEN 'western bulldogs'
            WHEN 'brisbane lions lions' THEN 'brisbane lions'
            ELSE LOWER(TRIM(g.ateam)) END AND tr.match_date < g.date::DATE
        ORDER BY tr.match_date DESC LIMIT 1
    ) away ON TRUE


), px as (
  select distinct on (event_id, selection_name) event_id, lower(trim(home_team)) as h, lower(trim(away_team)) as a,
         commence_time::date as d, lower(trim(selection_name)) as sel, odds
  from afl_match_markets
  where market = 'h2h' and odds > 1 and fetched_at < commence_time
  order by event_id, selection_name, fetched_at desc
), pairs as (
  select ph.d, ph.h, ph.a, ph.odds as h_odds, pa.odds as a_odds
  from px ph join px pa on pa.event_id = ph.event_id and pa.sel = ph.a and ph.sel = ph.h
), joined as (
  select g.id, g.hscore - g.ascore as result, n.predicted_margin as m, p.h_odds, p.a_odds,
         (1 / p.h_odds) / (1 / p.h_odds + 1 / p.a_odds) as mkt_home
  from afl_games g join new_view n on n.match_id = g.id
  join pairs p on p.d between g.date::date - 1 and g.date::date + 1
   and (p.h like '%' || split_part(lower(g.hteam), ' ', 1) || '%')
   and (p.a like '%' || split_part(lower(g.ateam), ' ', 1) || '%')
  where g.complete = 100 and g.hscore <> g.ascore and n.predicted_margin is not null)
select count(*) as games,
       round(100.0 * avg(((mkt_home > 0.5) = (result > 0))::int), 1) as favourite_tips_right_pct,
       round(100.0 * avg(((m > 0) = (result > 0))::int), 1) as model_tips_right_pct,
       sum(((m > 0) <> (mkt_home > 0.5))::int) as model_disagrees,
       round(100.0 * avg(case when (m > 0) <> (mkt_home > 0.5)
             then case when (m > 0) = (result > 0) then (case when m > 0 then h_odds else a_odds end) - 1 else -1 end end)::numeric, 1)
         as roi_backing_model_when_it_disagrees
from joined;
