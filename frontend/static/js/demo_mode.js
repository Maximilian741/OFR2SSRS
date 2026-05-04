// Self-contained guided tour for the Oracle2SSRS demo UI.
// Loaded via: <script src="/static/js/demo_mode.js?v=..."></script>
// Adds a "Take a tour" floating button. On click, runs a 6-step
// walkthrough that highlights key parts of the page and clicks
// through the tab panels using a sample report.
(function () {
  'use strict';

  if (window.__o2sDemoModeLoaded) return;
  window.__o2sDemoModeLoaded = true;

  // ------------------------------------------------------------------
  // Styles (injected once)
  // ------------------------------------------------------------------
  var STYLE_ID = 'o2s-demo-style';
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      '#o2s-demo-btn{position:fixed;right:20px;bottom:20px;z-index:99998;',
      'width:140px;height:40px;border:none;border-radius:20px;',
      'background:linear-gradient(135deg,#7c3aed 0%,#a855f7 100%);',
      'color:#fff;font:600 13px/40px system-ui,sans-serif;cursor:pointer;',
      'box-shadow:0 4px 16px rgba(124,58,237,.45);text-align:center;',
      'transition:transform .15s ease,box-shadow .15s ease;letter-spacing:.2px}',
      '#o2s-demo-btn:hover{transform:translateY(-1px);',
      'box-shadow:0 6px 22px rgba(124,58,237,.6)}',
      '.demo-highlight{position:relative;outline:3px solid #a855f7 !important;',
      'outline-offset:3px;border-radius:6px;',
      'box-shadow:0 0 0 6px rgba(168,85,247,.18),0 0 28px rgba(168,85,247,.55) !important;',
      'transition:outline .2s ease,box-shadow .2s ease;z-index:1}',
      '#o2s-demo-tip{position:absolute;z-index:99999;max-width:280px;',
      'background:#1f2937;color:#f9fafb;padding:12px 14px;border-radius:10px;',
      'font:500 13px/1.45 system-ui,sans-serif;',
      'box-shadow:0 10px 30px rgba(0,0,0,.35);cursor:pointer;',
      'border:1px solid #4c1d95}',
      '#o2s-demo-tip .o2s-tip-step{font-size:11px;opacity:.7;',
      'text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px}',
      '#o2s-demo-tip .o2s-tip-hint{font-size:11px;opacity:.65;',
      'margin-top:8px;font-style:italic}',
      '#o2s-demo-tip:before{content:"";position:absolute;left:-7px;top:18px;',
      'width:0;height:0;border:7px solid transparent;border-right-color:#1f2937}',
      '#o2s-demo-tip.tip-above:before{left:18px;top:auto;bottom:-13px;',
      'border:7px solid transparent;border-top-color:#1f2937}',
      '#o2s-demo-toast{position:fixed;left:50%;bottom:80px;transform:translateX(-50%);',
      'z-index:99999;background:linear-gradient(135deg,#7c3aed,#a855f7);',
      'color:#fff;padding:12px 22px;border-radius:24px;',
      'font:600 14px/1 system-ui,sans-serif;',
      'box-shadow:0 8px 24px rgba(124,58,237,.45);opacity:0;',
      'transition:opacity .25s ease}',
      '#o2s-demo-toast.show{opacity:1}'
    ].join('');
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = css;
    document.head.appendChild(s);
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  function $(sel) { return document.querySelector(sel); }

  function clearHighlights() {
    var marked = document.querySelectorAll('.demo-highlight');
    for (var i = 0; i < marked.length; i++) {
      marked[i].classList.remove('demo-highlight');
    }
  }

  function removeTip() {
    var t = document.getElementById('o2s-demo-tip');
    if (t && t.parentNode) t.parentNode.removeChild(t);
  }

  // Position tooltip relative to a target element. Returns the tip node.
  function showTip(target, stepNum, totalSteps, text) {
    removeTip();
    var tip = document.createElement('div');
    tip.id = 'o2s-demo-tip';

    var step = document.createElement('div');
    step.className = 'o2s-tip-step';
    step.textContent = 'Step ' + stepNum + ' of ' + totalSteps;
    tip.appendChild(step);

    var body = document.createElement('div');
    body.textContent = text;
    tip.appendChild(body);

    var hint = document.createElement('div');
    hint.className = 'o2s-tip-hint';
    hint.textContent = 'Click tooltip to continue';
    tip.appendChild(hint);

    document.body.appendChild(tip);

    // Position
    var top = 100, left = 100, above = false;
    if (target && target.getBoundingClientRect) {
      var r = target.getBoundingClientRect();
      var tw = tip.offsetWidth || 280;
      var th = tip.offsetHeight || 80;
      left = r.right + 14 + window.scrollX;
      top = r.top + window.scrollY;
      // If tooltip would go off the right edge, place below instead
      if (left + tw > window.innerWidth - 10) {
        left = Math.max(10, r.left + window.scrollX);
        top = r.bottom + 14 + window.scrollY;
        above = true;
      }
      // Clamp vertical
      if (top + th > window.scrollY + window.innerHeight - 10) {
        top = window.scrollY + window.innerHeight - th - 10;
      }
    }
    tip.style.top = top + 'px';
    tip.style.left = left + 'px';
    if (above) tip.classList.add('tip-above');
    return tip;
  }

  function highlight(el) {
    clearHighlights();
    if (el && el.classList) el.classList.add('demo-highlight');
  }

  // Wait for window.state.data (or DOM signal that the report loaded)
  // up to ~5s. We poll because state is a module-local const in app.js,
  // but app.js exposes nothing — fall back to DOM signals.
  function waitForData(cb) {
    var start = Date.now();
    var tries = 0;
    var iv = setInterval(function () {
      tries += 1;
      var ready = false;
      try {
        if (window.state && window.state.data) ready = true;
      } catch (e) { /* ignore */ }
      if (!ready) {
        // DOM signal: empty-state hidden + summary-section visible
        var empty = $('#empty-state');
        var summary = $('#summary-section');
        var emptyHidden = empty && (empty.hidden || empty.getAttribute('hidden') !== null
                                    || empty.style.display === 'none');
        var summaryVisible = summary && !summary.hidden
                             && summary.getAttribute('hidden') === null;
        if (emptyHidden && summaryVisible) ready = true;
      }
      if (ready || Date.now() - start > 5000) {
        clearInterval(iv);
        cb(ready);
      }
    }, 100);
  }

  function clickEl(el) {
    if (!el) return false;
    if (typeof el.click === 'function') {
      el.click();
    } else {
      var ev = new MouseEvent('click', { bubbles: true, cancelable: true });
      el.dispatchEvent(ev);
    }
    return true;
  }

  function showToast(msg, ms) {
    var existing = document.getElementById('o2s-demo-toast');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    var t = document.createElement('div');
    t.id = 'o2s-demo-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    // Trigger transition
    setTimeout(function () { t.classList.add('show'); }, 10);
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () {
        if (t.parentNode) t.parentNode.removeChild(t);
      }, 350);
    }, ms || 2400);
  }

  // ------------------------------------------------------------------
  // Tour steps
  // ------------------------------------------------------------------
  // Each step: { setup(done), target() => element, text }
  // The user clicks the tooltip to advance.
  var TOTAL = 6;

  function findSampleChip() {
    var chips = document.querySelectorAll('#samples-list .sample-chip');
    for (var i = 0; i < chips.length; i++) {
      var ds = chips[i].dataset && chips[i].dataset.sample
                ? String(chips[i].dataset.sample) : '';
      if (ds.toLowerCase().indexOf('mvwf_permit') !== -1
          || ds.toLowerCase().indexOf('mvwf') !== -1) {
        return chips[i];
      }
    }
    // Fallback: first chip if available
    return chips.length ? chips[0] : null;
  }

  function tabBtn(name) {
    return document.querySelector('.tab[data-tab="' + name + '"]');
  }

  function runTour(onDone) {
    var step = 0;

    function advance() {
      step += 1;
      removeTip();
      clearHighlights();
      if (step === 1) {
        var dz = $('#drop-zone');
        highlight(dz);
        var tip = showTip(dz, 1, TOTAL,
          'This is where you drop Oracle artifacts. Drag XML/RDF files or pick from samples below.');
        tip.addEventListener('click', advance);
      } else if (step === 2) {
        var chip = findSampleChip();
        if (!chip) {
          showToast('Sample chip not found, ending tour', 2200);
          finish();
          return;
        }
        highlight(chip);
        var tip2 = showTip(chip, 2, TOTAL,
          'Loading the MVWF_PERMIT sample report...');
        // Auto-advance after click + data load (no user click needed)
        clickEl(chip);
        waitForData(function () {
          // Brief pause so the user sees the chip light up
          setTimeout(advance, 400);
        });
      } else if (step === 3) {
        var mockTab = tabBtn('mockup');
        if (mockTab) clickEl(mockTab);
        highlight(mockTab);
        var tip3 = showTip(mockTab, 3, TOTAL,
          'HTML Mockup: a visual preview of the SSRS report rendered from the converted RDL.');
        tip3.addEventListener('click', advance);
      } else if (step === 4) {
        var rdlTab = tabBtn('rdl');
        if (rdlTab) clickEl(rdlTab);
        highlight(rdlTab);
        var tip4 = showTip(rdlTab, 4, TOTAL,
          'RDL XML: the actual SSRS-ready output you can drop into Report Builder or Visual Studio.');
        tip4.addEventListener('click', advance);
      } else if (step === 5) {
        var valTab = tabBtn('validate');
        if (valTab) clickEl(valTab);
        highlight(valTab);
        var tip5 = showTip(valTab, 5, TOTAL,
          'Validation: static checks — what to fix before deploying to your SSRS server.');
        tip5.addEventListener('click', advance);
      } else if (step === 6) {
        var exTab = tabBtn('extras');
        if (exTab) clickEl(exTab);
        highlight(exTab);
        var tip6 = showTip(exTab, 6, TOTAL,
          'Extras: bursting setup, AI-assist prompts, and the full audit trail of the conversion.');
        tip6.addEventListener('click', finish);
      }
    }

    function finish() {
      removeTip();
      clearHighlights();
      showToast('Tour complete', 2600);
      if (typeof onDone === 'function') onDone();
    }

    advance();
  }

  // ------------------------------------------------------------------
  // Floating button
  // ------------------------------------------------------------------
  function mountButton() {
    if (document.getElementById('o2s-demo-btn')) return;
    injectStyles();
    var btn = document.createElement('button');
    btn.id = 'o2s-demo-btn';
    btn.type = 'button';
    btn.textContent = 'Take a tour';
    btn.setAttribute('aria-label', 'Start guided tour');
    btn.addEventListener('click', function () {
      btn.disabled = true;
      btn.style.opacity = '0.55';
      runTour(function () {
        // Remove the button so the tour doesn't run again
        if (btn.parentNode) btn.parentNode.removeChild(btn);
      });
    });
    document.body.appendChild(btn);
  }

  function init() {
    try { mountButton(); }
    catch (e) { /* swallow — never break the host page */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
