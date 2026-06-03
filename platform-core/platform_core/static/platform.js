/**
 * platform.js — AI Trade Management ポータル JS
 *
 * 機能:
 *   1. ページ読み込み時に各モジュールのヘルスチェックを並列実行
 *   2. サイドバーボタンのクリックで iframe src を切り替え
 *   3. URL ハッシュでアクティブモジュールを保持 (リロード後復元)
 *   4. DAP 右パネル: チートシート表示 + トグル (Ctrl+/)
 */

(function () {
  "use strict";

  const DAP_BASE = "http://localhost:8010";

  // ── ヘルスチェック ─────────────────────────────────────────────

  /**
   * /ui/health/{key} を叩いてステータスドットを更新する。
   * @param {string} key
   * @param {HTMLElement} dotEl
   */
  async function checkHealth(key, dotEl) {
    const startingEl = document.getElementById(`starting-${key}`);
    try {
      const res = await fetch(`/ui/health/${key}`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) throw new Error("non-200");
      const data = await res.json();
      const online = data.status === "online";
      dotEl.className = "status-dot " + (online ? "online" : "offline");
      dotEl.title = online ? "稼働中" : "起動中…";
      if (startingEl) startingEl.classList.toggle("visible", !online);
    } catch {
      dotEl.className = "status-dot offline";
      dotEl.title = "起動中…";
      if (startingEl) startingEl.classList.add("visible");
    }
  }

  /** 全モジュールのヘルスチェックを並列実行する。 */
  function runHealthChecks() {
    document.querySelectorAll(".module-main-btn[data-key]").forEach((btn) => {
      const key = btn.dataset.key;
      const dot = document.getElementById(`dot-${key}`);
      if (key && dot) checkHealth(key, dot);
    });
  }

  // ── DAP 右パネル ──────────────────────────────────────────────

  const panel     = document.getElementById("dap-panel");
  const toggleBtn = document.getElementById("panel-toggle-btn");
  const closeBtn  = document.getElementById("dap-panel-close");
  const panelBody = document.getElementById("dap-panel-body");
  const panelIcon = document.getElementById("dap-panel-module-icon");
  const panelName = document.getElementById("dap-panel-module-name");

  let _currentModuleKey = "";
  let _cheatsheetCache  = {};  // key → data (メモリキャッシュ)

  /** パネルを開閉する */
  function togglePanel() {
    if (!panel) return;
    const isOpen = panel.classList.toggle("open");
    if (toggleBtn) toggleBtn.classList.toggle("active", isOpen);
    // 開いたときにチートシートをロード（まだ読み込まれていなければ）
    if (isOpen && _currentModuleKey) {
      loadCheatsheet(_currentModuleKey);
    }
  }

  /** パネルを閉じる */
  function closePanel() {
    if (!panel) return;
    panel.classList.remove("open");
    if (toggleBtn) toggleBtn.classList.remove("active");
  }

  /**
   * DAP から指定モジュールのチートシートを取得して表示する。
   * @param {string} moduleKey
   */
  async function loadCheatsheet(moduleKey) {
    if (!panelBody) return;

    // キャッシュ済みならすぐ描画
    if (_cheatsheetCache[moduleKey]) {
      renderCheatsheet(_cheatsheetCache[moduleKey]);
      return;
    }

    // ローディング表示
    panelBody.innerHTML = '<div class="dap-panel-loading"><div class="dap-spinner"></div><span>読み込み中…</span></div>';

    try {
      const res = await fetch(`${DAP_BASE}/api/cheatsheet/${moduleKey}`, {
        signal: AbortSignal.timeout(4000),
      });
      if (!res.ok) throw new Error("non-200");
      const data = await res.json();
      _cheatsheetCache[moduleKey] = data;
      renderCheatsheet(data);
    } catch {
      panelBody.innerHTML = '<div class="dap-panel-error">チートシートを取得できませんでした。<br>DAPモジュールが起動しているか確認してください。</div>';
    }
  }

  /**
   * チートシートデータを HTML として描画する。
   * @param {{ icon: string, module: string, sections: Array }} data
   */
  function renderCheatsheet(data) {
    if (!panelBody || !panelIcon || !panelName) return;

    panelIcon.textContent = data.icon || "📌";
    panelName.textContent = data.module || "チートシート";

    const html = (data.sections || []).map((sec) => {
      const items = (sec.items || [])
        .map((item) => `<li class="cs-item">${escHtml(item)}</li>`)
        .join("");
      return `
        <div class="cs-section">
          <span class="cs-section-title">${escHtml(sec.title)}</span>
          <ul class="cs-items">${items}</ul>
        </div>
      `;
    }).join("");

    panelBody.innerHTML = html || '<div class="dap-panel-error">コンテンツがありません。</div>';
  }

  /** HTML エスケープ */
  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── モジュール選択 ─────────────────────────────────────────────

  const frame   = document.getElementById("module-frame");
  const welcome = document.getElementById("welcome");
  const loading = document.getElementById("iframe-loading");

  /**
   * 指定キーのモジュールを選択して iframe に表示する。
   * @param {string} key
   * @param {string} iframeUrl
   */
  function selectModule(key, iframeUrl) {
    // active クラスを card に付け替え
    document.querySelectorAll(".module-card").forEach((card) => {
      card.classList.toggle("active", card.id === `card-${key}`);
    });

    if (frame && welcome) {
      if (iframeUrl) {
        welcome.style.display = "none";
        frame.style.display   = "block";
        // ローディングオーバーレイを表示
        if (loading) loading.classList.add("visible");
        frame.src = iframeUrl;
      } else {
        frame.style.display   = "none";
        welcome.style.display = "flex";
      }
    }

    history.replaceState(null, "", "#" + key);

    // DAP パネルのチートシートを更新（モジュールが変わったときはキャッシュを優先、パネルが開いていれば再描画）
    _currentModuleKey = key;
    if (panel && panel.classList.contains("open")) {
      loadCheatsheet(key);
    }
  }

  // ── DAP ウィジェットに公開するポータルナビゲーション API ──────────
  // chat-widget.js から window.__dap_portal_navigate__(key, path) で呼べる
  window.__dap_portal_navigate__ = function (moduleKey, subPath) {
    var btn = document.querySelector('.module-main-btn[data-key="' + moduleKey + '"]');
    var iframeUrl;
    if (btn && btn.dataset.iframeUrl && !subPath) {
      iframeUrl = btn.dataset.iframeUrl;
    } else {
      iframeUrl = "/proxy/" + moduleKey + (subPath || "/");
    }
    selectModule(moduleKey, iframeUrl);
  };

  // ── 初期化 ────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    // iframe ロード完了でオーバーレイを非表示
    if (frame && loading) {
      frame.addEventListener("load", function () {
        loading.classList.remove("visible");
      });
    }

    // サイドバーのモジュール名ボタンにクリックイベントを設定
    document.querySelectorAll(".module-main-btn[data-key]").forEach((btn) => {
      btn.addEventListener("click", function () {
        selectModule(btn.dataset.key, btn.dataset.iframeUrl);
      });
    });

    // ハッシュからアクティブモジュールを復元
    const hash = location.hash.replace("#", "");
    if (hash) {
      const target = document.querySelector(`.module-main-btn[data-key="${hash}"]`);
      if (target) selectModule(target.dataset.key, target.dataset.iframeUrl);
    } else {
      // 初期モジュールキーを記録
      const firstBtn = document.querySelector(".module-main-btn[data-key]");
      if (firstBtn) _currentModuleKey = firstBtn.dataset.key;
    }

    // DAP パネルのトグルボタン
    if (toggleBtn) toggleBtn.addEventListener("click", togglePanel);
    if (closeBtn)  closeBtn.addEventListener("click", closePanel);

    // キーボードショートカット: Ctrl+/ でパネル開閉
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "/") {
        e.preventDefault();
        togglePanel();
      }
    });

    // ヘルスチェック実行 (初回)
    runHealthChecks();

    // 30 秒ごとに再チェック
    setInterval(runHealthChecks, 30_000);
  });
})();
