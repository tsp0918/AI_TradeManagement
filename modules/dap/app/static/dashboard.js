/**
 * DAP Admin Dashboard — Platform 全体状況 + チュートリアル分析
 *
 * 担当タブ: ダッシュボード / チュートリアル分析 / チャット分析
 * 既存の シナリオ / レコーダー / Chat Widget タブは admin.js が担当。
 */
(function () {
  'use strict';

  const DAP = window.location.origin;

  // ── ユーティリティ ─────────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function fmt(n) { return n == null ? '—' : Number(n).toLocaleString('ja-JP'); }
  function pct(n) { return n == null ? '—' : n.toFixed(1) + '%'; }
  function ms(n)  { return n == null || n === 0 ? '—' : (n < 1000 ? Math.round(n) + 'ms' : (n/1000).toFixed(1) + 's'); }

  // ── ダッシュボードタブ ─────────────────────────────────────────────────────

  async function loadDashboard() {
    await Promise.all([loadModuleHealth(), loadPlatformKpi(), loadRecentEvents()]);
  }

  async function loadModuleHealth() {
    const container = $('dash-health-grid');
    if (!container) return;
    try {
      const data = await fetch(DAP + '/api/platform/health').then(r => r.json());
      const { modules, summary } = data;

      $('dash-health-summary').innerHTML =
        `<span class="kpi-val" style="color:${summary.down>0?'#f87171':'#34d399'}">${summary.up}/${summary.total}</span>
         <span class="kpi-label">モジュール正常稼働</span>`;

      container.innerHTML = modules.map(m => {
        const ok = m.reachable;
        return `<div class="health-card ${ok ? 'ok' : 'down'}">
          <div class="hc-dot"></div>
          <div class="hc-name">${esc(m.name)}</div>
          <div class="hc-port">:${m.port}</div>
        </div>`;
      }).join('');
    } catch (e) {
      container.innerHTML = '<div class="dash-error">ヘルスチェック失敗</div>';
    }
  }

  async function loadPlatformKpi() {
    const el = $('dash-kpi-row');
    if (!el) return;
    try {
      const d = await fetch(DAP + '/proxy/platform/api/metrics/summary').then(r => r.json()).catch(() => null);
      if (!d) { el.innerHTML = '<div class="dash-error">KPI 取得失敗</div>'; return; }
      const llm = d.llm_usage_30d || {};
      el.innerHTML = `
        <div class="kpi-card">
          <div class="kpi-val">${fmt(llm.total_requests)}</div>
          <div class="kpi-label">LLM リクエスト (30日)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">${fmt(llm.total_tokens)}</div>
          <div class="kpi-label">LLM トークン (30日)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">$${(llm.total_cost_usd || 0).toFixed(2)}</div>
          <div class="kpi-label">推定コスト (30日)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">${fmt(d.tx_count_30d)}</div>
          <div class="kpi-label">新規案件 (30日)</div>
        </div>`;
    } catch (e) {
      el.innerHTML = '<div class="dash-error">KPI 取得失敗</div>';
    }
  }

  async function loadRecentEvents() {
    const tbody = $('dash-events-body');
    if (!tbody) return;
    try {
      const events = await fetch(DAP + '/api/ux-events/recent?limit=15').then(r => r.json());
      if (!events.length) { tbody.innerHTML = '<tr><td colspan="5" class="analyticsEmpty">イベントなし</td></tr>'; return; }
      tbody.innerHTML = events.map(ev => {
        const t = new Date(ev.created_at).toLocaleTimeString('ja-JP');
        const ctx = ev.context ? JSON.stringify(ev.context).slice(0, 60) : '';
        return `<tr>
          <td>${esc(t)}</td>
          <td><span class="ev-badge ev-${esc(ev.module_key)}">${esc(ev.module_key)}</span></td>
          <td><code>${esc(ev.event_name)}</code></td>
          <td style="color:#6b88a8;font-size:10px">${esc(ctx)}</td>
          <td style="color:#6b88a8;font-size:10px">${esc(ev.session_id || '—')}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="5" class="analyticsEmpty">読み込み失敗</td></tr>';
    }
  }

  // ── チュートリアル分析タブ ─────────────────────────────────────────────────

  async function loadTutorialAnalytics() {
    const ucId = ($('tut-uc-select') || {}).value || null;
    const days = parseInt(($('tut-days-select') || {}).value || '30', 10);
    await Promise.all([loadTutFunnel(ucId, days), loadUcSummary(days), loadFaqCandidates(days)]);
  }

  async function loadTutFunnel(ucId, days) {
    const container = $('tut-funnel-body');
    if (!container) return;
    container.innerHTML = '<tr><td colspan="7" class="analyticsEmpty">読み込み中…</td></tr>';
    try {
      const params = new URLSearchParams({ days });
      if (ucId) params.set('uc_id', ucId);
      const d = await fetch(DAP + '/api/ux-events/analytics/tutorial?' + params).then(r => r.json());
      const rows = d.funnel || [];

      // UC ピッカーを populate（初回）
      const ucSel = $('tut-uc-select');
      if (ucSel && !ucSel.dataset.populated) {
        const ucs = [...new Set(rows.map(r => r.uc_id).filter(Boolean))];
        ucSel.innerHTML = '<option value="">すべての UC</option>' +
          ucs.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
        ucSel.dataset.populated = '1';
      }

      if (!rows.length) {
        container.innerHTML = '<tr><td colspan="7" class="analyticsEmpty">データなし</td></tr>'; return;
      }

      container.innerHTML = rows.map(r => {
        const cr = r.completion_rate;
        const barW = cr != null ? Math.round(cr) : 0;
        const barColor = cr >= 80 ? '#34d399' : cr >= 50 ? '#fbbf24' : '#f87171';
        return `<tr>
          <td>${esc(r.uc_id)}</td>
          <td>Step ${r.step_num}</td>
          <td>${fmt(r.shown)}</td>
          <td>${fmt(r.done)}</td>
          <td>
            <div style="display:flex;align-items:center;gap:6px">
              <div style="flex:1;height:6px;background:rgba(255,255,255,.1);border-radius:3px">
                <div style="width:${barW}%;height:100%;background:${barColor};border-radius:3px"></div>
              </div>
              <span style="font-size:10px;color:${barColor};min-width:36px">${pct(cr)}</span>
            </div>
          </td>
          <td>${fmt(r.skipped)}</td>
          <td>${ms(r.avg_ms)}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      container.innerHTML = '<tr><td colspan="7" class="analyticsEmpty">読み込み失敗</td></tr>';
    }
  }

  async function loadUcSummary(days) {
    const el = $('tut-uc-summary');
    if (!el) return;
    try {
      const d = await fetch(DAP + '/api/ux-events/analytics/tutorial?days=' + days).then(r => r.json());
      const rows = d.uc_summary || [];
      if (!rows.length) { el.innerHTML = '<div class="dash-error" style="color:#6b88a8">データなし</div>'; return; }
      el.innerHTML = rows.map(r => {
        const cr = r.total > 0 ? Math.round(r.completed / r.total * 100) : 0;
        return `<div class="uc-summary-row">
          <span class="uc-id">${esc(r.uc_id)}</span>
          <div style="flex:1;height:5px;background:rgba(255,255,255,.08);border-radius:3px;margin:0 8px">
            <div style="width:${cr}%;height:100%;background:#00d4ff;border-radius:3px"></div>
          </div>
          <span style="font-size:10px;color:#94a3b8;min-width:50px;text-align:right">${r.completed}/${r.total} 完了</span>
        </div>`;
      }).join('');
    } catch (e) {}
  }

  async function loadFaqCandidates(days) {
    const el = $('tut-faq-list');
    if (!el) return;
    try {
      const d = await fetch(DAP + '/api/ux-events/analytics/tutorial?days=' + days).then(r => r.json());
      const faqs = d.faq_candidates || [];
      if (!faqs.length) { el.innerHTML = '<div style="color:#6b88a8;font-size:11px">質問なし</div>'; return; }
      el.innerHTML = faqs.map(f =>
        `<div class="faq-row">
          <span class="faq-badge">${esc(f.uc_id)} Step${f.step_num}</span>
          <span class="faq-count">${f.question_count}件の質問</span>
        </div>`
      ).join('');
    } catch (e) {}
  }

  // ── チャット分析タブ ───────────────────────────────────────────────────────

  async function loadChatAnalytics() {
    const days = parseInt(($('chat-days-select') || {}).value || '30', 10);
    try {
      const d = await fetch(DAP + '/api/ux-events/analytics/chat?days=' + days).then(r => r.json());

      // 総質問数
      const totalEl = $('chat-total-q');
      if (totalEl) totalEl.textContent = fmt(d.total_questions);

      // ソース分布バー
      const srcEl = $('chat-source-bars');
      if (srcEl) {
        const colors = { cache: '#34d399', local: '#60a5fa', sonnet: '#a78bfa' };
        const labels = { cache: 'FAQ キャッシュ', local: 'Ollama (無料)', sonnet: 'Claude API' };
        srcEl.innerHTML = (d.source_breakdown || []).map(s => `
          <div class="src-row">
            <span class="src-label">${esc(labels[s.source] || s.source)}</span>
            <div style="flex:1;height:8px;background:rgba(255,255,255,.08);border-radius:4px;margin:0 8px">
              <div style="width:${s.pct}%;height:100%;background:${colors[s.source]||'#94a3b8'};border-radius:4px"></div>
            </div>
            <span class="src-pct">${pct(s.pct)} (${fmt(s.count)})</span>
          </div>`).join('');
      }

      // 日別推移（簡易バーチャート）
      const dailyEl = $('chat-daily-chart');
      if (dailyEl && d.daily_sessions) {
        const maxC = Math.max(...d.daily_sessions.map(r => r.count), 1);
        dailyEl.innerHTML = d.daily_sessions.slice(0, 14).reverse().map(r => {
          const h = Math.round(r.count / maxC * 40);
          return `<div class="day-bar-wrap" title="${r.day}: ${r.count}件">
            <div class="day-bar" style="height:${h}px"></div>
            <div class="day-label">${r.day.slice(5)}</div>
          </div>`;
        }).join('');
      }
    } catch (e) {
      console.error('[dashboard] chat analytics error', e);
    }
  }

  // ── チャットログタブ ──────────────────────────────────────────────────────

  async function loadChatLogs() {
    const container = $('chatlog-session-list');
    if (!container) return;
    const limit = parseInt(($('chatlog-limit-select') || {}).value || '20', 10);
    container.innerHTML = '<div class="analyticsEmpty">読み込み中…</div>';
    try {
      const d = await fetch(DAP + `/api/chat/logs?limit=${limit}`).then(r => r.json());
      const sessions = d.sessions || [];
      if (!sessions.length) {
        container.innerHTML = '<div class="analyticsEmpty">ログなし</div>';
        return;
      }
      container.innerHTML = `
        <table class="analyticsTable" style="width:100%">
          <thead><tr>
            <th>セッション ID</th>
            <th>開始</th>
            <th>最終</th>
            <th>ターン数</th>
            <th></th>
          </tr></thead>
          <tbody>${sessions.map(s => `<tr>
            <td style="font-family:monospace;font-size:11px;color:#94a3b8">${esc(s.session_id)}</td>
            <td style="font-size:11px">${esc(new Date(s.started_at).toLocaleString('ja-JP'))}</td>
            <td style="font-size:11px">${esc(new Date(s.last_at).toLocaleString('ja-JP'))}</td>
            <td style="text-align:center">${s.turn_count}</td>
            <td><button class="btnGhost btnSm chatlog-view-btn" data-sid="${esc(s.session_id)}">詳細</button></td>
          </tr>`).join('')}</tbody>
        </table>`;

      container.querySelectorAll('.chatlog-view-btn').forEach(btn => {
        btn.addEventListener('click', () => loadChatDetail(btn.dataset.sid));
      });
    } catch (e) {
      container.innerHTML = '<div class="analyticsEmpty">読み込み失敗</div>';
    }
  }

  async function loadChatDetail(sessionId) {
    const panel = $('chatlog-detail-panel');
    const body  = $('chatlog-detail-body');
    const sidEl = $('chatlog-detail-sid');
    if (!panel || !body) return;
    body.innerHTML = '<div class="analyticsEmpty">読み込み中…</div>';
    panel.style.display = 'block';
    if (sidEl) sidEl.textContent = sessionId;
    try {
      const d = await fetch(DAP + `/api/chat/logs/${encodeURIComponent(sessionId)}`).then(r => r.json());
      const turns = d.turns || [];
      if (!turns.length) { body.innerHTML = '<div class="analyticsEmpty">データなし</div>'; return; }
      body.innerHTML = turns.map(t => {
        const ctx = t.page_context || {};
        const port = ctx.port ? `:${ctx.port}` : '';
        const page = ctx.page_path || '';
        const mode = t.intake_mode ? `<span class="ev-badge ev-dap" style="margin-left:6px">${esc(t.intake_mode)}</span>` : '';
        const actStr = (t.actions || []).map(a => `<code style="font-size:10px">${esc(a.type)}${a.target ? ':'+a.target : ''}</code>`).join(' ');
        return `<div class="chatlog-turn">
          <div class="chatlog-turn-meta">Turn ${t.turn_num} · ${esc(new Date(t.created_at).toLocaleTimeString('ja-JP'))} · ${esc(port+page)}${mode}</div>
          <div class="chatlog-bubble user"><span class="chatlog-role">User</span>${esc(t.user_message)}</div>
          <div class="chatlog-bubble asst"><span class="chatlog-role">AI</span>${esc(t.assistant_reply)}</div>
          ${actStr ? `<div class="chatlog-actions">アクション: ${actStr}</div>` : ''}
        </div>`;
      }).join('');
    } catch (e) {
      body.innerHTML = '<div class="analyticsEmpty">読み込み失敗</div>';
    }
  }

  // ── タブ切り替えフック ─────────────────────────────────────────────────────

  function hookTabChange() {
    document.addEventListener('dap:tab-changed', function (e) {
      const tab = e.detail;
      if (tab === 'tab-dashboard')  loadDashboard();
      if (tab === 'tab-tutorial')   loadTutorialAnalytics();
      if (tab === 'tab-chat-stats') loadChatAnalytics();
      if (tab === 'tab-chat-logs')  loadChatLogs();
    });
  }

  // フィルター変更
  function hookFilters() {
    const ucSel   = $('tut-uc-select');
    const daysSel = $('tut-days-select');
    if (ucSel)   ucSel.addEventListener('change',   loadTutorialAnalytics);
    if (daysSel) daysSel.addEventListener('change', loadTutorialAnalytics);

    const chatDays = $('chat-days-select');
    if (chatDays) chatDays.addEventListener('change', loadChatAnalytics);

    const reloadBtn = $('dash-reload-btn');
    if (reloadBtn) reloadBtn.addEventListener('click', loadDashboard);

    const logLimit = $('chatlog-limit-select');
    if (logLimit) logLimit.addEventListener('change', loadChatLogs);

    const logClose = $('chatlog-detail-close');
    if (logClose) logClose.addEventListener('click', () => {
      const p = $('chatlog-detail-panel');
      if (p) p.style.display = 'none';
    });
  }

  // ── 初期化 ────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    hookTabChange();
    hookFilters();
    // デフォルトタブがダッシュボードの場合は即時ロード
    if ($('tab-dashboard') && $('tab-dashboard').classList.contains('isActive')) {
      loadDashboard();
    }
  });

  // 外部公開（admin.html のタブ切り替えから呼べるように）
  window.DashboardJS = { loadDashboard, loadTutorialAnalytics, loadChatAnalytics, loadChatLogs };
})();
