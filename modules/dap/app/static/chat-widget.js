// cache-bust: 1780155858
/**
 * DAP Chat Widget v2 — 先輩担当者モード
 *
 * 主要機能:
 *  - 先輩コレーグとして全インタラクションで伴走（ペルソナ追跡・適応）
 *  - ページロード時に /api/chat/greet を呼び出しプロアクティブ案内
 *  - alert バナー: チャット外に自発的アラートを表示（重複表示なし）
 *  - guidance ステップ: クロスモジュール・ガイドツアー（tooltip / fill_hint / navigate）
 *  - intake_state: ヒアリング進捗バー
 *  - 行動イベント追跡: /api/chat/event でページ訪問・ガイド既読を記録
 *  - クロスページセッション: cookie で session_id を共有
 */
(function () {
  'use strict';

  if (window !== window.top) return;
  if (document.getElementById('dap-chat-root')) return;

  // 外部アクセス（HTTPS）時は dap.{domain} サブドメインを自動解決、ローカルは localhost:8010
  const DAP_BASE = (function () {
    var h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8010';
    var parts = h.split('.');
    if (parts.length >= 2) return window.location.protocol + '//dap.' + parts.slice(-2).join('.');
    return 'http://localhost:8010';
  })();

  // ── セッション ID（cookie 経由でポートをまたいで共有）─────────────
  function getOrCreateSessionId() {
    const match = document.cookie.match(/(?:^|;\s*)dap_session_id=([^;]+)/);
    if (match) return match[1];
    const id = 'dap_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 9);
    document.cookie = 'dap_session_id=' + id + '; path=/; SameSite=Lax; Max-Age=86400';
    return id;
  }
  const SESSION_ID = getOrCreateSessionId();

  // ── モジュール名検出 ──────────────────────────────────────────────
  const PORT_MAP = {
    '8000': 'プラットフォーム',
    '8001': 'AI 該非判定',
    '8002': '品目管理',
    '8003': 'R&D リスク管理',
    '8004': '特許検索',
    '8005': 'スクリーニング',
    '8006': 'HS コード判定',
    '8010': 'DAP 管理',
  };
  function detectCurrentModule() {
    const port = window.location.port;
    if (PORT_MAP[port]) return PORT_MAP[port];
    const t = document.title || '';
    if (/R&?D|リスク管理/.test(t))   return 'R&D リスク管理';
    if (/品目|item/i.test(t))         return '品目管理';
    if (/スクリーニング/i.test(t))     return 'スクリーニング';
    if (/特許|patent/i.test(t))       return '特許検索';
    if (/該非|判定/i.test(t))         return 'AI 該非判定';
    return 'コンプライアンス支援';
  }
  const currentModule = detectCurrentModule();

  // ── コンテキスト収集 ──────────────────────────────────────────────
  function gatherContext() {
    const port      = window.location.port;
    const page_path = window.location.pathname;
    const form_fields = {};
    let count = 0;
    document.querySelectorAll('input[type=text],input[type=search],textarea,select').forEach(function (el) {
      if (count >= 8 || !el.offsetParent) return;
      const rawVal = (el.value || '').trim();
      if (!rawVal) return;
      let label = (el.labels && el.labels[0] ? el.labels[0].textContent.trim() : '') ||
                  el.name || el.id || el.placeholder || '';
      if (!label) return;
      form_fields[label] = rawVal.slice(0, 150);
      count++;
    });
    const interactive_elements = [];
    document.querySelectorAll('a[href],button,[role="button"]').forEach(function (el) {
      if (!el.offsetParent) return;
      const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
      if (!text || text.length > 40) return;
      interactive_elements.push({ label: text });
    });
    const extra = window.__dap_chat_context__ || {};
    return Object.assign({ port, page_path, form_fields, interactive_elements: interactive_elements.slice(0, 20) }, extra);
  }

  // ── 要素検索 ──────────────────────────────────────────────────────
  // ── iframe 対応ヘルパー ───────────────────────────────────────────
  // ポータル表示時は <iframe id="module-frame"> 内の document を返す
  function getModuleDoc() {
    try {
      var frame = document.getElementById('module-frame');
      return (frame && frame.contentDocument) ? frame.contentDocument : document;
    } catch(e) { return document; }
  }
  // iframe 要素の親フレーム内オフセット（座標補正用）
  function getIframeOffset() {
    try {
      var frame = document.getElementById('module-frame');
      if (frame && frame.contentDocument) {
        var r = frame.getBoundingClientRect();
        return { top: r.top, left: r.left };
      }
    } catch(e) {}
    return { top: 0, left: 0 };
  }

  function findInteractiveElement(label) {
    if (!label) return null;
    var doc = getModuleDoc();
    return Array.from(doc.querySelectorAll('a[href],button,[role="button"]'))
      .find(function (el) {
        const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
        return t === label || t.includes(label) || (label.includes(t) && t.length > 2);
      }) || null;
  }
  function findFieldElement(label) {
    if (!label) return null;
    var doc = getModuleDoc();
    const INPUTS = 'input[type=text],input[type=email],input[type=number],input:not([type]),textarea,select';

    // 1. data-dap-field 属性（明示的マーキング — 最優先）
    const byAttr = doc.querySelector('[data-dap-field="' + label + '"]');
    if (byAttr) return byAttr;

    // 2. label[for] → input[id] の紐付け
    const allLabels = Array.from(doc.querySelectorAll('label'));
    for (const lbl of allLabels) {
      if (lbl.textContent.trim().includes(label)) {
        const forId = lbl.getAttribute('for');
        if (forId) {
          const el = doc.getElementById(forId);
          if (el && el.matches(INPUTS)) return el;
        }
        // label が直接 input を包んでいる場合
        const nested = lbl.querySelector(INPUTS);
        if (nested) return nested;
      }
    }

    // 3. placeholder / name / id / aria-label の部分一致
    const byAttrMatch = Array.from(doc.querySelectorAll(INPUTS)).find(function (el) {
      return [el.placeholder, el.name, el.id, el.getAttribute('aria-label')]
        .some(function (v) { return v && v.includes(label); });
    });
    if (byAttrMatch) return byAttrMatch;

    // 4. テキストノードが隣接する input（旧フォールバック）
    return Array.from(doc.querySelectorAll(INPUTS)).find(function (el) {
      const prev = el.previousElementSibling;
      return prev && prev.textContent && prev.textContent.includes(label);
    }) || null;
  }

  // ── API 呼び出し ──────────────────────────────────────────────────
  async function apiPost(path, body) {
    const resp = await fetch(DAP_BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(function () { return {}; });
      throw new Error(err.detail || 'API Error ' + resp.status);
    }
    return resp.json();
  }

  async function sendMessage(userText) {
    const data = await apiPost('/api/chat', {
      message: userText, history: [], context: gatherContext(), session_id: SESSION_ID,
    });
    return {
      reply:          data.reply          || '',
      actions:        data.actions        || [],
      choices:        data.choices        || [],
      guidance:       data.guidance       || [],
      alert:          data.alert          || null,
      intake_state:   data.intake_state   || null,
      persona_summary: data.persona_summary || null,
    };
  }

  async function callGreet() {
    try {
      return await apiPost('/api/chat/greet', { session_id: SESSION_ID, context: gatherContext() });
    } catch (e) { return null; }
  }

  function trackEvent(eventType, ctx) {
    apiPost('/api/chat/event', {
      session_id: SESSION_ID, event_type: eventType, context: Object.assign(gatherContext(), ctx || {}),
    }).catch(function () {});
  }

  // ── ガイドID重複排止（sessionStorage）───────────────────────────
  function getShownGuides() {
    try { return new Set(JSON.parse(sessionStorage.getItem('dap_shown') || '[]')); }
    catch (e) { return new Set(); }
  }
  function markGuideShown(id) {
    if (!id) return;
    const s = getShownGuides();
    s.add(id);
    try { sessionStorage.setItem('dap_shown', JSON.stringify([...s])); } catch (e) {}
    trackEvent('guide_shown', { guide_id: id });
  }
  function isGuideShown(id) { return id && getShownGuides().has(id); }

  // ── ページ行動トラッカー（再訪問・ユーザー操作検知）──────────────
  var _pageKey = (window.location.port || '80') + ':' + window.location.pathname;
  var _userEngaged = false;  // ページ読み込み後5秒以内にユーザーが自分で操作したか

  function getPageVisits() {
    try { return JSON.parse(localStorage.getItem('dap_page_visits') || '{}'); }
    catch (e) { return {}; }
  }
  /** ページ訪問を記録し、前回までの訪問数を返す（0 = 初回）*/
  function recordPageVisit() {
    try {
      var visits = getPageVisits();
      var prev = visits[_pageKey] || { count: 0 };
      visits[_pageKey] = { count: prev.count + 1, last: Date.now() };
      localStorage.setItem('dap_page_visits', JSON.stringify(visits));
      return prev.count;
    } catch (e) { return 0; }
  }
  function isReturningVisitor() {
    var v = getPageVisits()[_pageKey];
    return v && v.count > 0;
  }

  // 5秒以内のユーザー能動操作（クリック・スクロール・キー入力）を検知
  function _onUserEngaged() { _userEngaged = true; }
  document.addEventListener('click',   _onUserEngaged, { capture: true, passive: true });
  document.addEventListener('scroll',  _onUserEngaged, { capture: true, passive: true });
  document.addEventListener('keydown', _onUserEngaged, { capture: true, passive: true });
  setTimeout(function () {
    document.removeEventListener('click',   _onUserEngaged, { capture: true });
    document.removeEventListener('scroll',  _onUserEngaged, { capture: true });
    document.removeEventListener('keydown', _onUserEngaged, { capture: true });
  }, 5000);

  // ── クロスページ guidance ペンディング ─────────────────────────
  function savePendingGuidance(steps, fromStep) {
    try { sessionStorage.setItem('dap_pending_guidance', JSON.stringify({ steps, from: fromStep })); }
    catch (e) {}
  }
  function loadPendingGuidance() {
    try {
      const raw = sessionStorage.getItem('dap_pending_guidance');
      if (!raw) return null;
      sessionStorage.removeItem('dap_pending_guidance');
      return JSON.parse(raw);
    } catch (e) { return null; }
  }

  // ── ワークフロー状態 sessionStorage 永続化（ページ遷移後も復元）──
  function _saveWfState() {
    try {
      if (_wfState) sessionStorage.setItem('dap_wf_state', JSON.stringify(_wfState));
      else sessionStorage.removeItem('dap_wf_state');
    } catch(e) {}
  }
  function _restoreWfState() {
    try {
      var raw = sessionStorage.getItem('dap_wf_state');
      return raw ? JSON.parse(raw) : null;
    } catch(e) { return null; }
  }

  // ── CSS ───────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #dap-chat-root * { box-sizing: border-box; font-family: 'DM Sans','Hiragino Sans',sans-serif; }

    /* ── フローティングボタン ── */
    #dap-chat-btn {
      position: fixed; bottom: 28px; right: 28px;
      width: 56px; height: 56px; border-radius: 50%;
      background: linear-gradient(135deg, #00D4FF 0%, #0099CC 100%);
      border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 0 0 0 rgba(0,212,255,0.4), 0 4px 20px rgba(0,212,255,0.35);
      animation: dap-pulse 2.8s infinite; z-index: 999998;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    #dap-chat-btn:hover { transform: scale(1.08); box-shadow: 0 0 0 6px rgba(0,212,255,0.15), 0 6px 24px rgba(0,212,255,0.45); }
    #dap-chat-btn svg { width: 26px; height: 26px; fill: #0A0E1A; pointer-events: none; }
    @keyframes dap-pulse {
      0%   { box-shadow: 0 0 0 0 rgba(0,212,255,0.4), 0 4px 20px rgba(0,212,255,0.35); }
      60%  { box-shadow: 0 0 0 10px rgba(0,212,255,0), 0 4px 20px rgba(0,212,255,0.35); }
      100% { box-shadow: 0 0 0 0 rgba(0,212,255,0), 0 4px 20px rgba(0,212,255,0.35); }
    }
    /* アラートバッジ */
    #dap-chat-btn-badge {
      position: absolute; top: -4px; right: -4px;
      width: 16px; height: 16px; border-radius: 50%;
      background: #FF6B35; border: 2px solid #0A0E1A;
      display: none; align-items: center; justify-content: center;
      font-size: 9px; font-weight: 700; color: #fff;
    }
    #dap-chat-btn-badge.is-visible { display: flex; }

    /* ── アラートバナー ── */
    #dap-alert-banner {
      position: fixed; bottom: 96px; right: 28px;
      width: 340px; border-radius: 12px;
      border-left: 4px solid #FFA500;
      background: #12192E;
      box-shadow: 0 8px 32px rgba(0,0,0,0.5);
      z-index: 999997;
      overflow: hidden;
      transform: translateX(110%);
      opacity: 0;
      transition: transform 0.35s cubic-bezier(.34,1.56,.64,1), opacity 0.25s ease;
    }
    #dap-alert-banner.is-visible { transform: translateX(0); opacity: 1; }
    #dap-alert-banner.severity-danger { border-left-color: #FF4444; }
    #dap-alert-banner.severity-info   { border-left-color: #00D4FF; }
    .dap-alert-inner { padding: 12px 14px; }
    .dap-alert-top { display: flex; align-items: flex-start; gap: 10px; }
    .dap-alert-icon { font-size: 16px; flex-shrink: 0; margin-top: 1px; }
    .dap-alert-body { flex: 1; min-width: 0; }
    .dap-alert-label {
      font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
      color: rgba(255,165,0,0.85); text-transform: uppercase; margin-bottom: 4px;
    }
    .dap-alert-banner.severity-danger .dap-alert-label { color: rgba(255,68,68,0.9); }
    .dap-alert-banner.severity-info   .dap-alert-label { color: rgba(0,212,255,0.9); }
    .dap-alert-msg { font-size: 12px; color: #C8D4EC; line-height: 1.55; }
    .dap-alert-dismiss {
      flex-shrink: 0; width: 22px; height: 22px;
      background: transparent; border: none; cursor: pointer;
      color: #5A6478; font-size: 14px; line-height: 1;
      display: flex; align-items: center; justify-content: center;
      border-radius: 4px; transition: background 0.15s, color 0.15s;
    }
    .dap-alert-dismiss:hover { background: rgba(255,255,255,0.06); color: #F0F4FF; }
    .dap-alert-choices { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    .dap-alert-choice-btn {
      padding: 4px 10px; border-radius: 14px; font-size: 11px; cursor: pointer;
      background: transparent; border: 1px solid rgba(255,165,0,0.45);
      color: rgba(255,165,0,0.9); font-family: inherit;
      transition: background 0.15s; white-space: nowrap;
    }
    .dap-alert-banner.severity-danger .dap-alert-choice-btn { border-color: rgba(255,68,68,0.45); color: rgba(255,100,100,0.9); }
    .dap-alert-banner.severity-info   .dap-alert-choice-btn { border-color: rgba(0,212,255,0.45); color: rgba(0,212,255,0.9); }
    .dap-alert-choice-btn:hover { background: rgba(255,165,0,0.1); }

    /* ── チャットパネル ── */
    #dap-chat-panel {
      position: fixed; bottom: 96px; right: 28px;
      width: 380px; height: 560px;
      background: #0D1220;
      border: 1px solid rgba(0,212,255,0.18);
      border-radius: 16px;
      display: flex; flex-direction: column;
      overflow: hidden; z-index: 999999;
      box-shadow: 0 20px 60px rgba(0,0,0,0.65), 0 0 0 1px rgba(0,212,255,0.04);
      transform: scale(0.92) translateY(12px); opacity: 0; pointer-events: none;
      transition: transform 0.25s cubic-bezier(.34,1.56,.64,1), opacity 0.2s ease;
    }
    #dap-chat-panel.is-open { transform: scale(1) translateY(0); opacity: 1; pointer-events: auto; }

    /* ── ヘッダー ── */
    .dap-chat-header {
      display: flex; align-items: center; gap: 10px;
      padding: 13px 14px 11px;
      border-bottom: 1px solid rgba(0,212,255,0.08);
      flex-shrink: 0; background: rgba(0,212,255,0.03);
    }
    .dap-chat-header-icon {
      width: 34px; height: 34px; border-radius: 50%;
      background: linear-gradient(135deg, rgba(0,212,255,0.15) 0%, rgba(0,153,204,0.15) 100%);
      border: 1px solid rgba(0,212,255,0.3);
      display: flex; align-items: center; justify-content: center;
      font-size: 16px; flex-shrink: 0;
    }
    .dap-chat-header-info { flex: 1; min-width: 0; }
    .dap-chat-header-title { font-size: 13px; font-weight: 700; color: #F0F4FF; line-height: 1.2; }
    .dap-chat-header-sub {
      font-size: 10px; color: rgba(0,212,255,0.65); margin-top: 1px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      display: flex; align-items: center; gap: 5px;
    }
    .dap-continuing-badge {
      display: inline-flex; align-items: center; gap: 3px;
      font-size: 9px; font-weight: 700; letter-spacing: 0.03em;
      background: rgba(255,180,0,0.15); color: rgba(255,180,0,0.9);
      border: 1px solid rgba(255,180,0,0.3); border-radius: 10px; padding: 1px 6px;
      white-space: nowrap; flex-shrink: 0;
    }
    .dap-chat-header-actions { display: flex; gap: 4px; flex-shrink: 0; align-items: center; }
    .dap-chat-header-btn {
      width: 28px; height: 28px; border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.08); background: transparent; cursor: pointer;
      color: #8892A4; font-size: 13px;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s, color 0.15s;
    }
    .dap-chat-header-btn:hover { background: rgba(255,255,255,0.06); color: #F0F4FF; }
    #dap-task-done-btn {
      display: none; padding: 3px 8px; border-radius: 5px; font-size: 10px; font-weight: 600;
      background: rgba(255,180,0,0.12); color: rgba(255,180,0,0.9);
      border: 1px solid rgba(255,180,0,0.35); cursor: pointer;
      white-space: nowrap; font-family: inherit; transition: background 0.15s;
    }
    #dap-task-done-btn:hover { background: rgba(255,180,0,0.22); }
    #dap-task-done-btn.is-visible { display: flex; align-items: center; gap: 3px; }

    /* ── ヒアリング進捗バー ── */
    #dap-intake-bar {
      display: none; padding: 7px 14px; font-size: 11px;
      background: rgba(0,212,255,0.04);
      border-bottom: 1px solid rgba(0,212,255,0.08);
      gap: 8px; align-items: center; flex-shrink: 0;
    }
    #dap-intake-bar.is-visible { display: flex; }
    .dap-intake-icon { font-size: 12px; flex-shrink: 0; }
    .dap-intake-info { flex: 1; min-width: 0; }
    .dap-intake-label { color: rgba(0,212,255,0.8); font-weight: 600; font-size: 10px; }
    .dap-intake-fields { color: #8892A4; font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .dap-intake-progress-wrap { flex-shrink: 0; }
    .dap-intake-turn { font-size: 10px; color: #5A6478; }
    .dap-risk-flags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 3px; }
    .dap-risk-tag {
      font-size: 9px; padding: 1px 6px; border-radius: 8px; font-weight: 600;
      background: rgba(255,100,50,0.15); color: rgba(255,120,60,0.9);
      border: 1px solid rgba(255,100,50,0.25);
    }

    /* ── メッセージエリア ── */
    .dap-chat-msgs {
      flex: 1; overflow-y: auto; padding: 14px 14px 8px;
      display: flex; flex-direction: column; gap: 10px; scroll-behavior: smooth;
    }
    .dap-chat-msgs::-webkit-scrollbar { width: 3px; }
    .dap-chat-msgs::-webkit-scrollbar-track { background: transparent; }
    .dap-chat-msgs::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }

    /* ── バブル ── */
    .dap-msg { display: flex; gap: 8px; align-items: flex-end; }
    .dap-msg.user { flex-direction: row-reverse; }
    .dap-msg-avatar {
      width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center; font-size: 12px;
    }
    .dap-msg.bot .dap-msg-avatar { background: rgba(0,212,255,0.1); border: 1px solid rgba(0,212,255,0.2); }
    .dap-msg.user .dap-msg-avatar { background: rgba(200,169,110,0.15); border: 1px solid rgba(200,169,110,0.2); }
    .dap-msg-bubble {
      max-width: 80%; padding: 9px 12px;
      border-radius: 14px; font-size: 13px; line-height: 1.6;
      word-break: break-word; white-space: pre-wrap;
    }
    .dap-msg.bot .dap-msg-bubble {
      background: #111827; color: #D0D8EC;
      border-radius: 4px 14px 14px 14px; border: 1px solid rgba(255,255,255,0.05);
    }
    .dap-msg.user .dap-msg-bubble {
      background: rgba(0,212,255,0.1); border: 1px solid rgba(0,212,255,0.18);
      color: #E8F4FF; border-radius: 14px 4px 14px 14px;
    }
    /* ガイダンスステップバブル */
    .dap-msg.guidance .dap-msg-bubble {
      background: rgba(0,212,255,0.05); border: 1px solid rgba(0,212,255,0.2);
      color: #A0C4D8; font-size: 12px; border-radius: 4px 14px 14px 14px;
    }

    /* ── ローディング ── */
    .dap-msg-loading .dap-msg-bubble { display: flex; align-items: center; gap: 5px; padding: 12px 14px; }
    .dap-dot { width: 6px; height: 6px; border-radius: 50%; background: rgba(0,212,255,0.5); animation: dap-bounce 1.1s ease-in-out infinite; }
    .dap-dot:nth-child(2) { animation-delay: 0.18s; }
    .dap-dot:nth-child(3) { animation-delay: 0.36s; }
    @keyframes dap-bounce { 0%,80%,100% { transform: translateY(0); opacity: 0.4; } 40% { transform: translateY(-5px); opacity: 1; } }

    /* ── ウェルカム ── */
    .dap-welcome { text-align: center; padding: 18px 8px; color: #8892A4; font-size: 12px; line-height: 1.7; }
    .dap-welcome strong { color: #00D4FF; display: block; font-size: 13px; margin-bottom: 5px; }
    .dap-welcome.is-continuing strong { color: rgba(255,180,0,0.9); }

    /* ── 選択肢 ── */
    .dap-choices { display: flex; flex-wrap: wrap; gap: 5px; padding: 5px 0 0 34px; }
    .dap-choice-btn {
      padding: 4px 11px; border-radius: 20px; font-size: 12px; cursor: pointer;
      background: transparent; border: 1px solid rgba(0,212,255,0.38);
      color: rgba(0,212,255,0.88); font-family: inherit;
      transition: background 0.15s, border-color 0.15s; line-height: 1.4;
    }
    .dap-choice-btn:hover:not(:disabled) { background: rgba(0,212,255,0.1); border-color: rgba(0,212,255,0.7); }
    .dap-choice-btn:disabled { opacity: 0.3; cursor: default; }

    /* ── 入力エリア ── */
    .dap-chat-input-wrap {
      padding: 11px 13px; border-top: 1px solid rgba(0,212,255,0.07);
      display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0;
    }
    #dap-chat-textarea {
      flex: 1; min-height: 38px; max-height: 100px;
      padding: 9px 12px; resize: none; overflow-y: auto;
      background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
      border-radius: 10px; color: #F0F4FF; font-size: 13px; line-height: 1.5;
      outline: none; transition: border-color 0.2s; font-family: inherit;
    }
    #dap-chat-textarea::placeholder { color: #3A4560; }
    #dap-chat-textarea:focus { border-color: rgba(0,212,255,0.32); }
    #dap-chat-textarea:disabled { opacity: 0.5; }
    #dap-chat-send {
      width: 36px; height: 36px; border-radius: 9px; flex-shrink: 0;
      background: #00D4FF; border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s, transform 0.1s;
    }
    #dap-chat-send:hover { background: #33DEFF; transform: scale(1.04); }
    #dap-chat-send:disabled { background: rgba(0,212,255,0.2); cursor: not-allowed; transform: none; }
    #dap-chat-send svg { width: 16px; height: 16px; fill: #0A0E1A; }

    /* ── ツールチップ ── */
    .dap-tooltip {
      position: fixed; z-index: 1000000;
      background: #0D1220; border: 1px solid rgba(0,212,255,0.5);
      color: #D0D8EC; font-size: 12px; line-height: 1.5;
      padding: 7px 11px; border-radius: 8px; max-width: 240px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.5);
      pointer-events: none; font-family: inherit;
      animation: dap-tooltip-in 0.2s ease;
    }
    @keyframes dap-tooltip-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
    .dap-tooltip::before {
      content: ''; position: absolute; bottom: 100%; left: 16px;
      border: 6px solid transparent; border-bottom-color: rgba(0,212,255,0.5);
      margin-bottom: -1px;
    }

    /* ── フィールドヒント ── */
    .dap-fill-hint {
      margin: 4px 0; padding: 7px 10px; border-radius: 7px;
      background: rgba(0,212,255,0.05); border: 1px solid rgba(0,212,255,0.2);
      font-size: 11px; animation: dap-tooltip-in 0.2s ease;
    }
    .dap-fill-hint-label { color: rgba(0,212,255,0.85); font-weight: 600; margin-bottom: 3px; }
    .dap-fill-hint-example { color: #5A6478; font-size: 10px; margin-top: 3px; font-style: italic; }

    /* ── スポットライトオーバーレイ ── */
    #dap-spotlight-overlay {
      position: fixed; inset: 0; z-index: 999990;
      pointer-events: none; opacity: 0;
      transition: opacity 0.35s ease;
    }
    #dap-spotlight-overlay.is-visible { opacity: 1; pointer-events: auto; }
    #dap-spotlight-canvas {
      position: absolute; inset: 0; width: 100%; height: 100%;
    }
    #dap-spotlight-tooltip {
      position: fixed; z-index: 999995;
      background: #0D1220; border: 1.5px solid rgba(0,212,255,0.65);
      color: #D0D8EC; font-size: 13px; line-height: 1.6;
      padding: 10px 14px; border-radius: 10px; max-width: 280px;
      box-shadow: 0 6px 28px rgba(0,0,0,0.6);
      font-family: 'DM Sans','Hiragino Sans',sans-serif;
      opacity: 0; transform: translateY(-6px);
      transition: opacity 0.25s ease, transform 0.25s ease;
      pointer-events: none;
    }
    #dap-spotlight-tooltip.is-visible { opacity: 1; transform: translateY(0); }
    #dap-spotlight-dismiss {
      position: fixed; z-index: 999996;
      bottom: 32px; left: 50%; transform: translateX(-50%);
      padding: 9px 26px; border-radius: 24px;
      background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.3);
      color: #fff; font-size: 13px; font-family: 'DM Sans','Hiragino Sans',sans-serif;
      cursor: pointer; backdrop-filter: blur(6px);
      opacity: 0; transition: opacity 0.3s ease;
      pointer-events: none;
    }
    #dap-spotlight-dismiss.is-visible { opacity: 1; pointer-events: auto; }

    /* ── 確認ボタン（ルーキー向けペース制御）── */
    .dap-guidance-ack-btn {
      display: block; margin: 10px 0 4px 34px;
      padding: 8px 22px; border-radius: 22px;
      background: linear-gradient(135deg, #00D4FF 0%, #0099CC 100%);
      color: #0A0E1A; border: none; font-size: 13px; font-weight: 700;
      cursor: pointer; font-family: inherit;
      transition: transform 0.15s, opacity 0.15s;
      animation: dap-tooltip-in 0.25s ease;
    }
    .dap-guidance-ack-btn:hover:not(:disabled) { transform: scale(1.04); }
    .dap-guidance-ack-btn:disabled { opacity: 0.4; cursor: default; }

    /* ── ワークフローモード ── */
    #dap-workflow-bar {
      display: none; flex-direction: column; gap: 6px;
      padding: 10px 14px; border-bottom: 1px solid rgba(0,212,255,0.08);
      background: rgba(0,212,255,0.04); flex-shrink: 0;
    }
    #dap-workflow-bar.is-visible { display: flex; }
    .dap-wf-top { display: flex; align-items: center; gap: 8px; }
    .dap-wf-label { font-size: 10px; font-weight: 700; color: rgba(0,212,255,0.75); letter-spacing: 0.06em; text-transform: uppercase; flex-shrink: 0; }
    .dap-wf-title { font-size: 11px; color: #C8D4EC; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .dap-wf-step-info { font-size: 10px; color: #5A6478; flex-shrink: 0; }
    .dap-wf-progress { height: 4px; background: rgba(255,255,255,0.07); border-radius: 2px; overflow: hidden; }
    .dap-wf-progress-fill { height: 100%; background: linear-gradient(90deg, #00D4FF, #0099CC); border-radius: 2px; transition: width 0.4s ease; }
    .dap-wf-actions { display: flex; gap: 6px; margin-top: 2px; }
    .dap-wf-btn {
      padding: 3px 10px; border-radius: 12px; font-size: 10px; font-weight: 600; cursor: pointer;
      font-family: inherit; transition: background 0.15s; white-space: nowrap;
    }
    .dap-wf-btn-next { background: rgba(0,212,255,0.15); border: 1px solid rgba(0,212,255,0.4); color: rgba(0,212,255,0.9); }
    .dap-wf-btn-next:hover { background: rgba(0,212,255,0.28); }
    .dap-wf-btn-stop { background: transparent; border: 1px solid rgba(255,255,255,0.1); color: #5A6478; }
    .dap-wf-btn-stop:hover { background: rgba(255,255,255,0.05); color: #8892A4; }

    /* ── UC 選択パネル ── */
    #dap-uc-panel {
      position: absolute; bottom: 100%; right: 0; left: 0; margin: 0 0 6px 0;
      background: #111827; border: 1px solid rgba(0,212,255,0.2);
      border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.55);
      overflow: hidden; z-index: 1000001;
      display: none; flex-direction: column;
      max-height: 340px; overflow-y: auto;
    }
    #dap-uc-panel.is-visible { display: flex; }
    .dap-uc-header { padding: 10px 14px; font-size: 11px; font-weight: 700; color: rgba(0,212,255,0.8); border-bottom: 1px solid rgba(0,212,255,0.1); flex-shrink: 0; }
    .dap-uc-item {
      padding: 10px 14px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.04);
      transition: background 0.12s;
    }
    .dap-uc-item:hover { background: rgba(0,212,255,0.06); }
    .dap-uc-item-title { font-size: 12px; font-weight: 600; color: #D0D8EC; }
    .dap-uc-item-persona { font-size: 10px; color: #5A6478; margin-top: 2px; }
    .dap-uc-item-desc { font-size: 10px; color: #7A8498; margin-top: 3px; line-height: 1.4; }
    .dap-uc-item-steps { font-size: 10px; color: rgba(0,212,255,0.5); margin-top: 2px; }
    .dap-uc-item-demo { background: linear-gradient(90deg, rgba(0,212,255,0.04), transparent); }
    .dap-uc-item-demo .dap-uc-item-title { color: #00D4FF; }

    /* ── デモモードインジケーター ── */
    #dap-workflow-bar.demo-mode {
      background: linear-gradient(135deg, rgba(0,16,32,0.98), rgba(0,8,20,0.99));
      border-bottom-color: rgba(0,212,255,0.18);
    }
    #dap-workflow-bar.demo-mode .dap-wf-label::before { content: '🎬 '; }
    #dap-workflow-bar.demo-mode .dap-wf-progress-fill {
      background: linear-gradient(90deg, #00D4FF 0%, #00FF88 100%);
    }

    /* ── タイプライターフィル アニメーション ── */
    @keyframes dap-fill-glow {
      0%   { box-shadow: 0 0 0 0 rgba(0,212,255,0.7); }
      50%  { box-shadow: 0 0 0 5px rgba(0,212,255,0.15); }
      100% { box-shadow: 0 0 0 0 rgba(0,212,255,0); }
    }
    .dap-fill-typing {
      border-color: rgba(0,212,255,0.85) !important;
      animation: dap-fill-glow 0.7s ease-out infinite !important;
    }
  `;
  document.head.appendChild(style);

  // ── DOM 構築 ───────────────────────────────────────────────────────
  const root = document.createElement('div');
  root.id = 'dap-chat-root';
  document.body.appendChild(root);

  // フローティングボタン + バッジ
  const btn = document.createElement('button');
  btn.id = 'dap-chat-btn';
  btn.setAttribute('aria-label', '先輩担当者に相談する');
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 10H6v-2h12v2zm0-3H6V7h12v2z"/>
    </svg>
    <span id="dap-chat-btn-badge"></span>
  `;
  root.appendChild(btn);

  // アラートバナー
  const alertBanner = document.createElement('div');
  alertBanner.id = 'dap-alert-banner';
  alertBanner.innerHTML = `
    <div class="dap-alert-inner">
      <div class="dap-alert-top">
        <div class="dap-alert-icon">⚠️</div>
        <div class="dap-alert-body">
          <div class="dap-alert-label">先輩からのお知らせ</div>
          <div class="dap-alert-msg" id="dap-alert-msg"></div>
        </div>
        <button class="dap-alert-dismiss" id="dap-alert-dismiss" title="閉じる">✕</button>
      </div>
      <div class="dap-alert-choices" id="dap-alert-choices"></div>
    </div>
  `;
  root.appendChild(alertBanner);

  // チャットパネル
  const panel = document.createElement('div');
  panel.id = 'dap-chat-panel';
  panel.innerHTML = `
    <div class="dap-chat-header">
      <div class="dap-chat-header-icon">👔</div>
      <div class="dap-chat-header-info">
        <div class="dap-chat-header-title">先輩担当者 (DAP)</div>
        <div class="dap-chat-header-sub" id="dap-module-label">${currentModule}</div>
      </div>
      <div class="dap-chat-header-actions">
        <button id="dap-task-done-btn" title="タスク完了・セッション終了">✓ 完了</button>
        <button class="dap-chat-header-btn" id="dap-chat-clear" title="会話を消去">🗑</button>
        <button class="dap-chat-header-btn" id="dap-chat-close" title="閉じる">✕</button>
      </div>
    </div>
    <div id="dap-intake-bar">
      <div class="dap-intake-icon">📋</div>
      <div class="dap-intake-info">
        <div class="dap-intake-label">ヒアリング進行中</div>
        <div class="dap-intake-fields" id="dap-intake-fields">品目・仕向国を確認中...</div>
        <div class="dap-risk-flags" id="dap-risk-flags"></div>
      </div>
      <div class="dap-intake-progress-wrap">
        <div class="dap-intake-turn" id="dap-intake-turn">─/8</div>
      </div>
    </div>
    <div id="dap-workflow-bar">
      <div class="dap-wf-top">
        <span class="dap-wf-label">業務フロー</span>
        <span class="dap-wf-title" id="dap-wf-title">—</span>
        <span class="dap-wf-step-info" id="dap-wf-step-info">0/0</span>
      </div>
      <div class="dap-wf-progress"><div class="dap-wf-progress-fill" id="dap-wf-progress-fill" style="width:0%"></div></div>
      <div class="dap-wf-actions">
        <button class="dap-wf-btn dap-wf-btn-next" id="dap-wf-next-btn">次のステップへ ▶</button>
        <button class="dap-wf-btn dap-wf-btn-stop" id="dap-wf-stop-btn">中断</button>
      </div>
    </div>
    <div class="dap-chat-msgs" id="dap-chat-msgs">
      <div class="dap-welcome">
        <strong>先輩担当者が伴走します</strong>
        今この画面で何をしようとしていますか？<br>
        状況を教えてもらえれば一緒に進めます。
      </div>
    </div>
    <div style="position:relative;">
      <div id="dap-uc-panel">
        <div class="dap-uc-header">📋 業務フローを選択してください</div>
        <div id="dap-uc-list"></div>
      </div>
    </div>
    <div class="dap-chat-input-wrap">
      <button id="dap-wf-start-btn" title="業務フロー開始" style="width:36px;height:36px;border-radius:9px;flex-shrink:0;background:rgba(0,212,255,0.1);border:1px solid rgba(0,212,255,0.25);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px;transition:background 0.15s;" onclick="toggleUcPanel()">📋</button>
      <textarea id="dap-chat-textarea" rows="1" placeholder="質問・相談をどうぞ… (Shift+Enter で送信)"></textarea>
      <button id="dap-chat-send" disabled>
        <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
      </button>
    </div>
  `;
  root.appendChild(panel);

  // ── スポットライトオーバーレイ DOM ────────────────────────────────
  const spotlightOverlay = document.createElement('div');
  spotlightOverlay.id = 'dap-spotlight-overlay';
  const spotlightCanvas = document.createElement('canvas');
  spotlightCanvas.id = 'dap-spotlight-canvas';
  spotlightOverlay.appendChild(spotlightCanvas);
  const spotlightTooltip = document.createElement('div');
  spotlightTooltip.id = 'dap-spotlight-tooltip';
  spotlightOverlay.appendChild(spotlightTooltip);
  const spotlightDismiss = document.createElement('button');
  spotlightDismiss.id = 'dap-spotlight-dismiss';
  spotlightDismiss.textContent = '確認しました ✓';
  spotlightOverlay.appendChild(spotlightDismiss);
  document.body.appendChild(spotlightOverlay);
  var _spotlightTimer = null;
  var _spotlightResolve = null;

  // ── 要素参照 ──────────────────────────────────────────────────────
  const msgsEl       = panel.querySelector('#dap-chat-msgs');
  const textarea     = panel.querySelector('#dap-chat-textarea');
  const sendBtn      = panel.querySelector('#dap-chat-send');
  const closeBtn     = panel.querySelector('#dap-chat-close');
  const clearBtn     = panel.querySelector('#dap-chat-clear');
  const taskDoneBtn  = panel.querySelector('#dap-task-done-btn');
  const moduleLabelEl = panel.querySelector('#dap-module-label');
  const intakeBar    = panel.querySelector('#dap-intake-bar');
  const intakeFields = panel.querySelector('#dap-intake-fields');
  const intakeTurn   = panel.querySelector('#dap-intake-turn');
  const riskFlags    = panel.querySelector('#dap-risk-flags');
  const alertMsg     = alertBanner.querySelector('#dap-alert-msg');
  const alertChoices = alertBanner.querySelector('#dap-alert-choices');
  const alertDismiss = alertBanner.querySelector('#dap-alert-dismiss');
  const chatBtnBadge = btn.querySelector('#dap-chat-btn-badge');
  let currentAlertGuideId = null;

  // ── ワークフローモード ────────────────────────────────────────────
  var _wfState = null;  // { uc_id, uc_title, current_step, total_steps, guidance }
  var _ucList = null;   // キャッシュ

  var wfBar      = panel.querySelector('#dap-workflow-bar');
  var wfTitle    = panel.querySelector('#dap-wf-title');
  var wfStepInfo = panel.querySelector('#dap-wf-step-info');
  var wfFill     = panel.querySelector('#dap-wf-progress-fill');
  var wfNextBtn  = panel.querySelector('#dap-wf-next-btn');
  var wfStopBtn  = panel.querySelector('#dap-wf-stop-btn');
  var ucPanel    = panel.querySelector('#dap-uc-panel');
  var ucListEl   = panel.querySelector('#dap-uc-list');

  function _updateWfBar() {
    if (!_wfState) {
      wfBar.classList.remove('is-visible', 'demo-mode');
      _saveWfState();
      return;
    }
    wfBar.classList.add('is-visible');
    if (_wfState.uc_id && _wfState.uc_id.startsWith('DEMO')) {
      wfBar.classList.add('demo-mode');
    } else {
      wfBar.classList.remove('demo-mode');
    }
    wfTitle.textContent = _wfState.uc_title;
    wfStepInfo.textContent = _wfState.current_step + '/' + _wfState.total_steps;
    var pct = _wfState.total_steps > 0 ? (_wfState.current_step - 1) / _wfState.total_steps * 100 : 0;
    wfFill.style.width = pct + '%';
    _saveWfState();
  }

  // guidance_steps が含まれる場合は executeGuidanceSteps で詳細案内を実行する。
  // 含まれない場合は旧来の navigate_to / highlight フォールバックを使う。
  function _runStepGuidance(guidance, delayMs) {
    if (!guidance) return;
    setTimeout(function () {
      if (guidance.guidance_steps && guidance.guidance_steps.length > 0) {
        executeGuidanceSteps(guidance.guidance_steps, 0);
      } else {
        // 後方互換フォールバック
        appendGuidanceMsg('👉 ' + guidance.title + ': ' + guidance.detail);
        if (guidance.navigate_to) {
          waitForUserAck('画面を開く →', 20000).then(function () { _portalNavigate(guidance.navigate_to); });
        } else if (guidance.highlight) {
          setTimeout(function () { highlightElementWithTooltip(guidance.highlight, guidance.detail, 8000); }, 600);
        }
      }
    }, delayMs || 400);
  }

  async function _startWorkflow(ucId) {
    ucPanel.classList.remove('is-visible');
    const data = await apiPost('/api/workflow/start', { session_id: SESSION_ID, uc_id: ucId });
    _wfState = {
      uc_id: ucId,
      uc_title: data.uc_title,
      current_step: data.current_step,
      total_steps: data.total_steps,
      guidance: data.next_guidance,
    };
    _updateWfBar();
    appendMsg('bot', '📋 ' + data.message);
    _runStepGuidance(data.next_guidance, 400);
  }

  async function _advanceWorkflow() {
    if (!_wfState) return;
    const data = await apiPost('/api/workflow/complete_step', {
      session_id: SESSION_ID,
      step_num: _wfState.current_step,
    });
    if (data.status === 'completed') {
      appendMsg('bot', data.message);
      _wfState = null;
      _updateWfBar();
      return;
    }
    _wfState.current_step = data.current_step;
    _wfState.guidance = data.next_guidance;
    _updateWfBar();
    appendMsg('bot', '✅ ステップ完了。次: ' + (data.message || ''));
    if (data.next_guidance) {
      const g = data.next_guidance;
      appendGuidanceMsg('👉 Step ' + g.step_num + ': ' + g.title + ' — ' + g.detail);
      _runStepGuidance(g, 300);
    }
  }

  async function _stopWorkflow() {
    if (!_wfState) return;
    await fetch(DAP_BASE + '/api/workflow/abandon?session_id=' + encodeURIComponent(SESSION_ID), { method: 'POST' }).catch(function () {});
    _wfState = null;
    _updateWfBar();
    appendMsg('bot', '業務フローを中断しました。');
  }

  async function _loadUcList() {
    if (_ucList) return _ucList;
    const resp = await fetch(DAP_BASE + '/api/workflow/uc-list');
    _ucList = resp.ok ? await resp.json() : [];
    return _ucList;
  }

  function toggleUcPanel() {
    if (ucPanel.classList.contains('is-visible')) {
      ucPanel.classList.remove('is-visible');
      return;
    }
    _loadUcList().then(function (list) {
      ucListEl.innerHTML = '';
      list.forEach(function (uc) {
        const isDemo = uc.uc_id && uc.uc_id.startsWith('DEMO');
        const item = document.createElement('div');
        item.className = 'dap-uc-item' + (isDemo ? ' dap-uc-item-demo' : '');
        item.innerHTML = '<div class="dap-uc-item-title">' + (isDemo ? '🎬 ' : '') + uc.uc_id + ': ' + uc.title + '</div>'
          + '<div class="dap-uc-item-persona">' + uc.persona + '</div>'
          + (uc.description ? '<div class="dap-uc-item-desc">' + uc.description + '</div>' : '')
          + '<div class="dap-uc-item-steps">' + uc.total_steps + ' ステップ</div>';
        item.addEventListener('click', function () { _startWorkflow(uc.uc_id); });
        ucListEl.appendChild(item);
      });
      ucPanel.classList.add('is-visible');
    }).catch(function () {
      appendMsg('bot', 'UC 一覧の取得に失敗しました。DAP が起動しているか確認してください。');
    });
  }
  window.toggleUcPanel = toggleUcPanel;

  wfNextBtn.addEventListener('click', _advanceWorkflow);
  wfStopBtn.addEventListener('click', _stopWorkflow);

  // ── アラートバナー ────────────────────────────────────────────────
  function showAlertBanner(alert, choices) {
    if (!alert || !alert.message) return;
    const guideId = alert.guide_id || alert.type;
    if (guideId && isGuideShown(guideId)) return;

    currentAlertGuideId = guideId;
    alertBanner.className = '';
    alertBanner.classList.add('severity-' + (alert.severity || 'warn'));
    alertMsg.textContent = alert.message;

    alertChoices.innerHTML = '';
    (choices || []).slice(0, 3).forEach(function (c) {
      const b = document.createElement('button');
      b.className = 'dap-alert-choice-btn';
      b.textContent = c.label;
      b.addEventListener('click', function () {
        dismissAlertBanner();
        openPanel();
        setTimeout(function () { handleSend(c.message || c.label); }, 200);
      });
      alertChoices.appendChild(b);
    });

    alertBanner.classList.add('is-visible');
    chatBtnBadge.classList.add('is-visible');
    chatBtnBadge.textContent = '!';

    // 30秒後に自動消去
    setTimeout(dismissAlertBanner, 30000);
  }

  function dismissAlertBanner() {
    alertBanner.classList.remove('is-visible');
    chatBtnBadge.classList.remove('is-visible');
    if (currentAlertGuideId) {
      markGuideShown(currentAlertGuideId);
      trackEvent('guide_dismissed', { guide_id: currentAlertGuideId });
      currentAlertGuideId = null;
    }
  }

  alertDismiss.addEventListener('click', dismissAlertBanner);

  // ── パネル開閉 ─────────────────────────────────────────────────────
  let isOpen = false;
  function openPanel() {
    isOpen = true;
    panel.classList.add('is-open');
    btn.style.animation = 'none';
    alertBanner.classList.remove('is-visible');  // パネル開時はバナー非表示
    textarea.focus();
  }
  function closePanel() {
    isOpen = false;
    panel.classList.remove('is-open');
    btn.style.animation = '';
  }
  btn.addEventListener('click', function () { isOpen ? closePanel() : openPanel(); });
  closeBtn.addEventListener('click', closePanel);

  // ── セッションリセット ────────────────────────────────────────────
  function resetSession(title, body) {
    fetch(DAP_BASE + '/api/chat/session/' + SESSION_ID, { method: 'DELETE' }).catch(function () {});
    moduleLabelEl.innerHTML = currentModule;
    taskDoneBtn.classList.remove('is-visible');
    intakeBar.classList.remove('is-visible');
    msgsEl.innerHTML = `<div class="dap-welcome"><strong>${title}</strong>${body}</div>`;
    // shown_guides はセッションごとにクリア（次回は改めてガイド表示）
    try { sessionStorage.removeItem('dap_shown'); } catch (e) {}
  }

  clearBtn.addEventListener('click', function () {
    resetSession('会話を消去しました', '<br>新しい相談をどうぞ。');
  });
  taskDoneBtn.addEventListener('click', function () {
    resetSession('タスクを完了しました 👏', '<br>お疲れ様でした。次の業務があれば何でも。');
  });

  // ── ヒアリング進捗バー更新 ────────────────────────────────────────
  function updateIntakeBar(intake) {
    if (!intake || intake.is_complete) {
      intakeBar.classList.remove('is-visible');
      return;
    }
    intakeBar.classList.add('is-visible');
    const parts = [];
    if (intake.product_name)        parts.push('品目: ' + intake.product_name);
    if (intake.destination_country) parts.push('仕向国: ' + intake.destination_country);
    intakeFields.textContent = parts.length ? parts.join(' / ') : '情報収集中...';
    intakeTurn.textContent = (intake.turn_count || 0) + '/8';

    riskFlags.innerHTML = '';
    (intake.risk_flags || []).forEach(function (flag) {
      const tag = document.createElement('span');
      tag.className = 'dap-risk-tag';
      tag.textContent = flag.slice(0, 20);
      riskFlags.appendChild(tag);
    });
  }

  // ── ツールチップ ──────────────────────────────────────────────────
  function showTooltipNear(el, tooltipText, durationMs) {
    if (!el || !tooltipText) return;
    const rect = el.getBoundingClientRect();
    const tip = document.createElement('div');
    tip.className = 'dap-tooltip';
    tip.textContent = tooltipText;
    document.body.appendChild(tip);
    // 位置計算（要素の下に配置）
    const tipRect = tip.getBoundingClientRect();
    let top = rect.bottom + 8;
    let left = rect.left;
    if (left + 240 > window.innerWidth) left = window.innerWidth - 250;
    if (top + tipRect.height > window.innerHeight) top = rect.top - tipRect.height - 8;
    tip.style.top  = top  + 'px';
    tip.style.left = left + 'px';
    const dur = durationMs || 5000;
    setTimeout(function () { if (tip.parentNode) tip.remove(); }, dur);
    return tip;
  }

  /**
   * スポットライトモーダルで該当要素を強調する。
   * 背景をグレーアウト（Canvas）し、対象要素のみ切り抜いて浮かび上がらせる。
   * @returns {Promise} dismiss ボタン押下 or タイムアウトで resolve
   */
  function highlightElementWithTooltip(target, tooltipText, durationMs) {
    const el = findInteractiveElement(target) || findFieldElement(target);
    if (!el) return Promise.resolve();

    return new Promise(function (resolve) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });

      // スクロール完了を待ってから描画
      setTimeout(function () {
        var dur = durationMs || 7000;
        var rect = el.getBoundingClientRect();
        var ifrOff = getIframeOffset();
        var pad = 12;
        var rx = rect.left - pad + ifrOff.left;
        var ry = rect.top - pad + ifrOff.top;
        var rw = rect.width + pad * 2;
        var rh = rect.height + pad * 2;
        var borderR = 10;

        // Canvas でグレーアウト + 切り抜き穴
        var cvs = spotlightCanvas;
        cvs.width  = window.innerWidth;
        cvs.height = window.innerHeight;
        var ctx = cvs.getContext('2d');
        ctx.clearRect(0, 0, cvs.width, cvs.height);
        ctx.fillStyle = 'rgba(0,0,0,0.62)';
        ctx.fillRect(0, 0, cvs.width, cvs.height);
        // 切り抜き（composite destination-out）
        ctx.globalCompositeOperation = 'destination-out';
        ctx.beginPath();
        ctx.moveTo(rx + borderR, ry);
        ctx.lineTo(rx + rw - borderR, ry);
        ctx.quadraticCurveTo(rx + rw, ry, rx + rw, ry + borderR);
        ctx.lineTo(rx + rw, ry + rh - borderR);
        ctx.quadraticCurveTo(rx + rw, ry + rh, rx + rw - borderR, ry + rh);
        ctx.lineTo(rx + borderR, ry + rh);
        ctx.quadraticCurveTo(rx, ry + rh, rx, ry + rh - borderR);
        ctx.lineTo(rx, ry + borderR);
        ctx.quadraticCurveTo(rx, ry, rx + borderR, ry);
        ctx.closePath();
        ctx.fill();
        ctx.globalCompositeOperation = 'source-over';
        // 切り抜き周囲にシアンのグロー
        ctx.strokeStyle = 'rgba(0,212,255,0.85)';
        ctx.lineWidth = 2.5;
        ctx.shadowColor = '#00D4FF';
        ctx.shadowBlur = 18;
        ctx.stroke();

        // ツールチップ配置（切り抜き枠の下、または上）
        if (tooltipText) {
          spotlightTooltip.textContent = tooltipText;
          var tipTop = ry + rh + 14;
          if (tipTop + 80 > window.innerHeight) tipTop = ry - 80;
          var tipLeft = rx;
          if (tipLeft + 290 > window.innerWidth) tipLeft = window.innerWidth - 295;
          spotlightTooltip.style.top  = tipTop + 'px';
          spotlightTooltip.style.left = tipLeft + 'px';
          spotlightTooltip.style.display = 'block';
          setTimeout(function () { spotlightTooltip.classList.add('is-visible'); }, 50);
        }

        // オーバーレイ表示
        spotlightOverlay.classList.add('is-visible');
        setTimeout(function () { spotlightDismiss.classList.add('is-visible'); }, 300);

        var done = false;
        function dismiss() {
          if (done) return;
          done = true;
          spotlightDismiss.classList.remove('is-visible');
          spotlightTooltip.classList.remove('is-visible');
          setTimeout(function () {
            spotlightOverlay.classList.remove('is-visible');
            var c = spotlightCanvas.getContext('2d');
            c.clearRect(0, 0, spotlightCanvas.width, spotlightCanvas.height);
          }, 350);
          if (_spotlightTimer) { clearTimeout(_spotlightTimer); _spotlightTimer = null; }
          resolve();
        }

        _spotlightTimer = setTimeout(dismiss, dur);
        spotlightDismiss.onclick = dismiss;
        // オーバーレイ自体をクリックしても閉じる（ただし切り抜き部分は pass-through）
        spotlightCanvas.onclick = dismiss;
      }, 350);
    });
  }

  function showFillHintNear(target, hint, example) {
    const el = findFieldElement(target);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const prev = el.style.borderColor;
    el.style.borderColor = 'rgba(0,212,255,0.6)';
    // ヒントエレメントを挿入
    const hintEl = document.createElement('div');
    hintEl.className = 'dap-fill-hint';
    hintEl.innerHTML =
      '<div class="dap-fill-hint-label">💡 ' + hint + '</div>' +
      (example ? '<div class="dap-fill-hint-example">例: ' + example + '</div>' : '');
    el.parentNode && el.parentNode.insertBefore(hintEl, el.nextSibling);
    setTimeout(function () {
      el.style.borderColor = prev;
      if (hintEl.parentNode) hintEl.remove();
    }, 8000);
    el.focus();
  }

  // ── ポータル対応ナビゲーション ────────────────────────────────────
  // ポート番号 → ポータルのモジュールキー対応表
  var _PORT_TO_MODULE = {
    '8001': 'ai_validation',
    '8002': 'ai_classification',
    '8003': 'rnd_assessment',
    '8004': 'patent_search',
    '8005': 'screening',
    '8006': 'hs_classifier',
    '8010': 'dap',
  };

  // localhost URL を外部公開 URL に書き換える（外部アクセス時のみ）
  function _rewriteLocalUrl(url) {
    if (!url || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') return url;
    if (!/https?:\/\/localhost/.test(url)) return url;
    // app.{domain} = ポータルのホスト
    var parts = window.location.hostname.split('.');
    var portalOrigin = window.location.protocol + '//app.' + parts.slice(-2).join('.');
    return url.replace(/https?:\/\/localhost:\d+/, portalOrigin);
  }

  /**
   * URL に応じてナビゲートする。
   * - ポータル上（window.__dap_portal_navigate__ あり）: iframe を切り替える
   * - スタンドアロン: window.location.href で直接移動
   */
  function _portalNavigate(url) {
    url = _rewriteLocalUrl(url);
    if (!url) return;
    var _frame = document.getElementById('module-frame');
    // ポータル上（iframe あり）の場合はフルページ遷移せず iframe 内で処理
    if (_frame && typeof window.__dap_portal_navigate__ === 'function') {
      try {
        var parsed = new URL(url, window.location.href);
        var port   = parsed.port;
        var moduleKey = _PORT_TO_MODULE[port];
        // /proxy/KEY/... 形式のURLにも対応
        if (!moduleKey) {
          var proxyMatch = parsed.pathname.match(/^\/proxy\/([^/]+)(\/.*)?$/);
          if (proxyMatch) moduleKey = proxyMatch[1];
        }
        if (moduleKey) {
          var subPath = parsed.pathname + parsed.search + parsed.hash;
          // /proxy/{key}/... 形式の場合はプレフィックスを除去（二重プロキシ防止）
          var proxyPrefix = '/proxy/' + moduleKey;
          if (subPath.startsWith(proxyPrefix)) {
            subPath = subPath.slice(proxyPrefix.length) || '/';
          }
          window.__dap_portal_navigate__(moduleKey, subPath);
          return;
        }
        // 同一オリジンの /ui/* など platform-core 直提供ページ: iframe に直接ロード
        // （フルページ遷移するとポータルから離れ DAP ウィジェットが消える）
        if (parsed.origin === window.location.origin) {
          _frame.src = parsed.pathname + parsed.search + parsed.hash;
          return;
        }
      } catch (e) { /* URL解析失敗 → フォールバック */ }
    }
    window.location.href = url;
  }

  // ── アクション実行 ────────────────────────────────────────────────
  function executeActions(actions) {
    if (!Array.isArray(actions)) return;
    actions.forEach(function (action) {
      if (!action || !action.type) return;
      switch (action.type) {
        case 'highlight':
          highlightElementWithTooltip(action.target, action.tooltip || null);
          break;
        case 'fill_field':
          const fieldEl = findFieldElement(action.target);
          if (fieldEl && action.value) {
            fieldEl.value = action.value;
            fieldEl.dispatchEvent(new Event('input', { bubbles: true }));
            fieldEl.dispatchEvent(new Event('change', { bubbles: true }));
            fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            fieldEl.focus();
          }
          break;
        case 'navigate_to':
          if (action.url) {
            setTimeout(function () { _portalNavigate(action.url); }, 800);
          }
          break;
        case 'start_workflow':
          if (action.uc_id) {
            setTimeout(function () { _startWorkflow(action.uc_id); }, 200);
          }
          break;
      }
    });
  }

  // ── タイプライターフィル（デモモード用）────────────────────────────
  function typewriterFill(el, text, speedMs) {
    return new Promise(function (resolve) {
      if (!el) { resolve(); return; }
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.focus();
      el.value = '';
      el.classList.add('dap-fill-typing');
      var i = 0;
      var sp = speedMs || 55;
      function typeNext() {
        if (i >= text.length) {
          el.classList.remove('dap-fill-typing');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          resolve();
          return;
        }
        el.value += text[i++];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        setTimeout(typeNext, sp);
      }
      setTimeout(typeNext, 250);
    });
  }

  // ── guidance ステップ実行 ─────────────────────────────────────────
  let guidanceRunning = false;
  async function executeGuidanceSteps(steps, fromIndex) {
    if (!steps || !steps.length || guidanceRunning) return;
    guidanceRunning = true;
    const start = fromIndex || 0;
    for (let i = start; i < steps.length; i++) {
      const step = steps[i];
      if (!step) continue;
      switch (step.type) {
        case 'navigate':
          if (step.url) {
            appendGuidanceMsg('📍 ' + (step.message || '次のページに移動します'));
            await sleep(1200);
            var _isDemoNav = _wfState && _wfState.uc_id && _wfState.uc_id.startsWith('DEMO');
            if (!_isDemoNav) {
              appendGuidanceMsg('移動先でも続きをご案内しますので、慌てなくて大丈夫です 👍');
              await waitForUserAck('ページを開く →', 30000);
            }
            var _portalFrame = document.getElementById('module-frame');
            if (_portalFrame && typeof window.__dap_portal_navigate__ === 'function') {
              // ポータル iframe モード: iframe load を待って同一コンテキストで続行
              await new Promise(function (resolve) {
                var _loadDone = false;
                function _onLoad() {
                  if (_loadDone) return;
                  _loadDone = true;
                  _portalFrame.removeEventListener('load', _onLoad);
                  resolve();
                }
                _portalFrame.addEventListener('load', _onLoad);
                _portalNavigate(step.url);
                setTimeout(function () { _onLoad(); }, 8000); // タイムアウト保険
              });
              // DEMO はリダイレクト経由が多いため長めに待つ
              var _navWait = (_wfState && _wfState.uc_id && _wfState.uc_id.startsWith('DEMO')) ? 1600 : 800;
              await sleep(_navWait); // DOM 安定待ち
              appendGuidanceMsg('✅ ページに移動しました。続けてご案内します。');
              await sleep(600);
              break; // ループを continue（return しない）
            }
            // スタンドアロンモード: フルページ遷移（セッションに保存して再開）
            savePendingGuidance(steps, i + 1);
            guidanceRunning = false;
            var _navUrl = step.url;
            try {
              _navUrl += (_navUrl.indexOf('?') >= 0 ? '&' : '?') + '_dap_from=' + (i + 1);
            } catch(e) {}
            _portalNavigate(_navUrl);
            return;
          }
          break;
        case 'highlight':
          appendGuidanceMsg('👆 ' + step.message);
          await sleep(1200);  // メッセージを読む時間
          // スポットライトモーダル — dismiss or タイムアウトまで await
          await highlightElementWithTooltip(step.target, step.tooltip, 7000);
          await sleep(400);   // フェードアウト完了待ち
          break;
        case 'fill_hint':
          appendGuidanceMsg('📝 ' + step.message);
          await sleep(1200);  // メッセージを読む時間（600ms → 1200ms）
          showFillHintNear(step.target, step.hint || step.message, step.example);
          await sleep(4000);  // 入力を確認する時間（2000ms → 4000ms）
          break;
        case 'explain':
          appendGuidanceMsg(step.message);
          await sleep(2800);  // 説明を読む時間（1200ms → 2800ms）
          break;
        case 'watch':
          appendGuidanceMsg('⏳ ' + step.message);
          if (step.target) highlightElementWithTooltip(step.target, step.tooltip, 10000);  // ハイライト延長
          await sleep(4000);  // 操作待ち時間（1500ms → 4000ms）
          break;
        case 'fill_field_from_context':
          // クロスモジュール引き継ぎ: セッションの transfer_context から値を取得してフィールドに入力
          try {
            const ctxResp = await fetch(DAP_BASE + '/api/chat/transfer-context?session_id=' + encodeURIComponent(SESSION_ID));
            const ctx = ctxResp.ok ? await ctxResp.json() : {};
            const val = ctx[step.context_key];
            if (val) {
              const fieldEl = findFieldElement(step.target);
              if (fieldEl) {
                fieldEl.value = val;
                fieldEl.dispatchEvent(new Event('input', { bubbles: true }));
                fieldEl.dispatchEvent(new Event('change', { bubbles: true }));
                fieldEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                fieldEl.focus();
                appendGuidanceMsg('✅ ' + step.target + ' に「' + val + '」を入力しました（前の手続きから引き継ぎ）');
              } else {
                appendGuidanceMsg('📝 ' + step.target + ' に「' + val + '」を入力してください（前の手続きから引き継ぎ）');
                if (step.target) showFillHintNear(step.target, val, null);
              }
            } else {
              // 引き継ぎデータがない場合は通常の fill_hint にフォールバック
              appendGuidanceMsg('📝 ' + (step.message || step.target + ' を入力してください'));
              if (step.target && step.hint) showFillHintNear(step.target, step.hint, step.example);
            }
          } catch (e) {
            appendGuidanceMsg('📝 ' + (step.message || step.target + ' を入力してください'));
          }
          await sleep(2500);
          break;
        case 'fill_demo':
          // デモモード専用: タイプライターアニメーションでフィールドに値を入力
          appendGuidanceMsg('⌨️ ' + (step.message || (step.target + ' にデモデータを入力します')));
          await sleep(600);
          {
            const demoEl = findFieldElement(step.target);
            if (demoEl && step.value) {
              await typewriterFill(demoEl, step.value, 45);
              appendGuidanceMsg('✅ 「' + step.target + '」に入力しました');
            } else {
              appendGuidanceMsg('⚠ フィールド「' + (step.target || '?') + '」が見つかりません。手動で入力してください。');
            }
          }
          await sleep(900);
          break;
        case 'api_call':
          // バックグラウンドAPI呼び出し（デモデータシードなど）
          try {
            if (step.message) appendGuidanceMsg('⚙️ ' + step.message);
            var apiUrl = _rewriteLocalUrl(step.url || '');
            await fetch(apiUrl, {
              method: (step.method || 'POST').toUpperCase(),
              headers: { 'Content-Type': 'application/json' },
              body: step.body ? JSON.stringify(step.body) : undefined
            });
            await sleep(300);
          } catch(e) {}
          break;
        case 'pause':
          // プレゼンターコントロール: ボタンを押すまで停止（autoMs=0 → 無制限待機）
          if (step.message) appendGuidanceMsg('⏸ ' + step.message);
          await waitForUserAck(step.button_label || '次へ →', 0);
          break;
      }
    }
    guidanceRunning = false;
    // デモモード: guidance_steps 完了後に自動で次ステップへ進める
    if (_wfState && _wfState.uc_id && _wfState.uc_id.startsWith('DEMO')) {
      setTimeout(function () {
        if (_wfState) _advanceWorkflow();
      }, 800);
    }
  }

  function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  /**
   * ルーキー向けペース制御: ボタンを表示してクリック待ち
   * @param {string} label   ボタンラベル
   * @param {number} autoMs  自動進行するまでの ms（0 = 無制限）
   */
  function waitForUserAck(label, autoMs) {
    return new Promise(function (resolve) {
      var btn = document.createElement('button');
      btn.className = 'dap-guidance-ack-btn';
      btn.textContent = label || '準備OK →';
      msgsEl.appendChild(btn);
      msgsEl.scrollTop = msgsEl.scrollHeight;
      var done = false;
      function finish() {
        if (done) return;
        done = true;
        btn.disabled = true;
        btn.style.opacity = '0.4';
        setTimeout(function () { if (btn.parentNode) btn.remove(); }, 400);
        resolve();
      }
      btn.addEventListener('click', finish);
      if (autoMs > 0) setTimeout(finish, autoMs);
    });
  }

  function appendGuidanceMsg(text) {
    const wrap = document.createElement('div');
    wrap.className = 'dap-msg guidance';
    const avatar = document.createElement('div');
    avatar.className = 'dap-msg-avatar';
    avatar.textContent = '👔';
    const bubble = document.createElement('div');
    bubble.className = 'dap-msg-bubble';
    bubble.textContent = text;
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  // ── メッセージ表示 ────────────────────────────────────────────────
  function appendMsg(role, text) {
    const wrap = document.createElement('div');
    wrap.className = 'dap-msg ' + (role === 'user' ? 'user' : 'bot');
    const avatar = document.createElement('div');
    avatar.className = 'dap-msg-avatar';
    avatar.textContent = role === 'user' ? '👤' : '👔';
    const bubble = document.createElement('div');
    bubble.className = 'dap-msg-bubble';
    bubble.textContent = text;
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return wrap;
  }

  function appendBotMessage(reply, actions, choices, guidance, intakeState) {
    if (reply) appendMsg('bot', reply);
    if (actions && actions.length > 0)  setTimeout(function () { executeActions(actions); }, 300);
    if (choices && choices.length > 0) {
      const choicesEl = document.createElement('div');
      choicesEl.className = 'dap-choices';
      choices.forEach(function (choice) {
        const choiceBtn = document.createElement('button');
        choiceBtn.className = 'dap-choice-btn';
        choiceBtn.textContent = choice.label || '';
        choiceBtn.addEventListener('click', function () {
          choicesEl.querySelectorAll('.dap-choice-btn').forEach(function (b) { b.disabled = true; });
          handleSend(choice.message || choice.label);
        });
        choicesEl.appendChild(choiceBtn);
      });
      msgsEl.appendChild(choicesEl);
      msgsEl.scrollTop = msgsEl.scrollHeight;
    }
    // ヒアリング進捗バー更新
    if (intakeState) updateIntakeBar(intakeState);
    // ガイダンスステップ実行（非ブロッキング）
    if (guidance && guidance.length > 0) {
      setTimeout(function () { executeGuidanceSteps(guidance, 0); }, 500);
    }
  }

  function appendLoading() {
    const wrap = document.createElement('div');
    wrap.className = 'dap-msg bot dap-msg-loading';
    const avatar = document.createElement('div');
    avatar.className = 'dap-msg-avatar';
    avatar.textContent = '👔';
    const bubble = document.createElement('div');
    bubble.className = 'dap-msg-bubble';
    bubble.innerHTML = '<div class="dap-dot"></div><div class="dap-dot"></div><div class="dap-dot"></div>';
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return wrap;
  }

  // ── 送信 ──────────────────────────────────────────────────────────
  async function handleSend(overrideText) {
    const text = overrideText !== undefined ? overrideText : textarea.value.trim();
    if (!text) return;

    if (overrideText === undefined) { textarea.value = ''; textarea.style.height = 'auto'; }
    sendBtn.disabled = true;
    textarea.disabled = true;
    dismissAlertBanner();  // 送信時はアラートバナーを閉じる

    appendMsg('user', text);
    const loadingEl = appendLoading();

    try {
      const data = await sendMessage(text);
      loadingEl.remove();
      appendBotMessage(data.reply, data.actions, data.choices, data.guidance, data.intake_state);
      // バックエンドからのアラートを処理
      if (data.alert && data.alert.message) {
        setTimeout(function () { showAlertBanner(data.alert, []); }, 2000);
      }
    } catch (err) {
      loadingEl.remove();
      appendMsg('bot', '⚠ エラーが発生しました: ' + err.message);
    } finally {
      textarea.disabled = false;
      sendBtn.disabled = !textarea.value.trim();
      textarea.focus();
    }
  }

  textarea.addEventListener('input', function () {
    sendBtn.disabled = !this.value.trim();
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 100) + 'px';
  });
  textarea.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.shiftKey) { e.preventDefault(); if (!sendBtn.disabled) handleSend(); }
  });
  sendBtn.addEventListener('click', function () { handleSend(); });

  // ── ページロード時の初期化 ─────────────────────────────────────────
  // Step 1: ページ訪問イベント記録
  trackEvent('page_view', { port: window.location.port, page_path: window.location.pathname });

  // Step 1b: ワークフロー状態の復元（ページ遷移後）
  // _dap_from URL パラメータがあればクロスオリジン遷移（サーバーから再取得）
  // なければ sessionStorage から復元（同一オリジン遷移）
  var _urlSearchParams = new URLSearchParams(window.location.search);
  var _dapFromParam = _urlSearchParams.get('_dap_from');
  var _crossOriginResume = _dapFromParam !== null;
  var _crossOriginFromIdx = _crossOriginResume ? parseInt(_dapFromParam, 10) : -1;
  if (_crossOriginResume) {
    // URL をクリーンにする
    try { history.replaceState({}, '', window.location.pathname + window.location.hash); } catch(e) {}
  } else {
    // 同一オリジン: sessionStorage から復元
    var _restoredWfState = _restoreWfState();
    if (_restoredWfState) { _wfState = _restoredWfState; _updateWfBar(); }
  }

  // Step 2: クロスページ guidance ペンディングチェック
  var pendingGuidance = null;
  if (_crossOriginResume && _crossOriginFromIdx >= 0) {
    // クロスオリジン遷移後: サーバーからセッション状態を取得して再開
    setTimeout(function () {
      fetch(DAP_BASE + '/api/workflow/status?session_id=' + encodeURIComponent(SESSION_ID))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.uc_id) return;
          _wfState = { uc_id: d.uc_id, uc_title: d.uc_title, current_step: d.current_step, total_steps: d.total_steps, guidance: d.next_guidance };
          _updateWfBar();
          var gs = d.next_guidance && d.next_guidance.guidance_steps;
          if (!gs || _crossOriginFromIdx >= gs.length) return;
          openPanel();
          appendMsg('bot', '👔 新しいページに移動しました。まずページの内容をざっと確認してみてください。');
          setTimeout(function () {
            appendMsg('bot', '準備ができたら下のボタンを押してください。焦らなくて大丈夫です！');
            waitForUserAck('続ける →', 60000).then(function () {
              executeGuidanceSteps(gs, _crossOriginFromIdx);
            });
          }, 1500);
        })
        .catch(function () {});
    }, 2000);
  } else {
    pendingGuidance = loadPendingGuidance();
    if (pendingGuidance && pendingGuidance.steps) {
      setTimeout(function () {
        openPanel();
        appendMsg('bot', '👔 新しいページに移動しました。まずページの内容をざっと確認してみてください。');
        setTimeout(function () {
          appendMsg('bot', '準備ができたら下のボタンを押してください。焦らなくて大丈夫です！');
          waitForUserAck('続ける →', 60000).then(function () {
            executeGuidanceSteps(pendingGuidance.steps, pendingGuidance.from || 0);
          });
        }, 1500);
      }, 2500);
    }
  }

  // Step 3: 既存セッション確認 + greet
  fetch(DAP_BASE + '/api/chat/session/' + SESSION_ID)
    .then(function (res) { return res.ok ? res.json() : null; })
    .then(function (data) {
      if (data && data.has_history) {
        moduleLabelEl.innerHTML = currentModule + ' <span class="dap-continuing-badge">↩ 引継中</span>';
        taskDoneBtn.classList.add('is-visible');
        msgsEl.innerHTML = `<div class="dap-welcome is-continuing">
          <strong>前の会話を引き継いでいます</strong>
          タスクの続きを支援します。完了したら「✓ 完了」を押してください。
        </div>`;
      }

      // Step 4: プロアクティブ greet（1.8秒後）
      // 条件: pending guidance なし / チャット未開 / 初回訪問 / ユーザーが自分で操作していない
      var _prevVisitCount = recordPageVisit();  // 訪問記録 & 前回訪問数取得
      if (!pendingGuidance) {
        setTimeout(function () {
          if (isOpen) return;              // チャット開いてたらスキップ
          if (_prevVisitCount > 0) return; // 再訪問ページはスキップ
          if (_userEngaged) return;        // ユーザーが自分で操作中ならスキップ
          callGreet().then(function (greetData) {
            if (!greetData || !greetData.reply) return;
            const alert = greetData.alert || { message: greetData.reply, severity: 'info', guide_id: 'greet_' + Date.now() };
            showAlertBanner(alert, greetData.choices || []);
          });
        }, 1800);
      }
    })
    .catch(function () {
      // DAP 未起動時: silently skip、greet もスキップ
    });

})();
