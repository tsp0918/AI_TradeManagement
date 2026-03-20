/* ============================================================
   DAP Coach Admin — State-managed SPA
   ============================================================ */

// ============================================================
// SECTION 1: Constants & Label Maps
// ============================================================

const TRIGGER_LABELS = {
  "field_blur":     "フォーカスアウト時",
  "field_focus":    "フォーカス取得時",
  "field_change":   "値変更時",
  "field_input":    "入力中（リアルタイム）",
  "attempt_action": "ボタン押下時",
  "page_load":      "ページ読込時",
};

const RULE_TYPE_OPTIONS = [
  { value: "always",                   label: "常に表示" },
  { value: "missing_or_generic",       label: "空欄または汎用値の場合" },
  { value: "text_quality",             label: "入力内容が不十分な場合" },
  { value: "any_of_targets_missing",   label: "必須項目が未入力の場合" },
];

const TONE_OPTIONS = [
  { value: "senior_supportive", label: "先輩（サポート）" },
  { value: "strict_risk",       label: "厳格（リスク）" },
  { value: "neutral",           label: "ニュートラル" },
];

const ACTION_TYPE_OPTIONS = [
  { value: "tooltip",         label: "💬 ツールチップ" },
  { value: "highlight",       label: "✨ ハイライト" },
  { value: "checklist",       label: "📋 チェックリスト" },
  { value: "insert_template", label: "📝 テンプレート挿入" },
  { value: "block_action",    label: "🚫 アクションブロック" },
];

const ACTION_ICONS = {
  "tooltip":         "💬",
  "highlight":       "✨",
  "checklist":       "📋",
  "insert_template": "📝",
  "block_action":    "🚫",
};

const ACTION_DESCRIPTIONS = {
  "tooltip":
    "要素のすぐ近くに吹き出し形式で表示されます。フォーカスアウト時やボタン押下時にふわっとポップアップし、ユーザーに気付きを与えます。",
  "highlight":
    "対象の入力欄を光る枠線（グロー効果）でハイライトします。視線を集めるだけなのでツールチップと組み合わせると効果的です。",
  "checklist":
    "画面にパネルとして箇条書きリストを表示します。入力に必要な複数の条件や確認事項を一覧で示せます。",
  "insert_template":
    "対象の入力欄に定型文を自動入力します。ユーザーはそれを参考・編集して使用できます。記述例を提供したい場合に最適です。",
  "block_action":
    "ボタンのクリックをブロックし警告を表示します。必須項目の記入が完了するまで先に進めなくするゲートとして使います。",
};

// ============================================================
// SECTION 2: Application State
// ============================================================

const state = {
  currentAppId:    null,
  currentAppKey:   null,
  apps:            [],
  pages:           [],
  targets:         new Map(),   // pageId -> DapTarget[]
  rules:           [],
  interventions:   new Map(),   // pageId -> DapIntervention[]
  publishedVersion: null,
  hasPendingChanges: false,     // true if edits made since last publish
  expandedCardId:  null,        // currently open card id
  expandedPageId:  null,        // which page the expanded card belongs to
  dirtyCard:       null,        // draft copy while editing
};

// ============================================================
// SECTION 3: API Layer
// ============================================================

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`${res.status}: ${t}`);
  }
  return res.json();
}

// ============================================================
// SECTION 4: Boot / Data Loading
// ============================================================

async function boot() {
  await loadApps();
  if (state.apps.length) {
    await setApp(state.apps[0].id, state.apps[0].app_key);
  }
}

async function loadApps() {
  const apps = await api("/api/apps");
  state.apps = apps;
  const sel = document.getElementById("appSelect");
  sel.innerHTML = "";
  apps.forEach(a => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = `${a.name}`;
    sel.appendChild(opt);
  });
  if (apps.length) sel.value = String(apps[0].id);
}

async function setApp(appId, appKey) {
  state.currentAppId  = appId;
  state.currentAppKey = appKey || state.apps.find(a => a.id === appId)?.app_key || "";
  state.expandedCardId = null;
  state.dirtyCard      = null;

  const [pages, rules, rel] = await Promise.all([
    api(`/api/pages?app_id=${appId}`),
    api(`/api/rules?app_id=${appId}`),
    api(`/api/releases/latest?app_id=${appId}&env=local`),
  ]);

  state.pages = pages;
  state.rules = rules;
  state.publishedVersion = rel.version || null;
  state.hasPendingChanges = false;
  updateDeployStatus();

  // Load targets + interventions for each page in parallel
  state.targets.clear();
  state.interventions.clear();
  await Promise.all(pages.map(p => loadPageData(p.id)));

  renderAllPages();
}

async function loadPageData(pageId) {
  const [targets, interventions] = await Promise.all([
    api(`/api/targets?page_id=${pageId}`),
    api(`/api/interventions?page_id=${pageId}`),
  ]);
  state.targets.set(pageId, targets);
  state.interventions.set(pageId, interventions);
}

// ============================================================
// SECTION 5: Deploy Status
// ============================================================

function updateDeployStatus() {
  const badge = document.getElementById("versionBadge");
  const bar   = document.getElementById("deployStatusBar");
  const btn   = document.getElementById("btnPublish");
  const pub   = state.publishedVersion;
  const dirty = state.hasPendingChanges;

  // --- Sidebar badge ---
  if (pub && !dirty) {
    badge.textContent = "● 配信中 v" + pub;
    badge.className   = "versionBadge isLive";
  } else if (pub && dirty) {
    badge.textContent = "⚠ v" + pub + " (変更あり)";
    badge.className   = "versionBadge hasPending";
  } else {
    badge.textContent = "○ 未配信";
    badge.className   = "versionBadge noPub";
  }

  // --- Publish button ---
  if (dirty || !pub) {
    btn.classList.add("needsPublish");
  } else {
    btn.classList.remove("needsPublish");
  }

  // --- Scenarios tab status bar ---
  if (!bar) return;
  if (pub && !dirty) {
    bar.className = "deployStatusBar isLive";
    bar.innerHTML = `<span class="dsIcon">🟢</span><span class="dsText">配信中 <strong>v${pub}</strong> — すべてのサポートがライブです</span>`;
  } else if (pub && dirty) {
    bar.className = "deployStatusBar isPending";
    bar.innerHTML = `<span class="dsIcon">🟡</span><span class="dsText">配信中 <strong>v${pub}</strong> — 未配信の変更があります <button class="dsPublishBtn" onclick="publish()">今すぐ Publish →</button></span>`;
  } else {
    bar.className = "deployStatusBar isDraft";
    bar.innerHTML = `<span class="dsIcon">⚫</span><span class="dsText">未配信 — サイドバーの <strong>Publish</strong> ボタンで配信してください</span>`;
  }
}

