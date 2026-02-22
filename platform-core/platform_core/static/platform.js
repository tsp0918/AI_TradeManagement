/**
 * platform.js — AI Trade Management ポータル JS
 *
 * 機能:
 *   1. ページ読み込み時に各モジュールのヘルスチェックを並列実行
 *   2. サイドバーボタンのクリックで iframe src を切り替え
 *   3. URL ハッシュでアクティブモジュールを保持 (リロード後復元)
 *   4. モジュール起動 (POST /ui/launch/{key}) / 停止 (POST /ui/stop/{key})
 *   5. ヘルスチェック結果に応じて launch/stop ボタンを動的表示切替
 */

(function () {
  "use strict";

  // ── ヘルスチェック ─────────────────────────────────────────────

  /**
   * /ui/health/{key} を叩いてドットとボタンを更新する。
   * @param {string} key
   * @param {HTMLElement} dotEl
   */
  async function checkHealth(key, dotEl) {
    try {
      const res = await fetch(`/ui/health/${key}`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) throw new Error("non-200");
      const data = await res.json();
      const online = data.status === "online";
      dotEl.className = "status-dot " + (online ? "online" : "offline");
      _updateActionButtons(key, online);
    } catch {
      dotEl.className = "status-dot offline";
      _updateActionButtons(key, false);
    }
  }

  /**
   * ヘルスチェック結果に応じて launch/stop ボタンを表示切替。
   * @param {string} key
   * @param {boolean} online
   */
  function _updateActionButtons(key, online) {
    const launchBtn = document.getElementById(`btn-launch-${key}`);
    const stopBtn   = document.getElementById(`btn-stop-${key}`);
    if (!launchBtn || !stopBtn) return;
    if (online) {
      launchBtn.style.display = "none";
      stopBtn.style.display   = "";
    } else {
      launchBtn.style.display = "";
      launchBtn.disabled      = false;
      launchBtn.innerHTML     = "▶ 起動";
      stopBtn.style.display   = "none";
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

  // ── モジュール起動/停止 ───────────────────────────────────────

  /**
   * モジュールを起動する。
   * @param {string} key
   * @param {HTMLElement} btn  launch ボタン要素
   */
  window.launchModule = async function (key, btn) {
    btn.disabled    = true;
    btn.innerHTML   = '<span class="spinning">⏳</span> 起動中…';

    try {
      const res  = await fetch(`/ui/launch/${key}`, { method: "POST" });
      const data = await res.json();

      const dot = document.getElementById(`dot-${key}`);
      if (data.status === "online") {
        if (dot) { dot.className = "status-dot online"; }
        _updateActionButtons(key, true);
        // 自動的にそのモジュールを表示
        const mainBtn = document.querySelector(`.module-main-btn[data-key="${key}"]`);
        if (mainBtn) selectModule(key, mainBtn.dataset.iframeUrl);
      } else if (data.status === "failed") {
        if (dot) { dot.className = "status-dot offline"; }
        _updateActionButtons(key, false);
        alert(`${key} の起動に失敗しました (code: ${data.returncode})`);
      } else {
        // "launching" → ヘルスチェックで追跡
        if (dot) { dot.className = "status-dot offline"; }
        btn.disabled  = false;
        btn.innerHTML = "▶ 起動";
      }
    } catch (err) {
      btn.disabled  = false;
      btn.innerHTML = "▶ 起動";
      console.error("launch error:", err);
    }
  };

  /**
   * モジュールを停止する。
   * @param {string} key
   */
  window.stopModule = async function (key) {
    const stopBtn = document.getElementById(`btn-stop-${key}`);
    if (stopBtn) { stopBtn.disabled = true; }

    try {
      await fetch(`/ui/stop/${key}`, { method: "POST" });
    } finally {
      const dot = document.getElementById(`dot-${key}`);
      if (dot) { dot.className = "status-dot offline"; }
      _updateActionButtons(key, false);
    }
  };

  // ── モジュール選択 ─────────────────────────────────────────────

  const frame   = document.getElementById("module-frame");
  const welcome = document.getElementById("welcome");

  /**
   * 指定キーのモジュールを選択して iframe に表示する。
   * @param {string} key
   * @param {string} iframeUrl
   */
  function selectModule(key, iframeUrl) {
    // active クラスを card に付け替え
    document.querySelectorAll(".module-card").forEach((card) => {
      card.classList.toggle(
        "active",
        card.id === `card-${key}`
      );
    });

    if (frame && welcome) {
      if (iframeUrl) {
        welcome.style.display = "none";
        frame.style.display   = "block";
        frame.src = iframeUrl;
      } else {
        frame.style.display   = "none";
        welcome.style.display = "flex";
      }
    }

    history.replaceState(null, "", "#" + key);
  }

  // ── 初期化 ────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    // サイドバーのモジュール名ボタンにイベントを設定
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
    }

    // ヘルスチェック実行 (初回)
    runHealthChecks();

    // 30 秒ごとに再チェック
    setInterval(runHealthChecks, 30_000);
  });
})();
