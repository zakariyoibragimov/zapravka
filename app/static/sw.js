const CACHE_NAME = 'azs-bonus-mobile-v4';
const API_CACHE_NAME = 'azs-bonus-mobile-api-v2';
const APP_SHELL = [
  '/mobile',
  '/manifest.webmanifest',
  '/static/icons/azs-bonus-icon.svg',
  '/static/icons/azs-bonus-maskable.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') {
    return;
  }

  const url = new URL(request.url);
  const isNavigation = request.mode === 'navigate';
  const isStaticAsset = url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest';
  const isMobileApi = url.pathname.startsWith('/api/mobile/');
  const isLiveNewsApi = url.pathname === '/api/mobile/news';

  if (isNavigation) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put('/mobile', clone));
          return response;
        })
        .catch(() => caches.match('/mobile'))
    );
    return;
  }

  if (isStaticAsset) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) {
          return cached;
        }
        return fetch(request).then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        });
      })
    );
    return;
  }

  if (isMobileApi) {
    event.respondWith(
      caches.open(API_CACHE_NAME).then(async (cache) => {
        const cached = await cache.match(request);

        if (isLiveNewsApi) {
          try {
            const networkResponse = await fetch(request);
            if (networkResponse.ok) {
              cache.put(request, networkResponse.clone());
            }
            return networkResponse;
          } catch {
            if (cached) {
              return cached;
            }
            return new Response(JSON.stringify({ detail: 'offline' }), {
              status: 503,
              headers: { 'Content-Type': 'application/json' },
            });
          }
        }

        const networkResponsePromise = fetch(request)
          .then((response) => {
            if (response.ok) {
              cache.put(request, response.clone());
            }
            return response;
          })
          .catch(() => null);

        if (cached) {
          event.waitUntil(networkResponsePromise);
          return cached;
        }

        const networkResponse = await networkResponsePromise;
        if (networkResponse) {
          return networkResponse;
        }

        return new Response(JSON.stringify({ detail: 'offline' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        });
      })
    );
  }
});