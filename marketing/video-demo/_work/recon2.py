"""Recon 2: switch to 快速碰撞 mode and dump the quick panel + full pack list."""
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:4178/app/#collision"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    pg.evaluate("() => { const d=document.querySelector('#collision-backend'); if(d) d.open = true; }")
    pg.wait_for_timeout(800)

    print("=== AVAILABLE PACKS (datalist) ===")
    print(pg.evaluate("""() => [...document.querySelectorAll('#fusion-pack-sources option')]
        .map(o => o.value).join('\\n')"""))

    # switch to quick mode
    pg.get_by_role("button", name="快速碰撞").first.click()
    pg.wait_for_timeout(1500)

    print("\n=== QUICK MODE STRUCTURE ===")
    print(pg.evaluate("""() => {
      const ws = document.querySelector('#fusion-workspace');
      const walk = (el, depth) => {
        if (depth > 4) return [];
        return [...el.children].flatMap(c => {
          const own = `${'  '.repeat(depth)}<${c.tagName.toLowerCase()}${c.id?'#'+c.id:''}${c.dataset&&c.dataset.action?'{act:'+c.dataset.action+'}':''}>`;
          const label = (c.textContent||'').trim().replace(/\\s+/g,' ').slice(0,70);
          return [own + (label && depth<4 ? '  «'+label+'»' : '')].concat(walk(c, depth+1));
        });
      };
      return walk(ws, 0).join('\\n');
    }"""))

    print("\n=== VISIBLE CONTROLS (quick mode) ===")
    print(pg.evaluate("""() => [...document.querySelectorAll('button, input, select, textarea')]
      .filter(e => e.offsetParent !== null)
      .map(e => `${e.tagName.toLowerCase()}${e.id?'#'+e.id:''}${e.type?'['+e.type+']':''}${e.dataset.action?'{act:'+e.dataset.action+'}':''} :: ${(e.textContent||e.value||e.placeholder||'').trim().slice(0,50)}`)
      .join('\\n')"""))

    print("\n=== ERRORS ===", errs)
    b.close()