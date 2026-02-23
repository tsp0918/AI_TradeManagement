/**
 * platform.js — AI Trade Management ポータル JS
 *
 * 機能:
 *   1. ページ読み込み時に各モジュールのヘルスチェックを並列実行
 *   2. サイドバーボタンのクリックで iframe src を切り替え
 *   3. URL ハッシュでアクティブモジュールを保持 (リロード後復元)
 */

(function () {
  "use strict";

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
  }

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
    }

    // ヘルスチェック実行 (初回)
    runHealthChecks();

    // 30 秒ごとに再チェック
    setInterval(runHealthChecks, 30_000);
  });
})();
