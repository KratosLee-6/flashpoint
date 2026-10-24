const CACHE='sparkroom-d4ec9511c306c10e';const ASSETS=["./", "./index.html", "./styles.css", "./skills.js", "./core.js", "./storage.js", "./runtime.js", "./spark-fx.js", "./fusion-ui.js", "./app.js", "./seed.js", "./manifest.webmanifest", "./icon.svg", "./icon-192.png", "./icon-512.png"];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS))));
self.addEventListener('message',e=>{if(e.data?.type==='ACTIVATE')self.skipWaiting()});
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('sparkroom-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method!=='GET'||u.origin!==self.location.origin||u.pathname.startsWith('/api/'))return;
e.respondWith(caches.open(CACHE).then(c=>c.match(e.request).then(hit=>hit||fetch(e.request).catch(()=>e.request.mode==='navigate'?c.match('./index.html'):Response.error()))));});
