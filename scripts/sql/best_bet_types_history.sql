-- Best Bets types over all settled history: $10 win bets at SP.
-- Read-only. Paste into Railway -> Postgres -> Database -> Query.
--
-- Favourite = lowest SP in the race (the live tracker uses the live Ladbrokes
-- price instead). ML counts only scores made before the jump where the start
-- time is known. Proven Edge is not covered (needs notes parsing).
WITH runners AS (
    SELECT
        r.id AS race_id,
        h.id AS horse_id,
        r.race_class,
        r.start_time_utc,
        p.score AS analyzer_score,
        COALESCE(
            p.pfai_score,
            CASE WHEN COALESCE(h.csv_data->>'pfaiScore', h.csv_data->>'PFAI Score', h.csv_data->>'pfai_score') ~ '^-?[0-9]+(\.[0-9]+)?$'
                 THEN COALESCE(h.csv_data->>'pfaiScore', h.csv_data->>'PFAI Score', h.csv_data->>'pfai_score')::float END
        ) AS pfai,
        CASE WHEN p.ml_score IS NOT NULL
              AND (r.start_time_utc IS NULL OR p.ml_scored_at IS NULL OR p.ml_scored_at < r.start_time_utc)
             THEN p.ml_score END AS ml_score,
        res.sp,
        res.finish_position
    FROM races r
    JOIN horses h ON h.race_id = r.id
    JOIN predictions p ON p.horse_id = h.id
    JOIN results res ON res.horse_id = h.id
    WHERE COALESCE(h.is_scratched, FALSE) = FALSE
      AND res.finish_position > 0
      AND res.sp IS NOT NULL AND res.sp > 1
      AND COALESCE(r.results_incomplete, FALSE) = FALSE
),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY analyzer_score DESC NULLS LAST) AS a_rank,
        ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY pfai DESC NULLS LAST)           AS p_rank,
        ROW_NUMBER() OVER (PARTITION BY race_id ORDER BY ml_score DESC NULLS LAST)       AS m_rank,
        MIN(sp)  OVER (PARTITION BY race_id) AS fav_sp,
        COUNT(pfai)     OVER (PARTITION BY race_id) AS n_pfai,
        COUNT(ml_score) OVER (PARTITION BY race_id) AS n_ml,
        LEAD(ml_score) OVER (PARTITION BY race_id ORDER BY ml_score DESC NULLS LAST) AS ml_next
    FROM runners
),
flags AS (
    SELECT *,
        (sp = fav_sp) AS is_fav,
        (race_class ILIKE '%maiden%' OR race_class ILIKE '%mdn%') AS is_maiden,
        (a_rank = 1 AND p_rank = 1 AND m_rank = 1 AND n_pfai > 0 AND n_ml > 0) AS triple,
        (m_rank = 1 AND n_ml > 0) AS ml_top
    FROM ranked
),
signals AS (
    SELECT *,
        (ml_top AND is_fav AND sp BETWEEN 2.50 AND 3.99)             AS sweet_spot,
        (triple AND is_fav)                                          AS full_consensus,
        (ml_top AND is_fav AND ml_score - COALESCE(ml_next, 0) >= 20) AS gap_20,
        (triple AND is_maiden)                                       AS maiden_agreement
    FROM flags
),
bets AS (
    SELECT 'Maiden Agreement (route)'         AS signal, * FROM signals WHERE maiden_agreement
    UNION ALL SELECT 'Full Model + Market Consensus (route)', * FROM signals WHERE full_consensus
    UNION ALL SELECT 'ML Market Sweet Spot (badge)',          * FROM signals WHERE sweet_spot
    UNION ALL SELECT 'ML Agreement + 20 Gap (badge)',         * FROM signals WHERE gap_20
    UNION ALL SELECT 'Elite Consensus (all 3 badges)',        * FROM signals WHERE sweet_spot AND full_consensus AND gap_20
    UNION ALL SELECT 'Baseline: every SP favourite',          * FROM signals WHERE is_fav
    UNION ALL SELECT 'Baseline: triple agreement, any race',  * FROM signals WHERE triple
    UNION ALL SELECT 'Baseline: ML top pick',                 * FROM signals WHERE ml_top
    UNION ALL SELECT 'Baseline: analyzer top pick',           * FROM signals WHERE a_rank = 1
)
SELECT
    signal,
    COUNT(*)                                                         AS bets,
    SUM((finish_position = 1)::int)                                  AS wins,
    ROUND(100.0 * AVG((finish_position = 1)::int), 1)                AS strike_pct,
    ROUND(SUM(CASE WHEN finish_position = 1 THEN 10 * (sp - 1) ELSE -10 END)::numeric, 0) AS profit_10_dollar,
    ROUND(100.0 * AVG(CASE WHEN finish_position = 1 THEN sp - 1 ELSE -1 END)::numeric, 1) AS roi_pct,
    ROUND(AVG(sp)::numeric, 2)                                       AS avg_sp,
    MIN(start_time_utc)::date                                        AS first_race,
    MAX(start_time_utc)::date                                        AS last_race
FROM bets
GROUP BY signal
ORDER BY signal;
