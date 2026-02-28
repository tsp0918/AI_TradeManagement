(function () {
  const STYLE_ID = "dap-coach-style";
  const ROOT_ID  = "dap-coach-root";

  // ── rAF tracking ────────────────────────────────────────────
  let _rafId = null;
  const _cbs = new Set();

  function _addCb(fn)  { _cbs.add(fn); if (!_rafId) _rafId = requestAnimationFrame(_loop); }
  function _delCb(fn)  { _cbs.delete(fn); if (!_cbs.size && _rafId) { cancelAnimationFrame(_rafId); _rafId = null; } }
  function _clearCbs() { _cbs.clear(); if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; } }
  function _loop()     { _cbs.forEach(fn => { try { fn(); } catch {} }); _rafId = _cbs.size ? requestAnimationFrame(_loop) : null; }

  // elの位置をrAFで追跡し、box のスタイルを更新する
  function trackBox(el, box, pad) {
    pad = pad ?? 4;
    const fn = () => {
      const r = el.getBoundingClientRect();
      box.style.left   = `${r.left   - pad}px`;
      box.style.top    = `${r.top    - pad}px`;
      box.style.width  = `${Math.max(8, r.width)  + pad * 2}px`;
      box.style.height = `${Math.max(8, r.height) + pad * 2}px`;
    };
    fn();
    _addCb(fn);
    return fn;
  }

  // elの隣にpanelを配置し、rAFで追跡する
  function trackNear(el, panel) {
    const fn = () => {
      const r   = el.getBoundingClientRect();
      const vw  = window.innerWidth;
      const vh  = window.innerHeight;
      const pad = 10;
      let left  = r.right + pad;
      let top   = r.top;
      if (left + 410 > vw) { left = r.left; top = r.bottom + pad; }
      left = Math.min(Math.max(10, left), vw - 420);
      top  = Math.min(Math.max(10, top),  vh - 240);
      panel.style.left = `${left}px`;
      panel.style.top  = `${top}px`;
    };
    fn();
    _addCb(fn);
    return fn;
  }

  // ── Styles ──────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
/* ── キーフレーム ── */
@keyframes dap-pop-in {
  0%   { opacity:0; transform: scale(0.86) translateY(-8px); }
  60%  { transform: scale(1.03) translateY(0); }
  100% { opacity:1; transform: scale(1) translateY(0); }
}
@keyframes dap-pulse-ring {
  0%   { box-shadow: 0 0 0 0   rgba(140,106,80,0.50), 0 0 0 4px rgba(140,106,80,0.20); }
  60%  { box-shadow: 0 0 0 10px rgba(140,106,80,0),   0 0 0 4px rgba(140,106,80,0.12); }
  100% { box-shadow: 0 0 0 0   rgba(140,106,80,0),   0 0 0 4px rgba(140,106,80,0.08); }
}

#${ROOT_ID}{
  position:fixed; inset:0; pointer-events:none; z-index:2147483647;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans JP", sans-serif;
}

/* ── スポットライト（el以外を暗転）── */
.dap-spotlight{
  position:absolute; border-radius:10px;
  box-shadow: 0 0 0 9999px rgba(20,12,4,0.60);
  pointer-events:none;
}

/* ── ハイライト ── */
.dap-highlight{
  position:absolute;
  border: 2px solid rgba(140,106,80,0.9);
  border-radius: 10px;
  background: rgba(196,160,136,0.06);
  animation: dap-pulse-ring 0.9s ease-out 2;
}

