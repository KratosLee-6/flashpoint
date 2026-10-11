"""Recon 3: settings page model-access fields + how the quick panel sources them."""
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:4178/app/#settings"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(1500)

    print("=== SETTINGS MODEL-ACCESS CONTROLS ===")
    print(pg.evaluate("""() => [...document.querySelectorAll('[data-model-access], #model-access-key, #model-access-state, button')]
      .filter(e => e.offsetParent !== null)
      .map(e => `${e.tagName.toLowerCase()}${e.id?'#'+e.id:''}${e.dataset.modelAccess?'[ma='+e.dataset.modelAccess+']':''}${e.dataset.action?'{act:'+e.dataset.action+'}':''} :: ${(e.textContent||e.value||e.placeholder||'').trim().slice(0,46)}`)
      .filter(s => s.includes('ma=') || s.includes('model') || s.includes('模型') || s.includes('接口') || s.includes('保存') || s.includes('清除') || s.includes('探测'))
      .join('\\n')"""))

    print("\n=== ALL data-action IN SETTINGS ===")
    print(pg.evaluate("""() => [...document.querySelectorAll('[data-action]')]
      .filter(e => e.offsetParent !== null)
      .map(e => `{${e.dataset.action}} :: ${(e.textContent||'').trim().slice(0,40)}`)
      .join('\\n')"""))

    print("\n=== PROVIDER CATALOG (if any select) ===")
    print(pg.evaluate("""() => [...document.querySelectorAll('select')]
      .map(s => s.id + ' :: ' + [...s.options].map(o=>o.textContent.trim().slice(0,30)).join(' | '))
      .join('\\n')"""))

    b.close()