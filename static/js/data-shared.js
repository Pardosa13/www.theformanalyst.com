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
    const edge = 'border-left:2px solid rgba(128,128,128,0.35)';
    let html = '';
    for (const a of angles) {
      html += `<tr><td style="min-width:190px"><strong>${esc(a.name)}</strong></td>`;
      // All bets, at the starting price
      html += `<td class="text-center" style="${edge}">${a.bets}</td><td class="text-center">${a.wins}</td><td class="text-center">${a.win_rate.toFixed(1)}%</td>`;
      html += `<td class="text-center ${a.bets ? cls(a.win_profit) : ''}">${a.bets ? money(a.win_profit) : '—'}</td>`;
      html += `<td class="text-center ${cls(a.win_roi)}"><strong>${pct(a.win_roi)}</strong></td>`;
      // Only the bets that have a Betfair price
      html += `<td class="text-center" style="${edge}">${a.betfair_bets}</td>`;
      html += `<td class="text-center ${cls(a.sp_roi_on_betfair_bets)}">${pct(a.sp_roi_on_betfair_bets)}</td>`;
      html += `<td class="text-center ${cls(a.betfair_roi)}"><strong>${pct(a.betfair_roi)}</strong></td>`;
      html += `<td class="text-center ${cls(a.betfair_roi_without_top3)}">${pct(a.betfair_roi_without_top3)}</td>`;
      // Place bets
      html += `<td class="text-center" style="${edge}">${a.placed}</td><td class="text-center">${a.place_rate.toFixed(1)}%</td>`;
      html += `<td class="text-center ${a.place_bets ? cls(a.place_profit) : ''}">${a.place_bets ? money(a.place_profit) : '—'}</td>`;
      html += `<td class="text-center ${cls(a.place_roi)}"><strong>${pct(a.place_roi)}</strong></td></tr>`;
    }
    return html;
  }

  function angleTable(title, blurb, angles) {
    if (!angles.length) return '';
    const edge = 'border-left:2px solid rgba(128,128,128,0.35)';
    const th = (label, first) => `<th class="text-center" style="white-space:nowrap${first ? ';' + edge : ''}">${label}</th>`;
    let html = `<h5 style="margin:20px 0 4px">${title}</h5><p class="text-muted mb-2" style="font-size:12px">${blurb}</p>`;
    html += '<div class="table-responsive"><table class="table table-hover table-sm"><thead class="table-light">';
    html += '<tr><th></th>';
    html += `<th colspan="5" class="text-center" style="${edge}">Win bets: every bet, at the starting price</th>`;
    html += `<th colspan="4" class="text-center" style="${edge}">Win bets: only the bets that have a Betfair price</th>`;
    html += `<th colspan="4" class="text-center" style="${edge}">Place bets</th></tr>`;
    html += '<tr><th>Angle</th>';
    html += th('Bets', true) + th('Winners') + th('Winners %') + th('Profit') + th('Return');
    html += th('Bets', true) + th('Return at starting price') + th('Return at Betfair') + th('Betfair, 3 biggest winners removed');
    html += th('Placed', true) + th('Placed %') + th('Profit') + th('Return');
    html += '</tr></thead><tbody>' + angleRows(angles) + '</tbody></table></div>';
    return html;
  }

  function renderReviewAngles(container, data) {
    let waiting = 0, estimated = 0, betfair = 0, bets = 0;
    for (const a of data.angles) { waiting += a.place_waiting; estimated += a.place_estimated; betfair += a.betfair_bets; bets += a.bets; }
    let html = '<div class="card-body">';
    html += `<p class="text-muted mb-1" style="font-size:12px">$10 on every runner that fit each angle, across ${data.races} races. One $10 win bet and one $10 place bet. These are angles to watch, not bets.</p>`;
    html += angleTable('🎯 Shorter prices', 'Steadier. These sit closest to break even.',
      data.angles.filter((a) => a.group !== 'long'));
    html += angleTable('🎲 Long shots', 'Mostly $10 and longer. Few winners, big swings. Any profit has only shown at the Betfair price, and a few big winners carry it.',
      data.angles.filter((a) => a.group === 'long'));

    html += '<div class="mt-3 p-3 bg-light rounded"><h6>📖 How to read this:</h6><ul class="mb-0 small">';
    html += '<li><strong>Return</strong> — profit or loss for every $100 bet. +5% means $5 profit per $100. -5% means $5 lost.</li>';
    html += '<li><strong>The table has three parts.</strong> Read each part on its own. They do not all count the same bets.</li>';
    html += '<li><strong>Part 1: every bet, at the starting price.</strong> All bets that fit the angle, paid at the official starting price.</li>';
    html += `<li><strong>Part 2: only the bets that have a Betfair price.</strong> Betfair prices load once a month, so the newest races are not in this part yet${bets ? ` (${betfair} of ${bets} bets so far)` : ''}. It shows the same bets paid two ways: at the starting price and at the Betfair price (less 8% commission). That is the fair way to compare the two prices.</li>`;
    html += '<li><strong>Why Part 1 and Part 2 can disagree.</strong> Part 1 includes the newest races and Part 2 does not. If the newest races went badly, Part 1 looks worse.</li>';
    html += '<li><strong>Why Betfair pays more.</strong> On your saved results, winners paid about 20% more at Betfair than at the starting price, and two to three times more on big long shots.</li>';
    html += '<li><strong>Betfair, 3 biggest winners removed</strong> — the Betfair return again, without the three biggest winners. If this is far lower, a few lucky results carry the angle. This is the number to trust most.</li>';
    html += '<li><strong>Part 3: place bets.</strong> Paid at the Betfair place price, less 8% commission. A place is top 3 in races of 8 or more runners, top 2 in races of 5 to 7.</li>';
    html += `<li><strong>Price ranges</strong> ($3-$6 and so on) use the last Ladbrokes win price before the race. ${data.band_bets_at_sp} of ${data.band_bets} had no saved Ladbrokes price, so the starting price was used.</li>`;
    html += '<li><strong>Favourite</strong> — the runner with the shortest starting price.</li>';
    html += '<li><strong>Long-shot angles</strong> — the "two or more" row counts a runner that fits at least two of: 2-3kg less than the field average, settles at the back in a sprint, trainer out of form, low win rate at this track, final 200m rated average, won a photo finish last start, racing again within 7 days.</li>';
    if (waiting || estimated) {
      html += `<li>${estimated} place bets use the Ladbrokes place price for now. ${waiting} placed with no price yet and are left out of place profit.</li>`;
    }
    html += '<li>Uses the filters at the top of the page.</li>';
    html += '</ul></div>';

    const checked = data.angles.filter((a) => a.audit);
    if (checked.length) {
      html += '<details class="mt-3"><summary style="cursor:pointer;font-weight:600">What an earlier check found for each angle</summary>';
      html += '<p class="text-muted mt-2 mb-2" style="font-size:12px">From checks run in November 2026 on results up to October, at Betfair prices. These angles were picked after seeing those results, so the tables above are the fairer test.</p>';
      html += '<ul class="small">';
      for (const a of checked) html += `<li><strong>${esc(a.name)}</strong> — ${esc(a.audit)}</li>`;
      html += '</ul></details>';
    }
    html += '</div>';
    container.innerHTML = html;
  }

  // ── Jockeys & Trainers In Form ───────────────────────────────────────
  const KINDS = [['jockeys', '🏇 Jockeys'], ['trainers', '👔 Trainers'], ['combos', '🤝 Jockey + Trainer']];
  const COLUMNS = {
    name: ['Name', false], runs: ['Runs', true], wins: ['Wins', true], strike_rate: ['Strike %', true],
    place_rate: ['Top 3 %', true], roi: ['Return', true], profit: ['Profit', true],
    pf_sr: ['PuntingForm last 100', true], pf_ae: ['PuntingForm vs market', true], upcoming: ['Coming up', true],
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
      html += '<li><strong>Return</strong> — profit or loss per $100 bet, backing every one of their runners at the starting price. <strong>Profit</strong> is the same in dollars at $10 a bet. Tap any heading to sort by it.</li>';
      html += '<li><strong>PuntingForm last 100</strong> — PuntingForm\'s own count: wins out of their last 100 runs.</li>';
      html += '<li><strong>PuntingForm vs market</strong> — their last 100 wins against what their prices predicted. 1.00 is exactly as expected. Above 1.00 means winning more often than the prices say.</li>';
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
