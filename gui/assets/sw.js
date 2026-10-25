// v0.9.125.9 — PWA installability service worker.
// THE RULE: every fetch falls through to the NETWORK untouched. The
// agent web UI is stateful and dynamic (chat, notes, handwriting,
// cache-busted assets ?v=<version>); serving anything from a SW cache
// would resurrect exactly the stale-app.js class of bugs the
// ?v= cache-bust was built to kill. This handler exists ONLY so
// browsers that require a service worker for the install prompt
// (Chrome on Android) are satisfied — the install then launches the
// app STANDALONE (no browser URL bar / banner).
self.addEventListener('install', function () {
  self.skipWaiting();
});
self.addEventListener('activate', function (e) {
  e.waitUntil(self.clients.claim());
});
self.addEventListener('fetch', function () {
  // default behaviour = network
});