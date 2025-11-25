/* static/js/betfair-live.js
   Minimal SSE client to update Odds and Result columns on the meeting page.
*/
(function () {
  function q(selector, root) { return (root || document).querySelector(selector); }
  function qs(selector, root) { return Array.from((root || document).querySelectorAll(selector)); }

  var resultsTable = q('table.results, table#results, table.meeting-results');
  if (!resultsTable) {
    console.debug("betfair-live: no results table found; not running");
    return;
  }

  var columnOdds = 'betfair-odds';
  var columnResult = 'betfair-result';

  function ensureColumns() {
    var ths = qs('thead tr th', resultsTable);
    if (!ths.some(th => th.classList && th.classList.contains(columnOdds))) {
      var headerRow = q('thead tr', resultsTable);
      if (headerRow) {
        var thOdds = document.createElement('th');
        thOdds.textContent = 'Odds';
        thOdds.className = columnOdds;
        headerRow.appendChild(thOdds);
        var thRes = document.createElement('th');
        thRes.textContent = 'Result';
        thRes.className = columnResult;
        headerRow.appendChild(thRes);
      }
    }
  }

  function findRowBySelection(selectionId) {
    if (!selectionId) return null;
    var sel = qs('tr[data-selection-id="' + selectionId + '"]', resultsTable);
    if (sel.length) return sel[0];
    sel = qs('[data-selection-id="' + selectionId + '"]', resultsTable);
    if (sel.length) {
      return sel[0].closest('tr');
    }
    return null;
  }

  function findRowByHorseName(name) {
    name = name && name.trim().toLowerCase();
    if (!name) return null;
    var rows = qs('tbody tr', resultsTable);
    for (var i=0;i<rows.length;i++){
      var r = rows[i];
      var text = r.textContent.trim().toLowerCase();
      if (text.indexOf(name) !== -1) return r;
    }
    return null;
  }

  function setCell(row, key, text) {
    var cell = row.querySelector('td.' + key);
    if (!cell) {
      cell = document.createElement('td');
      cell.className = key;
      row.appendChild(cell);
    }
    cell.textContent = text !== null && text !== undefined ? String(text) : '';
  }

  ensureColumns();

  var s = new EventSource('/stream');
  s.onopen = function() { console.debug('betfair-live: SSE open'); };
  s.onerror = function(e) { console.warn('betfair-live: SSE error', e); };
  s.onmessage = function(evt) {
    try {
      var data = JSON.parse(evt.data);
      if (data.type === 'marketUpdate' || data.marketId) {
        var marketId = data.marketId;
        (data.runners || []).forEach(function (r) {
          var sel = r.selectionId;
          var odds = r.lastPriceTraded || '';
          var row = findRowBySelection(sel);
          if (!row && r.runnerName) {
            row = findRowByHorseName(r.runnerName);
          }
          if (!row) return;
          setCell(row, 'betfair-odds', odds);
          if (data.status === 'CLOSED') {
            var pos = r.status || '';
            var label = pos ? (pos === 1 ? 'Winner' : pos + ' place') : 'Settled';
            setCell(row, 'betfair-result', label + (odds ? ' @ ' + odds : ''));
            row.classList.add('betfair-settled');
          } else {
            setCell(row, 'betfair-result', '');
            row.classList.remove('betfair-settled');
          }
        });
      }
    } catch (err) {
      console.error('betfair-live: invalid SSE message', err);
    }
  };
})();
