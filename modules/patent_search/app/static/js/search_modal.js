// app/static/js/search_modal.js
// ============================================================
// Search results table row-click -> Patent detail modal
// - Works for Google/J-PlatPat (same UI)
// - Splits description into sections (Technical field / Background / Embodiments)
// - Fix: close modal on page load + bfcache restore (back/forward)
// - Fix: Japanese detection uses Unicode ranges (no mojibake)
// ============================================================

function badgeLabel(source) {
  if (source === "google_patents") return "Google";
  if (source === "j_platpat") return "J-PlatPat";
  return source || "";
}

function detectLang(text) {
  if (!text) return "en";

  // Safe Unicode-range Japanese detection (avoids mojibake)
  // Hiragana:   \u3040-\u309F
  // Katakana:   \u30A0-\u30FF
  // Kanji:      \u4E00-\u9FFF
  // Halfwidth:  \uFF66-\uFF9F
  // JP punct:   \u3000-\u303F
  const japaneseRegex =
    /[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F\u3000-\u303F]/;

  return japaneseRegex.test(text) ? "ja" : "en";
}

// ============================================================
// Description section splitter (robust heuristic, EN/JA)
// ============================================================
function splitDescription(description) {
  const text = (description || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!text.trim()) {
    return { tech: "", bg: "", emb: "", other: "" };
  }

  const lang = detectLang(text);

  const EN = {
    tech: ["TECHNICAL FIELD", "FIELD OF THE INVENTION", "FIELD"],
    bg: ["BACKGROUND", "BACKGROUND ART", "DESCRIPTION OF RELATED ART", "PRIOR ART"],
    emb: [
      "DETAILED DESCRIPTION",
      "DETAILED DESCRIPTION OF THE INVENTION",
      "DESCRIPTION OF EMBODIMENTS",
      "EMBODIMENTS?",
      "BEST MODE",
    ],
  };

  const JA = {
    tech: ["技術分野", "発明の属する技術分野"],
    bg: ["背景技術", "従来技術", "先行技術文献", "発明の背景"],
    emb: ["発明を実施するための形態", "実施の形態", "実施例", "発明の詳細な説明"],
  };

  const dict = lang === "ja" ? JA : EN;

  function findPos(headers) {
    let best = null;
    headers.forEach((h) => {
      // Look for headings near line start
      const re = new RegExp(
        `(^|\\n)\\s*(\\[\\s*)?${h}(\\s*\\]|\\s*[:：]|\\s*\\n)`,
        "i"
      );
      const m = re.exec(text);
      if (m) {
        const idx = m.index;
        if (best === null || idx < best) best = idx;
      }
    });
    return best;
  }

  const positions = {
    tech: findPos(dict.tech),
    bg: findPos(dict.bg),
    emb: findPos(dict.emb),
  };

  const found = Object.entries(positions)
    .filter(([, idx]) => idx !== null)
    .sort((a, b) => a[1] - b[1]);

  if (found.length === 0) {
    return { tech: "", bg: "", emb: "", other: text.trim() };
  }

  const chunks = { tech: "", bg: "", emb: "", other: "" };

  for (let i = 0; i < found.length; i++) {
    const [sec, start] = found[i];
    const end = i + 1 < found.length ? found[i + 1][1] : text.length;
    chunks[sec] = text.slice(start, end).trim();
  }

  const firstStart = found[0][1];
  if (firstStart > 0) {
    const head = text.slice(0, firstStart).trim();
    if (head) chunks.other = head;
  }

  return chunks;
}

// ============================================================
// Modal controls
// ============================================================
function openModal() {
  const overlay = document.getElementById("modalOverlay");
  const modal = document.getElementById("patentModal");
  if (!overlay || !modal) return;

  overlay.style.display = "";
  modal.style.display = "";
  overlay.classList.remove("hidden");
  modal.classList.remove("hidden");
}

function closeModal() {
  const overlay = document.getElementById("modalOverlay");
  const modal = document.getElementById("patentModal");
  if (!overlay || !modal) return;

  overlay.classList.add("hidden");
  modal.classList.add("hidden");
  overlay.style.display = "none";
  modal.style.display = "none";
}

function setActiveTab(tab) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.toggle("active", p.id === `tab-${tab}`);
  });
}

function fillModal(p) {
  const source = p.source || "google_patents";
  const pub = p.publication_number || "";
  const title = p.title || "(no title)";
  const assignee = Array.isArray(p.assignees) ? p.assignees.join(", ") : (p.assignees || "");
  const date = p.publication_date || "";

  const modalTitle = document.getElementById("modalTitle");
  const modalSub = document.getElementById("modalSub");
  const secTech = document.getElementById("secTech");
  const secBg = document.getElementById("secBg");
  const secEmb = document.getElementById("secEmb");
  const secOther = document.getElementById("secOther");
  const link = document.getElementById("modalLink");

  if (modalTitle) modalTitle.textContent = title;
  if (modalSub)
    modalSub.textContent = `${badgeLabel(source)} / ${pub}${assignee ? " / " + assignee : ""}${
      date ? " / " + date : ""
    }`;

  const sections = splitDescription(p.description || "");

  if (secTech) secTech.textContent = sections.tech || "(not found)";
  if (secBg) secBg.textContent = sections.bg || "(not found)";
  if (secEmb) secEmb.textContent = sections.emb || "(not found)";
  if (secOther) secOther.textContent = sections.other || "(empty)";

  if (link) {
    link.href = p.url || "#";
    link.textContent = p.url ? `Open ${badgeLabel(source)} page` : "Open source";
  }
}

// Force-close modal on load/back-forward restore (bfcache)
function forceCloseModalOnShow() {
  closeModal();
  // Optional: reset tab to default
  setActiveTab("tech");
}

// ============================================================
// Init
// ============================================================
document.addEventListener("DOMContentLoaded", () => {
  // Always close modal on first load
  forceCloseModalOnShow();

  // Close button and overlay handlers — set up unconditionally
  const btnClose = document.getElementById("modalClose");
  const overlay = document.getElementById("modalOverlay");
  if (btnClose) btnClose.addEventListener("click", closeModal);
  if (overlay) overlay.addEventListener("click", closeModal);

  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.addEventListener("click", () => setActiveTab(b.dataset.tab));
  });

  // Table row click handler
  const table = document.getElementById("patentTable");
  if (!table) return;

  const data = window.__SEARCH_RESULTS__ || [];

  const tbody = table.querySelector("tbody");
  if (!tbody) return;

  tbody.addEventListener("click", (e) => {
    const tr = e.target.closest("tr.patent-row");
    if (!tr) return;

    const idx = Number(tr.dataset.index);
    const p = data[idx];
    if (!p) return;

    setActiveTab("tech");
    fillModal(p);
    openModal();
  });
});

// This fires even when the page is restored from BFCache (back/forward)
window.addEventListener("pageshow", () => {
  forceCloseModalOnShow();
});