// ============================================================
// SECTION 6: Rendering — Pages & Scenario Cards
// ============================================================

function renderAllPages() {
  const container = document.getElementById("pagesContainer");
  container.innerHTML = "";

  if (!state.pages.length) {
    container.innerHTML = `
      <div class="emptyState">
        ページがありません。<br>
        <p>「ページを追加」から始めてください。</p>
      </div>`;
    return;
  }

  state.pages.forEach(page => {
    container.appendChild(buildPageSection(page));
  });
}

function buildPageSection(page) {
  const section = document.createElement("div");
  section.className = "pageSection";
  section.dataset.pageId = page.id;

  section.innerHTML = `
    <div class="pageHeader">
      <div class="pageHeaderLeft">
        <span class="pageIcon">📄</span>
        <span class="pageName">${esc(page.name || page.page_key)}</span>
        <span class="pageUrl">${esc(page.url_regex)}</span>
      </div>
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="btnGhost btnSm" data-action="addTarget" data-page-id="${page.id}"
          title="CSSセレクタでターゲットを登録">＋ ターゲット追加</button>
        <button class="btnIcon danger" data-action="deletePage" data-page-id="${page.id}"
          title="ページを削除">🗑</button>
      </div>
    </div>
    <div class="pageTargets" id="pageTargets_${page.id}">${buildTargetsHTML(page.id)}</div>
    <div class="interventionList" data-page-id="${page.id}"></div>
    <button class="btnAddIntervention" data-action="addIntervention" data-page-id="${page.id}">
      ＋ 新しいガイダンスを追加
    </button>`;

  fillInterventionList(section.querySelector(".interventionList"), page.id);
  return section;
}

function buildTargetsHTML(pageId) {
  const targets = state.targets.get(pageId) || [];
  if (!targets.length) {
    return `<div class="emptyTargets">ターゲット未登録 — 「＋ ターゲット追加」または Recorder (Shift+Alt+R) で登録してください</div>`;
  }
  return `<div class="targetsGrid">${targets.map(t => `
    <div class="targetChip">
      <span class="targetChipIcon">🎯</span>
      <span class="targetChipKey">${esc(t.target_key)}</span>
      <span class="targetChipDesc">${esc(t.description || "")}</span>
      <button class="btnIconXs danger" data-action="deleteTarget"
        data-target-id="${t.id}" data-page-id="${pageId}" title="削除">✕</button>
    </div>`).join("")}</div>`;
}

function fillInterventionList(listEl, pageId) {
  listEl.innerHTML = "";
  const ivs = state.interventions.get(pageId) || [];
  if (!ivs.length) {
    listEl.innerHTML = `<div class="emptyInterventions">ガイダンスがまだありません</div>`;
    return;
  }
  ivs.forEach(iv => {
    listEl.appendChild(buildCard(iv, pageId));
  });
}

function rerenderPageSection(pageId) {
  const section = document.querySelector(`.pageSection[data-page-id="${pageId}"]`);
  if (!section) return;
  const listEl = section.querySelector(".interventionList");
  fillInterventionList(listEl, pageId);
}

function buildCard(iv, pageId) {
  const card = document.createElement("div");
  card.className = "interventionCard" + (iv.id === state.expandedCardId ? " isExpanded" : "");
  card.dataset.ivId   = iv.id;
  card.dataset.pageId = pageId;

  if (iv.id === state.expandedCardId && state.dirtyCard) {
    card.innerHTML = buildExpandedHTML(state.dirtyCard, pageId);
  } else {
    card.innerHTML = buildCollapsedHTML(iv, pageId);
  }
  return card;
}

// ---- Collapsed card HTML ----
function buildCollapsedHTML(iv, pageId) {
  const trigger  = iv.trigger || {};
  const ruleType = resolveRuleTypeFromKey(iv.rule_key);
  const targets  = state.targets.get(pageId) || [];
  const targetDesc = targets.find(t => t.target_key === trigger.target_id)?.description
                     || trigger.target_id || "";

  const actionBadges = (iv.actions || []).map(a =>
    `<span class="bdg bdg-action">${ACTION_ICONS[a.type] || ""} ${ACTION_TYPE_OPTIONS.find(o=>o.value===a.type)?.label || a.type}</span>`
  ).join("");

  const triggerLabel = TRIGGER_LABELS[trigger.type] || trigger.type || "";
  const ruleLabel    = RULE_TYPE_OPTIONS.find(o => o.value === ruleType)?.label || "常に表示";

  return `
    <div class="cardSummaryRow">
      <span class="cardIcon">${actionIconFor(iv)}</span>
      <span class="cardName">${esc(iv.name || iv.intervention_key)}</span>
      <button class="btnIcon btnEdit" data-action="editCard" data-iv-id="${iv.id}" data-page-id="${pageId}"
        title="編集">✏️ 編集</button>
      <button class="btnIcon danger" data-action="deleteCard" data-iv-id="${iv.id}" data-page-id="${pageId}"
        title="削除">🗑</button>
    </div>
    <div class="cardMeta">
      ${targetDesc ? `<span class="metaTarget">🎯 ${esc(targetDesc)}</span>` : ""}
      ${triggerLabel ? `<span class="bdg bdg-trigger">${triggerLabel}</span>` : ""}
      <span class="bdg bdg-rule">${ruleLabel}</span>
    </div>
    ${actionBadges ? `<div class="cardActionSummary">${actionBadges}</div>` : ""}`;
}

function actionIconFor(iv) {
  const types = (iv.actions || []).map(a => a.type);
  if (types.includes("tooltip"))         return "💬";
  if (types.includes("checklist"))       return "📋";
  if (types.includes("insert_template")) return "📝";
  if (types.includes("block_action"))    return "🚫";
  if (types.includes("highlight"))       return "✨";
  return "📌";
}

