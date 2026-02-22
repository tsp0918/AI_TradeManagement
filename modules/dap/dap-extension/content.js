(async function () {
  // ---- Change here if you want ----
  const SERVER_BASE = "http://localhost:8710";
  const APP_KEY = "rd_risk_local";
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
