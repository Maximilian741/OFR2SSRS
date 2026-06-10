/* =========================================================
   Oracle2SSRS — Auto-AI button
   Adds a "Fix all with AI" button to the Extras tab that calls
   Claude on every AI prompt and applies the validated results.
   Self-contained, defensive, can't break the host page.
   ========================================================= */
(function () {
  "use strict";
  if (window.__o2sAiAutoLoaded) return;
  window.__o2sAiAutoLoaded = true;

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return Array.from(document.querySelectorAll(sel)); }

  function ensureStyles() {
    if (document.getElementById("o2s-ai-auto-style")) return;
    const s = document.createElement("style");
    s.id = "o2s-ai-auto-style";
    s.textContent = `
      .o2s-ai-auto-bar {
        display: flex; align-items: center; gap: 12px;
        margin: 0 0 14px 0; padding: 10px 14px;
        background: linear-gradient(135deg, #5b8cff, #7d6bff);
        color: #fff; border-radius: 10px;
        font-size: 13px;
      }
      .o2s-ai-auto-bar .o2s-ai-status { flex: 1; opacity: 0.95; }
      .o2s-ai-auto-bar button {
        background: rgba(0,0,0,0.25); color: #fff; border: 1px solid rgba(255,255,255,0.25);
        padding: 7px 14px; border-radius: 6px; cursor: pointer; font-weight: 600;
        font-size: 12px; font-family: inherit;
      }
      .o2s-ai-auto-bar button:hover { background: rgba(0,0,0,0.4); }
      .o2s-ai-auto-bar button:disabled { opacity: 0.55; cursor: not-allowed; }
      .o2s-ai-auto-progress {
        margin-top: 6px; font-size: 11px; opacity: 0.9;
        font-family: Consolas, monospace;
      }
      .o2s-ai-not-configured {
        background: linear-gradient(135deg, #b0b0b0, #888);
      }
    `;
    document.head.appendChild(s);
  }

  let configured = null;
  let model = "?";

  async function checkStatus() {
    try {
      const res = await fetch("/api/ai/status");
      const j = await res.json();
      configured = !!j.configured;
      model = j.model || "?";
    } catch (e) {
      configured = false;
    }
  }

  function injectBar() {
    const host = document.getElementById("extras-host");
    if (!host) return;
    if (host.querySelector(".o2s-ai-auto-bar")) return;  // already injected

    const bar = document.createElement("div");
    bar.className = "o2s-ai-auto-bar" + (configured ? "" : " o2s-ai-not-configured");

    const status = document.createElement("div");
    status.className = "o2s-ai-status";
    if (configured) {
      status.innerHTML = '<b>Auto-fix with AI</b> &mdash; click to run Claude (' + model + ') ' +
                        'on every prompt below and apply each valid result automatically.';
    } else {
      status.innerHTML = '<b>Auto-AI not configured.</b> Set <code style="background:rgba(0,0,0,0.25);padding:1px 5px;border-radius:3px;">ANTHROPIC_API_KEY</code> ' +
                        'in your <code style="background:rgba(0,0,0,0.25);padding:1px 5px;border-radius:3px;">.env</code> ' +
                        'and restart Flask. (See <code style="background:rgba(0,0,0,0.25);padding:1px 5px;border-radius:3px;">.env.example</code>.)';
    }
    bar.appendChild(status);

    if (configured) {
      const btn = document.createElement("button");
      btn.textContent = "Fix all with AI";
      btn.addEventListener("click", () => runAutoFix(btn, bar));
      bar.appendChild(btn);
    }

    // Insert at the top of the extras host, above the bursting/prompts/audit sections
    host.insertBefore(bar, host.firstChild);
  }

  async function runAutoFix(btn, bar) {
    btn.disabled = true;
    btn.textContent = "Running…";
    let progress = bar.querySelector(".o2s-ai-auto-progress");
    if (!progress) {
      progress = document.createElement("div");
      progress.className = "o2s-ai-auto-progress";
      bar.appendChild(progress);
    }
    progress.textContent = "Calling Claude…";

    try {
      const res = await fetch("/api/auto-fix", { method: "POST" });
      const j = await res.json();
      if (!res.ok || j.error) throw new Error(j.error || "auto-fix failed");
      const sum = j.summary || {};
      progress.textContent =
        `Done: applied ${sum.applied || 0} / ${sum.total || 0}. ` +
        `Rejected ${sum.rejected || 0}, failed ${sum.failed || 0} ` +
        `(model: ${sum.model || "?"}).`;
      // Surface the first failure reason so the user sees what's wrong
      const firstFail = (j.results || []).find(r => !r.applied && r.error);
      if (firstFail) {
        const errLine = document.createElement("div");
        errLine.className = "o2s-ai-auto-progress";
        errLine.style.cssText = "color: #ffe4e4; font-weight: 600;";
        errLine.textContent = `First failure: ${firstFail.error.slice(0, 200)}`;
        bar.appendChild(errLine);
      }
      btn.textContent = "Done ✓";

      // Tag each prompt card with applied/failed status if app.js's renderExtrasTab
      // is present and exposes the cards.
      (j.results || []).forEach(r => {
        // Best-effort matching by name in card summary text
        const cards = $$(".extras-prompt");
        cards.forEach(c => {
          const summary = c.querySelector("summary");
          if (!summary) return;
          if (summary.textContent.includes(r.name || "__none__")) {
            const tag = document.createElement("span");
            tag.style.cssText = "margin-left:8px;font-size:10px;padding:2px 7px;border-radius:999px;";
            if (r.applied) {
              tag.textContent = "Auto-applied ✓";
              tag.style.background = "#2bb673"; tag.style.color = "#fff";
            } else {
              tag.textContent = "AI failed";
              tag.title = r.error || "";
              tag.style.background = "#e04b4b"; tag.style.color = "#fff";
            }
            // Avoid duplicate tags
            if (!summary.querySelector(".o2s-ai-applied-tag")) {
              tag.classList.add("o2s-ai-applied-tag");
              summary.appendChild(tag);
            }
          }
        });
      });

      // Show a toast if app.js's toast() is available
      if (typeof window.toast === "function") {
        window.toast(`Auto-AI applied ${sum.applied || 0}/${sum.total || 0}`, "ok");
      }
    } catch (err) {
      progress.textContent = "Error: " + (err.message || err);
      btn.textContent = "Retry";
      btn.disabled = false;
    }
  }

  // Mount when conversion completes (poll for #extras-host content)
  let mountObserver = null;
  function startObserving() {
    const host = document.getElementById("extras-host");
    if (!host) return;
    if (mountObserver) return;
    mountObserver = new MutationObserver(() => {
      // Whenever app.js re-renders the extras tab, re-inject our bar.
      if (host.children.length > 0 && !host.querySelector(".o2s-ai-auto-bar")) {
        injectBar();
      }
    });
    mountObserver.observe(host, { childList: true, subtree: false });
  }

  async function init() {
    ensureStyles();
    await checkStatus();
    startObserving();
    // Also try once at load in case data is already there
    setTimeout(() => {
      const host = document.getElementById("extras-host");
      if (host && host.children.length > 0) injectBar();
    }, 500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