// ---- Expanded card HTML ----
function buildExpandedHTML(dirty, pageId) {
  const targets = state.targets.get(pageId) || [];
  const triggerType    = dirty.triggerType    || (dirty.trigger?.type)      || "field_blur";
  const triggerTargetId = dirty.triggerTargetId || dirty.trigger?.target_id  || "";
  const triggerDelayMs  = dirty.triggerDelayMs  ?? (dirty.trigger?.delay_ms) ?? 0;
  const ruleType  = dirty._ruleType || "always";
  const tone      = dirty.coachTone || (dirty.coach?.tone) || "senior_supportive";
  const opening   = dirty.coachOpening || (dirty.coach?.opening) || "";
  const name      = dirty._name || dirty.name || "";

  // Target options
  const targetOpts = targets.map(t =>
    `<option value="${esc(t.target_key)}" ${triggerTargetId === t.target_key ? "selected" : ""}>${esc(t.description || t.target_key)}</option>`
  ).join("");

  // Trigger radios
  const triggerRadios = [
    { value: "field_blur",     label: "フォーカスアウト時",       hint: "入力欄からフォーカスが外れた瞬間" },
    { value: "field_focus",    label: "フォーカス取得時",         hint: "入力欄をクリック・Tabで入った瞬間" },
    { value: "field_change",   label: "値変更時",                 hint: "値が確定して変わった瞬間（select / checkbox 向き）" },
    { value: "field_input",    label: "入力中（リアルタイム）",   hint: "キー入力ごとに評価（feedback 遅延推奨）" },
    { value: "attempt_action", label: "ボタン押下時",             hint: "送信・実行ボタンのクリック直前" },
    { value: "page_load",      label: "ページ読込時",             hint: "ページを開いた直後に自動発動" },
  ].map(opt => `
    <label class="radioLabel" title="${esc(opt.hint)}">
      <input type="radio" name="triggerType_${dirty.id}" value="${opt.value}"
        ${triggerType === opt.value ? "checked" : ""} data-field="triggerType">
      ${opt.label}
    </label>`).join("");

  // Rule radios
  const ruleRadios = RULE_TYPE_OPTIONS.map(opt => `
    <label class="radioLabel">
      <input type="radio" name="ruleType_${dirty.id}" value="${opt.value}"
        ${ruleType === opt.value ? "checked" : ""} data-field="_ruleType">
      ${opt.label}
    </label>`).join("");

  // Tone select
  const toneOpts = TONE_OPTIONS.map(o =>
    `<option value="${o.value}" ${tone === o.value ? "selected" : ""}>${o.label}</option>`
  ).join("");

  // Actions
  const actionsHTML = (dirty.actions || []).map((action, idx) =>
    buildActionRowHTML(action, idx, pageId)
  ).join("");

  const showTargetSelector = triggerType !== "page_load";

  return `
    <div class="expandedTitle">ガイダンスを編集</div>
    <div class="cardEditLayout">

      <!-- ===== LEFT: EDIT FORM ===== -->
      <div class="cardEditForm expandedForm">

        <div class="formSection">
          <div class="formSectionTitle">名前</div>
          <input class="input" data-field="_name" placeholder="ガイダンス名"
            value="${esc(name)}">
        </div>

        ${showTargetSelector ? `
        <div class="formSection" id="targetSection_${dirty.id}">
          <div class="formSectionTitle">🎯 対象要素</div>
          ${targets.length
            ? `<select class="select" data-field="triggerTargetId">${targetOpts}</select>`
            : `<div style="color:var(--yellow);font-size:12px;">⚠ Recorderでターゲットを先に登録してください</div>`}
        </div>` : `
        <div class="formSection">
          <div class="formSectionTitle">🎯 対象要素</div>
          <div style="color:var(--muted);font-size:13px;">（ページ読込時はページ全体が対象）</div>
        </div>`}

        <div class="formSection">
          <div class="formSectionTitle">⚡ トリガー</div>
          <div class="radioGroup">${triggerRadios}</div>
          <label class="formLabel" style="margin-top:8px;">⏱ 遅延（ms）— イベント発火から何ミリ秒後に評価するか</label>
          <input type="number" class="input" data-field="triggerDelayMs"
            placeholder="0" min="0" max="30000" step="500"
            value="${triggerDelayMs}"
            style="width:140px;">
          <div style="font-size:11px;color:var(--muted);margin-top:3px;">例: 3000 = 3秒後。field_input など頻繁に発火するトリガーに設定推奨。</div>
        </div>

        <div class="formSection">
          <div class="formSectionTitle">📋 表示条件</div>
          <div class="radioGroup">${ruleRadios}</div>
        </div>

        <div class="formSection">
          <div class="formSectionTitle">💬 コーチメッセージ</div>
          <label class="formLabel">トーン</label>
          <select class="select" data-field="coachTone">${toneOpts}</select>
          <label class="formLabel">本文（先輩からのひとこと）</label>
          <input class="input" data-field="coachOpening" placeholder="例: Notes欄が空欄です。具体的な内容を入力してください。"
            value="${esc(opening)}">
        </div>

        <div class="formSection">
          <div class="formSectionTitle">アクション</div>
          <div class="actionsWrap" id="actionsWrap_${dirty.id}">
            ${actionsHTML || `<div style="color:var(--muted);font-size:12px;">アクションがありません</div>`}
          </div>
          <button class="btnGhost btnSm" style="margin-top:8px;"
            data-action="addAction" data-iv-id="${dirty.id}">＋ アクションを追加</button>
        </div>

        <div class="formActions">
          <button class="btnDanger btnSm" data-action="deleteCard"
            data-iv-id="${dirty.id}" data-page-id="${pageId}">削除</button>
          <button class="btnGhost" data-action="cancelCard">キャンセル</button>
          <button class="btnPrimary" data-action="saveCard"
            data-iv-id="${dirty.id}" data-page-id="${pageId}">保存</button>
        </div>
      </div>

      <!-- ===== RIGHT: PREVIEW PANEL ===== -->
      ${buildPreviewHTML(dirty, pageId)}

    </div>`;
}

function buildActionRowHTML(action, idx, pageId) {
  const targets = state.targets.get(pageId) || [];
  const typeOpts = ACTION_TYPE_OPTIONS.map(o =>
    `<option value="${o.value}" ${action.type === o.value ? "selected" : ""}>${o.label}</option>`
  ).join("");

  const subFields = buildSubFieldsHTML(action, idx, targets);

  return `
    <div class="actionRow" data-action-idx="${idx}">
      <div class="actionSubFields">
        <select class="select" data-action-field="type" data-action-idx="${idx}">${typeOpts}</select>
        <div class="actionSub" data-sub-idx="${idx}">${subFields}</div>
      </div>
      <button class="btnIcon danger" data-action="removeAction" data-action-idx="${idx}"
        title="削除">✕</button>
    </div>`;
}

