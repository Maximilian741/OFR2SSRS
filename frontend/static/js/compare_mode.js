// Self-contained "Compare Two Reports" feature for Oracle2SSRS.
// Loaded via: <script src="/static/js/compare_mode.js?v=..."></script>
// Adds a floating "Compare" button next to the existing "Take a tour" button.
// On click, opens a modal with two drop zones (A and B). Each accepts one .xml
// file. "Run comparison" submits both via FormData to POST /api/compare and
// renders the response inside the modal.
(function () {
  'use strict';

  if (window.__o2sCompareModeLoaded) return;
  window.__o2sCompareModeLoaded = true;

  // ------------------------------------------------------------------
  // Styles (injected once)
  // ------------------------------------------------------------------
  var STYLE_ID = 'o2s-compare-style';
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      '#o2s-compare-btn{position:fixed;right:170px;bottom:20px;z-index:99998;',
      'width:120px;height:40px;border:none;border-radius:20px;',
      'background:linear-gradient(135deg,#7c3aed 0%,#a855f7 100%);',
      'color:#fff;font:600 13px/40px system-ui,sans-serif;cursor:pointer;',
      'box-shadow:0 4px 16px rgba(124,58,237,.45);text-align:center;',
      'transition:transform .15s ease,box-shadow .15s ease;letter-spacing:.2px}',
      '#o2s-compare-btn:hover{transform:translateY(-1px);',
      'box-shadow:0 6px 22px rgba(124,58,237,.6)}',
      '#o2s-compare-overlay{position:fixed;inset:0;z-index:99997;',
      'background:rgba(15,23,42,.55);display:none;',
      'align-items:flex-start;justify-content:center;padding:40px 20px;',
      'overflow-y:auto}',
      '#o2s-compare-overlay.show{display:flex}',
      '#o2s-compare-modal{background:#fff;width:100%;max-width:980px;',
      'border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.35);',
      'font:14px/1.5 system-ui,sans-serif;color:#0f172a;overflow:hidden}',
      '#o2s-compare-header{padding:18px 22px;background:linear-gradient(135deg,#7c3aed,#a855f7);',
      'color:#fff;display:flex;align-items:center;justify-content:space-between}',
      '#o2s-compare-header h2{margin:0;font-size:18px;font-weight:700}',
      '#o2s-compare-close{background:rgba(255,255,255,.18);border:none;color:#fff;',
      'width:30px;height:30px;border-radius:15px;cursor:pointer;font-size:18px;line-height:30px}',
      '#o2s-compare-close:hover{background:rgba(255,255,255,.32)}',
      '#o2s-compare-body{padding:22px}',
      '.o2s-cmp-zones{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}',
      '.o2s-cmp-zone{border:2px dashed #cbd5e1;border-radius:10px;padding:22px 14px;',
      'text-align:center;background:#f8fafc;cursor:pointer;transition:border-color .15s,background .15s}',
      '.o2s-cmp-zone:hover{border-color:#a855f7;background:#faf5ff}',
      '.o2s-cmp-zone.dragover{border-color:#7c3aed;background:#ede9fe}',
      '.o2s-cmp-zone.has-file{border-style:solid;border-color:#7c3aed;background:#f5f3ff}',
      '.o2s-cmp-zone-label{font-weight:700;color:#7c3aed;margin-bottom:6px;letter-spacing:.3px}',
      '.o2s-cmp-zone-hint{font-size:12px;color:#64748b}',
      '.o2s-cmp-zone-name{font-size:13px;color:#0f172a;margin-top:8px;word-break:break-all;font-weight:600}',
      '.o2s-cmp-actions{display:flex;justify-content:flex-end;gap:10px;margin-bottom:14px}',
      '.o2s-cmp-btn{padding:9px 18px;border-radius:8px;border:none;font-weight:600;',
      'cursor:pointer;font-size:13px}',
      '.o2s-cmp-btn-primary{background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff}',
      '.o2s-cmp-btn-primary:hover:not(:disabled){opacity:.92}',
      '.o2s-cmp-btn-primary:disabled{opacity:.45;cursor:not-allowed}',
      '.o2s-cmp-btn-ghost{background:#f1f5f9;color:#334155}',
      '.o2s-cmp-btn-ghost:hover{background:#e2e8f0}',
      '#o2s-compare-results{border-top:1px solid #e2e8f0;padding-top:16px;display:none}',
      '#o2s-compare-results.show{display:block}',
      '.o2s-cmp-summary{padding:12px 14px;background:#f1f5f9;border-radius:8px;',
      'margin-bottom:16px;font-size:13px;color:#0f172a;font-family:ui-monospace,Menlo,monospace}',
      '.o2s-cmp-section{margin-bottom:18px}',
      '.o2s-cmp-section h3{margin:0 0 8px;font-size:14px;color:#0f172a;',
      'font-weight:700;letter-spacing:.2px}',
      '.o2s-cmp-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:12px}',
      '.o2s-cmp-col{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px}',
      '.o2s-cmp-col.added{background:#ecfdf5;border-color:#a7f3d0}',
      '.o2s-cmp-col.removed{background:#fef2f2;border-color:#fecaca}',
      '.o2s-cmp-col h4{margin:0 0 6px;font-size:12px;text-transform:uppercase;',
      'letter-spacing:.4px;color:#475569}',
      '.o2s-cmp-list{margin:0;padding:0;list-style:none}',
      '.o2s-cmp-list li{padding:3px 0;color:#0f172a;font-family:ui-monospace,Menlo,monospace}',
      '.o2s-cmp-changed{margin-top:10px}',
      '.o2s-cmp-changed-row{padding:8px 10px;border:1px solid #e2e8f0;',
      'border-radius:6px;margin-bottom:6px;background:#fff}',
      '.o2s-cmp-changed-row.differs{border-color:#fbbf24;background:#fffbeb}',
      '.o2s-cmp-changed-name{font-family:ui-monospace,Menlo,monospace;font-weight:600;',
      'font-size:12px;color:#0f172a}',
      '.o2s-cmp-changed-meta{font-size:11px;color:#64748b;margin-top:2px}',
      '.o2s-cmp-diff{margin-top:6px;font-family:ui-monospace,Menlo,monospace;',
      'font-size:11px;background:#0f172a;color:#e2e8f0;border-radius:6px;',
      'padding:8px 10px;max-height:200px;overflow:auto;white-space:pre;line-height:1.4}',
      '.o2s-cmp-diff .add{color:#86efac}',
      '.o2s-cmp-diff .del{color:#fca5a5}',
      '.o2s-cmp-diff .hunk{color:#93c5fd}',
      '.o2s-cmp-cx{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;',
      'text-align:center}',
      '.o2s-cmp-cx-tile{padding:14px;background:#f8fafc;border:1px solid #e2e8f0;',
      'border-radius:8px}',
      '.o2s-cmp-cx-tile .lbl{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.4px}',
      '.o2s-cmp-cx-tile .val{font-size:22px;font-weight:700;color:#0f172a;margin-top:4px}',
      '.o2s-cmp-cx-tile.delta-up .val{color:#dc2626}',
      '.o2s-cmp-cx-tile.delta-down .val{color:#16a34a}',
      '.o2s-cmp-error{padding:10px 14px;background:#fef2f2;border:1px solid #fecaca;',
      'color:#991b1b;border-radius:6px;font-size:13px}',
      '.o2s-cmp-empty{color:#94a3b8;font-style:italic}'
    ].join('');
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = css;
    document.head.appendChild(s);
  }

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  var fileA = null;
  var fileB = null;
  var overlayEl = null;

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function renderDiffLines(diffText) {
    if (!diffText) return '<span class="o2s-cmp-empty">(no textual changes)</span>';
    return diffText.split('\n').map(function (line) {
      var cls = '';
      if (line.startsWith('+++') || line.startsWith('---')) cls = 'hunk';
      else if (line.startsWith('@@')) cls = 'hunk';
      else if (line.startsWith('+')) cls = 'add';
      else if (line.startsWith('-')) cls = 'del';
      return '<span class="' + cls + '">' + escapeHtml(line) + '</span>';
    }).join('\n');
  }

  function renderListCol(title, items, klass) {
    var body;
    if (!items || items.length === 0) {
      body = '<span class="o2s-cmp-empty">(none)</span>';
    } else {
      body = '<ul class="o2s-cmp-list">' + items.map(function (n) {
        return '<li>' + escapeHtml(n) + '</li>';
      }).join('') + '</ul>';
    }
    return '<div class="o2s-cmp-col ' + (klass || '') + '"><h4>' +
      escapeHtml(title) + '</h4>' + body + '</div>';
  }

  function renderParamChanges(rows) {
    if (!rows || rows.length === 0) {
      return '<div class="o2s-cmp-empty">No shared parameters.</div>';
    }
    return rows.map(function (r) {
      var cls = r.changed ? 'differs' : '';
      var meta = r.changed ? escapeHtml(r.details) : 'identical';
      return '<div class="o2s-cmp-changed-row ' + cls + '">' +
        '<div class="o2s-cmp-changed-name">' + escapeHtml(r.name) + '</div>' +
        '<div class="o2s-cmp-changed-meta">' + meta + '</div>' +
        '</div>';
    }).join('');
  }

  function renderBodyChanges(rows) {
    if (!rows || rows.length === 0) {
      return '<div class="o2s-cmp-empty">No shared items.</div>';
    }
    return rows.map(function (r) {
      var differs = !!r.sql_unified_diff;
      var cls = differs ? 'differs' : '';
      var meta = 'complexity: ' + r.complexity_a + ' -> ' + r.complexity_b;
      var html = '<div class="o2s-cmp-changed-row ' + cls + '">' +
        '<div class="o2s-cmp-changed-name">' + escapeHtml(r.name) + '</div>' +
        '<div class="o2s-cmp-changed-meta">' + meta +
        (differs ? ' (text differs)' : ' (identical)') + '</div>';
      if (differs) {
        html += '<div class="o2s-cmp-diff">' + renderDiffLines(r.sql_unified_diff) + '</div>';
      }
      html += '</div>';
      return html;
    }).join('');
  }

  function renderSection(title, section, isParam) {
    var added = renderListCol('Only in B (' + section.only_in_b.length + ')', section.only_in_b, 'added');
    var removed = renderListCol('Only in A (' + section.only_in_a.length + ')', section.only_in_a, 'removed');
    var changes = isParam ? renderParamChanges(section.in_both) : renderBodyChanges(section.in_both);
    return '<div class="o2s-cmp-section">' +
      '<h3>' + escapeHtml(title) + '</h3>' +
      '<div class="o2s-cmp-cols">' + removed + added + '</div>' +
      '<div class="o2s-cmp-changed">' + changes + '</div>' +
      '</div>';
  }

  function renderResults(data) {
    var resultsEl = document.getElementById('o2s-compare-results');
    if (!resultsEl) return;
    if (data.error) {
      resultsEl.innerHTML = '<div class="o2s-cmp-error">' +
        escapeHtml(data.error) + '</div>';
      resultsEl.classList.add('show');
      return;
    }
    var cx = data.complexity_score || {a: 0, b: 0, delta: 0};
    var deltaCls = cx.delta > 0 ? 'delta-up' : (cx.delta < 0 ? 'delta-down' : '');
    var html = '';
    html += '<div class="o2s-cmp-summary">' + escapeHtml(data.summary || '') + '</div>';
    html += renderSection('Parameters', data.parameters, true);
    html += renderSection('Queries', data.queries, false);
    html += renderSection('Formulas', data.formulas, false);
    html += '<div class="o2s-cmp-section">' +
      '<h3>Complexity score</h3>' +
      '<div class="o2s-cmp-cx">' +
      '<div class="o2s-cmp-cx-tile"><div class="lbl">' + escapeHtml(data.name_a || 'A') +
      '</div><div class="val">' + cx.a + '</div></div>' +
      '<div class="o2s-cmp-cx-tile"><div class="lbl">' + escapeHtml(data.name_b || 'B') +
      '</div><div class="val">' + cx.b + '</div></div>' +
      '<div class="o2s-cmp-cx-tile ' + deltaCls + '"><div class="lbl">Delta</div>' +
      '<div class="val">' + (cx.delta > 0 ? '+' : '') + cx.delta + '</div></div>' +
      '</div></div>';
    resultsEl.innerHTML = html;
    resultsEl.classList.add('show');
  }

  function updateRunButton() {
    var btn = document.getElementById('o2s-cmp-run');
    if (btn) btn.disabled = !(fileA && fileB);
  }

  function setZoneFile(zone, file, label) {
    if (!zone) return;
    zone.classList.add('has-file');
    var nameEl = zone.querySelector('.o2s-cmp-zone-name');
    if (nameEl) nameEl.textContent = file.name;
  }

  function bindZone(zone, slot) {
    var input = zone.querySelector('input[type=file]');
    zone.addEventListener('click', function () { input.click(); });
    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('dragover');
    });
    zone.addEventListener('dragleave', function () {
      zone.classList.remove('dragover');
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dragover');
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) {
        if (slot === 'a') fileA = f; else fileB = f;
        setZoneFile(zone, f, slot);
        updateRunButton();
      }
    });
    input.addEventListener('change', function () {
      var f = input.files && input.files[0];
      if (f) {
        if (slot === 'a') fileA = f; else fileB = f;
        setZoneFile(zone, f, slot);
        updateRunButton();
      }
    });
  }

  function buildModal() {
    var overlay = document.createElement('div');
    overlay.id = 'o2s-compare-overlay';
    overlay.innerHTML = [
      '<div id="o2s-compare-modal" role="dialog" aria-label="Compare two reports">',
      '<div id="o2s-compare-header">',
      '<h2>Compare Two Reports</h2>',
      '<button id="o2s-compare-close" aria-label="Close">&times;</button>',
      '</div>',
      '<div id="o2s-compare-body">',
      '<div class="o2s-cmp-zones">',
      '<div class="o2s-cmp-zone" data-slot="a">',
      '<div class="o2s-cmp-zone-label">Report A</div>',
      '<div class="o2s-cmp-zone-hint">Drop .xml here or click to browse</div>',
      '<div class="o2s-cmp-zone-name"></div>',
      '<input type="file" accept=".xml" hidden />',
      '</div>',
      '<div class="o2s-cmp-zone" data-slot="b">',
      '<div class="o2s-cmp-zone-label">Report B</div>',
      '<div class="o2s-cmp-zone-hint">Drop .xml here or click to browse</div>',
      '<div class="o2s-cmp-zone-name"></div>',
      '<input type="file" accept=".xml" hidden />',
      '</div>',
      '</div>',
      '<div class="o2s-cmp-actions">',
      '<button class="o2s-cmp-btn o2s-cmp-btn-ghost" id="o2s-cmp-reset">Reset</button>',
      '<button class="o2s-cmp-btn o2s-cmp-btn-primary" id="o2s-cmp-run" disabled>Run comparison</button>',
      '</div>',
      '<div id="o2s-compare-results"></div>',
      '</div>',
      '</div>'
    ].join('');
    document.body.appendChild(overlay);

    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) closeModal();
    });
    overlay.querySelector('#o2s-compare-close').addEventListener('click', closeModal);
    overlay.querySelector('#o2s-cmp-reset').addEventListener('click', resetModal);
    overlay.querySelector('#o2s-cmp-run').addEventListener('click', runCompare);

    var zones = overlay.querySelectorAll('.o2s-cmp-zone');
    bindZone(zones[0], 'a');
    bindZone(zones[1], 'b');
    return overlay;
  }

  function ensureModal() {
    if (overlayEl && document.body.contains(overlayEl)) return overlayEl;
    overlayEl = buildModal();
    return overlayEl;
  }

  function openModal() {
    try {
      var ov = ensureModal();
      ov.classList.add('show');
    } catch (err) {
      console.error('compare_mode openModal failed', err);
    }
  }

  function closeModal() {
    if (overlayEl) overlayEl.classList.remove('show');
  }

  function resetModal() {
    fileA = null; fileB = null;
    if (!overlayEl) return;
    var zones = overlayEl.querySelectorAll('.o2s-cmp-zone');
    zones.forEach(function (z) {
      z.classList.remove('has-file');
      var n = z.querySelector('.o2s-cmp-zone-name');
      if (n) n.textContent = '';
      var inp = z.querySelector('input[type=file]');
      if (inp) inp.value = '';
    });
    var res = document.getElementById('o2s-compare-results');
    if (res) { res.innerHTML = ''; res.classList.remove('show'); }
    updateRunButton();
  }

  function runCompare() {
    if (!fileA || !fileB) return;
    var btn = document.getElementById('o2s-cmp-run');
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Comparing...';
    var fd = new FormData();
    fd.append('file_a', fileA);
    fd.append('file_b', fileB);
    fetch('/api/compare', {method: 'POST', body: fd})
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, body: j}; }); })
      .then(function (resp) {
        if (!resp.ok && resp.body && resp.body.error) {
          renderResults({error: resp.body.error});
        } else {
          renderResults(resp.body);
        }
      })
      .catch(function (err) {
        renderResults({error: 'Request failed: ' + err.message});
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = orig;
        updateRunButton();
      });
  }

  function mountButton() {
    if (document.getElementById('o2s-compare-btn')) return;
    var b = document.createElement('button');
    b.id = 'o2s-compare-btn';
    b.type = 'button';
    b.textContent = 'Compare';
    b.title = 'Compare two Oracle reports';
    b.addEventListener('click', openModal);
    document.body.appendChild(b);
  }

  function init() {
    try {
      injectStyles();
      mountButton();
    } catch (err) {
      console.error('compare_mode init failed', err);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
