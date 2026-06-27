/**
 * DAP Tutorial Widget v2 — 歩調を合わせるチュートリアル
 *
 * v1 からの改善:
 *  - navigate: 自動遷移を廃止、ユーザーがボタンを押して遷移
 *  - highlight: 対象要素クリックを検知するまで「次へ」を無効化
 *  - fill_hint: 対象フィールドへの入力を検知するまで「次へ」を無効化
 *  - sessionStorage でページ遷移をまたいで状態を復元
 *  - 常時表示の「スキップ」リンクで詰まりを防止
 */
(function () {
  'use strict';

  if (window.__dap_tutorial_loaded__) return;
  window.__dap_tutorial_loaded__ = true;

  // ── 定数 ──────────────────────────────────────────────────────────────────
  const DAP_BASE = (function () {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8010';
    const parts = h.split('.');
    if (parts.length >= 2) return window.location.protocol + '//dap.' + parts.slice(-2).join('.');
    return 'http://localhost:8010';
  })();

  const PLATFORM_URL = (function () {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8000';
    const parts = h.split('.');
    if (parts.length >= 2) return window.location.protocol + '//app.' + parts.slice(-2).join('.');
    return 'http://localhost:8000';
  })();

  const _TKEY     = 'dap_tut_state'; // sessionStorage キー
  const _SID_KEY  = 'dap_tut_session'; // セッション ID
  let _SESSION_ID = (function () {
    let s = sessionStorage.getItem(_SID_KEY);
    if (!s) { s = 'tut_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 7); sessionStorage.setItem(_SID_KEY, s); }
    return s;
  })();
  let _gsShownAt = 0; // guidance_step 表示開始時刻

  // ── 行動イベント発火（fire-and-forget）────────────────────────────────────
  function _emit(event_name, context) {
    const payload = {
      session_id: _SESSION_ID,
      module_key: 'dap',
      event_type: 'tutorial',
      event_name,
      context: context || {},
    };
    fetch(DAP_BASE + '/api/ux-events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(function () {});
  }

  function _stepCtx(extra) {
    const step = _uc ? (_uc.steps || [])[_stepIdx] : null;
    return Object.assign({
      uc_id:    _uc ? _uc.id : null,
      step_num: step ? step.num : null,
      gs_idx:   _gsIdx,
    }, extra || {});
  }

  // ── 状態 ──────────────────────────────────────────────────────────────────
  let _uc               = null;
  let _stepIdx          = 0;
  let _gsIdx            = 0;
  let _chatHist         = [];
  let _rafId            = null;
  let _highlightEl      = null;
  let _ucList           = [];
  let _gsCompleted      = true;  // 現在の guidance_step のユーザーアクションが完了したか
  let _completionCleanups = [];  // step 変更時に除去すべきリスナー群

  // ── セッション状態の保存/復元 ─────────────────────────────────────────────
  function _saveState() {
    if (!_uc) return;
    sessionStorage.setItem(_TKEY, JSON.stringify({
      ucId: _uc.id, stepIdx: _stepIdx, gsIdx: _gsIdx,
    }));
  }

  function _loadState() {
    try {
      const raw = sessionStorage.getItem(_TKEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function _clearState() { sessionStorage.removeItem(_TKEY); }

  // ── 完了状態管理 ──────────────────────────────────────────────────────────
  function cleanupCompletionWatchers() {
    _completionCleanups.forEach(fn => { try { fn(); } catch (e) {} });
    _completionCleanups = [];
  }

  function markGsCompleted() {
    _gsCompleted = true;
    _emit('tut_action_done', _stepCtx({ elapsed_ms: Date.now() - _gsShownAt }));
    updateNextButtonState();
  }

  function updateNextButtonState() {
    const btn  = document.getElementById('dap-tut-next-btn');
    const hint = document.getElementById('dap-tut-action-hint');
    if (!btn) return;
    btn.disabled      = !_gsCompleted;
    btn.style.opacity = _gsCompleted ? '1' : '0.38';
    btn.style.cursor  = _gsCompleted ? 'pointer' : 'not-allowed';
    if (hint) hint.style.display = _gsCompleted ? 'none' : 'flex';
  }

  // ── CSS ───────────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('dap-tut-style')) return;
    const s = document.createElement('style');
    s.id = 'dap-tut-style';
    s.textContent = `
@keyframes dap-tut-pulse {
  0%   { box-shadow:0 0 0 0 rgba(0,212,255,.5),0 0 0 3px rgba(0,212,255,.2); }
  60%  { box-shadow:0 0 0 12px rgba(0,212,255,0),0 0 0 3px rgba(0,212,255,.1); }
  100% { box-shadow:0 0 0 0 rgba(0,212,255,0),0 0 0 3px rgba(0,212,255,.05); }
}
@keyframes dap-tut-slidein {
  from { opacity:0; transform:translateY(14px); }
  to   { opacity:1; transform:translateY(0); }
}

/* FAB */
#dap-tut-fab {
  position:fixed;bottom:88px;right:20px;z-index:2147483640;
  width:44px;height:44px;border-radius:50%;
  background:linear-gradient(135deg,#0f172a,#1e3a5f);
  border:1.5px solid rgba(0,212,255,.4);
  color:#00d4ff;font-size:18px;cursor:pointer;
  box-shadow:0 4px 16px rgba(0,0,0,.4);
  display:flex;align-items:center;justify-content:center;
  transition:transform .15s,box-shadow .15s;
}
#dap-tut-fab:hover{transform:scale(1.1);box-shadow:0 6px 20px rgba(0,212,255,.3);}
#dap-tut-fab[data-active="true"]{background:linear-gradient(135deg,#0a3a5f,#0066cc);border-color:rgba(0,212,255,.8);}

/* UC ピッカー */
#dap-tut-picker {
  position:fixed;bottom:140px;right:20px;z-index:2147483641;
  width:340px;max-height:480px;overflow-y:auto;
  background:#0f172a;border:1px solid rgba(0,212,255,.25);
  border-radius:12px;padding:12px;
  box-shadow:0 8px 32px rgba(0,0,0,.6);
  animation:dap-tut-slidein .2s ease;display:none;
  font-family:system-ui,-apple-system,"Noto Sans JP",sans-serif;
}
#dap-tut-picker h3{color:#00d4ff;font-size:13px;font-weight:600;margin:0 0 10px;padding-bottom:8px;border-bottom:1px solid rgba(0,212,255,.15);}
.dap-tut-uc-item{padding:9px 10px;border-radius:8px;cursor:pointer;border:1px solid rgba(255,255,255,.06);margin-bottom:6px;background:rgba(255,255,255,.03);transition:background .12s;}
.dap-tut-uc-item:hover{background:rgba(0,212,255,.08);border-color:rgba(0,212,255,.3);}
.dap-tut-uc-title{color:#e2e8f0;font-size:12px;font-weight:500;}
.dap-tut-uc-meta{color:rgba(0,212,255,.5);font-size:10px;margin-top:2px;}

/* ステップパネル */
#dap-tut-panel {
  position:fixed;bottom:140px;right:20px;z-index:2147483641;
  width:320px;background:#0f172a;border:1px solid rgba(0,212,255,.3);
  border-radius:14px;box-shadow:0 8px 32px rgba(0,0,0,.6);
  animation:dap-tut-slidein .25s ease;
  display:none;flex-direction:column;
  font-family:system-ui,-apple-system,"Noto Sans JP",sans-serif;
  font-size:12px;color:#e2e8f0;overflow:hidden;
}
.tut-header{display:flex;align-items:center;justify-content:space-between;padding:10px 12px 8px;background:rgba(0,212,255,.06);border-bottom:1px solid rgba(0,212,255,.12);}
.tut-title{font-size:11px;font-weight:600;color:#00d4ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:220px;}
#dap-tut-close-btn{background:none;border:none;color:rgba(255,255,255,.4);cursor:pointer;font-size:15px;line-height:1;padding:0 2px;}
#dap-tut-close-btn:hover{color:#fff;}
#dap-tut-progressbar{height:3px;background:rgba(0,212,255,.12);}
#dap-tut-progressbar-fill{height:100%;background:#00d4ff;transition:width .3s ease;}
.tut-body{padding:12px;overflow-y:auto;}
.tut-step-num{font-size:10px;color:rgba(0,212,255,.6);margin-bottom:4px;}
.tut-step-name{font-size:13px;font-weight:600;color:#f1f5f9;margin-bottom:6px;}
.tut-gs-msg{font-size:11px;color:#cbd5e1;line-height:1.55;}
.tut-gs-target{display:inline-block;margin-top:6px;padding:2px 8px;border-radius:4px;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.25);color:#00d4ff;font-size:10px;}

/* アクション待ちヒント */
#dap-tut-action-hint {
  display:none;align-items:center;justify-content:space-between;
  margin:8px 12px 0;padding:6px 8px;
  background:rgba(251,191,36,.07);border:1px solid rgba(251,191,36,.2);
  border-radius:6px;font-size:10px;color:rgba(251,191,36,.8);
}
#dap-tut-skip-gs {
  color:rgba(255,255,255,.3);font-size:10px;text-decoration:none;
  flex-shrink:0;margin-left:8px;white-space:nowrap;
}
#dap-tut-skip-gs:hover{color:rgba(255,255,255,.6);}

/* ナビゲーションボタン（tutorial-widget 内） */
.tut-nav-btn {
  display:inline-block;margin-top:10px;padding:7px 12px;
  border-radius:7px;border:1px solid rgba(0,212,255,.35);
  background:rgba(0,212,255,.12);color:#00d4ff;
  font-size:11px;cursor:pointer;font-family:inherit;
  transition:background .12s;width:100%;text-align:center;
}
.tut-nav-btn:hover{background:rgba(0,212,255,.22);}

/* インラインチャット */
#dap-tut-chat{border-top:1px solid rgba(0,212,255,.1);padding:10px 12px 12px;display:none;}
#dap-tut-chat-history{max-height:140px;overflow-y:auto;margin-bottom:8px;}
.tut-chat-msg{margin-bottom:6px;}
.tut-chat-msg.user .bubble{background:rgba(0,212,255,.12);border-radius:8px 8px 2px 8px;padding:5px 8px;font-size:11px;color:#e2e8f0;text-align:right;}
.tut-chat-msg.bot .bubble{background:rgba(255,255,255,.05);border-radius:8px 8px 8px 2px;padding:5px 8px;font-size:11px;color:#cbd5e1;}
.tut-chat-msg.bot.thinking .bubble{color:rgba(0,212,255,.5);font-style:italic;}
#dap-tut-chat-row{display:flex;gap:6px;}
#dap-tut-chat-input{flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:6px;color:#e2e8f0;font-size:11px;padding:5px 8px;outline:none;font-family:inherit;}
#dap-tut-chat-input:focus{border-color:rgba(0,212,255,.4);}
#dap-tut-chat-send{background:rgba(0,212,255,.15);border:1px solid rgba(0,212,255,.3);border-radius:6px;color:#00d4ff;font-size:11px;padding:5px 10px;cursor:pointer;transition:background .12s;}
#dap-tut-chat-send:hover{background:rgba(0,212,255,.25);}

/* フッター */
.tut-footer{display:flex;gap:6px;padding:8px 12px 12px;border-top:1px solid rgba(0,212,255,.08);}
.tut-btn{flex:1;padding:7px 0;border-radius:7px;font-size:11px;cursor:pointer;font-family:inherit;transition:background .12s;border:1px solid transparent;}
.tut-btn-secondary{background:rgba(255,255,255,.05);color:#94a3b8;border-color:rgba(255,255,255,.1);}
.tut-btn-secondary:hover{background:rgba(255,255,255,.09);}
.tut-btn-secondary:disabled{opacity:.3;cursor:not-allowed;}
.tut-btn-primary{background:rgba(0,212,255,.15);color:#00d4ff;border-color:rgba(0,212,255,.3);}
.tut-btn-primary:hover:not(:disabled){background:rgba(0,212,255,.25);}
.tut-btn-ask{background:rgba(139,92,246,.12);color:#a78bfa;border-color:rgba(139,92,246,.25);}
.tut-btn-ask:hover{background:rgba(139,92,246,.22);}

/* ハイライトリング */
#dap-tut-ring{position:fixed;pointer-events:none;z-index:2147483639;border-radius:6px;border:2px solid #00d4ff;animation:dap-tut-pulse 1.6s infinite;display:none;transition:all .15s ease;}
`;
    document.head.appendChild(s);
  }

  // ── DOM 構築 ───────────────────────────────────────────────────────────────
  function buildDOM() {
    // FAB
    const fab = document.createElement('button');
    fab.id = 'dap-tut-fab'; fab.title = 'チュートリアル'; fab.innerHTML = '📚';
    fab.addEventListener('click', onFabClick);
    document.body.appendChild(fab);

    // ハイライトリング
    const ring = document.createElement('div');
    ring.id = 'dap-tut-ring';
    document.body.appendChild(ring);

    // UC ピッカー
    const picker = document.createElement('div');
    picker.id = 'dap-tut-picker';
    picker.innerHTML = '<h3>📋 チュートリアルを選択</h3><div id="dap-tut-uc-list">読み込み中…</div>';
    document.body.appendChild(picker);

    // ステップパネル
    const panel = document.createElement('div');
    panel.id = 'dap-tut-panel';
    panel.innerHTML = `
      <div class="tut-header">
        <span class="tut-title" id="dap-tut-uc-title">チュートリアル</span>
        <button id="dap-tut-close-btn" title="終了">✕</button>
      </div>
      <div id="dap-tut-progressbar"><div id="dap-tut-progressbar-fill"></div></div>
      <div class="tut-body" id="dap-tut-body">
        <div class="tut-step-num"  id="dap-tut-step-num"></div>
        <div class="tut-step-name" id="dap-tut-step-name"></div>
        <div class="tut-gs-msg"    id="dap-tut-gs-msg"></div>
        <span class="tut-gs-target" id="dap-tut-gs-target" style="display:none"></span>
      </div>
      <div id="dap-tut-action-hint">
        <span>👆 上記の操作を実行してください</span>
        <a id="dap-tut-skip-gs" href="#">スキップ</a>
      </div>
      <div id="dap-tut-chat">
        <div id="dap-tut-chat-history"></div>
        <div id="dap-tut-chat-row">
          <input id="dap-tut-chat-input" type="text" placeholder="このステップについて質問する…" />
          <button id="dap-tut-chat-send">送信</button>
        </div>
      </div>
      <div class="tut-footer">
        <button class="tut-btn tut-btn-secondary" id="dap-tut-prev-btn">← 前へ</button>
        <button class="tut-btn tut-btn-ask"       id="dap-tut-ask-btn">💬 質問</button>
        <button class="tut-btn tut-btn-primary"   id="dap-tut-next-btn">次へ →</button>
      </div>
    `;
    document.body.appendChild(panel);

    // イベントバインド
    document.getElementById('dap-tut-close-btn').addEventListener('click', closeTutorial);
    document.getElementById('dap-tut-prev-btn').addEventListener('click', prevGuidanceStep);
    document.getElementById('dap-tut-next-btn').addEventListener('click', nextGuidanceStep);
    document.getElementById('dap-tut-ask-btn').addEventListener('click', toggleChat);
    document.getElementById('dap-tut-skip-gs').addEventListener('click', function (e) {
      e.preventDefault(); skipCurrentGs();
    });
    document.getElementById('dap-tut-chat-send').addEventListener('click', sendChatMessage);
    document.getElementById('dap-tut-chat-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') sendChatMessage();
    });
  }

  // ── FAB / UC ピッカー ─────────────────────────────────────────────────────
  function onFabClick() {
    const panel  = document.getElementById('dap-tut-panel');
    const picker = document.getElementById('dap-tut-picker');
    const fab    = document.getElementById('dap-tut-fab');
    if (_uc) {
      panel.style.display = panel.style.display !== 'none' ? 'none' : 'flex';
      return;
    }
    const open = picker.style.display !== 'none';
    picker.style.display  = open ? 'none' : 'block';
    fab.dataset.active    = open ? 'false' : 'true';
    if (!open) loadUcList();
  }

  async function loadUcList() {
    const container = document.getElementById('dap-tut-uc-list');
    if (_ucList.length) { renderUcList(); return; }
    try {
      const res = await fetch(DAP_BASE + '/api/tutorial/list');
      _ucList = await res.json();
      renderUcList();
    } catch (e) {
      container.innerHTML = '<span style="color:#f87171;font-size:11px">読み込みに失敗しました</span>';
    }
  }

  function renderUcList() {
    const container = document.getElementById('dap-tut-uc-list');
    container.innerHTML = _ucList.map(function (uc) {
      return `<div class="dap-tut-uc-item" data-id="${uc.id}">
        <div class="dap-tut-uc-title">${esc(uc.title)}</div>
        <div class="dap-tut-uc-meta">${esc(uc.persona)} · ${uc.step_count}ステップ</div>
      </div>`;
    }).join('');
    container.querySelectorAll('.dap-tut-uc-item').forEach(function (el) {
      el.addEventListener('click', function () { startTutorial(el.dataset.id); });
    });
  }

  // ── チュートリアル開始 ────────────────────────────────────────────────────
  async function startTutorial(ucId) {
    document.getElementById('dap-tut-picker').style.display = 'none';
    document.getElementById('dap-tut-fab').dataset.active   = 'false';
    try {
      const res = await fetch(DAP_BASE + '/api/tutorial/' + ucId);
      _uc = await res.json();
    } catch (e) {
      alert('チュートリアルデータの取得に失敗しました'); return;
    }
    _stepIdx = 0; _gsIdx = 0; _chatHist = [];
    _saveState();
    renderStep();
  }

  // ── ステップ描画 ──────────────────────────────────────────────────────────
  function renderStep() {
    document.getElementById('dap-tut-panel').style.display = 'flex';
    const steps = _uc.steps || [];
    const step  = steps[_stepIdx];
    if (!step) { closeTutorial(); return; }

    document.getElementById('dap-tut-uc-title').textContent = _uc.title;
    document.getElementById('dap-tut-progressbar-fill').style.width =
      (steps.length > 0 ? (_stepIdx / steps.length) * 100 : 0) + '%';
    document.getElementById('dap-tut-step-num').textContent  = `Step ${step.num} / ${steps.length}`;
    document.getElementById('dap-tut-step-name').textContent = step.title;

    cleanupCompletionWatchers();
    _gsIdx = 0;
    renderGuidanceStep();
  }

  // ── ガイダンスステップ描画（歩調制御の核心）────────────────────────────────
  function renderGuidanceStep() {
    cleanupCompletionWatchers();
    _gsCompleted  = false;
    _gsShownAt    = Date.now();

    const step = (_uc.steps || [])[_stepIdx];
    if (!step) return;
    const gs = (step.guidance_steps || [])[_gsIdx];

    clearHighlight();

    if (!gs) {
      // このステップの全 guidance_steps が完了
      document.getElementById('dap-tut-gs-msg').textContent    = step.detail || 'このステップは完了です。';
      document.getElementById('dap-tut-gs-target').style.display = 'none';
      _gsCompleted = true;
    } else {
      // ステップ表示イベント
      _emit('tut_step_shown', _stepCtx({ gs_type: gs.type, target: gs.target || null }));
      document.getElementById('dap-tut-gs-msg').textContent = gs.message || gs.tooltip || '';
      const targetEl = document.getElementById('dap-tut-gs-target');
      if (gs.target) {
        targetEl.textContent   = '📍 ' + gs.target;
        targetEl.style.display = 'inline-block';
      } else {
        targetEl.style.display = 'none';
      }

      // ── 種別ごとにセットアップ ──
      if (gs.type === 'navigate') {
        _setupNavigateGs(gs);
      } else if (gs.type === 'highlight') {
        _setupHighlightGs(gs);
      } else if (gs.type === 'fill_hint') {
        _setupFillGs(gs);
      } else {
        // explain など → 読むだけで完了
        _gsCompleted = true;
      }
    }

    updateNavButtons();
    updateNextButtonState();
    _saveState();
  }

  // ── navigate: ユーザーがボタンを押して遷移 ─────────────────────────────────
  function _setupNavigateGs(gs) {
    const url = (gs.url || '').replace(/\{PLATFORM_URL\}/g, PLATFORM_URL);

    // 既存メッセージの下にボタンを追加
    const msgEl = document.getElementById('dap-tut-gs-msg');
    const btn   = document.createElement('button');
    btn.className   = 'tut-nav-btn';
    btn.textContent = '🔗 このページを開く';
    btn.addEventListener('click', function () {
      // 次 gs から再開するよう状態を保存してから遷移
      const step    = (_uc.steps || [])[_stepIdx];
      const gsTotal = (step.guidance_steps || []).length;
      let nextSI = _stepIdx, nextGI = _gsIdx + 1;
      if (nextGI >= gsTotal) { nextSI = _stepIdx + 1; nextGI = 0; }
      sessionStorage.setItem(_TKEY, JSON.stringify({ ucId: _uc.id, stepIdx: nextSI, gsIdx: nextGI }));
      navigateTo(url);
    });
    msgEl.appendChild(document.createElement('br'));
    msgEl.appendChild(btn);

    // ボタンを押すこと自体が「完了」（押したら遷移するので次へは不要）
    _gsCompleted = true;
  }

  // ── highlight: 対象クリックを待つ ─────────────────────────────────────────
  function _setupHighlightGs(gs) {
    const el = findElement(gs.target);
    if (!el) {
      // 要素が見つからない → 3秒後に自動解除
      const tid = setTimeout(function () { markGsCompleted(); }, 3000);
      _completionCleanups.push(function () { clearTimeout(tid); });
      return;
    }
    highlightElement(el);
    const handler = function () { markGsCompleted(); };
    // iframe 対応
    const doc = getModuleDoc();
    el.addEventListener('click', handler);
    _completionCleanups.push(function () { el.removeEventListener('click', handler); });
  }

  // ── fill_hint: 入力値の変化を待つ ─────────────────────────────────────────
  function _setupFillGs(gs) {
    const el = findElement(gs.target);
    if (!el) {
      const tid = setTimeout(function () { markGsCompleted(); }, 3000);
      _completionCleanups.push(function () { clearTimeout(tid); });
      return;
    }
    highlightElement(el);
    // 既に値が入っていれば即完了
    if ((el.value || '').trim()) { markGsCompleted(); return; }
    const handler = function () {
      if ((el.value || '').trim()) markGsCompleted();
    };
    el.addEventListener('input', handler);
    el.addEventListener('change', handler);
    _completionCleanups.push(function () {
      el.removeEventListener('input', handler);
      el.removeEventListener('change', handler);
    });
  }

  // ── スキップ（ユーザーが詰まったとき） ───────────────────────────────────
  function skipCurrentGs() {
    cleanupCompletionWatchers();
    _gsCompleted = true;
    _emit('tut_skipped', _stepCtx({ elapsed_ms: Date.now() - _gsShownAt }));
    updateNextButtonState();
  }

  // ── ナビゲーション ────────────────────────────────────────────────────────
  function nextGuidanceStep() {
    if (!_gsCompleted) return; // 完了前は押せない（disabled でも念のため）
    const step    = (_uc.steps || [])[_stepIdx];
    const gsTotal = (step.guidance_steps || []).length;

    if (_gsIdx < gsTotal - 1) {
      _gsIdx++;
      renderGuidanceStep();
    } else if (_stepIdx < (_uc.steps || []).length - 1) {
      _stepIdx++; _gsIdx = 0;
      renderStep();
    } else {
      finishTutorial();
    }
  }

  function prevGuidanceStep() {
    cleanupCompletionWatchers();
    if (_gsIdx > 0) {
      _gsIdx--;
    } else if (_stepIdx > 0) {
      _stepIdx--;
      const step = (_uc.steps || [])[_stepIdx];
      _gsIdx = Math.max(0, (step.guidance_steps || []).length - 1);
    }
    renderGuidanceStep();
  }

  function updateNavButtons() {
    const isFirst  = _stepIdx === 0 && _gsIdx === 0;
    const step     = (_uc.steps || [])[_stepIdx];
    const gsTotal  = step ? (step.guidance_steps || []).length : 0;
    const isLastGs = _gsIdx >= gsTotal - 1;
    const isLast   = isLastGs && _stepIdx >= (_uc.steps || []).length - 1;

    const prevBtn = document.getElementById('dap-tut-prev-btn');
    const nextBtn = document.getElementById('dap-tut-next-btn');
    prevBtn.disabled      = isFirst;
    prevBtn.style.opacity = isFirst ? '0.35' : '1';
    nextBtn.textContent   = isLast ? '✓ 完了' : '次へ →';
  }

  function finishTutorial() {
    _emit('tut_completed', { uc_id: _uc ? _uc.id : null });
    _clearState();
    clearHighlight();
    cleanupCompletionWatchers();
    document.getElementById('dap-tut-gs-msg').textContent =
      `「${_uc.title}」のチュートリアルが完了しました！`;
    document.getElementById('dap-tut-step-name').textContent = '✅ 完了';
    document.getElementById('dap-tut-progressbar-fill').style.width = '100%';
    document.getElementById('dap-tut-action-hint').style.display = 'none';
    const prevBtn = document.getElementById('dap-tut-prev-btn');
    const nextBtn = document.getElementById('dap-tut-next-btn');
    prevBtn.disabled    = true;
    nextBtn.disabled    = false;
    nextBtn.style.opacity = '1';
    nextBtn.textContent = '閉じる';
    nextBtn.onclick     = closeTutorial;
  }

  function closeTutorial() {
    // 完了前に閉じた場合は abandoned として記録
    if (_uc) {
      const step = (_uc.steps || [])[_stepIdx];
      _emit('tut_abandoned', { uc_id: _uc.id, step_num: step ? step.num : null });
    }
    _clearState();
    cleanupCompletionWatchers();
    _uc = null; _stepIdx = 0; _gsIdx = 0;
    clearHighlight();
    document.getElementById('dap-tut-panel').style.display  = 'none';
    document.getElementById('dap-tut-picker').style.display = 'none';
    document.getElementById('dap-tut-chat').style.display   = 'none';
    document.getElementById('dap-tut-chat-history').innerHTML = '';
    document.getElementById('dap-tut-action-hint').style.display = 'none';
    _chatHist = [];
    document.getElementById('dap-tut-fab').dataset.active = 'false';
    // next ボタンを初期化
    const nb = document.getElementById('dap-tut-next-btn');
    nb.textContent = '次へ →'; nb.onclick = nextGuidanceStep;
  }

  // ── 要素検索 ──────────────────────────────────────────────────────────────
  function getModuleDoc() {
    try {
      const f = document.getElementById('module-frame');
      return (f && f.contentDocument) ? f.contentDocument : document;
    } catch (e) { return document; }
  }

  function findElement(label) {
    if (!label) return null;
    const doc = getModuleDoc();
    // ① label テキスト → input/textarea
    for (const l of Array.from(doc.querySelectorAll('label'))) {
      if ((l.textContent || '').trim() === label) {
        const forEl = l.getAttribute('for') && doc.getElementById(l.getAttribute('for'));
        if (forEl) return forEl;
        const inner = l.querySelector('input,textarea,select');
        if (inner) return inner;
      }
    }
    // ② クリック可能要素をテキストで完全一致
    const clickables = Array.from(doc.querySelectorAll(
      'button,[role="button"],a,input[type="button"],input[type="submit"],input[type="text"],input[type="search"],textarea,select'
    ));
    const exact = clickables.find(el =>
      (el.innerText || el.value || el.textContent || '').trim() === label
    );
    if (exact) return exact;
    // ③ 部分一致
    return clickables.find(el =>
      (el.innerText || el.value || el.textContent || '').trim().includes(label)
    ) || null;
  }

  // ── ハイライト ────────────────────────────────────────────────────────────
  function highlightElement(el) {
    if (!el) return;
    clearHighlight();
    _highlightEl = el;
    const ring = document.getElementById('dap-tut-ring');
    ring.style.display = 'block';
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });

    function getIframeOffset() {
      try {
        const f = document.getElementById('module-frame');
        if (f && f.contentDocument && f.contentDocument.contains(el)) {
          const fr = f.getBoundingClientRect();
          return { x: fr.left, y: fr.top };
        }
      } catch (e) {}
      return { x: 0, y: 0 };
    }

    function updateRing() {
      if (!_highlightEl) return;
      const r = el.getBoundingClientRect(), off = getIframeOffset(), pad = 4;
      ring.style.left   = (r.left   + off.x - pad) + 'px';
      ring.style.top    = (r.top    + off.y - pad + window.scrollY) + 'px';
      ring.style.width  = (Math.max(10, r.width)  + pad * 2) + 'px';
      ring.style.height = (Math.max(10, r.height) + pad * 2) + 'px';
    }
    updateRing();

    function loop() {
      if (!_highlightEl) return;
      updateRing();
      _rafId = requestAnimationFrame(loop);
    }
    _rafId = requestAnimationFrame(loop);
  }

  function clearHighlight() {
    _highlightEl = null;
    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
    const ring = document.getElementById('dap-tut-ring');
    if (ring) ring.style.display = 'none';
  }

  // ── ページ遷移 ────────────────────────────────────────────────────────────
  function navigateTo(url) {
    const frame = document.getElementById('module-frame');
    if (frame) {
      // ポータル iframe モード: src を変えるだけ、親ページは保持
      frame.src = url;
      // iframe ロード後に次の guidance_step へ自動進行
      frame.onload = function () {
        frame.onload = null;
        // 状態は既に保存済みなので次 gs をそのまま描画
        renderGuidanceStep();
      };
    } else {
      // フルページ遷移: 状態は sessionStorage に保存済み
      window.location.href = url;
    }
  }

  // ── インラインチャット ────────────────────────────────────────────────────
  function toggleChat() {
    const chatEl = document.getElementById('dap-tut-chat');
    const vis    = chatEl.style.display !== 'none';
    chatEl.style.display = vis ? 'none' : 'block';
    if (!vis) document.getElementById('dap-tut-chat-input').focus();
  }

  async function sendChatMessage() {
    const input = document.getElementById('dap-tut-chat-input');
    const msg   = input.value.trim();
    if (!msg || !_uc) return;
    input.value = '';
    const step = (_uc.steps || [])[_stepIdx];
    _emit('tut_question', _stepCtx({ message_len: msg.length }));
    appendChatMsg('user', msg);
    const thinkingId = appendChatMsg('bot', '考え中…', true);
    try {
      const res = await fetch(DAP_BASE + '/api/tutorial/assist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: _SESSION_ID,
          uc_id: _uc.id,
          step_num: step ? step.num : 1,
          message: msg,
        }),
      });
      const data = await res.json();
      updateChatMsg(thinkingId, data.reply || '回答を取得できませんでした');
      _emit('tut_answer', _stepCtx({ source: data.source }));
    } catch (e) {
      updateChatMsg(thinkingId, '通信エラーが発生しました');
    }
  }

  let _chatMsgSeq = 0;
  function appendChatMsg(role, text, thinking) {
    const id   = 'tcm-' + (++_chatMsgSeq);
    const hist = document.getElementById('dap-tut-chat-history');
    const div  = document.createElement('div');
    div.className = 'tut-chat-msg ' + role + (thinking ? ' thinking' : '');
    div.id        = id;
    div.innerHTML = `<div class="bubble">${esc(text)}</div>`;
    hist.appendChild(div);
    hist.scrollTop = hist.scrollHeight;
    return id;
  }

  function updateChatMsg(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('thinking');
    el.querySelector('.bubble').textContent = text;
    document.getElementById('dap-tut-chat-history').scrollTop = 99999;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── 初期化 ────────────────────────────────────────────────────────────────
  async function init() {
    if (document.readyState === 'loading') {
      await new Promise(r => document.addEventListener('DOMContentLoaded', r));
    }
    injectStyles();
    buildDOM();

    // ページ遷移後のチュートリアル状態復元
    const saved = _loadState();
    if (saved) {
      try {
        const res = await fetch(DAP_BASE + '/api/tutorial/' + saved.ucId);
        if (!res.ok) throw new Error('fetch failed');
        _uc      = await res.json();
        _stepIdx = saved.stepIdx || 0;
        _gsIdx   = saved.gsIdx   || 0;
        renderStep();
      } catch (e) {
        _clearState();
      }
    }
  }

  init();
})();