function buildSubFieldsHTML(action, idx, targets) {
  const targetOpts = targets.map(t =>
    `<option value="${esc(t.target_key)}" ${(action.params?.target_id === t.target_key) ? "selected" : ""}>${esc(t.description || t.target_key)}</option>`
  ).join("");

  const targetSel = targets.length
    ? `<select class="select" data-action-field="params.target_id" data-action-idx="${idx}">${targetOpts}</select>`
    : `<div style="color:var(--muted);font-size:12px;">（ターゲット未登録）</div>`;

  const desc = ACTION_DESCRIPTIONS[action.type] || "";
  const descHTML = desc
    ? `<div class="actionDesc">${esc(desc)}</div>`
    : "";

  switch (action.type) {
    case "tooltip":
      return descHTML + `
        <label class="formLabel">表示先ターゲット</label>${targetSel}
        <label class="formLabel">メッセージ（省略時はcoachメッセージ使用）</label>
        <input class="input" placeholder="オプション" data-action-field="params.content"
          data-action-idx="${idx}" value="${esc(action.params?.content || "")}">`;

    case "highlight":
      return descHTML + `
        <label class="formLabel">ハイライトするターゲット</label>${targetSel}`;

    case "checklist":
      return descHTML + `
        <label class="formLabel">タイトル</label>
        <input class="input" placeholder="チェックリストのタイトル"
          data-action-field="params.title" data-action-idx="${idx}"
          value="${esc(action.params?.title || "")}">
        <label class="formLabel">項目（1行1項目）</label>
        <textarea class="textarea"
          data-action-field="params.items_text" data-action-idx="${idx}"
          placeholder="例:\n・用途を具体的に記載する\n・想定ユーザーを明記する">${esc((action.params?.items || []).join("\n"))}</textarea>`;

    case "insert_template":
      return descHTML + `
        <label class="formLabel">挿入先ターゲット</label>${targetSel}
        <label class="formLabel">テンプレートテキスト</label>
        <textarea class="textarea"
          data-action-field="params.template" data-action-idx="${idx}"
          placeholder="挿入するテキストを入力">${esc(action.params?.template || "")}</textarea>`;

    case "block_action":
      return descHTML + `
        <label class="formLabel">ブロック対象ターゲット</label>${targetSel}
        <label class="formLabel">メッセージ（オプション）</label>
        <input class="input" placeholder="例: まず必須入力を完了してください"
          data-action-field="params.reason" data-action-idx="${idx}"
          value="${esc(action.params?.reason || "")}">`;

    default:
      return descHTML;
  }
}

// ============================================================
// SECTION 6.5: Preview Panel
// ============================================================

function buildPreviewHTML(dirty, pageId) {
  const targets      = state.targets.get(pageId) || [];
  const triggerLabel = TRIGGER_LABELS[dirty.triggerType] || dirty.triggerType || "フォーカスアウト時";
  const targetDesc   = targets.find(t => t.target_key === dirty.triggerTargetId)?.description || "対象要素";
  const overlayHTML  = buildPreviewOverlayHTML(dirty);

  const hasHighlight = (dirty.actions || []).some(a => a.type === "highlight");
  const hasTemplate  = (dirty.actions || []).some(a => a.type === "insert_template");
  const templateText = (dirty.actions || []).find(a => a.type === "insert_template")?.params?.template || "";

  return `
    <div class="previewPanel">
      <div class="previewHeader">
        <span class="previewLabel">👁 プレビュー</span>
        <span class="previewMeta">${esc(triggerLabel)}</span>
      </div>
      <div class="mockBrowser">
        <div class="mockBrowserBar">
          <div class="mockBrowserDots">
            <span></span><span></span><span></span>
          </div>
          <div class="mockBrowserUrl">localhost:8000/</div>
        </div>
        <div class="mockBrowserContent">
          <div class="mockFormGroup">
            <div class="mockFormLabel">${esc(targetDesc)}</div>
            <div class="mockTextarea ${hasHighlight ? "isHighlighted" : ""}">${
              hasTemplate ? `<span class="mockTemplateText">${esc(templateText.slice(0, 80) || "（テンプレートが挿入されます）")}</span>` : ""
            }</div>
          </div>
          <div id="prevOverlay_${dirty.id}" class="prevOverlayWrap">${overlayHTML}</div>
        </div>
      </div>
    </div>`;
}

function buildPreviewOverlayHTML(dirty) {
  const actions  = dirty.actions || [];
  const opening  = dirty.coachOpening || dirty.coach?.opening || "";
  const tone     = dirty.coachTone || dirty.coach?.tone || "senior_supportive";
  const toneIcon = tone === "strict_risk" ? "⚠️" : tone === "neutral" ? "ℹ️" : "👨‍💼";
  const toneLabel = TONE_OPTIONS.find(o => o.value === tone)?.label || "先輩";
  let html = "";

  // Tooltip
  const tooltipAction = actions.find(a => a.type === "tooltip");
  if (tooltipAction) {
    const msg = tooltipAction.params?.content || opening || "（メッセージを入力してください）";
    html += `
      <div class="prevTooltip">
        <div class="prevTooltipHeader">${toneIcon} ${esc(toneLabel)}</div>
        <div class="prevTooltipBody">${esc(msg)}</div>
        <div class="prevTooltipArrow"></div>
      </div>`;
  }

  // Checklist
  const clAction = actions.find(a => a.type === "checklist");
  if (clAction) {
    const title = clAction.params?.title || "チェックリスト";
    const rawItems = clAction.params?.items_text
      ? clAction.params.items_text.split("\n").filter(Boolean)
      : (clAction.params?.items || []);
    const items = rawItems.length ? rawItems : ["項目1", "項目2", "項目3"];
    html += `
      <div class="prevChecklist">
        <div class="prevChecklistTitle">📋 ${esc(title)}</div>
        <ul class="prevChecklistItems">
          ${items.slice(0, 5).map(i => `<li>☐ ${esc(i)}</li>`).join("")}
        </ul>
      </div>`;
  }

  // Block action
  const blockAction = actions.find(a => a.type === "block_action");
  if (blockAction) {
    const reason = blockAction.params?.reason || "必須入力を完了してください";
    html += `<div class="prevBlock">🚫 ${esc(reason)}</div>`;
  }

  // No actions yet
  if (!html) {
    html = `<div class="prevEmpty">アクションを追加すると<br>ここに表示されます</div>`;
  }

  return html;
}

