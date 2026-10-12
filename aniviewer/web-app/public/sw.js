const CACHE_PREFIX = 'msm-aniviewer-web-';
const CACHE_NAME = `${CACHE_PREFIX}v5`;

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then(async (keys) => {
        const staleKeys = keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME);
        await Promise.all(staleKeys.map((key) => caches.delete(key)));
        await self.clients.claim();

        // One controlled refresh moves pages that were executing a stale cached
        // renderer onto the newly activated build. Fresh installs do not reload.
        if (staleKeys.length) {
          const clients = await self.clients.matchAll({ type: 'window' });
          await Promise.all(clients.map((client) => client.navigate(client.url)));
        }
      }),
  );
});

function cacheResponse(request, response) {
  if (response.ok) {
    const copy = response.clone();
    void caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
  }
  return response;
}

async function matchCurrentCache(request) {
  const cache = await caches.open(CACHE_NAME);
  return cache.match(request);
}

function networkFirst(request) {
  return fetch(request)
    .then((response) => cacheResponse(request, response))
    .catch(async () => {
      const cached = await matchCurrentCache(request);
      if (cached) return cached;
      throw new Error(`Offline asset unavailable: ${request.url}`);
    });
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const applicationCode = ['script', 'style', 'worker'].includes(request.destination);
  if (request.mode === 'navigate' || applicationCode || url.pathname.includes('/dof-manifests/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  event.respondWith(
    matchCurrentCache(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => cacheResponse(request, response));
    }),
  );
});
