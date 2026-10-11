"""Recon: open the collision view and dump its real DOM so selectors are not guesses."""
import sys
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:4178/app/#collision"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    logs = []
    pg.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:200]}"))
    pg.on("pageerror", lambda e: logs.append(f"[pageerror] {e}"))
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2500)

    print("=== TITLE ===", pg.title())
    print("=== BACKEND STATUS ===")
    print(pg.evaluate("() => window.SparkFusion ? Object.keys(window.SparkFusion) : 'NO SparkFusion'"))

    # open the fusion workspace details if collapsed
    pg.evaluate("() => { const d=document.querySelector('#collision-backend'); if(d && !d.open) d.open = true; }")
    pg.wait_for_timeout(1500)

    print("=== TOP-LEVEL STRUCTURE of #collision-backend ===")
    print(pg.evaluate("""() => {
      const d = document.querySelector('#collision-backend');
      if (!d) return 'missing';
      const walk = (el, depth) => {
        if (depth > 3) return [];
        return [...el.children].flatMap(c => {
          const own = `${'  '.repeat(depth)}<${c.tagName.toLowerCase()}${c.id?'#'+c.id:''}${c.className?' .'+String(c.className).split(' ').slice(0,3).join('.'):''}>`;
          const label = (c.textContent||'').trim().slice(0,60);
          return [own + (label && depth<3 ? '  «'+label.replace(/\\s+/g,' ')+'»' : '')].concat(walk(c, depth+1));
        });
      };
      return walk(d, 0).join('\\n');
    }"""))

    print("=== BUTTONS / INPUTS ===")
    print(pg.evaluate("""() => [...document.querySelectorAll('button, input, select, textarea, summary')]
      .filter(e => e.offsetParent !== null)
      .map(e => `${e.tagName.toLowerCase()}${e.id?'#'+e.id:''}${e.type?'['+e.type+']':''}${e.dataset.action?'{act:'+e.dataset.action+'}':''} :: ${(e.textContent||e.value||e.placeholder||'').trim().slice(0,42)}`)
      .join('\\n')"""))

    print("=== CONSOLE ===")
    print("\n".join(logs[-25:]))
    b.close()