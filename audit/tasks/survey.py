"""Every table: size, row count, and per-column fill rate, distinct values,
range and most common values (from Postgres's own statistics, plus exact
min/max for dates and numbers)."""
from audit.run_audit import emit

SKIP_PREFIX = ("budget_", "debt_", "chat_", "users")


def run(ctx):
    ctx.q("survey.db", "select version(), current_database(), pg_size_pretty(pg_database_size(current_database())) as size, now()")
    _, tables = ctx.q("survey.tables", """
        select c.relname, c.reltuples::bigint as est_rows,
               pg_size_pretty(pg_total_relation_size(c.oid)) as size,
               s.last_analyze, s.last_autoanalyze
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        left join pg_stat_user_tables s on s.relid=c.oid
        where n.nspname='public' and c.relkind='r'
        order by pg_total_relation_size(c.oid) desc""")
    for (tname, est, *_rest) in tables:
        if tname.startswith(SKIP_PREFIX):
            continue
        sec = f"survey.t.{tname}"
        try:
            _, cnt = ctx.rows(f'select count(*) from "{tname}"')
            emit(sec, f"#count {cnt[0][0]}")
        except Exception as e:
            ctx.conn.rollback()
            emit(sec, f"#count ERROR {e}")
            continue
        ctx.q(sec + ".cols", """
            select a.attname as col, format_type(a.atttypid, a.atttypmod) as type,
                   round((1-coalesce(s.null_frac,0))::numeric*100,1) as fill_pct,
                   s.n_distinct,
                   left(coalesce(s.most_common_vals::text,''),160) as top_vals,
                   left(coalesce(s.most_common_freqs::text,''),80) as top_freqs
            from pg_attribute a
            join pg_class c on c.oid=a.attrelid
            left join pg_stats s on s.tablename=c.relname and s.attname=a.attname and s.schemaname='public'
            where c.relname=%s and a.attnum>0 and not a.attisdropped
            order by a.attnum""", (tname,))
        # exact min/max for dates, timestamps and numbers
        _, cols = ctx.rows("""
            select column_name, data_type from information_schema.columns
            where table_schema='public' and table_name=%s
              and data_type in ('date','timestamp without time zone','timestamp with time zone',
                                'integer','bigint','double precision','numeric','real','smallint')""", (tname,))
        if cols:
            parts = ", ".join(f'min("{c}")::text, max("{c}")::text' for c, _ in cols)
            try:
                _, mm = ctx.rows(f'select {parts} from "{tname}"')
                for i, (c, _) in enumerate(cols):
                    emit(sec + ".range", f"{c} | {mm[0][2*i]} | {mm[0][2*i+1]}")
            except Exception as e:
                ctx.conn.rollback()
                emit(sec + ".range", f"#ERROR {e}")
