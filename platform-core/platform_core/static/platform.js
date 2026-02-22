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
   * /ui/health/{key} (同一オリジン) を叩いてドットを更新する。
   * @param {string} moduleKey
   * @param {HTMLElement} dotEl
   */
  async function checkHealth(moduleKey, dotEl) {
    try {
      const res = await fetch(`/ui/health/${moduleKey}`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) throw new Error("non-200");
      const data = await res.json();
      dotEl.className =
        "status-dot " + (data.status === "online" ? "online" : "offline");
    } catch {
      dotEl.className = "status-dot offline";
    }
  }

  /**
   * 全モジュールのヘルスチェックを並列実行する。
   */
  function runHealthChecks() {
    document.querySelectorAll(".module-btn[data-key]").forEach((btn) => {
      const key = btn.dataset.key;
      const dot = btn.querySelector(".status-dot");
      if (key && dot) checkHealth(key, dot);
    });
  }

  // ── モジュール選択 ─────────────────────────────────────────────

  const frame = document.getElementById("module-frame");
  const welcome = document.getElementById("welcome");

  /**
   * 指定キーのモジュールを選択して iframe に表示する。
   * @param {string} key
   * @param {string} iframeUrl
   */
  function selectModule(key, iframeUrl) {
    // active クラスを付け替え
    document.querySelectorAll(".module-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.key === key);
    });

    // iframe 表示 / welcome 非表示
    if (frame && welcome) {
      if (iframeUrl) {
        welcome.style.display = "none";
        frame.style.display = "block";
        frame.src = iframeUrl;
      } else {
        // URL が空の場合はウェルカム画面
        frame.style.display = "none";
        welcome.style.display = "flex";
      }
    }

    // ハッシュを更新 (popstate をトリガーしないよう replaceState)
    history.replaceState(null, "", "#" + key);
  }

  // ── 初期化 ────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    // サイドバーボタンにイベントを設定
    document.querySelectorAll(".module-btn[data-key]").forEach((btn) => {
      btn.addEventListener("click", function () {
        selectModule(btn.dataset.key, btn.dataset.iframeUrl);
      });
    });

    // ハッシュからアクティブモジュールを復元
    const hash = location.hash.replace("#", "");
    if (hash) {
      const target = document.querySelector(`.module-btn[data-key="${hash}"]`);
      if (target) {
        selectModule(target.dataset.key, target.dataset.iframeUrl);
      }
    }

    // ヘルスチェック実行 (初回)
    runHealthChecks();

    // 30 秒ごとに再チェック
    setInterval(runHealthChecks, 30_000);
  });
})();