/* ── ツールチップ ── */
.dap-tooltip{
  position:absolute; max-width:390px; pointer-events:auto;
  background: #FDFBF8;
  color: #2C1E12;
  border: 1px solid #DDD3C5;
  border-radius: 12px;
  box-shadow: 0 20px 48px rgba(44,30,18,0.18), 0 2px 8px rgba(44,30,18,0.08);
  padding: 13px 13px 11px;
  animation: dap-pop-in 0.22s cubic-bezier(0.22,1,0.36,1) both;
}
.dap-tooltip h4{ margin:0 0 6px 0; font-size:14px; font-weight:600; letter-spacing:0.1px; color:#2C1E12; }
.dap-tooltip p{ margin:0; font-size:13px; line-height:1.5; white-space:pre-wrap; color:#5A4A3A; }

/* ── アクションボタン ── */
.dap-actions{ margin-top:11px; display:flex; gap:8px; justify-content:flex-end; }
.dap-btn{
  pointer-events:auto; border:1px solid #C4B5A2; background:#F7F3EE;
  border-radius:8px; padding:6px 12px; font-size:12px; cursor:pointer;
  font-weight:600; color:#5A4A3A; transition: background 0.12s, color 0.12s;
}
.dap-btn:hover{ background:#EDE6DC; }
.dap-btn-primary{ background:#8C6A50; color:#FDFBF8; border-color:#8C6A50; }
.dap-btn-primary:hover{ background:#7A5A42; }

/* ── パネル（チェックリスト用） ── */
.dap-panel{
  position:absolute; width:390px; pointer-events:auto;
  background: #FDFBF8; color:#2C1E12;
  border:1px solid #DDD3C5; border-radius:14px;
  box-shadow: 0 20px 48px rgba(44,30,18,0.18);
  padding: 13px;
  animation: dap-pop-in 0.22s cubic-bezier(0.22,1,0.36,1) both;
}
.dap-panel h3{ margin:0 0 8px 0; font-size:14px; font-weight:600; color:#2C1E12; }
.dap-list{ margin:6px 0 0; padding-left:18px; font-size:13px; line-height:1.6; color:#5A4A3A; }
.dap-list li{ margin-bottom:3px; }
.dap-small{ font-size:12px; color:#9C8A78; margin-bottom:6px; }
.dap-topbar{ display:flex; justify-content:space-between; align-items:center; gap:8px; }
.dap-close{ background:transparent; border:none; font-size:18px; cursor:pointer; line-height:1; color:#9C8A78; }
.dap-close:hover{ color:#2C1E12; }

/* ── バッジ ── */
.dap-badge{
  display:inline-block; font-size:10px; padding:2px 7px; border-radius:999px;
  background: #F7EFE6; border:1px solid #DDD3C5; color:#8C6A50;
  margin-left:6px; font-weight:600; vertical-align:middle;
}
`;
    document.head.appendChild(style);
  }

  function ensureRoot() {
    injectStyles();
    let root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement("div");
      root.id = ROOT_ID;
      document.documentElement.appendChild(root);
    }
    return root;
  }

  // ── Public API ───────────────────────────────────────────────

  function clearAll() {
    _clearCbs();
    ensureRoot().innerHTML = "";
  }

  /** ハイライト枠（rAFで追跡、スクロールに追随） */
  function highlight(el) {
    const root = ensureRoot();
    const box  = document.createElement("div");
    box.className = "dap-highlight";
    root.appendChild(box);
    const fn = trackBox(el, box, 4);
    return () => { _delCb(fn); box.remove(); };
  }

  /** スポットライト（el以外を暗転する背景、rAFで追跡） */
  function spotlight(el) {
    const root = ensureRoot();
    const spot = document.createElement("div");
    spot.className = "dap-spotlight";
    root.appendChild(spot);
    const fn = trackBox(el, spot, 8);
    return () => { _delCb(fn); spot.remove(); };
  }

  /** ツールチップ（elの横に表示、rAFで追跡）
   *  secondaryText=null のときは「閉じる」ボタンを出さない
   */
  function tooltip(el, title, message, { badge, onPrimary, onSecondary, primaryText = "OK", secondaryText = null } = {}) {
    const root = ensureRoot();
    const tip  = document.createElement("div");
    tip.className = "dap-tooltip";

    const h = document.createElement("h4");
    h.textContent = title || "先輩より";
    if (badge) {
      const b = document.createElement("span");
      b.className   = "dap-badge";
      b.textContent = badge;
      h.appendChild(b);
    }

    const p = document.createElement("p");
    p.textContent = message || "";

    const actions = document.createElement("div");
    actions.className = "dap-actions";

    const fn = trackNear(el, tip);
    function dismiss() { _delCb(fn); tip.remove(); }

    if (secondaryText) {
      const btn2 = document.createElement("button");
      btn2.className   = "dap-btn";
      btn2.textContent = secondaryText;
      btn2.onclick = () => { dismiss(); onSecondary && onSecondary(); };
      actions.appendChild(btn2);
    }

    const btn1 = document.createElement("button");
    btn1.className   = "dap-btn dap-btn-primary";
    btn1.textContent = primaryText;
    btn1.onclick = () => { dismiss(); onPrimary && onPrimary(); };
    actions.appendChild(btn1);

    tip.appendChild(h);
    tip.appendChild(p);
    tip.appendChild(actions);
    root.appendChild(tip);
    return dismiss;
  }

  /** パネル（固定位置・右上）— 通常のチェックリスト用 */
  function panel(title, items = [], { subtitle, onClose } = {}) {
    const root = ensureRoot();
    const p    = document.createElement("div");
    p.className  = "dap-panel";
    p.style.right = "16px";
    p.style.top   = "16px";
    p.style.left  = "unset";

    const top = document.createElement("div");
    top.className = "dap-topbar";

    const h = document.createElement("h3");
    h.textContent = title || "チェック";

    const close = document.createElement("button");
    close.className   = "dap-close";
    close.textContent = "×";
    close.onclick = () => { p.remove(); onClose && onClose(); };

    top.appendChild(h);
    top.appendChild(close);
    p.appendChild(top);

    if (subtitle) {
      const s = document.createElement("div");
      s.className   = "dap-small";
      s.textContent = subtitle;
      p.appendChild(s);
    }

    if (items.length) {
      const ul = document.createElement("ul");
      ul.className = "dap-list";
      for (const it of items) {
        const li = document.createElement("li");
        li.textContent = it;
        ul.appendChild(li);
      }
      p.appendChild(ul);
    }

    root.appendChild(p);
    return () => p.remove();
  }

  /** パネル（elの横に追跡表示）— page_load フェーズ2用 */
  function panelNear(el, title, items = [], { subtitle, onClose } = {}) {
    const root = ensureRoot();
    const p    = document.createElement("div");
    p.className = "dap-panel";

    const top = document.createElement("div");
    top.className = "dap-topbar";

    const h = document.createElement("h3");
    h.textContent = title || "チェック";

    const fn = trackNear(el, p);

    const close = document.createElement("button");
    close.className   = "dap-close";
    close.textContent = "×";
    close.onclick = () => { _delCb(fn); p.remove(); onClose && onClose(); };

    top.appendChild(h);
    top.appendChild(close);
    p.appendChild(top);

    if (subtitle) {
      const s = document.createElement("div");
      s.className   = "dap-small";
      s.textContent = subtitle;
      p.appendChild(s);
    }

    if (items.length) {
      const ul = document.createElement("ul");
      ul.className = "dap-list";
      for (const it of items) {
        const li = document.createElement("li");
        li.textContent = it;
        ul.appendChild(li);
      }
      p.appendChild(ul);
    }

    root.appendChild(p);
    return () => { _delCb(fn); p.remove(); };
  }

  function insertTemplateInto(el, template) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag !== "input" && tag !== "textarea") return false;

    el.focus();
    const cur = el.value || "";
    el.value  = cur ? (cur + "\n" + template) : template;

    el.dispatchEvent(new Event("input",  { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  window.DAPOverlay = { clearAll, highlight, spotlight, tooltip, panel, panelNear, insertTemplateInto };
})();
