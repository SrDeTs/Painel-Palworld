/* Painel Palworld — service worker
   Rede primeiro para evitar UI antiga depois de atualizar o painel.
   API e SSE nunca passam pelo cache. */
"use strict";

const VERSAO = "pp-v4";
const CASCO = [
  "/index.html", "/app.js", "/style.css", "/logo.jpg",
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

  if (url.origin !== self.location.origin ||
      url.pathname.startsWith("/api/") ||
      url.pathname === "/metrics" ||
      ev.request.method !== "GET") {
    return;
  }

  ev.respondWith((async () => {
    const cache = await caches.open(VERSAO);
    try {
      // Sempre tenta a versão atual do servidor primeiro. Isso é especialmente
      // importante no CasaOS/ZimaOS, onde os arquivos do bind mount podem ser
      // atualizados sem trocar a URL do painel.
      const resposta = await fetch(ev.request, { cache: "no-store" });
      if (resposta.ok) await cache.put(ev.request, resposta.clone());
      return resposta;
    } catch (_) {
      const emCache = await cache.match(ev.request);
      if (emCache) return emCache;

      // Navegação offline pode cair no último index conhecido.
      if (ev.request.mode === "navigate") {
        const index = await cache.match("/index.html");
        if (index) return index;
      }
      throw _;
    }
  })());
});
