(function () {
  function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }
  function nowIso(){ return new Date().toISOString(); }

  function getPseudoUserId(){
    const key = "dap_pseudo_user_id";
    let v = localStorage.getItem(key);
    if(!v){
      v = `u_${Math.random().toString(36).slice(2)}_${Date.now()}`;
      localStorage.setItem(key, v);
    }
    return v;
  }

  async function postEvent(serverBase, payload){
    try{
      await fetch(`${serverBase}/api/events`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify(payload),
        keepalive: true
      });
    }catch{}
  }

  function matchPage(pages){
    const url = window.location.href;
    for(const p of pages || []){
      try{
        const re = new RegExp(p.url_regex);
        if(re.test(url)) return p.page_id;
      }catch{}
    }
    return null;
  }

  function getValue(el){
    if(!el) return "";
    const tag = (el.tagName || "").toLowerCase();
    if(tag === "input" || tag === "textarea" || tag === "select") return (el.value || "").trim();
    return (el.textContent || "").trim();
  }

  // --- anchors ---
  function findByAttr(expr){
    const [k, ...rest] = expr.split("=");
    const v = rest.join("=");
    if(!k || !v) return null;
    return document.querySelector(`[${CSS.escape(k)}="${CSS.escape(v)}"]`);
  }
  function findByAria(val){
    return document.querySelector(`[aria-label="${CSS.escape(val)}"]`);
  }
  function findByLabelText(labelText){
    const labels = Array.from(document.querySelectorAll("label"));
    const label = labels.find(l => (l.textContent || "").trim() === labelText);
    if(!label) return null;
    const forId = label.getAttribute("for");
    if(forId){
      const el = document.getElementById(forId);
      if(el) return el;
    }
    return label.querySelector("input, textarea, select") || null;
  }
  function findByTextButton(txt){
    const buttons = Array.from(document.querySelectorAll("button,[role='button'],input[type='button'],input[type='submit']"));
    return buttons.find(b => ((b.innerText || b.value || "").trim() === txt)) || null;
  }
  function resolveAnchor(a){
    const {strategy, value} = a;
    try{
      if(strategy === "attr") return findByAttr(value);
      if(strategy === "aria") return findByAria(value);
      if(strategy === "label_text") return findByLabelText(value);
      if(strategy === "text_button") return findByTextButton(value);
      if(strategy === "css") return document.querySelector(value);
      if(strategy === "id") return document.getElementById(value);
      if(strategy === "name") return document.querySelector(`[name="${CSS.escape(value)}"]`);
    }catch{}
    return null;
  }
  async function resolveTarget(def, timeoutMs=6000){
    const start = Date.now();
    while(Date.now() - start < timeoutMs){
      for(const a of (def.anchors || [])){
        const el = resolveAnchor(a);
        if(el) return el;
      }
      await sleep(200);
    }
    return null;
  }

  // --- Rules ---
  function evalTextQuality(val, params){
    const minChars = params.min_chars ?? 0;
    const forbidden = params.forbidden_phrases ?? [];
    const mustAny = params.must_include_any ?? [];
    if(val.length < minChars) return {ok:false, reason:`min_chars:${minChars}`};
    for(const f of forbidden){
      if(val.includes(f)) return {ok:false, reason:`forbidden:${f}`};
    }
    if(mustAny.length){
      const hit = mustAny.some(k => val.includes(k));
      if(!hit) return {ok:false, reason:`must_include_any`};
    }
    return {ok:true};
  }

  function evalMissingOrGeneric(val, params){
    const generic = params.generic_values ?? ["不明","未定","TBD","顧客"];
    if(!val) return {ok:false, reason:"empty"};
    if(generic.includes(val)) return {ok:false, reason:`generic:${val}`};
    return {ok:true};
  }

  function evalAnyOfTargetsMissing(getTargetValueById, params){
    const required = params.required_target_ids || [];
    const missing = [];
    for(const tid of required){
      const v = (getTargetValueById(tid) || "").trim();
      if(!v) missing.push(tid);
    }
    if(missing.length) return {ok:false, reason:`missing:${missing.join(",")}`, missing};
    return {ok:true};
  }

  function runRule(rule, ctx){
    if(!rule) return {ok:true};
    const params = rule.params || {};
    if(rule.type === "text_quality") return evalTextQuality(ctx.value || "", params);
    if(rule.type === "missing_or_generic") return evalMissingOrGeneric(ctx.value || "", params);
    if(rule.type === "any_of_targets_missing") return evalAnyOfTargetsMissing(ctx.getTargetValueById, params);
    return {ok:true};
  }

  // --- Tone mapping for badge ---
  function toneBadge(tone){
    if(tone === "strict_risk") return "リスク観点";
    if(tone === "senior_supportive") return "先輩のヒント";
    return "ガイド";
  }

  class DapEngine {
    constructor({config, serverBase, appKey, env, etag}){
      this.config = config;
      this.serverBase = serverBase;
      this.appKey = appKey;
      this.env = env;
      this.etag = etag;

      this.pageId = null;
      this.rules = new Map();     // rule_id -> rule
      this.targets = new Map();   // target_id -> {def, el}
      this.interventions = [];

      this.blockMap = new Map();  // target_id -> {until, reason}
      this.enabled = true;
      this.pseudoUserId = getPseudoUserId();
      this._mutationObserver = null;
    }

    async init(){
      this.pageId = matchPage(this.config.pages || []);
      if(!this.pageId) return;

      (this.config.rules || []).forEach(r => this.rules.set(r.rule_id, r));
      this.interventions = (this.config.interventions || []).filter(iv => iv.page_id === this.pageId);

      const defs = (this.config.targets || []).filter(t => t.page_id === this.pageId);
      for(const d of defs){
        const el = await resolveTarget(d);
        this.targets.set(d.target_id, {def:d, el});
      }

      this.attachListeners();

      await postEvent(this.serverBase, {
        ts: nowIso(),
        app_key: this.appKey,
        page_id: this.pageId,
        user_pseudo_id: this.pseudoUserId,
        event_type: "outcome",
        event_name: "engine_ready",
        context: { env: this.env, etag: this.etag, targets: defs.length }
      });
    }

    attachListeners(){
      document.addEventListener("focusout", (e)=>{
        if(!this.enabled) return;
        this.handleFieldEvent("field_blur", e.target);
      }, true);

      document.addEventListener("change", (e)=>{
        if(!this.enabled) return;
        this.handleFieldEvent("field_change", e.target);
      }, true);

      // capture click for attempt_action + blocking
      document.addEventListener("click", (e)=>{
        if(!this.enabled) return;
        this.handleAttemptAction(e);
      }, true);

      this._mutationObserver = new MutationObserver(()=> this.refreshMissingTargets());
      this._mutationObserver.observe(document.documentElement, {childList:true, subtree:true});
    }

    async refreshMissingTargets(){
      for(const [tid, obj] of this.targets.entries()){
        if(obj.el && document.contains(obj.el)) continue;
        const el = await resolveTarget(obj.def, 1200);
        if(el) this.targets.set(tid, {def: obj.def, el});
      }
    }

    targetIdFromElement(el){
      for(const [tid, obj] of this.targets.entries()){
        if(obj.el === el) return tid;
      }
      return null;
    }

    getRule(ruleId){
      return ruleId ? (this.rules.get(ruleId) || null) : null;
    }

    getTargetValueById(tid){
      const t = this.targets.get(tid);
      return getValue(t?.el);
    }

    async handleFieldEvent(triggerType, element){
      const tid = this.targetIdFromElement(element);
      if(!tid) return;

      for(const iv of this.interventions){
        const trg = iv.trigger || {};
        if(trg.type !== triggerType) continue;
        if((trg.target_id || trg.target_key) !== tid) continue;
        await this.evaluateAndRun(iv, {eventTarget: tid});
      }
    }

    async handleAttemptAction(e){
      const tid = this.targetIdFromElement(e.target);
      if(!tid) return;

      // if already blocked
      const blk = this.blockMap.get(tid);
      if(blk && blk.until > Date.now()){
        e.preventDefault(); e.stopPropagation();
        window.DAPOverlay.tooltip(
          e.target,
          "先輩より",
          blk.reason || "この操作の前に入力を整えよう。",
          { badge: "実行前チェック", primaryText:"了解" }
        );
        await postEvent(this.serverBase, {
          ts: nowIso(), app_key: this.appKey, page_id: this.pageId, user_pseudo_id: this.pseudoUserId,
          event_type: "friction", event_name: "blocked_click",
          context: { target_id: tid, reason: blk.reason || "" }
        });
        return;
      }

      for(const iv of this.interventions){
        const trg = iv.trigger || {};
        if(trg.type !== "attempt_action") continue;
        if((trg.target_id || trg.target_key) !== tid) continue;

        const ran = await this.evaluateAndRun(iv, {attemptTarget: tid});
        if(ran && iv.block_action){
          e.preventDefault(); e.stopPropagation();
        }
      }
    }

    async evaluateAndRun(iv, meta){
      const rule = this.getRule(iv.rule_id);
      const coach = iv.coach || {};
      const tone = coach.tone || "neutral";
      const opening = coach.opening || "少しだけ整えよう。";

      // Build ctx for rule
      const ctx = {
        value: "",
        getTargetValueById: (tid) => this.getTargetValueById(tid),
      };

      // Use field_target_id if provided
      if(rule?.params?.field_target_id){
        ctx.value = this.getTargetValueById(rule.params.field_target_id);
      } else if (iv.trigger?.target_id || iv.trigger?.target_key){
        ctx.value = this.getTargetValueById(iv.trigger.target_id || iv.trigger.target_key);
      }

      const r = runRule(rule, ctx);
      if(r.ok) return false;

      // Telemetry: fired
      await postEvent(this.serverBase, {
        ts: nowIso(),
        app_key: this.appKey,
        page_id: this.pageId,
        user_pseudo_id: this.pseudoUserId,
        event_type: "intervention",
        event_name: "fired",
        context: {
          intervention_id: iv.id,
          rule_id: iv.rule_id,
          reason: r.reason || "",
          missing: r.missing || [],
          meta
        }
      });

      // Execute actions
      window.DAPOverlay.clearAll();

      for(const act of (iv.actions || [])){
        const type = act.type;
        const params = act.params || {};

        if(type === "highlight"){
          const t = this.targets.get(params.target_id);
          if(t?.el) window.DAPOverlay.highlight(t.el);
        }

        if(type === "tooltip"){
          const t = this.targets.get(params.target_id);
          if(t?.el){
            window.DAPOverlay.highlight(t.el);
            window.DAPOverlay.tooltip(
              t.el,
              "先輩より",
              `${opening}\n\n${params.content || ""}`,
              { badge: toneBadge(tone), primaryText: params.primaryText || "OK" }
            );
          }
        }

        if(type === "checklist"){
          window.DAPOverlay.panel(
            params.title || "チェック",
            params.items || [],
            { subtitle: opening }
          );
        }

        if(type === "insert_template"){
          const t = this.targets.get(params.target_id);
          if(t?.el){
            window.DAPOverlay.highlight(t.el);
            window.DAPOverlay.tooltip(
              t.el,
              "先輩より",
              `${opening}\n\nテンプレを挿入する？`,
              {
                badge: toneBadge(tone),
                primaryText: "挿入する",
                secondaryText: "あとで",
                onPrimary: () => window.DAPOverlay.insertTemplateInto(t.el, params.template || "")
              }
            );
          }
        }

        if(type === "block_action"){
          const actionTid = params.target_id;
          const ttlMs = params.ttl_ms ?? 8000;
          this.blockMap.set(actionTid, {
            until: Date.now() + ttlMs,
            reason: params.reason || "必要項目が不足しています。先に入力を整えてください。"
          });
        }
      }

      return true;
    }
  }

  window.DAPEngine = { DapEngine };
})();
