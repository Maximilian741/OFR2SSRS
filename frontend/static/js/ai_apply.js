/* ai_apply.js — wires "Apply this translation" panels into each AI-prompt card.
 *
 * Self-contained IIFE. Watches the Extras tab for prompt cards rendered by
 * app.js and injects a textarea + button below each prompt's <pre>.
 *
 * Posts to /api/apply-fix with { target: {kind, name}, new_body }.
 */
(function () {
  "use strict";

  var INJECTED_FLAG = "data-ai-apply-injected";

  function toast(msg, kind) {
    // Prefer the host app's toast if present; otherwise fall back to alert.
    try {
      if (typeof window.toast === "function") {
        window.toast(msg, kind || "ok");
        return;
      }
    } catch (e) { /* noop */ }
    // Lightweight fallback toast
    var div = document.createElement("div");
    div.textContent = msg;
    div.style.cssText =
      "position:fixed;bottom:20px;right:20px;z-index:99999;" +
      "padding:8px 14px;border-radius:6px;font-family:sans-serif;font-size:13px;" +
      "color:#fff;background:" + (kind === "err" ? "#b00020" : "#2a7d2a") + ";" +
      "box-shadow:0 2px 8px rgba(0,0,0,0.25);";
    document.body.appendChild(div);
    setTimeout(function () { try { div.remove(); } catch (e) {} }, 3500);
  }

  // Map ai_assist scope → our backend "kind" enum.
  function scopeToKind(scope) {
    if (!scope) return "udf";
    var s = String(scope).toLowerCase();
    if (s.indexOf("query") !== -1) return "query";
    if (s.indexOf("formula") !== -1) return "formula";
    return "udf"; // package_fn, others
  }

  function buildPanel(card, idx) {
    var summary = card.querySelector("summary");
    var nameEl = summary ? summary.querySelector("b") : null;
    var tagEl = summary ? summary.querySelector(".extras-tag") : null;
    var name = nameEl ? (nameEl.textContent || "").trim() : "";
    var scope = tagEl ? (tagEl.textContent || "").trim() : "";
    var kind = scopeToKind(scope);

    var wrap = document.createElement("div");
    wrap.className = "ai-apply-panel";
    wrap.style.cssText =
      "margin-top:10px;padding:10px;border:1px solid var(--border,#ccc);" +
      "border-radius:6px;background:var(--surface-2,#f7f7f7);";

    var label = document.createElement("div");
    label.textContent =
      "Paste the AI's T-SQL response below, then click Apply. " +
      "Target: " + (kind || "udf") + " — " + (name || "?");
    label.style.cssText =
      "font-size:12px;margin-bottom:6px;color:var(--text-muted,#555);";
    wrap.appendChild(label);

    var ta = document.createElement("textarea");
    ta.rows = 6;
    ta.placeholder = "CREATE FUNCTION dbo.fn_..." + "\n  (...) RETURNS ... AS BEGIN ... END";
    ta.style.cssText =
      "width:100%;box-sizing:border-box;font-family:Consolas,Menlo,monospace;" +
      "font-size:12px;padding:6px;border:1px solid var(--border,#ccc);" +
      "border-radius:4px;background:#fff;color:#111;resize:vertical;";
    wrap.appendChild(ta);

    var actions = document.createElement("div");
    actions.style.cssText = "margin-top:6px;display:flex;align-items:center;gap:8px;";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn";
    btn.textContent = "Apply this translation";
    btn.style.cssText =
      "padding:5px 12px;font-size:13px;border-radius:4px;cursor:pointer;" +
      "border:1px solid var(--accent,#356aff);background:var(--accent,#356aff);color:#fff;";
    actions.appendChild(btn);

    var status = document.createElement("span");
    status.className = "ai-apply-status";
    status.style.cssText = "font-size:12px;color:var(--text-muted,#555);";
    actions.appendChild(status);

    wrap.appendChild(actions);

    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var body = (ta.value || "").trim();
      if (!body) { toast("Paste a T-SQL response first", "err"); return; }
      btn.disabled = true;
      status.textContent = "Applying...";

      var payload = {
        target: { kind: kind, name: name },
        new_body: body
      };

      fetch("/api/apply-fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, status: r.status, json: j }; });
      }).then(function (res) {
        if (!res.ok) {
          var msg = (res.json && (res.json.error || res.json.issues)) || ("HTTP " + res.status);
          if (Array.isArray(msg)) msg = msg.join("; ");
          status.textContent = "Failed: " + msg;
          status.style.color = "#b00020";
          toast("Apply failed: " + msg, "err");
          btn.disabled = false;
          return;
        }
        // Success — badge the card and disable the button.
        var info = (res.json && res.json.info) || {};
        var warns = (res.json && res.json.warnings) || [];
        toast("Applied " + (info.where ? "to " + info.where : "fix") +
              (warns.length ? " (" + warns.length + " warning(s))" : ""), "ok");
        btn.textContent = "Applied";
        btn.disabled = true;
        btn.style.background = "var(--ok,#2a7d2a)";
        btn.style.borderColor = "var(--ok,#2a7d2a)";
        ta.disabled = true;

        if (summary && !summary.querySelector(".ai-apply-badge")) {
          var badge = document.createElement("span");
          badge.className = "ai-apply-badge";
          badge.textContent = "Applied ✓";
          badge.style.cssText =
            "margin-left:6px;padding:2px 6px;font-size:11px;border-radius:10px;" +
            "background:#2a7d2a;color:#fff;";
          summary.appendChild(badge);
        }
        status.textContent = info.where || "applied";
        status.style.color = "#2a7d2a";
      }).catch(function (err) {
        status.textContent = "Network error";
        status.style.color = "#b00020";
        toast("Network error: " + err, "err");
        btn.disabled = false;
      });
    });

    return wrap;
  }

  function injectAll() {
    var cards = document.querySelectorAll(".extras-prompt");
    cards.forEach(function (card, idx) {
      if (card.getAttribute(INJECTED_FLAG) === "1") return;
      var pre = card.querySelector("pre.code-block, pre");
      if (!pre) return; // not yet rendered fully
      var panel = buildPanel(card, idx);
      // Insert AFTER the <pre>
      if (pre.nextSibling) {
        pre.parentNode.insertBefore(panel, pre.nextSibling);
      } else {
        pre.parentNode.appendChild(panel);
      }
      card.setAttribute(INJECTED_FLAG, "1");
    });
  }

  // Initial pass + observe future re-renders of the extras host.
  function init() {
    injectAll();
    var host = document.getElementById("extras-host");
    var observerTarget = host || document.body;
    try {
      var mo = new MutationObserver(function () { injectAll(); });
      mo.observe(observerTarget, { childList: true, subtree: true });
    } catch (e) { /* old browser? skip */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