function updatePreview() {
  if (!state.dirtyCard) return;
  const ivId    = state.dirtyCard.id;
  const pageId  = state.expandedPageId;
  const overlayEl = document.getElementById(`prevOverlay_${ivId}`);
  if (!overlayEl) return;

  overlayEl.innerHTML = buildPreviewOverlayHTML(state.dirtyCard);

  // Update highlight on mock field
  const mockField = overlayEl.closest(".mockBrowserContent")?.querySelector(".mockTextarea");
  if (mockField) {
    const hasHighlight = (state.dirtyCard.actions || []).some(a => a.type === "highlight");
    mockField.classList.toggle("isHighlighted", hasHighlight);

    // Update template text
    const hasTemplate  = (state.dirtyCard.actions || []).some(a => a.type === "insert_template");
    const templateText = (state.dirtyCard.actions || []).find(a => a.type === "insert_template")?.params?.template || "";
    let tmplSpan = mockField.querySelector(".mockTemplateText");
    if (hasTemplate) {
      if (!tmplSpan) {
        tmplSpan = document.createElement("span");
        tmplSpan.className = "mockTemplateText";
        mockField.appendChild(tmplSpan);
      }
      tmplSpan.textContent = templateText.slice(0, 80) || "（テンプレートが挿入されます）";
    } else if (tmplSpan) {
      tmplSpan.remove();
    }
  }

  // Update trigger meta label
  const targets    = state.targets.get(pageId) || [];
  const targetDesc = targets.find(t => t.target_key === state.dirtyCard.triggerTargetId)?.description || "対象要素";
  const metaEl = overlayEl.closest(".previewPanel")?.querySelector(".previewMeta");
  if (metaEl) {
    const trigLabel = TRIGGER_LABELS[state.dirtyCard.triggerType] || "フォーカスアウト時";
    metaEl.textContent = trigLabel;
  }
  const mockLabel = overlayEl.closest(".mockBrowserContent")?.querySelector(".mockFormLabel");
  if (mockLabel) mockLabel.textContent = targetDesc;
}

// ============================================================
// SECTION 7: Inline Edit State Management
// ============================================================

