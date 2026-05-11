"""
ml_shadow_routes.py  — Shadow ML scoring routes.

Register in app.py with:
    from ml_shadow_routes import register_ml_shadow_routes
    register_ml_shadow_routes(app, db)

Adds:
    GET  /ml-shadow                              — comparison dashboard
    POST /api/ml-shadow/score/<meeting_id>       — trigger ML scoring for a meeting
    GET  /api/ml-shadow/results/<meeting_id>     — JSON results for a meeting
    GET  /api/ml-shadow/global-stats             — aggregate ROI stats
"""

import logging
from flask import render_template_string, jsonify, request, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import text

log = logging.getLogger(__name__)

ML_SHADOW_CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap');

  :root {
    --bg: #0d0f14;
    --surface: #141720;
    --border: #1e2330;
    --accent: #00e5b0;
    --accent2: #ff6b35;
    --text: #e8eaf0;
    --muted: #5a6070;
    --win: #00e5b0;
    --lose: #ff4560;
    --draw: #ffa800;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    min-height: 100vh;
  }

  .header {
    border-bottom: 1px solid var(--border);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--surface);
  }

  .header h1 {
    font-family: 'Syne', sans-serif;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: var(--accent);
  }

  .header .badge {
    background: rgba(0,229,176,0.1);
    border: 1px solid rgba(0,229,176,0.3);
    color: var(--accent);
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 11px;
    letter-spacing: 1px;
  }

  .back-link {
    margin-left: auto;
    color: var(--muted);
    text-decoration: none;
    font-size: 12px;
  }
  .back-link:hover { color: var(--text); }

  .container { max-width: 1400px; margin: 0 auto; padding: 24px 32px; }

  .stats-bar {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 12px;
    margin-bottom: 28px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }
  .stat-card .label { color: var(--muted); font-size: 10px; letter-spacing: 1px; margin-bottom: 6px; }
  .stat-card .value { font-family: 'Syne', sans-serif; font-size: 24px; font-weight: 800; }
  .stat-card .value.green { color: var(--win); }
  .stat-card .value.red   { color: var(--lose); }
  .stat-card .value.amber { color: var(--draw); }

  .meeting-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 24px;
  }
  .meeting-bar select {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: 6px;
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    flex: 1;
    max-width: 400px;
  }
  .btn {
    background: var(--accent);
    color: #0d0f14;
    border: none;
    padding: 8px 18px;
    border-radius: 6px;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    letter-spacing: 0.5px;
  }
  .btn:hover { opacity: 0.85; }
  .btn.secondary {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
  }
  .btn.secondary:hover { border-color: var(--accent); color: var(--accent); }

  .race-section { margin-bottom: 32px; }
  .race-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }
  .race-number {
    font-family: 'Syne', sans-serif;
    font-size: 18px;
    font-weight: 800;
    color: var(--accent);
  }
  .race-info { color: var(--muted); font-size: 11px; }

  .agree-badge {
    margin-left: auto;
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 11px;
    letter-spacing: 0.5px;
  }
  .agree-badge.agree    { background: rgba(0,229,176,0.12); color: var(--win); border: 1px solid rgba(0,229,176,0.2); }
  .agree-badge.disagree { background: rgba(255,107,53,0.12); color: var(--accent2); border: 1px solid rgba(255,107,53,0.2); }

  .race-table { width: 100%; border-collapse: collapse; }
  .race-table th {
    text-align: left;
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 1px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
  }
  .race-table td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(30,35,48,0.5);
    vertical-align: middle;
  }
  .race-table tr:last-child td { border-bottom: none; }
  .race-table tr:hover td { background: rgba(255,255,255,0.02); }

  .pick-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 6px;
  }
  .pick-dot.analyzer { background: #4e9eff; }
  .pick-dot.ml       { background: var(--accent); }
  .pick-dot.both     { background: var(--draw); }

  .score-bar-wrap { display: flex; align-items: center; gap: 8px; }
  .score-bar {
    height: 4px;
    border-radius: 2px;
    flex-shrink: 0;
  }
  .score-bar.analyzer-bar { background: #4e9eff; }
  .score-bar.ml-bar       { background: var(--accent); }

  .result-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 500;
  }
  .result-badge.win     { background: rgba(0,229,176,0.15); color: var(--win); }
  .result-badge.lose    { background: rgba(255,69,96,0.12); color: var(--lose); }
  .result-badge.pending { color: var(--muted); }

  .legend {
    display: flex;
    gap: 20px;
    margin-bottom: 20px;
    padding: 12px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 11px;
    color: var(--muted);
  }
  .legend-item { display: flex; align-items: center; gap: 6px; }

  .no-data {
    text-align: center;
    padding: 60px 20px;
    color: var(--muted);
  }
  .no-data h3 { font-family: 'Syne', sans-serif; font-size: 20px; color: var(--text); margin-bottom: 8px; }

  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .roi-summary {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 24px;
  }
  .roi-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
  }
  .roi-card.analyzer { border-left: 3px solid #4e9eff; }
  .roi-card.ml       { border-left: 3px solid var(--accent); }
  .roi-card .roi-title { font-size: 11px; color: var(--muted); letter-spacing: 1px; margin-bottom: 8px; }
  .roi-card .roi-stats { display: flex; gap: 24px; }
  .roi-card .roi-stat .num { font-family: 'Syne', sans-serif; font-size: 22px; font-weight: 800; }
  .roi-card .roi-stat .lbl { font-size: 10px; color: var(--muted); }
</style>
"""

ML_SHADOW_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ML Shadow — The Form Analyst</title>
""" + ML_SHADOW_CSS + """
</head>
<body>

<div class="header">
  <h1>ML Shadow</h1>
  <span class="badge">EXPERIMENTAL</span>
  <a href="/history" class="back-link">← back to meetings</a>
</div>

<div class="container">

  <div id="global-stats" class="stats-bar">
    <div class="stat-card">
      <div class="label">RACES TRACKED</div>
      <div class="value" id="g-races">—</div>
    </div>
    <div class="stat-card">
      <div class="label">ANALYZER WINS</div>
      <div class="value" id="g-awins">—</div>
    </div>
    <div class="stat-card">
      <div class="label">ANALYZER ROI</div>
      <div class="value" id="g-aroi">—</div>
    </div>
    <div class="stat-card">
      <div class="label">ML WINS</div>
      <div class="value" id="g-mwins">—</div>
    </div>
    <div class="stat-card">
      <div class="label">ML ROI</div>
      <div class="value" id="g-mroi">—</div>
    </div>
    <div class="stat-card">
      <div class="label">AGREEMENT RATE</div>
      <div class="value" id="g-agree">—</div>
    </div>
  </div>

  <div class="meeting-bar">
    <select id="meeting-select" onchange="loadMeeting(this.value)">
      <option value="">— select a meeting —</option>
      {% for m in meetings %}
      <option value="{{ m.id }}" {% if selected_id == m.id %}selected{% endif %}>
        {{ m.meeting_name }}{% if m.has_ml %} ✓{% endif %}
      </option>
      {% endfor %}
    </select>
    <button class="btn" onclick="scoreMeeting()" id="score-btn">Generate ML Scores</button>
    <button class="btn secondary" onclick="location.reload()">Refresh</button>
  </div>

  <div class="legend">
    <div class="legend-item"><span class="pick-dot analyzer"></span> Analyzer top pick</div>
    <div class="legend-item"><span class="pick-dot ml"></span> ML top pick</div>
    <div class="legend-item"><span class="pick-dot both"></span> Both agree</div>
  </div>

  <div id="meeting-roi" class="roi-summary" style="display:none">
    <div class="roi-card analyzer">
      <div class="roi-title">ANALYZER — THIS MEETING</div>
      <div class="roi-stats">
        <div class="roi-stat"><div class="num" id="m-awins">—</div><div class="lbl">WINS</div></div>
        <div class="roi-stat"><div class="num" id="m-araces">—</div><div class="lbl">RACES</div></div>
        <div class="roi-stat"><div class="num" id="m-aroi">—</div><div class="lbl">ROI</div></div>
      </div>
    </div>
    <div class="roi-card ml">
      <div class="roi-title">ML MODEL — THIS MEETING</div>
      <div class="roi-stats">
        <div class="roi-stat"><div class="num" id="m-mwins">—</div><div class="lbl">WINS</div></div>
        <div class="roi-stat"><div class="num" id="m-mraces">—</div><div class="lbl">RACES</div></div>
        <div class="roi-stat"><div class="num" id="m-mroi">—</div><div class="lbl">ROI</div></div>
      </div>
    </div>
  </div>

  <div id="race-container">
    <div class="no-data">
      <h3>Select a meeting above</h3>
      <p>Meetings marked ✓ already have ML scores generated.</p>
    </div>
  </div>

</div>

<script>
const STAKE = 10;

function getMeetingId() {
  return document.getElementById('meeting-select').value;
}

async function loadGlobalStats() {
  try {
    const r = await fetch('/api/ml-shadow/global-stats');
    const d = await r.json();
    document.getElementById('g-races').textContent  = d.races  ?? '—';
    document.getElementById('g-awins').textContent  = d.a_wins ?? '—';
    document.getElementById('g-mwins').textContent  = d.m_wins ?? '—';

    const aroi = d.a_roi;
    const mroi = d.m_roi;
    const aEl  = document.getElementById('g-aroi');
    const mEl  = document.getElementById('g-mroi');
    aEl.textContent = aroi != null ? aroi.toFixed(1) + '%' : '—';
    aEl.className   = 'value ' + (aroi >= 0 ? 'green' : 'red');
    mEl.textContent = mroi != null ? mroi.toFixed(1) + '%' : '—';
    mEl.className   = 'value ' + (mroi >= 0 ? 'green' : 'red');

    const ag = d.agree_rate;
    document.getElementById('g-agree').textContent = ag != null ? ag.toFixed(1) + '%' : '—';
  } catch(e) { console.error(e); }
}

async function scoreMeeting() {
  const mid = getMeetingId();
  if (!mid) return alert('Please select a meeting first.');
  const btn = document.getElementById('score-btn');
  btn.innerHTML = '<span class="spinner"></span>Scoring...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/ml-shadow/score/' + mid, { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      await loadMeeting(mid);
    } else {
      alert('Error: ' + (d.error || 'Unknown'));
    }
  } catch(e) {
    alert('Request failed: ' + e);
  } finally {
    btn.innerHTML = 'Generate ML Scores';
    btn.disabled = false;
  }
}

async function loadMeeting(mid) {
  if (!mid) return;
  const container = document.getElementById('race-container');
  container.innerHTML = '<div class="no-data"><span class="spinner"></span> Loading...</div>';

  try {
    const r = await fetch('/api/ml-shadow/results/' + mid);
    const d = await r.json();

    if (!d.races || d.races.length === 0) {
      container.innerHTML = '<div class="no-data"><h3>No ML scores yet</h3><p>Click "Generate ML Scores" to score this meeting.</p></div>';
      return;
    }

    let aWins=0, mWins=0, aRaces=0, mRaces=0, aProfit=0, mProfit=0;

    let html = '';
    for (const race of d.races) {
      const topA = race.horses.reduce((a,b) => a.analyzer_score > b.analyzer_score ? a : b, race.horses[0]);
      const topM = race.horses.reduce((a,b) => a.ml_score > b.ml_score ? a : b, race.horses[0]);
      const agree = topA && topM && topA.horse_id === topM.horse_id;

      const hasResults = race.horses.some(h => h.finish_position > 0);
      if (hasResults && topA) {
        aRaces++;
        const aRes = race.horses.find(h => h.horse_id === topA.horse_id);
        if (aRes && aRes.finish_position === 1 && aRes.sp) { aWins++; aProfit += aRes.sp * STAKE - STAKE; }
        else { aProfit -= STAKE; }
      }
      if (hasResults && topM) {
        mRaces++;
        const mRes = race.horses.find(h => h.horse_id === topM.horse_id);
        if (mRes && mRes.finish_position === 1 && mRes.sp) { mWins++; mProfit += mRes.sp * STAKE - STAKE; }
        else { mProfit -= STAKE; }
      }

      html += `
        <div class="race-section">
          <div class="race-header">
            <span class="race-number">R${race.race_number}</span>
            <span class="race-info">${race.distance || ''}m &nbsp;·&nbsp; ${race.track_condition || ''}</span>
            <span class="agree-badge ${agree ? 'agree' : 'disagree'}">
              ${agree ? '✓ AGREE' : '✗ DISAGREE'}
            </span>
          </div>
          <table class="race-table">
            <thead>
              <tr>
                <th>HORSE</th>
                <th>BARRIER</th>
                <th>ANALYZER</th>
                <th>ML SCORE</th>
                <th>RESULT</th>
                <th>SP</th>
              </tr>
            </thead>
            <tbody>
      `;

      const sorted = [...race.horses].sort((a,b) => b.analyzer_score - a.analyzer_score);
      const maxA = Math.max(...sorted.map(h => h.analyzer_score));
      const maxM = Math.max(...sorted.map(h => h.ml_score));

      for (const h of sorted) {
        const isTopA = topA && h.horse_id === topA.horse_id;
        const isTopM = topM && h.horse_id === topM.horse_id;
        const isBoth = isTopA && isTopM;

        let dot = '';
        if (isBoth)       dot = '<span class="pick-dot both"></span>';
        else if (isTopA)  dot = '<span class="pick-dot analyzer"></span>';
        else if (isTopM)  dot = '<span class="pick-dot ml"></span>';
        else              dot = '<span style="display:inline-block;width:14px"></span>';

        const aBarW = maxA > 0 ? Math.round(h.analyzer_score / maxA * 80) : 0;
        const mBarW = maxM > 0 ? Math.round(h.ml_score / maxM * 80) : 0;

        let resultHtml = '<span class="result-badge pending">pending</span>';
        if (h.finish_position > 0) {
          if (h.finish_position === 1) {
            resultHtml = `<span class="result-badge win">1st ✓</span>`;
          } else {
            resultHtml = `<span class="result-badge lose">${h.finish_position}th</span>`;
          }
        }

        html += `
          <tr>
            <td>${dot}${h.horse_name}</td>
            <td style="color:var(--muted)">${h.barrier || '—'}</td>
            <td>
              <div class="score-bar-wrap">
                <span style="min-width:36px">${h.analyzer_score?.toFixed(1) ?? '—'}</span>
                <div class="score-bar analyzer-bar" style="width:${aBarW}px"></div>
              </div>
            </td>
            <td>
              <div class="score-bar-wrap">
                <span style="min-width:36px">${h.ml_score?.toFixed(1) ?? '—'}</span>
                <div class="score-bar ml-bar" style="width:${mBarW}px"></div>
              </div>
            </td>
            <td>${resultHtml}</td>
            <td style="color:var(--muted)">${h.sp ? '$' + h.sp : '—'}</td>
          </tr>
        `;
      }

      html += '</tbody></table></div>';
    }

    container.innerHTML = html;

    if (aRaces > 0 || mRaces > 0) {
      document.getElementById('meeting-roi').style.display = 'grid';
      document.getElementById('m-awins').textContent  = aWins;
      document.getElementById('m-araces').textContent = aRaces;
      const aRoiVal = aRaces > 0 ? ((aProfit / (aRaces * STAKE)) * 100).toFixed(1) + '%' : '—';
      document.getElementById('m-aroi').textContent   = aRoiVal;

      document.getElementById('m-mwins').textContent  = mWins;
      document.getElementById('m-mraces').textContent = mRaces;
      const mRoiVal = mRaces > 0 ? ((mProfit / (mRaces * STAKE)) * 100).toFixed(1) + '%' : '—';
      document.getElementById('m-mroi').textContent   = mRoiVal;
    }

  } catch(e) {
    container.innerHTML = '<div class="no-data"><h3>Error loading data</h3><p>' + e + '</p></div>';
  }
}

loadGlobalStats();
const initMid = document.getElementById('meeting-select').value;
if (initMid) loadMeeting(initMid);
</script>

</body>
</html>
"""


def register_ml_shadow_routes(app, db):
    """Call this from app.py to add ML shadow routes."""

    with app.app_context():
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS ml_score FLOAT"
                ))
                conn.commit()
            log.info("✓ ml_score column ready on predictions table")
        except Exception as e:
            log.warning(f"ml_score migration: {e}")

    @app.route('/ml-shadow')
    @login_required
    def ml_shadow():
        if not current_user.is_admin:
            return redirect(url_for('history'))

        from models import Meeting

        meetings_raw = Meeting.query.order_by(Meeting.uploaded_at.desc()).limit(100).all()

        rows = db.session.execute(text("""
            SELECT DISTINCT rc.meeting_id
            FROM predictions p
            JOIN horses h ON h.id = p.horse_id
            JOIN races rc ON rc.id = h.race_id
            WHERE p.ml_score IS NOT NULL
        """)).fetchall()
        scored_ids = {r[0] for r in rows}

        meetings = []
        for m in meetings_raw:
            meetings.append({
                'id': m.id,
                'meeting_name': m.meeting_name,
                'has_ml': m.id in scored_ids,
            })

        selected_id = request.args.get('meeting_id', type=int)
        return render_template_string(ML_SHADOW_TEMPLATE,
                                      meetings=meetings,
                                      selected_id=selected_id)

    @app.route('/api/ml-shadow/score/<int:meeting_id>', methods=['POST'])
    @login_required
    def ml_shadow_score(meeting_id):
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            from ml_predict import predict_meeting
            from models import Prediction

            all_scores, by_race = predict_meeting(meeting_id, db.session)

            if not all_scores:
                return jsonify({'success': False, 'error': 'No scores generated — model may not be loaded or meeting has no active horses.'})

            updated = 0
            for horse_id, ml_score in all_scores.items():
                pred = Prediction.query.filter_by(horse_id=horse_id).first()
                if pred:
                    pred.ml_score = ml_score
                    updated += 1

            db.session.commit()
            log.info(f"ML shadow: scored {updated} horses for meeting {meeting_id}")
            return jsonify({'success': True, 'scored': updated})

        except Exception as e:
            db.session.rollback()
            log.error(f"ML shadow score failed: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/ml-shadow/results/<int:meeting_id>')
    @login_required
    def ml_shadow_results(meeting_id):
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            from models import Meeting, Race

            meeting = Meeting.query.get_or_404(meeting_id)
            races   = Race.query.filter_by(meeting_id=meeting_id).order_by(Race.race_number).all()

            races_out = []
            for race in races:
                horses_out = []
                for horse in race.horses:
                    if horse.is_scratched:
                        continue
                    pred   = horse.prediction
                    result = horse.result

                    horses_out.append({
                        'horse_id':        horse.id,
                        'horse_name':      horse.horse_name,
                        'barrier':         horse.barrier,
                        'jockey':          horse.jockey,
                        'analyzer_score':  round(pred.score, 2) if pred else None,
                        'ml_score':        round(pred.ml_score, 2) if (pred and pred.ml_score is not None) else None,
                        'finish_position': result.finish_position if result else 0,
                        'sp':              result.sp if result else None,
                    })

                if horses_out:
                    races_out.append({
                        'race_number':     race.race_number,
                        'distance':        race.distance,
                        'track_condition': race.track_condition,
                        'race_class':      race.race_class,
                        'horses':          horses_out,
                    })

            return jsonify({'races': races_out, 'meeting_name': meeting.meeting_name})

        except Exception as e:
            log.error(f"ML shadow results failed: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/ml-shadow/global-stats')
    @login_required
    def ml_shadow_global_stats():
        if not current_user.is_admin:
            return jsonify({'error': 'Admin only'}), 403

        try:
            rows = db.session.execute(text("""
                SELECT
                    rc.id AS race_id,
                    p.score AS analyzer_score,
                    p.ml_score,
                    h.id AS horse_id,
                    r.finish_position,
                    r.sp
                FROM predictions p
                JOIN horses h ON h.id = p.horse_id
                JOIN races rc ON rc.id = h.race_id
                LEFT JOIN results r ON r.horse_id = h.id
                WHERE p.ml_score IS NOT NULL
                  AND h.is_scratched = FALSE
                  AND r.finish_position > 0
            """)).fetchall()

            from collections import defaultdict
            races = defaultdict(list)
            for row in rows:
                races[row.race_id].append(row)

            a_wins = m_wins = races_with_result = 0
            a_profit = m_profit = 0.0
            agree = 0
            STAKE = 10.0

            for race_id, horses in races.items():
                if not horses:
                    continue
                top_a = max(horses, key=lambda x: x.analyzer_score or 0)
                top_m = max(horses, key=lambda x: x.ml_score or 0)
                races_with_result += 1

                a_won = top_a.finish_position == 1
                m_won = top_m.finish_position == 1

                a_profit += (top_a.sp * STAKE - STAKE) if (a_won and top_a.sp) else -STAKE
                m_profit += (top_m.sp * STAKE - STAKE) if (m_won and top_m.sp) else -STAKE

                if a_won: a_wins += 1
                if m_won: m_wins += 1
                if top_a.horse_id == top_m.horse_id: agree += 1

            n = races_with_result
            return jsonify({
                'races':      n,
                'a_wins':     a_wins,
                'a_roi':      round(a_profit / (n * STAKE) * 100, 1) if n else None,
                'm_wins':     m_wins,
                'm_roi':      round(m_profit / (n * STAKE) * 100, 1) if n else None,
                'agree_rate': round(agree / n * 100, 1) if n else None,
            })

        except Exception as e:
            log.error(f"Global stats failed: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    log.info("✓ ML shadow routes registered at /ml-shadow")
