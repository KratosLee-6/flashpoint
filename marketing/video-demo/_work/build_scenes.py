"""Generate animated HTML scenes (1920x1080) for the demo video.

Scene 1 pain / Scene 4 data / Scene 5 CTA are built here.
Scene 2 and 3 use real screen-recording footage captured from the running app.
All facts in these scenes come from real repository artifacts (see notes in each scene).
"""
from pathlib import Path

OUT = Path(__file__).parent.parent / "scenes"
OUT.mkdir(parents=True, exist_ok=True)

BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;overflow:hidden}
body{
  font-family:"Microsoft YaHei","Noto Sans SC","PingFang SC",sans-serif;
  background:
    radial-gradient(1100px 700px at 82% -12%, #ffffff 0%, rgba(255,255,255,0) 62%),
    radial-gradient(760px 620px at 6% 108%, #dbe6ff 0%, rgba(219,230,255,0) 58%),
    linear-gradient(140deg,#eef3ff 0%,#e8effe 52%,#f3f0ff 100%);
  color:#0d1526; display:flex; flex-direction:column; position:relative;
}
.stage{padding:76px 104px;display:flex;flex-direction:column;flex:1;min-height:0}
.eyebrow{font-size:19px;letter-spacing:.34em;color:#2563eb;font-weight:800;font-family:"Segoe UI",sans-serif}
h1{font-size:74px;line-height:1.16;font-weight:900;letter-spacing:-.015em}
h1 .hl{color:#2563eb}
.lead{font-size:27px;line-height:1.6;color:#42506b;margin-top:20px;font-weight:500}
.card{background:#fff;border-radius:26px;box-shadow:0 18px 46px rgba(29,58,120,.10)}
.kicker{font-size:17px;letter-spacing:.2em;color:#6b7a99;font-weight:700}
.foot{position:absolute;left:104px;right:104px;bottom:40px;display:flex;justify-content:space-between;
      font-size:16px;color:#7b88a6;font-family:"Segoe UI",sans-serif;letter-spacing:.04em}
.bar{position:absolute;left:0;top:0;height:5px;width:100%;background:linear-gradient(90deg,#2563eb,#7c3aed);transform-origin:0 50%;transform:scaleX(0);animation:grow 1s cubic-bezier(.2,.8,.2,1) forwards}
@keyframes grow{to{transform:scaleX(1)}}
.fade{animation:fade .7s ease both}
@keyframes fade{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:none}}
"""


def page(body, extra_css="", dur_ms=12000):
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>{BASE_CSS}{extra_css}</style></head><body>
<div class="bar" style="animation-duration:{dur_ms}ms"></div>
{body}
</body></html>"""


# ───────────────────────────── Scene 1 · 痛点 ─────────────────────────────
PARENT_FILES = [
    ("SKILL.md", "18,924"), ("references/00-路由索引.md", "31,576"),
    ("references/01-分龄发展与教养重点.md", "72,240"), ("references/05-行为与情绪应对.md", "49,679"),
    ("references/11-工具与档案模板.md", "60,356"), ("references/13-家庭早教与启蒙.md", "64,641"),
    ("… 其余 12 个文件", ""),
]
KEY_FILES = [
    ("SKILL.md", "9,438"), ("icon.svg", "3,308"),
    ("icon.png", "59,953"),
]
TRUNC_TAIL = '  "workflow":[…,"跑 fixtures/b-device-key-attack.py 与 fixtures/b-device-key-clean.py &lt;&lt;&lt;HERE&gt;&gt;&gt;,'


def tree_rows(rows):
    out = []
    for name, size in rows:
        pad = "" if "/" not in name else "&nbsp;&nbsp;&nbsp;&nbsp;│&nbsp;"
        dim = "dim" if size == "" else ""
        sz = f'<span class="sz">{size}</span>' if size else ""
        out.append(f'<li class="{dim}"><span class="ic">{"▤" if name.endswith(".md") else "◆"}</span>{pad}{name}{sz}</li>')
    return "".join(out)


S1 = page(f"""
<div class="s1">
  <div class="trees">
    <div class="card tree fade" style="animation-delay:.35s">
      <div class="thead"><span class="chip a">A</span>
        <div><b>育儿顾问 Skill</b><small>19 个文件 · 给家长的建议框架</small></div></div>
      <ul class="flist">{tree_rows(PARENT_FILES)}</ul>
    </div>
    <div class="x fade" style="animation-delay:1.1s">×</div>
    <div class="card tree fade" style="animation-delay:.7s">
      <div class="thead"><span class="chip b">B</span>
        <div><b>设备密钥证明 Skill</b><small>3 个文件 · 签名验证与防重放</small></div></div>
      <ul class="flist">{tree_rows(KEY_FILES)}</ul>
    </div>
  </div>
  <div class="sub fade" style="animation-delay:4.6s">
    <span class="arrow">碰撞 →</span>
    <span class="claim">一份看起来很完整的融合方案</span>
  </div>
  <div class="code fade" style="animation-delay:5.4s">
    <div class="dotrow"><i></i><i></i><i></i><span class="fname">fusion-result.json</span>
      <span class="verdict">JSONDecodeError</span></div>
    <pre>$ python -c "json.load(open('fusion-result.json'))"
<span class="dimline">{{"candidates":[{{"orientation":"A","title":"设备密钥越界静态扫描器</span>
<span class="dimline">  （AST + regex 双引擎）","artifacts":[{{"path":"scanner/scan.py",…}}],</span>
<span class="dimline">  "workflow":["用 ast.parse 把 B 的设备密钥实现代码变成 Module，…</span>
<span class="badline">{TRUNC_TAIL}</span>
<span class="err">json.decoder.JSONDecodeError: Unterminated string starting at: line 1 column 8123</span>$ <span class="cursor"></span></pre>
  </div>
  <div class="stamp fade" style="animation-delay:8.2s">
    <span class="xmark">✕</span>
    <div><b>整份输出 · 16 次 · 0 次解析成功</b>
      <small>看起来是完整方案，其实连 JSON 都不是 —— 拿不到就等于没有</small></div>
  </div>
</div>
<div class="foot"><span>AI 闪光点 · Skill 碰撞台</span><span>01 / 痛点</span></div>
""", extra_css="""
.fade{opacity:0}
.s1{display:flex;flex-direction:column;justify-content:center;height:100%}
.trees{display:flex;align-items:center;gap:34px;justify-content:center}
.tree{width:640px;padding:30px 34px}
.thead{display:flex;align-items:center;gap:18px;margin-bottom:20px}
.thead b{font-size:27px;display:block}
.thead small{font-size:16px;color:#6b7a99;display:block;margin-top:3px}
.chip{width:58px;height:58px;border-radius:17px;display:grid;place-items:center;font-size:27px;font-weight:900;color:#fff;flex:none}
.chip.a{background:linear-gradient(140deg,#2563eb,#4f7ff0)}
.chip.b{background:linear-gradient(140deg,#7c3aed,#a855f7)}
.x{font-size:56px;font-weight:300;color:#94a3b8}
.flist{list-style:none;font-family:Consolas,monospace;font-size:19px;color:#2c3a55;line-height:2.0}
.flist li{display:flex;align-items:center;gap:6px}
.flist .ic{color:#8fa3c8}
.flist .sz{margin-left:auto;color:#93a1bd;font-size:17px;padding-left:20px}
.flist li.dim{color:#9aa7c0;font-style:italic}
.sub{display:flex;align-items:center;justify-content:center;gap:22px;margin-top:34px}
.arrow{font-size:30px;font-weight:800;color:#2563eb}
.claim{font-size:31px;font-weight:800}
.code{margin:26px auto 0;width:1300px;background:#0e1729;border-radius:20px;overflow:hidden;
      box-shadow:0 22px 60px rgba(14,23,41,.30)}
.dotrow{display:flex;align-items:center;gap:9px;padding:13px 20px;background:#16223a}
.dotrow i{width:12px;height:12px;border-radius:50%;background:#3c4d6e}
.fname{margin-left:12px;color:#8fa0bf;font-family:Consolas,monospace;font-size:17px}
.verdict{margin-left:auto;background:#7f1d1d;color:#fecaca;font-size:15px;font-weight:700;
         padding:5px 13px;border-radius:8px;font-family:Consolas,monospace}
.code pre{margin:0;padding:22px 26px;color:#cfe0ff;font-family:Consolas,monospace;font-size:19px;line-height:1.75;white-space:pre-wrap}
.dimline{color:#5d739c}
.badline{color:#fca5a5;background:rgba(239,68,68,.14);display:block;margin:0 -26px;padding:0 26px}
.err{color:#fb7185;font-weight:700}
.cursor{display:inline-block;width:10px;height:20px;background:#7dd3fc;vertical-align:-3px;animation:blink 1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.stamp{display:flex;align-items:center;gap:20px;margin:30px auto 0;background:#fff1f2;border:2px solid #fecdd3;
       border-radius:20px;padding:20px 30px;width:1300px}
.xmark{font-size:44px;color:#e11d48;font-weight:900}
.stamp b{font-size:27px;color:#9f1239;display:block}
.stamp small{font-size:19px;color:#be123c;display:block;margin-top:5px;opacity:.9}
""", dur_ms=12000)

# ───────────────────────────── Scene 4 · 数据 ─────────────────────────────
S4 = page("""
<div class="stage">
  <div class="fade" style="animation-delay:.3s">
    <div class="eyebrow">CONTROLLED EXPERIMENT / 对照实验</div>
    <h1 style="margin-top:14px">唯一变量：<span class="hl">是否拆段</span></h1>
    <p class="lead">同一模型 · 同一材料 · 同一套验收链。整份输出 vs 分段生成 + 逐段校验。</p>
  </div>
  <div class="chart fade" style="animation-delay:1.1s">
    <div class="col">
      <div class="val" style="animation-delay:2.0s">0<em>/32</em></div>
      <div class="barwrap"><div class="bar1"></div></div>
      <div class="lbl"><b>整份输出（旧）</b><small>shape=30 · json=2</small></div>
    </div>
    <div class="gapwrap">
      <div class="vs">同一批验收器<br>零改动</div>
    </div>
    <div class="col">
      <div class="val ok" style="animation-delay:2.6s">23<em>/25</em></div>
      <div class="barwrap"><div class="bar2"></div></div>
      <div class="lbl"><b>分段 + 逐段校验（新）</b><small>segment_rejected=2</small></div>
    </div>
  </div>
  <div class="punch fade" style="animation-delay:5.0s">
    差距不在模型变聪明 —— <b>在工序。</b>
  </div>
</div>
<div class="foot"><span>数据来源：ab-blocksplit / fulltext-n16 等留档，同材料同模型两臂 n=16</span><span>04 / 数据</span></div>
""", extra_css="""
.fade{opacity:0}
.chart{display:flex;align-items:flex-end;justify-content:center;gap:70px;margin-top:70px;flex:1}
.col{width:470px;display:flex;flex-direction:column;align-items:center}
.val{font-size:96px;font-weight:900;color:#94a3b8;line-height:1;font-family:"Segoe UI",sans-serif;
     opacity:0;animation:pop .6s cubic-bezier(.2,1.3,.4,1) forwards}
.val.ok{color:#2563eb}
.val em{font-style:normal;font-size:44px;opacity:.55}
@keyframes pop{from{opacity:0;transform:scale(.7)}to{opacity:1;transform:none}}
.barwrap{width:100%;height:420px;display:flex;align-items:flex-end;margin:26px 0 22px;
         background:rgba(255,255,255,.55);border-radius:22px;padding:14px;box-shadow:inset 0 2px 10px rgba(29,58,120,.06)}
.bar1,.bar2{width:100%;border-radius:14px;transform-origin:50% 100%;transform:scaleY(0)}
.bar1{height:9%;background:linear-gradient(180deg,#cbd5e1,#94a3b8);animation:rise 1.1s cubic-bezier(.2,.9,.25,1) 1.5s forwards}
.bar2{height:92%;background:linear-gradient(180deg,#60a5fa,#2563eb);animation:rise 1.3s cubic-bezier(.2,.9,.25,1) 1.9s forwards}
@keyframes rise{to{transform:scaleY(1)}}
.lbl{text-align:center}
.lbl b{font-size:26px;display:block}
.lbl small{font-size:18px;color:#6b7a99;display:block;margin-top:6px;font-family:Consolas,monospace}
.gapwrap{display:flex;align-items:center;justify-content:center;height:420px}
.vs{border-left:3px dashed #b9c6e0;border-right:3px dashed #b9c6e0;padding:0 34px;text-align:center;
    font-size:19px;color:#6b7a99;line-height:1.7;font-weight:600}
.punch{text-align:center;font-size:38px;font-weight:700;color:#33415e;margin-bottom:26px}
.punch b{color:#2563eb;font-size:44px}
""", dur_ms=13000)

# ───────────────────────────── Scene 5 · CTA ─────────────────────────────
COMBOS = [
    ("防骗话术", "证据采集", "来源可信度验证器"),
    ("润色去 AI 味", "防刷计分", "文本质量门禁"),
    ("编程助手", "默认拒绝权限", "权限扫描器"),
    ("育儿顾问", "设备密钥证明", "建议签名 + 防重放"),
]

rows = "".join(
    f"""<div class="row fade" style="animation-delay:{1.5 + i * .55}s">
      <span class="a">{c[0]}</span><span class="x">×</span><span class="b">{c[1]}</span>
      <span class="arr">→</span><span class="r">{c[2]}</span>
      {'<span class="ok">可运行</span>' if i == 3 else ''}
    </div>""" for i, c in enumerate(COMBOS))

S5 = page(f"""
<div class="stage">
  <div class="fade" style="animation-delay:.3s">
    <div class="eyebrow">VERIFIED COMBINATIONS / 已验证组合</div>
    <h1 style="margin-top:14px">四个<span class="hl">跨领域</span>碰撞包，<br>全部物化并自测通过</h1>
  </div>
  <div class="list">{rows}</div>
  <div class="cta fade" style="animation-delay:4.4s">
    <span class="q">你会让<span class="hl">哪两个技能</span>，一起解决一个问题？</span>
  </div>
</div>
<div class="foot"><span>AI 闪光点 · Skill 碰撞台 · 开源 MIT</span><span>05 / 开始</span></div>
""", extra_css="""
.fade{opacity:0}
.list{margin-top:46px;display:flex;flex-direction:column;gap:16px;flex:1}
.row{display:flex;align-items:center;gap:20px;background:#fff;border-radius:20px;padding:24px 32px;
     box-shadow:0 12px 32px rgba(29,58,120,.09);font-size:29px;font-weight:800}
.row .a,.row .b{color:#0d1526}
.row .x{color:#b6c2da;font-weight:400}
.row .arr{color:#94a3b8;font-weight:400;margin-left:8px}
.row .r{color:#2563eb}
.row .ok{margin-left:auto;background:#e8f7ee;color:#15803d;font-size:20px;font-weight:800;
         padding:8px 18px;border-radius:12px;letter-spacing:.05em}
.cta{margin:34px 0 30px;background:linear-gradient(120deg,#2563eb,#7c3aed);border-radius:26px;
     padding:34px 44px;box-shadow:0 20px 50px rgba(37,99,235,.32)}
.q{font-size:42px;font-weight:900;color:#fff}
.q .hl{color:#fde68a}
""", dur_ms=13000)

for name, html in [("scene1.html", S1), ("scene4.html", S4), ("scene5.html", S5)]:
    (OUT / name).write_text(html, encoding="utf-8")
    print("wrote", OUT / name, len(html), "bytes")