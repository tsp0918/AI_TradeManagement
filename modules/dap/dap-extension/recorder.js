(function () {
  const SERVER_BASE = "http://localhost:8010";
  const APP_KEY = "rd_risk_local"; // content.js で APP_KEY が動的に決まるため Recorder はデフォルト固定

  let recorderOn = false;

  function toast(msg){
    console.log("[DAP/Recorder]", msg);
  }

  function pickAnchors(el){
    const anchors = [];

    // 1) data-dap-* があれば最優先
    const dapField = el.getAttribute?.("data-dap-field");
    const dapAction = el.getAttribute?.("data-dap-action");
    if(dapField) anchors.push({strategy:"attr", value:`data-dap-field=${dapField}`});
    if(dapAction) anchors.push({strategy:"attr", value:`data-dap-action=${dapAction}`});

    // 2) name / id
    const name = el.getAttribute?.("name");
    const id = el.getAttribute?.("id");
    if(name) anchors.push({strategy:"name", value:name});
    if(id) anchors.push({strategy:"id", value:id});

    // 3) aria-label
    const aria = el.getAttribute?.("aria-label");
    if(aria) anchors.push({strategy:"aria", value:aria});

    // 4) label text (best effort)
    // find closest label
    try{
      const labels = Array.from(document.querySelectorAll("label"));
      const byFor = id ? labels.find(l => l.getAttribute("for") === id) : null;
      if(byFor){
        const txt = (byFor.textContent || "").trim();
        if(txt) anchors.push({strategy:"label_text", value:txt});
      } else {
        // nested
        const parentLabel = el.closest?.("label");
        if(parentLabel){
          const txt = (parentLabel.textContent || "").trim();
          if(txt) anchors.push({strategy:"label_text", value:txt});
        }
      }
    }catch{}

    // 5) CSS fallback (last resort)
    try{
      const css = cssPath(el);
      if(css) anchors.push({strategy:"css", value:css});
    }catch{}

    // dedupe
    const seen = new Set();
    return anchors.filter(a => {
      const k = `${a.strategy}:${a.value}`;
      if(seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  function cssPath(el){
    // Minimal but stable-ish css path:
    // prefer tag#id, else tag[name=], else tag:nth-of-type
    if(!el || !el.tagName) return "";
    const tag = el.tagName.toLowerCase();
    const id = el.getAttribute("id");
    if(id) return `${tag}#${CSS.escape(id)}`;

    const name = el.getAttribute("name");
    if(name) return `${tag}[name="${CSS.escape(name)}"]`;

    const cls = (el.getAttribute("class") || "").split(/\s+/).filter(Boolean)[0];
    if(cls) return `${tag}.${CSS.escape(cls)}`;

    // nth-of-type fallback within parent
    const parent = el.parentElement;
    if(!parent) return tag;

    const sameTag = Array.from(parent.children).filter(c => c.tagName === el.tagName);
    const idx = sameTag.indexOf(el) + 1;
    return `${tag}:nth-of-type(${idx})`;
  }

  function detectPageKeyFromUrl(){
    // MVP: 1ページ運用なら固定でOK。運用拡張時はここをURL別にマップ。
    return "rd_case_form";
  }

  window.addEventListener("keydown", (e)=>{
    if(e.shiftKey && e.altKey && (e.key === "r" || e.key === "R")){
      recorderOn = !recorderOn;
      toast(`Recorder ${recorderOn ? "ON" : "OFF"}`);
    }
  });

  document.addEventListener("click", async (e)=>{
    if(!recorderOn) return;

    // avoid capturing clicks on DAP overlay itself (we don't inject overlay in recorder)
    const el = e.target;
    if(!el) return;

    e.preventDefault();
    e.stopPropagation();

    const pageKey = detectPageKeyFromUrl();
    const defaultKey = "";
    const targetKey = prompt("Recorder: target_key を入力してください（例: t_enduse / t_enduser / t_run_assessment）", defaultKey);
    if(!targetKey) return;

    const description = prompt("description（任意）", "") || "";

    const anchors = pickAnchors(el);

    try{
      await fetch(`${SERVER_BASE}/api/recorder/target`,{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          app_key: APP_KEY,
          page_key: pageKey,
          target_key: targetKey,
          description,
          anchors
        })
      });
      toast(`Saved target: ${targetKey} (${anchors.length} anchors)`);
      alert(`Saved: ${targetKey}\nanchors=${anchors.length}\nAdminでTargetsをReloadして確認してください。`);
    }catch(err){
      console.warn("[DAP/Recorder] failed", err);
      alert("Recorder送信に失敗しました。DAPサーバ(8010)が起動しているか確認してください。");
    }
  }, true);
})();
