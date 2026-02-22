(function () {
  const STYLE_ID = "dap-coach-style";
  const ROOT_ID = "dap-coach-root";

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;

    // “頼れる先輩” = 目立ちすぎないが、確実に視線誘導できるトーン
    style.textContent = `
#${ROOT_ID}{ position:fixed; inset:0; pointer-events:none; z-index:2147483647; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;}
.dap-highlight{ position:absolute; border:2px solid rgba(17,17,17,0.85); border-radius:10px; box-shadow: 0 0 0 7px rgba(17,17,17,0.10); background: rgba(255,255,255,0.02);}
.dap-tooltip{ position:absolute; max-width:390px; pointer-events:auto; background:white; color:#111; border:1px solid rgba(0,0,0,0.14); border-radius:12px; box-shadow: 0 18px 40px rgba(0,0,0,0.18); padding:12px 12px 10px;}
.dap-tooltip h4{ margin:0 0 6px 0; font-size:14px; letter-spacing:0.2px;}
.dap-tooltip p{ margin:0; font-size:13px; line-height:1.45; white-space:pre-wrap;}
.dap-actions{ margin-top:10px; display:flex; gap:8px; justify-content:flex-end;}
.dap-btn{ pointer-events:auto; border:1px solid rgba(0,0,0,0.18); background:#fff; border-radius:10px; padding:7px 10px; font-size:12px; cursor:pointer; font-weight:800;}
.dap-btn-primary{ background:#111; color:#fff; border-color:#111;}
.dap-panel{ position:absolute; right:16px; top:16px; width:390px; pointer-events:auto; background:white; color:#111; border:1px solid rgba(0,0,0,0.14); border-radius:14px; box-shadow: 0 18px 40px rgba(0,0,0,0.18); padding:12px;}
.dap-panel h3{ margin:0 0 8px 0; font-size:14px;}
.dap-list{ margin:0; padding-left:18px; font-size:13px; line-height:1.5;}
.dap-small{ font-size:12px; color:#444; }
.dap-topbar{ display:flex; justify-content:space-between; align-items:center; gap:8px; }
.dap-close{ background:transparent; border:none; font-size:18px; cursor:pointer; line-height:1; }
.dap-badge{ display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px; background:rgba(0,0,0,0.06); color:#222; margin-left:6px;}
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

  function rectFor(el) {
    const r = el.getBoundingClientRect();
    return {
      left: r.left,
      top: r.top,
      width: r.width,
      height: r.height
    };
  }

  function placeNear(rect) {
    const pad = 10;
    let left = rect.left + rect.width + pad;
    let top = rect.top;

    const vw = window.innerWidth;
    const vh = window.innerHeight;

    if (left + 410 > vw) {
      left = rect.left;
      top = rect.top + rect.height + pad;
    }
    left = Math.min(Math.max(10, left), vw - 420);
    top = Math.min(Math.max(10, top), vh - 240);
    return { left, top };
  }

  function clearAll() {
    ensureRoot().innerHTML = "";
  }

  function highlight(el) {
    const root = ensureRoot();
    const rect = rectFor(el);
    const box = document.createElement("div");
    box.className = "dap-highlight";
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${Math.max(8, rect.width)}px`;
    box.style.height = `${Math.max(8, rect.height)}px`;
    root.appendChild(box);
    return () => box.remove();
  }

  function tooltip(el, title, message, { badge, onPrimary, onSecondary, primaryText="OK", secondaryText="閉じる" } = {}) {
    const root = ensureRoot();
    const rect = rectFor(el);
    const pos = placeNear(rect);
    const tip = document.createElement("div");
    tip.className = "dap-tooltip";
    tip.style.left = `${pos.left}px`;
    tip.style.top = `${pos.top}px`;

    const h = document.createElement("h4");
    h.textContent = title || "先輩より";
    if (badge) {
      const b = document.createElement("span");
      b.className = "dap-badge";
      b.textContent = badge;
      h.appendChild(b);
    }

    const p = document.createElement("p");
    p.textContent = message || "";

    const actions = document.createElement("div");
    actions.className = "dap-actions";

    const btn2 = document.createElement("button");
    btn2.className = "dap-btn";
    btn2.textContent = secondaryText;
    btn2.onclick = () => { tip.remove(); onSecondary && onSecondary(); };

    const btn1 = document.createElement("button");
    btn1.className = "dap-btn dap-btn-primary";
    btn1.textContent = primaryText;
    btn1.onclick = () => { tip.remove(); onPrimary && onPrimary(); };

    actions.appendChild(btn2);
    actions.appendChild(btn1);

    tip.appendChild(h);
    tip.appendChild(p);
    tip.appendChild(actions);

    root.appendChild(tip);
    return () => tip.remove();
  }

  function panel(title, items = [], { subtitle, onClose } = {}) {
    const root = ensureRoot();
    const p = document.createElement("div");
    p.className = "dap-panel";

    const top = document.createElement("div");
    top.className = "dap-topbar";

    const h = document.createElement("h3");
    h.textContent = title || "チェック";

    const close = document.createElement("button");
    close.className = "dap-close";
    close.textContent = "×";
    close.onclick = () => { p.remove(); onClose && onClose(); };

    top.appendChild(h);
    top.appendChild(close);
    p.appendChild(top);

    if (subtitle) {
      const s = document.createElement("div");
      s.className = "dap-small";
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

  function insertTemplateInto(el, template) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag !== "input" && tag !== "textarea") return false;

    el.focus();
    const cur = el.value || "";
    el.value = cur ? (cur + "\n" + template) : template;

    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  window.DAPOverlay = { clearAll, highlight, tooltip, panel, insertTemplateInto };
})();
