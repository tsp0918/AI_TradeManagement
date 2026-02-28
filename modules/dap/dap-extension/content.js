(async function () {
  const SERVER_BASE = "http://localhost:8010";

  // 現在のタブのポートから app_key を自動選択
  const PORT_TO_APP = {
    "8000": "rd_risk_local",
    "8001": "ai_validation_local",
    "8003": "rnd_assessment_local",
    "8005": "screening_local",
  };
  const APP_KEY = PORT_TO_APP[location.port] || "rd_risk_local";
  const ENV = "local";

  try {
    const res = await fetch(`${SERVER_BASE}/runtime/config?app_key=${encodeURIComponent(APP_KEY)}&env=${encodeURIComponent(ENV)}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`runtime fetch failed: ${res.status}`);
    const data = await res.json();
    const config = data.config || data;

    const engine = new window.DAPEngine.DapEngine({
      config,
      serverBase: SERVER_BASE,
      appKey: APP_KEY,
      env: ENV,
      etag: data.etag || null
    });
    await engine.init();
  } catch (e) {
    console.warn("[DAP] failed to start", e);
  }
})();
