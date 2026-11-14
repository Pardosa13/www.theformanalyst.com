// Sections shared by the Data and ML Data pages:
//   - Review Angles (win and place record of each angle)
//   - Jockeys & Trainers In Form
(function () {
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  const money = (v) => (v < 0 ? '-$' : '+$') + Math.abs(v).toFixed(2);
  const pct = (v) => (v === null || v === undefined ? '—' : (v > 0 ? '+' : '') + v.toFixed(1) + '%');
  const cls = (v) => (v === null || v === undefined ? '' : (v >= 0 ? 'text-success' : 'text-danger'));

  // ── Review Angles ────────────────────────────────────────────────────
  function angleRows(angles) {
    let html = '';
    for (const a of angles) {
      html += `<tr><td><strong>${esc(a.name)}</strong>${a.audit ? `<br><span class="text-muted" style="font-size:11px">Earlier check: ${esc(a.audit)}</span>` : ''}</td>`;
      html += `<td class="text-center">${a.bets}</td><td class="text-center">${a.wins}</td><td class="text-center">${a.win_rate.toFixed(1)}%</td>`;
      html += `<td class="text-center ${a.bets ? cls(a.win_profit) : ''}">${a.bets ? money(a.win_profit) : '—'}</td>`;
      html += `<td class="text-center ${cls(a.win_roi)}"><strong>${pct(a.win_roi)}</strong></td>`;
      html += `<td class="text-center ${cls(a.betfair_roi)}"><strong>${pct(a.betfair_roi)}</strong></td>`;
      html += `<td class="text-center ${cls(a.betfair_roi_without_top3)}">${pct(a.betfair_roi_without_top3)}</td>`;
      html += `<td class="text-center">${a.placed}</td><td class="text-center">${a.place_rate.toFixed(1)}%</td>`;
      html += `<td class="text-center ${a.place_bets ? cls(a.place_profit) : ''}">${a.place_bets ? money(a.place_profit) : '—'}</td>`;
      html += `<td class="text-center ${cls(a.place_roi)}"><strong>${pct(a.place_roi)}</strong></td></tr>`;
    }
    return html;
  }

  function angleTable(title, blurb, angles) {
    if (!angles.length) return '';
    let html = `<h5 style="margin:20px 0 4px">${title}</h5><p class="text-muted mb-2" style="font-size:12px">${blurb}</p>`;
    html += '<div class="table-responsive"><table class="table table-hover table-sm"><thead class="table-light"><tr>';
    html += '<th>Angle</th><th class="text-center">Bets</th><th class="text-center">Wins</th><th class="text-center">Win %</th>';
    html += '<th class="text-center">Win Profit (SP)</th><th class="text-center">Win ROI (SP)</th>';
    html += '<th class="text-center">Win ROI (Betfair)</th><th class="text-center">Betfair, no top 3</th>';
    html += '<th class="text-center">Placed</th><th class="text-center">Place %</th><th class="text-center">Place Profit</th><th class="text-center">Place ROI</th>';
    html += '</tr></thead><tbody>' + angleRows(angles) + '</tbody></table></div>';
    return html;
  }

  function renderReviewAngles(container, data) {
    let waiting = 0, estimated = 0;
    for (const a of data.angles) { waiting += a.place_waiting; estimated += a.place_estimated; }
    let html = '<div class="card-body">';
    html += `<p class="text-muted mb-1" style="font-size:12px">$10 on every runner that fit each angle, across ${data.races} races. One $10 win bet and one $10 place bet. These are angles to watch, not bets.</p>`;
    html += angleTable('🎯 Shorter prices', 'Steadier. These sit closest to break even.',
      data.angles.filter((a) => a.group !== 'long'));
    html += angleTable('🎲 Long shots', 'Mostly $10 and longer. Few winners, big swings. Any profit has only shown at the Betfair price, and a few big winners carry it.',
      data.angles.filter((a) => a.group === 'long'));
    html += '<div class="mt-3 p-3 bg-light rounded"><h6>📖 How to read this:</h6><ul class="mb-0 small">';
    html += '<li><strong>Win ROI (SP)</strong> — paid at the starting price. The starting price is poor on long shots, so most angles look bad here.</li>';
    html += '<li><strong>Win ROI (Betfair)</strong> — paid at the Betfair starting price, less 8% commission. Only races with a Betfair price count.</li>';
    html += '<li><strong>Betfair, no top 3</strong> — the same, with the three biggest winners taken out. If this is far lower, a few lucky results carry the angle.</li>';
    html += '<li><strong>Place</strong> — paid at the Betfair place price, less 8% commission. Top 3 in fields of 8+, top 2 in fields of 5-7.</li>';
    html += '<li><strong>Earlier check</strong> — what a past check found at Betfair prices (Feb-Jun / Jul-Oct). Picked with hindsight, so trust the live columns more.</li>';
    html += `<li><strong>Price bands</strong> ($3-$6 and so on) use the last Ladbrokes win price before the jump. ${data.band_bets_at_sp} of ${data.band_bets} had no saved Ladbrokes price, so the starting price was used.</li>`;
    html += '<li><strong>Favourite</strong> — the runner with the shortest starting price.</li>';
    if (waiting || estimated) {
      html += `<li>${estimated} place bets use the Ladbrokes place price for now. ${waiting} placed with no price yet and are left out of place profit.</li>`;
    }
    html += '<li>Uses the filters at the top of the page.</li>';
    html += '</ul></div></div>';
    container.innerHTML = html;
  }

  // ── Jockeys & Trainers In Form ───────────────────────────────────────
  const KINDS = [['jockeys', '🏇 Jockeys'], ['trainers', '👔 Trainers'], ['combos', '🤝 Jockey + Trainer']];
  const COLUMNS = {
    name: ['Name', false], runs: ['Runs', true], wins: ['Wins', true], strike_rate: ['Strike %', true],
    place_rate: ['Top 3 %', true], roi: ['ROI', true], profit: ['Profit', true],
    pf_sr: ['PF last 100', true], pf_ae: ['PF A/E', true], upcoming: ['Coming up', true],
  };

  function initPeopleForm(container) {
    const state = { days: 30, kind: 'jockeys', sort: 'strike_rate', desc: true, upcomingOnly: false, search: '', showAll: false, data: null };

    function load() {
      container.innerHTML = '<div class="card-body text-center"><div class="spinner-border" role="status"></div><p class="mt-2">Loading jockeys and trainers...</p></div>';
      fetch(`/api/data/people-form?days=${state.days}`, { credentials: 'same-origin' })
        .then((r) => r.json())
        .then((data) => { if (data.error) throw new Error(data.error); state.data = data; draw(); })
        .catch((error) => { container.innerHTML = '<div class="card-body text-danger">Error loading data. Please try again.</div>'; console.error('Error:', error); });
    }

    function rowsFor() {
      let rows = (state.data[state.kind] || []).slice();
      if (state.upcomingOnly) rows = rows.filter((r) => r.upcoming > 0);
      const q = state.search.trim().toLowerCase();
      if (q) rows = rows.filter((r) => r.name.toLowerCase().includes(q) || (r.tracks || '').toLowerCase().includes(q));
      const key = state.sort;
      rows.sort((a, b) => {
        const x = a[key], y = b[key];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        const d = typeof x === 'string' ? x.localeCompare(y) : x - y;
        return state.desc ? -d : d;
      });
      return rows;
    }

    function draw() {
      const d = state.data;
      const cols = state.kind === 'combos'
        ? ['name', 'runs', 'wins', 'strike_rate', 'place_rate', 'roi', 'profit', 'upcoming']
        : ['name', 'runs', 'wins', 'strike_rate', 'place_rate', 'roi', 'profit', 'pf_sr', 'pf_ae', 'upcoming'];
      const rows = rowsFor();
      const shown = state.showAll ? rows : rows.slice(0, 40);
      const btn = (on) => `btn btn-sm ${on ? 'btn-primary' : 'btn-outline-secondary'}`;

      let html = '<div class="card-body">';
      html += `<p class="text-muted mb-2" style="font-size:12px">Results from ${esc(d.from)} to ${esc(d.to)}. Every runner, not just top picks. $10 win bets at the starting price.</p>`;
      html += '<div class="d-flex flex-wrap gap-2 mb-2">';
      for (const days of d.windows) html += `<button type="button" class="${btn(days === state.days)}" data-days="${days}">${days === 365 ? '12 months' : days + ' days'}</button>`;
      html += '</div><div class="d-flex flex-wrap gap-2 mb-2">';
      for (const [kind, label] of KINDS) html += `<button type="button" class="${btn(kind === state.kind)}" data-kind="${kind}">${label}</button>`;
      html += '</div><div class="d-flex flex-wrap gap-3 align-items-center mb-3">';
      html += `<input type="text" class="form-control form-control-sm" style="max-width:220px" placeholder="Search name or track" value="${esc(state.search)}" data-search>`;
      html += `<label style="font-size:13px"><input type="checkbox" data-upcoming ${state.upcomingOnly ? 'checked' : ''}> Only those with runners coming up</label>`;
      html += '</div>';

      if (!rows.length) {
        html += '<p class="text-muted">Nobody fits. Try a longer period or clear the search.</p>';
      } else {
        html += '<div class="table-responsive"><table class="table table-sm table-hover"><thead class="table-dark"><tr>';
        for (const c of cols) {
          const arrow = state.sort === c ? (state.desc ? ' ▼' : ' ▲') : '';
          html += `<th class="${c === 'name' ? '' : 'text-center'}" style="cursor:pointer;white-space:nowrap" data-sort="${c}">${COLUMNS[c][0]}${arrow}</th>`;
        }
        html += '</tr></thead><tbody>';
        for (const r of shown) {
          html += `<tr><td><strong>${esc(r.name)}</strong></td><td class="text-center">${r.runs}</td><td class="text-center">${r.wins}</td>`;
          html += `<td class="text-center"><strong>${r.strike_rate.toFixed(1)}%</strong></td><td class="text-center">${r.place_rate.toFixed(1)}%</td>`;
          html += `<td class="text-center ${cls(r.roi)}"><strong>${pct(r.roi)}</strong></td><td class="text-center ${cls(r.profit)}">${money(r.profit)}</td>`;
          if (state.kind !== 'combos') {
            html += `<td class="text-center">${r.pf_l100 ? `${esc(r.pf_l100)} (${r.pf_sr.toFixed(0)}%)` : '—'}</td>`;
            html += `<td class="text-center ${r.pf_ae === null ? '' : cls(r.pf_ae - 1)}">${r.pf_ae === null ? '—' : r.pf_ae.toFixed(2)}</td>`;
          }
          html += `<td class="text-center" style="font-size:12px">${r.upcoming ? `<strong>${r.upcoming}</strong> ${esc(r.tracks)}` : '—'}</td></tr>`;
        }
        html += '</tbody></table></div>';
        if (rows.length > shown.length) html += `<button type="button" class="btn btn-sm btn-outline-secondary" data-show-all>Show all ${rows.length}</button>`;
      }

      html += '<div class="mt-3 p-3 bg-light rounded"><h6>📖 How to read this:</h6><ul class="mb-0 small">';
      html += `<li>Listed with at least ${d.min_runs.jockeys} rides (jockeys), ${d.min_runs.trainers} runners (trainers) or ${d.min_runs.combos} runs together (pairs) in the period.</li>`;
      html += '<li><strong>Strike %</strong> — how often they won. <strong>Top 3 %</strong> — how often they ran 1st, 2nd or 3rd.</li>';
      html += '<li><strong>ROI</strong> — return from $10 on every one of their runners at the starting price. Tap any heading to sort by it.</li>';
      html += '<li><strong>PF last 100</strong> — PuntingForm\'s own count of wins in their last 100 runs.</li>';
      html += '<li><strong>PF A/E</strong> — PuntingForm wins against what the market expected. Above 1.00 means winning more than their prices say.</li>';
      html += '<li><strong>Coming up</strong> — runners they have in meetings loaded but not yet run, and where.</li>';
      html += '<li>A small number of runs can look great by luck. Past checks found hot jockeys and trainers did not keep it up, so use this as a starting point, not a tip.</li>';
      html += '</ul></div></div>';
      container.innerHTML = html;

      container.querySelectorAll('[data-days]').forEach((b) => b.addEventListener('click', () => { state.days = Number(b.dataset.days); state.showAll = false; load(); }));
      container.querySelectorAll('[data-kind]').forEach((b) => b.addEventListener('click', () => {
        state.kind = b.dataset.kind; state.showAll = false;
        if (state.kind === 'combos' && (state.sort === 'pf_sr' || state.sort === 'pf_ae')) state.sort = 'strike_rate';
        draw();
      }));
      container.querySelectorAll('[data-sort]').forEach((th) => th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (state.sort === key) state.desc = !state.desc; else { state.sort = key; state.desc = COLUMNS[key][1]; }
        draw();
      }));
      const upcoming = container.querySelector('[data-upcoming]');
      if (upcoming) upcoming.addEventListener('change', () => { state.upcomingOnly = upcoming.checked; draw(); });
      const search = container.querySelector('[data-search]');
      if (search) search.addEventListener('change', () => { state.search = search.value; draw(); });
      const showAll = container.querySelector('[data-show-all]');
      if (showAll) showAll.addEventListener('click', () => { state.showAll = true; draw(); });
    }

    load();
  }

  window.DataShared = { renderReviewAngles, initPeopleForm };
})();
