/* Painel Palworld — service worker
   Cache-first só para o casco estático; API e SSE sempre na rede. */
"use strict";

const VERSAO = "pp-v3";
const CASCO = [
  "/", "/index.html", "/app.js", "/style.css", "/logo.jpg",
  "/manifest.webmanifest",
];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(VERSAO).then((c) => c.addAll(CASCO)));
  self.skipWaiting();
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(
    caches.keys().then((chaves) =>
      Promise.all(chaves.filter((k) => k !== VERSAO)
        .map((k) => caches.delete(k)))))
  self.clients.claim();
});

self.addEventListener("fetch", (ev) => {
  const url = new URL(ev.request.url);
  if (url.pathname.startsWith("/api/") || ev.request.method !== "GET") {
    return; // rede direta (dados vivos, SSE, ações)
  }
  // estático: cache com revalidação em segundo plano
  ev.respondWith(
    caches.open(VERSAO).then(async (cache) => {
      const em_cache = await cache.match(ev.request);
      const busca = fetch(ev.request).then((res) => {
        if (res.ok) cache.put(ev.request, res.clone());
        return res;
      }).catch(() => em_cache);
      return em_cache || busca;
    })
  );
});
