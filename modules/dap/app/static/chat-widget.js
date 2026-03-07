// cache-bust: 1772960000
/**
 * DAP Chat Widget
 * ダークテーマ対応のフローティングチャットボット
 * Claude API (via DAP port 8010 /api/chat) と通信する
 * 構造化レスポンス（reply / actions / choices）でUI操作まで完結させる
 * クロスページセッション: cookie で session_id を共有、サーバー側で履歴を保持
 */
(function () {
  'use strict';

  // 重複挿入ガード（メインフレームのみ。iframeへの誤注入を防ぐ）
  if (window !== window.top) return;   // iframe は無視
  if (document.getElementById('dap-chat-root')) return;

  const DAP_BASE = 'http://localhost:8010';

  // ── セッションID（cookie 経由でポートをまたいで共有）────────────────
  // RFC 6265: cookie の domain にポートは含まれない。
  // localhost:8003 でセットした cookie は localhost:8002 でも読める。
  function getOrCreateSessionId() {
    const match = document.cookie.match(/(?:^|;\s*)dap_session_id=([^;]+)/);
    if (match) return match[1];
    const id = 'dap_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 9);
    document.cookie = 'dap_session_id=' + id + '; path=/; SameSite=Lax; Max-Age=86400';
    return id;
  }
  const SESSION_ID = getOrCreateSessionId();

  // ── モジュール名（port → 表示名。document.title でも補完）─────────
  function detectCurrentModule() {
    const portMap = {
      '8000': 'プラットフォーム',
      '8001': 'AI 該非判定',
      '8002': '品目管理',
      '8003': 'R&D リスク管理',
      '8004': '特許検索',
      '8005': 'スクリーニング',
      '8006': 'HS コード判定',
      '8010': 'DAP 管理',
    };
    const port = window.location.port;
    if (portMap[port]) return portMap[port];
    // port が取れない場合は document.title でフォールバック
    const t = document.title || '';
    if (/R&?D|リスク管理/.test(t))     return 'R&D リスク管理';
    if (/品目|item/i.test(t))           return '品目管理';
    if (/スクリーニング/i.test(t))       return 'スクリーニング';
    if (/特許|patent/i.test(t))         return '特許検索';
    if (/該非|判定/i.test(t))           return 'AI 該非判定';
    return 'コンプライアンス支援';
  }
  const currentModule = detectCurrentModule();

  // ── コンテキスト収集 ─────────────────────────────────────────────
  function gatherContext() {
    const port      = window.location.port;
    const page_path = window.location.pathname;

    // 可視フォームフィールドをスキャン（label + value、最大 8 件）
    const form_fields = {};
    let count = 0;
    document.querySelectorAll('input[type=text], input[type=search], textarea, select').forEach(function (el) {
      if (count >= 8) return;
      if (!el.offsetParent) return;
      const rawVal = (el.value || '').trim();
      if (!rawVal) return;
      let label = '';
      if (el.labels && el.labels[0]) {
        label = el.labels[0].textContent.trim();
      } else if (el.name) {
        label = el.name;
      } else if (el.id) {
        label = el.id;
      } else if (el.placeholder) {
        label = el.placeholder;
      }
      if (!label) return;
      form_fields[label] = rawVal.slice(0, 150);
      count++;
    });

    // インタラクティブ要素（ボタン・リンク）をスキャン（最大 20 件）
    const interactive_elements = [];
    document.querySelectorAll('a[href], button, [role="button"]').forEach(function (el) {
      if (!el.offsetParent) return;
      const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
      if (!text || text.length > 40) return;
      interactive_elements.push({ label: text });
    });

    // 各モジュールが任意で注入できる追加コンテキスト（将来拡張用）
    const extra = window.__dap_chat_context__ || {};

    return Object.assign({ port, page_path, form_fields, interactive_elements: interactive_elements.slice(0, 20) }, extra);
  }

  // ── 要素検索ヘルパー ─────────────────────────────────────────────
  function findInteractiveElement(label) {
    if (!label) return null;
    const candidates = Array.from(document.querySelectorAll('a[href], button, [role="button"]'));
    return candidates.find(function (el) {
      const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
      return t === label || t.includes(label) || (label.includes(t) && t.length > 2);
    }) || null;
  }

  function findFieldElement(label) {
    if (!label) return null;
    const inputs = Array.from(document.querySelectorAll('input[type=text], input[type=email], input[type=number], textarea, select'));
    return inputs.find(function (el) {
      return [el.placeholder, el.name, el.id, el.getAttribute('aria-label')]
        .some(function (v) { return v && v.includes(label); });
    }) || null;
  }

  // ── アクション実行 ───────────────────────────────────────────────
  function executeActions(actions) {
    if (!Array.isArray(actions)) return;
    actions.forEach(function (action) {
      if (!action || !action.type) return;

      if (action.type === 'highlight') {
        const el = findInteractiveElement(action.target);
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        if (window.DAPOverlay && typeof window.DAPOverlay.highlight === 'function') {
          const dismiss = window.DAPOverlay.highlight(el);
          setTimeout(dismiss, 6000);
        } else {
          // DAPOverlay がない場合は CSS で代替ハイライト
          const prev = el.style.cssText;
          el.style.outline = '2px solid #00D4FF';
          el.style.outlineOffset = '3px';
          el.style.transition = 'outline 0.3s';
          setTimeout(function () { el.style.cssText = prev; }, 6000);
        }

      } else if (action.type === 'fill_field') {
        const el = findFieldElement(action.target);
        if (!el || !action.value) return;
        if (window.DAPOverlay && typeof window.DAPOverlay.insertTemplateInto === 'function') {
          window.DAPOverlay.insertTemplateInto(el, action.value);
        } else {
          el.value = action.value;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.focus();
      }
    });
  }

  // ── API 呼び出し ─────────────────────────────────────────────────
  async function sendMessage(userText) {
    const body = {
      message: userText,
      history: [],          // サーバーサイドセッションがある場合は使われない
      context: gatherContext(),
      session_id: SESSION_ID,
    };
    const resp = await fetch(DAP_BASE + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(function () { return {}; });
      throw new Error(err.detail || 'API エラー (' + resp.status + ')');
    }
    const data = await resp.json();
    return {
      reply:   data.reply   || '',
      actions: data.actions || [],
      choices: data.choices || [],
    };
  }

  // ── DOM 構築 ─────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #dap-chat-root * { box-sizing: border-box; font-family: 'DM Sans', 'Hiragino Sans', sans-serif; }

    /* ── フローティングボタン ── */
    #dap-chat-btn {
      position: fixed;
      bottom: 28px; right: 28px;
      width: 56px; height: 56px;
      border-radius: 50%;
      background: #00D4FF;
      border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 0 0 0 rgba(0,212,255,0.4), 0 4px 20px rgba(0,212,255,0.35);
      animation: dap-pulse 2.8s infinite;
      z-index: 999998;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    #dap-chat-btn:hover {
      transform: scale(1.08);
      box-shadow: 0 0 0 6px rgba(0,212,255,0.15), 0 6px 24px rgba(0,212,255,0.45);
    }
    #dap-chat-btn svg { width: 26px; height: 26px; fill: #0A0E1A; pointer-events: none; }
    @keyframes dap-pulse {
      0%   { box-shadow: 0 0 0 0 rgba(0,212,255,0.4), 0 4px 20px rgba(0,212,255,0.35); }
      60%  { box-shadow: 0 0 0 10px rgba(0,212,255,0), 0 4px 20px rgba(0,212,255,0.35); }
      100% { box-shadow: 0 0 0 0 rgba(0,212,255,0), 0 4px 20px rgba(0,212,255,0.35); }
    }

    /* ── チャットパネル ── */
    #dap-chat-panel {
      position: fixed;
      bottom: 96px; right: 28px;
      width: 360px; height: 520px;
      background: #0D1220;
      border: 1px solid rgba(0,212,255,0.2);
      border-radius: 16px;
      display: flex; flex-direction: column;
      overflow: hidden;
      z-index: 999999;
      box-shadow: 0 16px 48px rgba(0,0,0,0.6), 0 0 0 1px rgba(0,212,255,0.05);
      transform: scale(0.92) translateY(12px);
      opacity: 0;
      pointer-events: none;
      transition: transform 0.25s cubic-bezier(.34,1.56,.64,1), opacity 0.2s ease;
    }
    #dap-chat-panel.is-open {
      transform: scale(1) translateY(0);
      opacity: 1;
      pointer-events: auto;
    }

    /* ── ヘッダー ── */
    .dap-chat-header {
      display: flex; align-items: center; gap: 10px;
      padding: 14px 16px 12px;
      border-bottom: 1px solid rgba(0,212,255,0.1);
      flex-shrink: 0;
    }
    .dap-chat-header-icon {
      width: 32px; height: 32px; border-radius: 50%;
      background: rgba(0,212,255,0.12);
      border: 1px solid rgba(0,212,255,0.25);
      display: flex; align-items: center; justify-content: center;
      font-size: 15px; flex-shrink: 0;
    }
    .dap-chat-header-info { flex: 1; min-width: 0; }
    .dap-chat-header-title {
      font-size: 13px; font-weight: 600;
      color: #F0F4FF; line-height: 1.2;
    }
    .dap-chat-header-sub {
      font-size: 10px; color: rgba(0,212,255,0.7);
      margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      display: flex; align-items: center; gap: 5px;
    }
    .dap-continuing-badge {
      display: inline-flex; align-items: center; gap: 3px;
      font-size: 9px; font-weight: 600; letter-spacing: 0.03em;
      background: rgba(255,180,0,0.15); color: rgba(255,180,0,0.9);
      border: 1px solid rgba(255,180,0,0.3);
      border-radius: 10px; padding: 1px 6px;
      white-space: nowrap; flex-shrink: 0;
    }
    .dap-chat-header-actions { display: flex; gap: 4px; flex-shrink: 0; align-items: center; }
    .dap-chat-header-btn {
      width: 28px; height: 28px; border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.08);
      background: transparent; cursor: pointer;
      color: #8892A4; font-size: 13px;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s, color 0.15s;
    }
    .dap-chat-header-btn:hover { background: rgba(255,255,255,0.06); color: #F0F4FF; }
    #dap-task-done-btn {
      display: none;
      padding: 3px 8px; border-radius: 5px; font-size: 10px; font-weight: 600;
      background: rgba(255,180,0,0.12); color: rgba(255,180,0,0.9);
      border: 1px solid rgba(255,180,0,0.35); cursor: pointer;
      white-space: nowrap; font-family: inherit;
      transition: background 0.15s;
    }
    #dap-task-done-btn:hover { background: rgba(255,180,0,0.22); }
    #dap-task-done-btn.is-visible { display: flex; align-items: center; gap: 3px; }

    /* ── メッセージエリア ── */
    .dap-chat-msgs {
      flex: 1; overflow-y: auto; padding: 16px;
      display: flex; flex-direction: column; gap: 12px;
      scroll-behavior: smooth;
    }
    .dap-chat-msgs::-webkit-scrollbar { width: 4px; }
    .dap-chat-msgs::-webkit-scrollbar-track { background: transparent; }
    .dap-chat-msgs::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }

    /* ── メッセージバブル ── */
    .dap-msg { display: flex; gap: 8px; align-items: flex-end; }
    .dap-msg.user { flex-direction: row-reverse; }
    .dap-msg-avatar {
      width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 12px;
    }
    .dap-msg.bot .dap-msg-avatar {
      background: rgba(0,212,255,0.1); border: 1px solid rgba(0,212,255,0.2);
    }
    .dap-msg.user .dap-msg-avatar {
      background: rgba(200,169,110,0.15); border: 1px solid rgba(200,169,110,0.2);
    }
    .dap-msg-bubble {
      max-width: 78%; padding: 10px 13px;
      border-radius: 14px; font-size: 13px; line-height: 1.6;
      word-break: break-word; white-space: pre-wrap;
    }
    .dap-msg.bot .dap-msg-bubble {
      background: #111827; color: #D0D8EC;
      border-radius: 4px 14px 14px 14px;
      border: 1px solid rgba(255,255,255,0.05);
    }
    .dap-msg.user .dap-msg-bubble {
      background: rgba(0,212,255,0.12);
      border: 1px solid rgba(0,212,255,0.2);
      color: #E8F4FF;
      border-radius: 14px 4px 14px 14px;
    }

    /* ── ローディング ── */
    .dap-msg-loading .dap-msg-bubble {
      display: flex; align-items: center; gap: 5px;
      padding: 12px 14px;
    }
    .dap-dot {
      width: 6px; height: 6px; border-radius: 50%;
      background: rgba(0,212,255,0.5);
      animation: dap-bounce 1.1s ease-in-out infinite;
    }
    .dap-dot:nth-child(2) { animation-delay: 0.18s; }
    .dap-dot:nth-child(3) { animation-delay: 0.36s; }
    @keyframes dap-bounce {
      0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
      40% { transform: translateY(-5px); opacity: 1; }
    }

    /* ── ウェルカムメッセージ ── */
    .dap-welcome {
      text-align: center; padding: 20px 8px;
      color: #8892A4; font-size: 12px; line-height: 1.7;
    }
    .dap-welcome strong { color: #00D4FF; display: block; font-size: 13px; margin-bottom: 6px; }
    .dap-welcome.is-continuing strong { color: rgba(255,180,0,0.9); }

    /* ── 選択肢ボタン ── */
    .dap-choices {
      display: flex; flex-wrap: wrap; gap: 6px;
      padding: 6px 0 0 34px;
    }
    .dap-choice-btn {
      padding: 5px 12px; border-radius: 20px; font-size: 12px; cursor: pointer;
      background: transparent;
      border: 1px solid rgba(0,212,255,0.4);
      color: rgba(0,212,255,0.9);
      font-family: inherit;
      transition: background 0.15s, border-color 0.15s;
      line-height: 1.4;
    }
    .dap-choice-btn:hover:not(:disabled) { background: rgba(0,212,255,0.1); border-color: rgba(0,212,255,0.7); }
    .dap-choice-btn:disabled { opacity: 0.35; cursor: default; }

    /* ── 入力エリア ── */
    .dap-chat-input-wrap {
      padding: 12px 14px;
      border-top: 1px solid rgba(0,212,255,0.08);
      display: flex; gap: 8px; align-items: flex-end;
      flex-shrink: 0;
    }
    #dap-chat-textarea {
      flex: 1; min-height: 38px; max-height: 100px;
      padding: 9px 12px; resize: none; overflow-y: auto;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 10px; color: #F0F4FF; font-size: 13px;
      line-height: 1.5; outline: none;
      transition: border-color 0.2s;
      font-family: inherit;
    }
    #dap-chat-textarea::placeholder { color: #4A5568; }
    #dap-chat-textarea:focus { border-color: rgba(0,212,255,0.35); }
    #dap-chat-textarea:disabled { opacity: 0.5; }
    #dap-chat-send {
      width: 36px; height: 36px; border-radius: 9px; flex-shrink: 0;
      background: #00D4FF; border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s, transform 0.1s;
    }
    #dap-chat-send:hover { background: #33DEFF; transform: scale(1.04); }
    #dap-chat-send:disabled { background: rgba(0,212,255,0.25); cursor: not-allowed; transform: none; }
    #dap-chat-send svg { width: 16px; height: 16px; fill: #0A0E1A; }
  `;
  document.head.appendChild(style);

  // ルート要素
  const root = document.createElement('div');
  root.id = 'dap-chat-root';
  document.body.appendChild(root);

  // フローティングボタン
  const btn = document.createElement('button');
  btn.id = 'dap-chat-btn';
  btn.setAttribute('aria-label', 'AI アシスタントを開く');
  btn.innerHTML = `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 2C6.477 2 2 6.253 2 11.5c0 2.304.851 4.41 2.254 6.047L3 21l3.75-1.124A10.07 10.07 0 0012 21c5.523 0 10-4.253 10-9.5S17.523 2 12 2zm-1 13H9v-2h2v2zm0-4H9V7h2v4zm4 4h-2v-2h2v2zm0-4h-2V7h2v4z"/>
  </svg>`;
  root.appendChild(btn);

  // チャットパネル
  const panel = document.createElement('div');
  panel.id = 'dap-chat-panel';
  panel.innerHTML = `
    <div class="dap-chat-header">
      <div class="dap-chat-header-icon">🤖</div>
      <div class="dap-chat-header-info">
        <div class="dap-chat-header-title">AI アシスタント</div>
        <div class="dap-chat-header-sub" id="dap-module-label">${currentModule}</div>
      </div>
      <div class="dap-chat-header-actions">
        <button id="dap-task-done-btn" title="このタスクを完了としてセッションを終了">✓ タスク完了</button>
        <button class="dap-chat-header-btn" id="dap-chat-clear" title="会話を消去">🗑</button>
        <button class="dap-chat-header-btn" id="dap-chat-close" title="閉じる">✕</button>
      </div>
    </div>
    <div class="dap-chat-msgs" id="dap-chat-msgs">
      <div class="dap-welcome">
        <strong>こんにちは！</strong>
        今の画面について何でもお聞きください。<br>
        操作方法や入力のヒントをお伝えします。
      </div>
    </div>
    <div class="dap-chat-input-wrap">
      <textarea id="dap-chat-textarea" rows="1" placeholder="質問を入力… (Shift+Enter で送信)"></textarea>
      <button id="dap-chat-send" disabled>
        <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
      </button>
    </div>
  `;
  root.appendChild(panel);

  // ── 要素参照 ─────────────────────────────────────────────────────
  const msgsEl       = panel.querySelector('#dap-chat-msgs');
  const textarea     = panel.querySelector('#dap-chat-textarea');
  const sendBtn      = panel.querySelector('#dap-chat-send');
  const closeBtn     = panel.querySelector('#dap-chat-close');
  const clearBtn     = panel.querySelector('#dap-chat-clear');
  const taskDoneBtn  = panel.querySelector('#dap-task-done-btn');
  const moduleLabelEl = panel.querySelector('#dap-module-label');

  // ── パネル開閉 ────────────────────────────────────────────────────
  let isOpen = false;
  function openPanel() {
    isOpen = true;
    panel.classList.add('is-open');
    btn.style.animation = 'none';
    textarea.focus();
  }
  function closePanel() {
    isOpen = false;
    panel.classList.remove('is-open');
    btn.style.animation = '';
  }
  btn.addEventListener('click', function () { isOpen ? closePanel() : openPanel(); });
  closeBtn.addEventListener('click', closePanel);

  // ── セッションリセット共通処理 ────────────────────────────────────
  function resetSession(welcomeTitle, welcomeBody) {
    fetch(DAP_BASE + '/api/chat/session/' + SESSION_ID, { method: 'DELETE' }).catch(function () {});
    // ヘッダーをリセット
    moduleLabelEl.innerHTML = currentModule;
    taskDoneBtn.classList.remove('is-visible');
    msgsEl.innerHTML = `<div class="dap-welcome">
      <strong>${welcomeTitle}</strong>${welcomeBody}
    </div>`;
  }

  // ── 会話クリア（🗑 ボタン）────────────────────────────────────────
  clearBtn.addEventListener('click', function () {
    resetSession('会話を消去しました', '<br>新しい質問をどうぞ。');
  });

  // ── タスク完了ボタン ─────────────────────────────────────────────
  taskDoneBtn.addEventListener('click', function () {
    resetSession('タスクを完了しました', '<br>新しい業務を始める場合は何でもお聞きください。');
  });

  // ── メッセージ追加 ────────────────────────────────────────────────
  function appendMsg(role, text) {
    const wrap = document.createElement('div');
    wrap.className = 'dap-msg ' + (role === 'user' ? 'user' : 'bot');
    const avatar = document.createElement('div');
    avatar.className = 'dap-msg-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';
    const bubble = document.createElement('div');
    bubble.className = 'dap-msg-bubble';
    bubble.textContent = text;
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return wrap;
  }

  // ── ボット返答（アクション実行 + 選択肢ボタン付き）───────────────
  function appendBotMessage(reply, actions, choices) {
    // テキストバブル
    const msgWrap = appendMsg('bot', reply);

    // アクション実行（ハイライト・フィールド転記）
    if (actions && actions.length > 0) {
      setTimeout(function () { executeActions(actions); }, 300);
    }

    // 選択肢ボタン
    if (choices && choices.length > 0) {
      const choicesEl = document.createElement('div');
      choicesEl.className = 'dap-choices';
      choices.forEach(function (choice) {
        const choiceBtn = document.createElement('button');
        choiceBtn.className = 'dap-choice-btn';
        choiceBtn.textContent = choice.label || '';
        choiceBtn.addEventListener('click', function () {
          // 同グループの全ボタンを無効化
          choicesEl.querySelectorAll('.dap-choice-btn').forEach(function (b) {
            b.disabled = true;
          });
          handleSend(choice.message || choice.label);
        });
        choicesEl.appendChild(choiceBtn);
      });
      msgsEl.appendChild(choicesEl);
      msgsEl.scrollTop = msgsEl.scrollHeight;
    }

    return msgWrap;
  }

  function appendLoading() {
    const wrap = document.createElement('div');
    wrap.className = 'dap-msg bot dap-msg-loading';
    const avatar = document.createElement('div');
    avatar.className = 'dap-msg-avatar';
    avatar.textContent = '🤖';
    const bubble = document.createElement('div');
    bubble.className = 'dap-msg-bubble';
    bubble.innerHTML = '<div class="dap-dot"></div><div class="dap-dot"></div><div class="dap-dot"></div>';
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return wrap;
  }

  // ── 送信ロジック ──────────────────────────────────────────────────
  async function handleSend(overrideText) {
    const text = overrideText !== undefined ? overrideText : textarea.value.trim();
    if (!text) return;

    // UI 更新
    if (overrideText === undefined) {
      textarea.value = '';
      textarea.style.height = 'auto';
    }
    sendBtn.disabled = true;
    textarea.disabled = true;

    appendMsg('user', text);
    const loadingEl = appendLoading();

    try {
      const { reply, actions, choices } = await sendMessage(text);
      loadingEl.remove();
      appendBotMessage(reply, actions, choices);
    } catch (err) {
      loadingEl.remove();
      appendMsg('bot', '⚠ エラーが発生しました: ' + err.message);
    } finally {
      textarea.disabled = false;
      sendBtn.disabled = !textarea.value.trim();
      textarea.focus();
    }
  }

  // ── 入力イベント ──────────────────────────────────────────────────
  textarea.addEventListener('input', function () {
    sendBtn.disabled = !this.value.trim();
    // 高さ自動調整
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 100) + 'px';
  });

  textarea.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) handleSend();
    }
  });

  sendBtn.addEventListener('click', function () { handleSend(); });

  // ── クロスページセッション継続チェック ───────────────────────────
  // ページロード後、既存セッションの有無を確認してインジケーターを表示
  (function checkExistingSession() {
    fetch(DAP_BASE + '/api/chat/session/' + SESSION_ID)
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data || !data.has_history) return;
        // 前のページから会話が継続している
        // モジュール名は常に「現在のページ」のものを表示
        moduleLabelEl.innerHTML = currentModule +
          ' <span class="dap-continuing-badge">↩ 引継中</span>';
        taskDoneBtn.classList.add('is-visible');
        msgsEl.innerHTML = `<div class="dap-welcome is-continuing">
          <strong>前の会話を引き継いでいます</strong>
          タスクの続きから支援します。完了したら「✓ タスク完了」を押してください。
        </div>`;
      })
      .catch(function () { /* DAP未起動時などは無視 */ });
  })();

})();