function openCard(ivId, pageId) {
  // Close any existing expanded card first
  if (state.expandedCardId !== null && state.expandedCardId !== ivId) {
    const prevPageId = state.expandedPageId;
    state.expandedCardId = null;
    state.expandedPageId = null;
    state.dirtyCard      = null;
    if (prevPageId) rerenderPageSection(prevPageId);
  }

  const iv = findIntervention(ivId);
  if (!iv) return;

  state.expandedCardId = ivId;
  state.expandedPageId = pageId;
  state.dirtyCard = deepClone(iv);
  // Set UI-only fields
  state.dirtyCard._name          = iv.name || "";
  state.dirtyCard.triggerType    = iv.trigger?.type || "field_blur";
  state.dirtyCard.triggerTargetId = iv.trigger?.target_id || "";
  state.dirtyCard.triggerDelayMs  = iv.trigger?.delay_ms ?? 0;
  state.dirtyCard._ruleType      = resolveRuleTypeFromKey(iv.rule_key);
  state.dirtyCard.coachTone      = iv.coach?.tone || "senior_supportive";
  state.dirtyCard.coachOpening   = iv.coach?.opening || "";
  state.dirtyCard.id             = iv.id;

  rerenderPageSection(pageId);
  // Scroll the card into view
  setTimeout(() => {
    const card = document.querySelector(`.interventionCard[data-iv-id="${ivId}"]`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, 50);
}

function closeCard() {
  const pageId = state.expandedPageId;
  state.expandedCardId = null;
  state.expandedPageId = null;
  state.dirtyCard      = null;
  if (pageId) rerenderPageSection(pageId);
}

function updateDirtyField(field, value) {
  if (!state.dirtyCard) return;
  state.dirtyCard[field] = value;

  // Special: when triggerType changes, toggle target selector visibility
  if (field === "triggerType") {
    const ivId = state.dirtyCard.id;
    const section = document.getElementById(`targetSection_${ivId}`);
    if (section) section.style.display = value === "page_load" ? "none" : "";
  }

  updatePreview();
}

function updateDirtyActionField(actionIdx, field, value) {
  if (!state.dirtyCard?.actions) return;
  const action = state.dirtyCard.actions[actionIdx];
  if (!action) return;

  if (field === "type") {
    // Reset params when type changes
    action.type   = value;
    action.params = {};
    // Re-render the sub-fields for this action row
    const pageId  = state.expandedPageId;
    const targets = state.targets.get(pageId) || [];
    const subEl   = document.querySelector(`.actionSub[data-sub-idx="${actionIdx}"]`);
    if (subEl) subEl.innerHTML = buildSubFieldsHTML(action, actionIdx, targets);
  } else if (field.startsWith("params.")) {
    const key = field.slice(7);
    if (!action.params) action.params = {};
    action.params[key] = value;
  } else {
    action[field] = value;
  }

  updatePreview();
}

function addAction() {
  if (!state.dirtyCard) return;
  state.dirtyCard.actions = state.dirtyCard.actions || [];
  state.dirtyCard.actions.push({ type: "tooltip", params: {} });
  // Re-render just the actions wrap
  const ivId   = state.dirtyCard.id;
  const pageId = state.expandedPageId;
  const wrap   = document.getElementById(`actionsWrap_${ivId}`);
  if (wrap) {
    wrap.innerHTML = state.dirtyCard.actions.map((a, i) =>
      buildActionRowHTML(a, i, pageId)
    ).join("") || `<div style="color:var(--muted);font-size:12px;">アクションがありません</div>`;
  }
}

function removeAction(actionIdx) {
  if (!state.dirtyCard) return;
  state.dirtyCard.actions.splice(actionIdx, 1);
  const ivId   = state.dirtyCard.id;
  const pageId = state.expandedPageId;
  const wrap   = document.getElementById(`actionsWrap_${ivId}`);
  if (wrap) {
    wrap.innerHTML = state.dirtyCard.actions.map((a, i) =>
      buildActionRowHTML(a, i, pageId)
    ).join("") || `<div style="color:var(--muted);font-size:12px;">アクションがありません</div>`;
  }
}

async function saveCard(ivId, pageId) {
  if (!state.dirtyCard) return;
  const d = state.dirtyCard;

  // Resolve rule_key from _ruleType
  const ruleKey = d._ruleType === "always" ? null : resolveRuleKey(d._ruleType);

  // Normalize actions — convert items_text to items[]
  const actions = (d.actions || []).map(a => {
    const norm = { type: a.type, params: { ...(a.params || {}) } };
    if (a.type === "checklist" && norm.params.items_text !== undefined) {
      norm.params.items = norm.params.items_text
        .split("\n")
        .map(s => s.trim())
        .filter(Boolean);
      delete norm.params.items_text;
    }
    return norm;
  });

  const delayMs = Number(d.triggerDelayMs) || 0;
  const payload = {
    name:         d._name || d.name || "",
    trigger:      {
      type:      d.triggerType,
      target_id: d.triggerTargetId || undefined,
      ...(delayMs > 0 ? { delay_ms: delayMs } : {}),
    },
    rule_key:     ruleKey,
    block_action: actions.some(a => a.type === "block_action") ? 1 : 0,
    coach:        { tone: d.coachTone, opening: d.coachOpening },
    actions,
  };

  try {
    await api(`/api/interventions/${ivId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });

    // Update state in place
    const ivs  = state.interventions.get(pageId) || [];
    const idx  = ivs.findIndex(iv => iv.id === ivId);
    if (idx >= 0) {
      ivs[idx] = { ...ivs[idx], ...payload };
      state.interventions.set(pageId, ivs);
    }

    state.hasPendingChanges = true;
    updateDeployStatus();
    closeCard();
    showToast("保存しました（Publish で配信）", "success");
  } catch (err) {
    showToast("保存に失敗しました: " + err.message, "error");
  }
}

// ============================================================
// SECTION 8: Creating & Deleting Interventions
// ============================================================

async function createIntervention(pageId) {
  const ts  = Date.now().toString(36);
  const key = `iv_new_${ts}`;
  const targets = state.targets.get(pageId) || [];
  const firstTarget = targets[0]?.target_key || "";

  const payload = {
    page_id:           pageId,
    intervention_key:  key,
    name:              "新しいガイダンス",
    trigger:           { type: "field_blur", target_id: firstTarget },
    rule_key:          null,
    block_action:      0,
    coach:             { tone: "senior_supportive", opening: "" },
    actions:           [],
  };

  try {
    const result = await api("/api/interventions", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    const newIv = { ...payload, id: result.id, block_action: false };
    const ivs = state.interventions.get(pageId) || [];
    ivs.unshift(newIv);
    state.interventions.set(pageId, ivs);

    state.hasPendingChanges = true;
    updateDeployStatus();
    rerenderPageSection(pageId);
    openCard(result.id, pageId);
  } catch (err) {
    showToast("作成に失敗しました: " + err.message, "error");
  }
}

async function deleteIntervention(ivId, pageId) {
  if (!confirm("このガイダンスを削除しますか？")) return;
  try {
    await api(`/api/interventions/${ivId}`, { method: "DELETE" });
    const ivs = (state.interventions.get(pageId) || []).filter(iv => iv.id !== ivId);
    state.interventions.set(pageId, ivs);
    if (state.expandedCardId === ivId) {
      state.expandedCardId = null;
      state.expandedPageId = null;
      state.dirtyCard      = null;
    }
    state.hasPendingChanges = true;
    updateDeployStatus();
    rerenderPageSection(pageId);
    showToast("削除しました", "warn");
  } catch (err) {
    showToast("削除に失敗しました: " + err.message, "error");
  }
}

// ============================================================
// SECTION 8.5: Target CRUD
// ============================================================

function openAddTargetDialog(pageId) {
  document.getElementById("dlgTargetPageId").value = pageId;
  document.getElementById("dlgTargetKey").value    = "";
  document.getElementById("dlgTargetCss").value    = "";
  document.getElementById("dlgTargetDesc").value   = "";
  document.getElementById("addTargetDialog").showModal();
  // 最初のフィールドにフォーカス
  setTimeout(() => document.getElementById("dlgTargetKey").focus(), 80);
}

async function saveNewTarget() {
  const pageId = Number(document.getElementById("dlgTargetPageId").value);
  const key    = document.getElementById("dlgTargetKey").value.trim();
  const css    = document.getElementById("dlgTargetCss").value.trim();
  const desc   = document.getElementById("dlgTargetDesc").value.trim();

  if (!key || !css) {
    showToast("target_key と CSSセレクタは必須です", "warn");
    return;
  }

  try {
    const result = await api("/api/targets/upsert", {
      method: "POST",
      body: JSON.stringify({
        page_id:    pageId,
        target_key: key,
        description: desc || key,
        anchors: [{ strategy: "css", value: css }],
      }),
    });
    const targets    = state.targets.get(pageId) || [];
    const newTarget  = { id: result.id, page_id: pageId, target_key: key, description: desc || key, anchors: [{ strategy: "css", value: css }] };
    const existingIdx = targets.findIndex(t => t.target_key === key);
    if (existingIdx >= 0) {
      targets[existingIdx] = newTarget;
    } else {
      targets.push(newTarget);
    }
    state.targets.set(pageId, targets);

    document.getElementById("addTargetDialog").close();
    const targetsEl = document.getElementById(`pageTargets_${pageId}`);
    if (targetsEl) targetsEl.innerHTML = buildTargetsHTML(pageId);
    state.hasPendingChanges = true;
    updateDeployStatus();
    showToast("ターゲットを追加しました", "success");
  } catch (err) {
    showToast("追加に失敗しました: " + err.message, "error");
  }
}

async function deleteTarget(targetId, pageId) {
  if (!confirm("このターゲットを削除しますか？\n関連するガイダンスのトリガーに影響が出る場合があります。")) return;
  try {
    await api(`/api/targets/${targetId}`, { method: "DELETE" });
    const targets = (state.targets.get(pageId) || []).filter(t => t.id !== targetId);
    state.targets.set(pageId, targets);
    const targetsEl = document.getElementById(`pageTargets_${pageId}`);
    if (targetsEl) targetsEl.innerHTML = buildTargetsHTML(pageId);
    state.hasPendingChanges = true;
    updateDeployStatus();
    showToast("ターゲットを削除しました", "warn");
  } catch (err) {
    showToast("削除に失敗しました: " + err.message, "error");
  }
}

// ============================================================
// SECTION 9: Page CRUD
// ============================================================

function openAddPageDialog() {
  document.getElementById("dlgPageName").value  = "";
  document.getElementById("dlgPageKey").value   = "";
  document.getElementById("dlgPageRegex").value = "";
  document.getElementById("addPageDialog").showModal();
}

async function saveNewPage() {
  const name  = document.getElementById("dlgPageName").value.trim();
  const key   = document.getElementById("dlgPageKey").value.trim();
  const regex = document.getElementById("dlgPageRegex").value.trim();

  if (!key || !regex) {
    showToast("page_keyとURLパターンは必須です", "warn");
    return;
  }

  try {
    const result = await api("/api/pages", {
      method: "POST",
      body: JSON.stringify({ app_id: state.currentAppId, page_key: key, name, url_regex: regex }),
    });
    const newPage = { id: result.id, app_id: state.currentAppId, page_key: key, name, url_regex: regex, must_have: {} };
    state.pages.unshift(newPage);
    state.targets.set(result.id, []);
    state.interventions.set(result.id, []);

    document.getElementById("addPageDialog").close();
    renderAllPages();
    showToast("ページを追加しました", "success");
  } catch (err) {
    showToast("追加に失敗しました: " + err.message, "error");
  }
}

async function deletePage(pageId) {
  const page = state.pages.find(p => p.id === pageId);
  if (!confirm(`「${page?.name || "このページ"}」と関連するすべてのガイダンスを削除しますか？`)) return;
  try {
    await api(`/api/pages/${pageId}`, { method: "DELETE" });
    state.pages = state.pages.filter(p => p.id !== pageId);
    state.targets.delete(pageId);
    state.interventions.delete(pageId);
    if (state.expandedPageId === pageId) {
      state.expandedCardId = null;
      state.expandedPageId = null;
      state.dirtyCard      = null;
    }
    renderAllPages();
    showToast("ページを削除しました", "warn");
  } catch (err) {
    showToast("削除に失敗しました: " + err.message, "error");
  }
}

// ============================================================
// SECTION 10: Publish
// ============================================================

async function publish() {
  const btn = document.getElementById("btnPublish");
  btn.disabled    = true;
  btn.textContent = "配信中…";
  try {
    const result = await api("/api/publish", {
      method: "POST",
      body: JSON.stringify({ app_id: state.currentAppId, env: "local" }),
    });
    state.publishedVersion = result.version;
    state.hasPendingChanges = false;
    updateDeployStatus();
    showToast(`v${result.version} を配信しました ✓`, "success");
  } catch (err) {
    showToast("配信に失敗しました: " + err.message, "error");
  } finally {
    btn.disabled    = false;
    btn.textContent = "Publish";
  }
}

// ============================================================
// SECTION 11: Analytics
// ============================================================

async function loadAnalytics() {
  if (!state.currentAppKey) return;
  const tbody = document.getElementById("analyticsBody");
  tbody.innerHTML = `<tr><td colspan="5" class="analyticsEmpty">読み込み中…</td></tr>`;

  try {
    const rows = await api(`/api/analytics/summary?app_key=${encodeURIComponent(state.currentAppKey)}`);
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="analyticsEmpty">イベントがありません</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map(r => {
      const ts    = r.ts ? new Date(Number(r.ts)).toLocaleString("ja-JP") : "-";
      const ivId  = r.context?.intervention_id || r.context?.etag?.slice(0, 8) || "-";
      const user  = r.user_pseudo_id?.slice(0, 8) || "-";
      return `<tr>
        <td>${esc(ts)}</td>
        <td><span class="bdg bdg-action">${esc(r.event_name || r.event_type)}</span></td>
        <td>${esc(r.page_id || "-")}</td>
        <td>${esc(ivId)}</td>
        <td>${esc(user)}</td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="analyticsEmpty">読み込みエラー: ${esc(err.message)}</td></tr>`;
  }
}

// ============================================================
// SECTION 12: Toast & Utilities
// ============================================================

let _toastTimer = null;
function showToast(message, type = "success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className   = `toast ${type} show`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove("show"), 3000);
}

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function findIntervention(ivId) {
  for (const [, ivs] of state.interventions) {
    const found = ivs.find(iv => iv.id === ivId);
    if (found) return found;
  }
  return null;
}

function resolveRuleTypeFromKey(ruleKey) {
  if (!ruleKey) return "always";
  const rule = state.rules.find(r => r.rule_key === ruleKey);
  return rule?.rule_type || "always";
}

function resolveRuleKey(ruleType) {
  if (!ruleType || ruleType === "always") return null;
  const match = state.rules.find(r => r.rule_type === ruleType);
  return match?.rule_key || null;
}

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ============================================================
// SECTION 13: Tab Switching
// ============================================================

function setActiveTab(tabId) {
  document.querySelectorAll(".tab").forEach(el => el.classList.remove("isActive"));
  document.querySelectorAll(".tabBtn").forEach(el => el.classList.remove("isActive"));
  document.getElementById(tabId)?.classList.add("isActive");
  document.querySelector(`.tabBtn[data-tab="${tabId}"]`)?.classList.add("isActive");

  if (tabId === "tab-analytics") loadAnalytics();
  if (tabId === "tab-chat-widget") loadChatWidgetConfigs();
}

// ============================================================
// SECTION 14: Chat Widget Config
// ============================================================

async function loadChatWidgetConfigs() {
  const container = document.getElementById("chatWidgetCards");
  if (!container) return;
  container.innerHTML = '<div class="analyticsEmpty">読み込み中…</div>';

  let configs = [], templates = [];
  try {
    [configs, templates] = await Promise.all([
      api("/api/chat/app-configs"),
      api("/api/chat/coaching-templates").catch(() => []),
    ]);
  } catch (e) {
    container.innerHTML = `<div class="analyticsEmpty">読み込み失敗: ${e.message}</div>`;
    return;
  }

  // port → template name マップ
  const tmplMap = Object.fromEntries(templates.map(t => [t.port, t.name]));

  container.innerHTML = configs.map(cfg => {
    const hasTmpl = !!tmplMap[cfg.port];
    return `
    <div class="chatWidgetCard" data-port="${cfg.port}">
      <div class="chatWidgetCardHeader">
        <span class="chatWidgetLabel">${cfg.label}</span>
        <span class="chatPortBadge">:${cfg.port}</span>
        <label class="chatToggle" title="${cfg.enabled ? '有効' : '無効'}">
          <input type="checkbox" class="chatEnabledToggle" data-port="${cfg.port}" ${cfg.enabled ? "checked" : ""}>
          <span class="chatToggleTrack"><span class="chatToggleThumb"></span></span>
        </label>
      </div>
      ${hasTmpl ? `
      <div style="margin-top:8px;padding:6px 8px;background:#1a2e4a;border-radius:6px;display:flex;align-items:center;justify-content:space-between;gap:8px;">
        <span style="font-size:11px;color:#64b5f6;">💡 ${tmplMap[cfg.port]}</span>
        <button class="chatApplyTmplBtn btnGhost btnSm" data-port="${cfg.port}" style="font-size:10px;padding:2px 8px;">テンプレートを適用</button>
      </div>` : ""}
      <label class="formLabel" style="margin-top:12px;display:block;">コーチングプロンプト補足</label>
      <textarea class="input chatPromptSupp" data-port="${cfg.port}" rows="5"
        placeholder="例: このモジュールでは輸出管理担当者のみ使用します。品目コードは TS- 形式です。"
        style="width:100%;resize:vertical;font-size:11px;line-height:1.5;">${cfg.prompt_supplement || ""}</textarea>
      <button class="btnPrimary btnSm chatSaveBtn" data-port="${cfg.port}" style="margin-top:8px;">保存</button>
    </div>`;
  }).join("");

  // 保存ボタン
  container.querySelectorAll(".chatSaveBtn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const port = btn.dataset.port;
      const card = container.querySelector(`.chatWidgetCard[data-port="${port}"]`);
      const enabled = card.querySelector(".chatEnabledToggle").checked ? 1 : 0;
      const supplement = card.querySelector(".chatPromptSupp").value;
      try {
        await api(`/api/chat/app-configs/${port}`, {
          method: "PUT",
          body: JSON.stringify({ enabled, prompt_supplement: supplement }),
        });
        showToast(`ポート ${port} の設定を保存しました`);
      } catch (e) {
        showToast("保存に失敗しました: " + e.message, "error");
      }
    });
  });

  // テンプレート適用ボタン（モジュール個別）
  container.querySelectorAll(".chatApplyTmplBtn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const port = btn.dataset.port;
      try {
        await api(`/api/chat/app-configs/${port}/apply-template`, { method: "POST" });
        showToast(`ポート ${port} にテンプレートを適用しました`);
        await loadChatWidgetConfigs();
      } catch (e) {
        showToast("適用に失敗しました: " + e.message, "error");
      }
    });
  });
}

async function applyAllCoachingTemplates() {
  try {
    const result = await api("/api/chat/app-configs/apply-all-templates", { method: "POST" });
    showToast(`全 ${result.applied_ports.length} モジュールにテンプレートを適用しました`);
    await loadChatWidgetConfigs();
  } catch (e) {
    showToast("一括適用に失敗しました: " + e.message, "error");
  }
}

// ============================================================
// SECTION 15: Event Delegation (single listener pattern)
// ============================================================

document.addEventListener("DOMContentLoaded", () => {

  // Tab bar
  document.querySelectorAll(".tabBtn").forEach(btn => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
  });

  // App select
  document.getElementById("appSelect").addEventListener("change", e => {
    const appId  = Number(e.target.value);
    const appKey = state.apps.find(a => a.id === appId)?.app_key || "";
    setApp(appId, appKey);
  });

  // Publish
  document.getElementById("btnPublish").addEventListener("click", publish);

  // Add Page
  document.getElementById("btnAddPage").addEventListener("click", openAddPageDialog);
  document.getElementById("dlgPageSave").addEventListener("click", saveNewPage);
  document.getElementById("dlgPageCancel").addEventListener("click", () => {
    document.getElementById("addPageDialog").close();
  });

  // Add Target dialog
  document.getElementById("dlgTargetSave").addEventListener("click", saveNewTarget);
  document.getElementById("dlgTargetCancel").addEventListener("click", () => {
    document.getElementById("addTargetDialog").close();
  });
  // Enterキーでも保存
  document.getElementById("addTargetDialog").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveNewTarget(); }
    if (e.key === "Escape") document.getElementById("addTargetDialog").close();
  });

  // Analytics reload
  document.getElementById("btnLoadAnalytics").addEventListener("click", loadAnalytics);

  // Chat Widget reload
  document.getElementById("btnReloadChatConfigs").addEventListener("click", loadChatWidgetConfigs);

  // Chat Widget: 全テンプレート一括適用
  document.getElementById("btnApplyAllTemplates")?.addEventListener("click", applyAllCoachingTemplates);

  // Scenarios container — delegated click
  const container = document.getElementById("pagesContainer");

  container.addEventListener("click", e => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;

    const action  = btn.dataset.action;
    const ivId    = Number(btn.dataset.ivId);
    const pageId  = Number(btn.dataset.pageId);
    const actIdx  = Number(btn.dataset.actionIdx);

    switch (action) {
      case "editCard":
        openCard(ivId, pageId);
        break;
      case "deleteCard":
        deleteIntervention(ivId, pageId);
        break;
      case "cancelCard":
        closeCard();
        break;
      case "saveCard":
        saveCard(ivId, pageId);
        break;
      case "addIntervention":
        createIntervention(pageId);
        break;
      case "deletePage":
        deletePage(pageId);
        break;
      case "addAction":
        addAction();
        break;
      case "removeAction":
        removeAction(actIdx);
        break;
      case "addTarget":
        openAddTargetDialog(pageId);
        break;
      case "deleteTarget":
        deleteTarget(Number(btn.dataset.targetId), pageId);
        break;
    }
  });

  // Delegated input/change — sync to dirtyCard
  container.addEventListener("change", e => {
    if (!state.dirtyCard) return;
    const el = e.target;

    // Action-level fields
    if (el.dataset.actionField !== undefined && el.dataset.actionIdx !== undefined) {
      updateDirtyActionField(Number(el.dataset.actionIdx), el.dataset.actionField, el.value);
      return;
    }

    // Card-level fields
    if (el.dataset.field) {
      updateDirtyField(el.dataset.field, el.type === "checkbox" ? el.checked : el.value);
    }
  });

  container.addEventListener("input", e => {
    if (!state.dirtyCard) return;
    const el = e.target;

    if (el.dataset.actionField !== undefined && el.dataset.actionIdx !== undefined) {
      updateDirtyActionField(Number(el.dataset.actionIdx), el.dataset.actionField, el.value);
      return;
    }
    if (el.dataset.field) {
      updateDirtyField(el.dataset.field, el.value);
    }
  });

  // Boot
  boot();
});
