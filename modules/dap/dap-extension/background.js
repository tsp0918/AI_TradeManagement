// Minimal service worker required for MV3 content scripts
// to make cross-origin fetch requests without "message port closed" errors.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", () => self.clients.claim());
