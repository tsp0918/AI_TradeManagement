// cache-bust: 1772970000
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

  const DAP_BASE = 'http://localhost:8010';

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
  function findInteractiveElement(label) {
    if (!label) return null;
    return Array.from(document.querySelectorAll('a[href],button,[role="button"]'))
      .find(function (el) {
        const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
        return t === label || t.includes(label) || (label.includes(t) && t.length > 2);
      }) || null;
  }
  function findFieldElement(label) {
    if (!label) return null;
    return Array.from(document.querySelectorAll('input[type=text],input[type=email],input[type=number],textarea,select'))
      .find(function (el) {
        return [el.placeholder, el.name, el.id, el.getAttribute('aria-label')]
          .some(function (v) { return v && v.includes(label); });
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
    <div class="dap-chat-msgs" id="dap-chat-msgs">
      <div class="dap-welcome">
        <strong>先輩担当者が伴走します</strong>
        今この画面で何をしようとしていますか？<br>
        状況を教えてもらえれば一緒に進めます。
      </div>
    </div>
    <div class="dap-chat-input-wrap">
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
    const el = findInteractiveElement(target);
    if (!el) return Promise.resolve();

    return new Promise(function (resolve) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });

      // スクロール完了を待ってから描画
      setTimeout(function () {
        var dur = durationMs || 7000;
        var rect = el.getBoundingClientRect();
        var pad = 12;
        var rx = rect.left - pad;
        var ry = rect.top - pad;
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
            setTimeout(function () { window.location.href = action.url; }, 800);
          }
          break;
      }
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
            // 残りのステップを保存してからナビゲート
            savePendingGuidance(steps, i + 1);
            appendGuidanceMsg('📍 ' + (step.message || '次のページに移動します'));
            await sleep(1800);  // メッセージを読む時間
            appendGuidanceMsg('移動先でも続きをご案内しますので、慌てなくて大丈夫です 👍');
            await waitForUserAck('ページを開く →', 30000);  // ユーザーが準備できたら
            window.location.href = step.url;
            return;  // ページ遷移するのでここで終わり
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
      }
    }
    guidanceRunning = false;
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

  // Step 2: クロスページ guidance ペンディングチェック
  const pendingGuidance = loadPendingGuidance();
  if (pendingGuidance && pendingGuidance.steps) {
    // ページが落ち着いてから（2.5秒後）チャットを開く
    setTimeout(function () {
      openPanel();
      appendMsg('bot', '👔 新しいページに移動しました。まずページの内容をざっと確認してみてください。');
      // 1.5秒後に「続ける」ボタンを表示 — ユーザーが準備できたら案内を再開
      setTimeout(function () {
        appendMsg('bot', '準備ができたら下のボタンを押してください。焦らなくて大丈夫です！');
        waitForUserAck('続ける →', 60000).then(function () {
          executeGuidanceSteps(pendingGuidance.steps, pendingGuidance.from || 0);
        });
      }, 1500);
    }, 2500);
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

      // Step 4: プロアクティブ greet（1.8秒後、チャットが閉じている間だけ）
      if (!pendingGuidance) {
        setTimeout(function () {
          if (isOpen) return;  // チャット開いてたらスキップ
          callGreet().then(function (greetData) {
            if (!greetData || !greetData.reply) return;
            // アラートバナーとして表示（チャットを開かずに）
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
