"""Check how PuntingForm rating blobs map to races."""
import json
from collections import Counter

from audit.run_audit import emit


def _j(v):
    while isinstance(v, str):
        v = json.loads(v)
    return v


def run(ctx):
    cols, rows = ctx.rows("""select rc.id, rc.meeting_id, rc.race_number, rc.ratings_json, rc.speed_maps_json,
        (select string_agg(coalesce(h.csv_data->>'race id',''),',') from (select csv_data from horses where race_id=rc.id and not is_scratched limit 3) h) csv_race_ids
        from races rc where rc.ratings_json is not null order by rc.id desc limit 40""")
    for rid, mid, rno, rj, sj, csvids in rows:
        d = _j(rj) or {}
        items = d.get("payLoad") or []
        order = []
        for it in items:
            if not order or order[-1] != it.get("raceId"):
                order.append(it.get("raceId"))
        keys = sorted(items[0].keys()) if items else []
        s = _j(sj) or {}
        sm = [(r.get("raceId"), r.get("raceNo"), len(r.get("items") or [])) for r in (s.get("payLoad") or [])] if isinstance(s, dict) else []
        emit("pfblob", f"race={rid} meeting={mid} no={rno} n_items={len(items)} raceId_order={order[:14]} speedmap(raceId,raceNo,n)={sm[:3]} csv_race_ids={csvids}")
    emit("pfblob.keys", ",".join(keys))
    # how often is the first raceId of the blob the same across races of a meeting
    cols, rows = ctx.rows("""select meeting_id, race_number, ratings_json from races where ratings_json is not null and meeting_id in
        (select meeting_id from races where ratings_json is not null group by 1 order by 1 desc limit 60)""")
    first = {}
    for mid, rno, rj in rows:
        items = (_j(rj) or {}).get("payLoad") or []
        first.setdefault(mid, []).append((rno, items[0].get("raceId") if items else None,
                                          sorted({it.get('raceId') for it in items})))
    same = 0
    for mid, lst in first.items():
        ids = {f for _, f, _ in lst}
        same += len(ids) == 1
        lst.sort()
        emit("pfblob.meeting", f"meeting={mid} races={len(lst)} distinct_first_raceIds={len(ids)} sample={[(r, f) for r, f, _ in lst[:4]]} all_ids_r1={lst[0][2][:12]}")
    emit("pfblob", f"meetings where every race maps to the SAME first raceId: {same}/{len(first)}")
