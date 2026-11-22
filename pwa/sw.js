// Service worker: guarda o app para funcionar offline. Arquivos do projeto nunca passam por aqui.
const CACHE = 'enigma-suit-sct-v1';
const ASSETS = ['./', 'index.html', 'css/app.css', 'js/app.js', 'js/nifti.js', 'js/viewer.js', 'js/fsx.js',
  'js/help.js', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET' || new URL(e.request.url).origin !== location.origin) return;
  // rede primeiro (pega atualizações do GitHub Pages), cache se offline
  e.respondWith(fetch(e.request).then(r => {
    const copy = r.clone();
    caches.open(CACHE).then(c => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});
