(function(){
  'use strict';
  const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)], S=window.Spark,K=window.SparkSkills;
  const NAV=[['home','灵感花园','✳','今天值得看的东西'],['library','成品库','▦','会话、文档与文件'],['collision','碰撞台','✣','Skill 融合与执行方案'],['evolution','换代重测','↻','看模型变化留下什么'],['settings','数据与偏好','⚙','本地、备份与外观']];
  const KIND={session:'会话',document:'文档',note:'笔记',collision:'碰撞',file:'文件'};
  const state={route:location.hash.slice(1).split('?')[0]||'home',filters:{query:'',status:'all',kind:'',project:'',sort:'updated'},pendingConfirm:null,collisionMy:null,collisionOther:null,lastCollision:null};
  if(state.route==='fusion'){state.route='collision';state.fusionOpen=true;history.replaceState(null,'','#collision')}
  // 39 号文迁移 A1：桌面壳把整块状态存进 SQLite（经 Tauri invoke，异步），
  // 浏览器仍用 localStorage。adapter 只决定「存到哪」，语义由 storage.js 保证。
  const tauriCore=window.__TAURI__&&window.__TAURI__.core&&typeof window.__TAURI__.core.invoke==='function'?window.__TAURI__.core:null;
  const desktopStore=tauriCore?{getItem:key=>tauriCore.invoke('store_get',{key}),setItem:(key,value)=>tauriCore.invoke('store_set',{key,value})}:null;
  const store=desktopStore||localStorage;
  const workspace=window.SparkStorage.createWorkspace({storage:()=>store,initial:()=>S.initial(window.SPARK_SEED||{records:[]}),validate:S.validateState});
  let data=workspace.snapshot().state,limit=48,searchTimer;
  let pendingRestore=null,restoreRevision=0,readingBackup=false,pendingExternal=false;
  const audio=window.SparkRuntime.createAudio({Context:window.AudioContext||window.webkitAudioContext,enabled:()=>data.settings.sound,visible:()=>!document.hidden});
  const collisionJob=window.SparkRuntime.createCollision({changed:paintCollision});
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  function sound(kind){audio.play(kind)}
  function stopSounds(){audio.stop()}
  // 写入是异步的：写完才算「已保存」，失败则回到上一次确认过的状态，
  // 界面文字与旧版承诺一致：原有资料已保留。
  // candidate 在**执行时**才读 data，而不是调用时就抓取——否则连点两次时第二次
  // 会带着还没落地的旧快照提交，把前一次改动整份盖掉。
  // 返回 Promise，成功时 resolve(null)，失败时 resolve(失败原因)——
  // 「不能往下走」的调用方（编辑器、Skill 导入）必须 await 它再关弹窗：
  // 旧版靠同步抛错拦住了这一步，异步化后必须把成败与原因显式交回调用方。
  // 失败一律不 reject：收藏/偏好这类「点了就走」的调用点不必各写一遍 catch。
  let saving=Promise.resolve();
  function save(note){
    const label=note||'已保存 · 只这台设备';
    saving=saving.then(()=>workspace.commit(data)).then(snapshot=>{
      data=snapshot.state;
      $('#save-status').textContent=label;
      return null;
    },error=>{
      // 回滚到「最后一次成功落盘的状态」。workspace 只在 setItem 成功后才推进自己的
      // current，所以 snapshot() 天然就是这个基线——不需要另存一份 confirmed。
      data=workspace.snapshot().state;
      $('#save-status').textContent='保存失败 · 原有资料已保留';
      toast(error.message||'保存失败 · 原有资料已保留');
      return error;
    });
    return saving;
  }
  function guarded(action){try{action()}catch(error){paintPreferences();toast(error.message||'操作未完成，请重试')}}
  function icon(name){
    const paths={home:'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',library:'M4 4h6v7H4z M14 4h6v7h-6z M4 15h6v5H4z M14 15h6v5h-6z',collision:'M8 4a8 8 0 1 0 0 16 M16 4a8 8 0 1 1 0 16 M12 8v8 M8 12h8',evolution:'M20 8a8 8 0 1 0 0 8 M20 3v5h-5',settings:'M4 7h16 M4 17h16 M8 4v6 M16 14v6',session:'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3V6a2 2 0 0 1 1-2Z',document:'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6',note:'M4 20h4L20 8l-4-4L4 16z M13 7l4 4',sound:'M3 9h4l5-4v14l-5-4H3z M16 8a6 6 0 0 1 0 8 M19 5a10 10 0 0 1 0 14',search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',spark:'M12 3c0 6-3 9-9 9 6 0 9 3 9 9 0-6 3-9 9-9-6 0-9-3-9-9Z'};
    return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="'+(paths[name]||paths.document)+'"/></svg>';
  }
  function find(id){return data.records.find(r=>r.id===id)}
  function esc(v){return S.escape(v)}
  function kindIcon(k){return icon(k)}
  function tone(r){return r.kind==='collision'?'gold':r.kind==='document'?'blue':r.status==='final'?'green':'neutral'}
  function fmtDate(v,time){if(!v)return'—';const d=new Date(v);if(Number.isNaN(d.getTime()))return String(v).slice(0,16);const now=new Date();return d.toDateString()===now.toDateString()?(time?d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'今天'):d.toLocaleDateString('zh-CN',{month:'short',day:'numeric'})}
  function stats(){return S.stats(data.records)}
  function navigate(route,extra){
    if(route==='fusion'){route='collision';state.fusionOpen=true}
    if(!NAV.some(n=>n[0]===route))route='home';
    clearTimeout(searchTimer);stopSounds();collisionJob.cancel();limit=48;
    state.route=route;Object.assign(state.filters,extra||{});history.replaceState(null,'','#'+route);render();$('#main').focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});
  }
  function status(r){return r.status==='final'?'已定稿':r.status==='archived'?'已归档':'待处理'}
  function score(r){return r.score==null?'<span class="score-empty">待评</span>':'<span class="score-badge '+(r.score>=8?'high':'')+'">'+Number(r.score).toFixed(1)+'</span>'}
  function card(r){let tags=(r.tags||[]).slice(0,3).map(t=>'<span class="chip">'+esc(t)+'</span>').join('');return '<article class="spark-card" data-open="'+esc(r.id)+'"><div class="card-top"><span class="kind-mark '+tone(r)+'">'+kindIcon(r.kind)+'</span><span class="card-kind">'+(KIND[r.kind]||r.kind)+'</span><span class="card-date">'+fmtDate(r.updated)+'</span></div><h3><button class="card-open" data-open="'+esc(r.id)+'">'+esc(r.title)+'</button></h3><p>'+esc((r.summary||r.body||'还没有摘要，打开它补上第一句。').slice(0,190))+'</p><div class="card-bottom"><div class="chips">'+tags+'</div>'+score(r)+'</div><button class="favorite '+(r.favorite?'on':'')+'" data-fav="'+esc(r.id)+'" aria-label="收藏：'+esc(r.title)+'" aria-pressed="'+Boolean(r.favorite)+'">'+(r.favorite?'★':'☆')+'</button></article>'}
  function empty(title,copy,search=false){return '<div class="empty-state"><div class="empty-spark">✳</div><h3>'+title+'</h3><p>'+copy+'</p>'+(search?'<button class="btn subtle" data-action="clear-filters">清除全部筛选</button>':'<button class="btn primary" data-action="new">收下一点灵感 <span>↗</span></button>')+'</div>'}
  function home(){const st=stats(),recent=S.select(data.records,{sort:'updated'}).slice(0,6);return `<div class="page page-home">
    <section class="hero"><div class="hero-copy"><span class="eyebrow">YOUR IDEAS, CONNECTED.</span><h1>好想法，<br>总会<span>再相遇。</span></h1><p>收好做过的事。让散落的会话、作品与能力，成为下一次创造的起点。</p><div class="hero-actions"><button class="btn primary" data-nav="collision">去碰一碰 <span>→</span></button><button class="btn subtle" data-action="new">记录新灵感</button></div><div class="hero-note"><span class="tiny-dot"></span> ${st.total.toLocaleString()} 件资料，在你的设备上。</div></div>
    <div class="hero-art" aria-hidden="true"><div class="art-orbit orbit-one"></div><div class="art-orbit orbit-two"></div><div class="art-tile tile-one">${icon('document')}<span>一个想法</span></div><div class="art-tile tile-two">${icon('collision')}<span>另一种可能</span></div><div class="art-core">${icon('spark')}</div><span class="art-caption">A LITTLE SPARK. A NEW POSSIBILITY.</span></div></section>
    <section class="stat-row"><div><span>本地资料</span><strong>${st.total.toLocaleString()}</strong><small>随时找回</small></div><div><span>已定稿</span><strong>${st.final}</strong><small>留下最好的版本</small></div><div><span>收藏</span><strong>${st.favorites}</strong><small>值得再次打开</small></div><div><span>覆盖项目</span><strong>${st.projects}</strong><small>连接不同的工作</small></div></section>
    <div class="section-head"><div><span class="eyebrow">YOUR COLLECTION</span><h2>最近收好的</h2></div><button class="text-btn" data-nav="library">浏览全部 →</button></div><div class="card-grid">${recent.map(card).join('')||empty('从第一个想法开始','记下你想保留的东西。')}</div>
    <section class="home-bottom"><div><span class="eyebrow">SMALL STEPS</span><h2>下一次灵感，从这里开始。</h2><p>选一件资料，再给它一个新的方向。</p><button class="text-btn" data-nav="collision">打开碰撞台 →</button></div><div class="activity-list">${data.activity.slice(0,3).map(a=>'<div class="activity-row"><span class="activity-icon">'+kindIcon(a.kind)+'</span><div><b>'+esc(a.title)+'</b><small>'+fmtDate(a.at,true)+'</small></div></div>').join('')||'<p class="quiet-row">整理、收藏与碰撞的记录会留在这里。</p>'}</div></section></div>`}
  function moreButton(total){return total>limit?'<button class="btn subtle" data-action="load-more">继续浏览 · 已显示 '+Math.min(limit,total)+' / '+total+'</button>':''}
  function updateLibrary(){
    if(state.route!=='library')return;
    const list=S.select(data.records,state.filters);
    $('.result-grid').innerHTML=list.slice(0,limit).map(card).join('')||empty('没有找到匹配资料','试试更短的关键词，或清除筛选。',true);
    $('#result-count').textContent='共 '+list.length.toLocaleString()+' 件';
    $('.load-more').innerHTML=moreButton(list.length);
  }
  function library(){const st=stats(),projects=[...new Set(data.records.map(r=>r.project).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh')),list=S.select(data.records,state.filters);let filters='<button class="filter-pill '+(state.filters.status==='all'?'active':'')+'" data-filter="status" data-value="all">全部 <i>'+st.total+'</i></button><button class="filter-pill '+(state.filters.status==='inbox'?'active':'')+'" data-filter="status" data-value="inbox">待处理 <i>'+data.records.filter(r=>r.status==='inbox').length+'</i></button><button class="filter-pill '+(state.filters.status==='final'?'active':'')+'" data-filter="status" data-value="final">已定稿 <i>'+st.final+'</i></button><button class="filter-pill '+(state.filters.status==='favorites'?'active':'')+'" data-filter="status" data-value="favorites">收藏 <i>'+st.favorites+'</i></button><span class="filter-sep"></span>';['session','document','note','collision'].forEach(k=>filters+='<button class="filter-pill '+(state.filters.kind===k?'active':'')+'" data-filter="kind" data-value="'+k+'">'+KIND[k]+'</button>');let proj=projects.slice(0,18).map(p=>'<button class="project-item '+(state.filters.project===p?'active':'')+'" data-project="'+esc(p)+'"><span>'+esc(p)+'</span><i>'+data.records.filter(r=>r.project===p).length+'</i></button>').join('');return '<div class="page"><section class="page-heading"><div><span class="eyebrow">YOUR LOCAL ARCHIVE</span><h1>成品库</h1><p>把会话、文档和灵感放在一起。每一个分数都能回到原始证据。</p></div><button class="btn primary" data-action="new">＋ 收下一点灵感</button></section><section class="toolbar-panel"><label class="search-box"><span>⌕</span><input id="library-search" aria-label="搜索资料" value="'+esc(state.filters.query)+'" placeholder="搜索标题、正文、项目、标签…"><kbd>⌘ K</kbd></label><div class="filter-scroll">'+filters+'</div><div class="toolbar-bottom"><span id="result-count" role="status">共 '+list.length.toLocaleString()+' 件</span><select id="sort-select" aria-label="资料排序"><option value="updated" '+(state.filters.sort==='updated'?'selected':'')+'>最近更新</option><option value="score" '+(state.filters.sort==='score'?'selected':'')+'>归档分从高到低</option><option value="title" '+(state.filters.sort==='title'?'selected':'')+'>标题</option></select></div></section><div class="library-layout"><section class="library-results"><div class="result-head"><span>'+(state.filters.project?'项目：'+esc(state.filters.project):'全部项目')+'</span>'+(state.filters.project?'<button class="text-btn" data-clear-project>清除</button>':'')+'</div><div class="result-grid">'+(list.slice(0,limit).map(card).join('')||empty('没有找到匹配资料','试试更短的关键词，或清除筛选。',true))+'</div><div class="load-more">'+moreButton(list.length)+'</div></section><aside class="library-aside"><div class="aside-card"><span class="eyebrow">PROJECTS</span><h3>按项目找回</h3><div class="project-list">'+(proj||'<p class="muted">还没有归位项目。</p>')+'</div></div><div class="aside-card archive-note"><span class="eyebrow">ARCHIVE SCORE</span><p>归档分回答“这件事有没有足够证据被留下”，它不是作品好坏分。</p><button class="text-btn" data-nav="evolution">了解重测口径 <span>→</span></button></div></aside></div></div>'}
  function skillChoices(){return S.select(data.records,{sort:'updated'}).filter(r=>K.isSkill(r))}
  function selectedSkills(){const list=skillChoices(),a=list.find(r=>r.id===state.collisionMy)||list[0],b=list.find(r=>r.id===state.collisionOther)||list.find(r=>r.id!==a?.id);return [a,b]}
  function canCollide(){const [a,b]=selectedSkills();return Boolean(a&&b&&a.id!==b.id&&a.body!==b.body)}
  function contractList(items){return items.length?'<ul>'+items.map(x=>'<li><span class="source-line">L'+x.line+'</span> '+esc(x.text)+'</li>').join('')+'</ul>':'<p class="muted">尚未声明，需补充或人工确认。</p>'}
  function skillSlot(side,record,list){
    const contract=record?K.parse(record.body):null,id=side==='A'?'collision-my':'collision-other';
    return '<div class="versus-card '+(side==='A'?'mine':'other')+'"><span class="material-label">'+side+' · 真实 Skill</span><label class="skill-select"><span class="sr-only">选择 Skill '+side+'</span><select id="'+id+'" '+(!list.length?'disabled':'')+'>'+(list.length?list.map(r=>'<option value="'+esc(r.id)+'" '+(r.id===record?.id?'selected':'')+'>'+esc(r.title)+'</option>').join(''):'<option>先导入 SKILL.md</option>')+'</select></label>'+(contract?'<p>'+esc(contract.description||'未声明用途')+'</p><div class="skill-contract"><h4>输入</h4>'+contractList(contract.fields.inputs)+'<h4>输出</h4>'+contractList(contract.fields.outputs)+'</div><button class="text-btn" data-open="'+esc(record.id)+'">查看原文与指纹</button>':'<div class="skill-empty">'+icon('document')+'<p>选择本机的 SKILL.md，或粘贴完整内容。</p></div>')+'<button class="btn subtle" data-action="import-skill" data-id="'+side+'">导入 Skill '+side+'</button></div>';
  }
  function saveFusion(result){
    if(workspace.status().recovery)throw Error('请先恢复本地资料库，再保存方案');
    const body=JSON.stringify(result,null,2);
    if(result.type==='fusion-review-note'){
      if(data.records.some(record=>record.evidence?.type==='fusion-review-note'&&record.body===body))return;
      data.records.unshift(S.makeRecord({title:'人工审查 · '+result.task_id,body,summary:'单题、两份原始回答的人工意见；不是全批次验收',kind:'document',project:'Skill 融合',source:'本机证据审查',tags:['人工意见','待验收'],evidence:{type:'fusion-review-note',artifact:result.artifact_id,evidence_sha256:result.evidence_sha256,value_verified:false}}));
      save();return;
    }
    const existing=data.records.find(r=>r.evidence?.type==='fusion-proposal'&&r.body===body);
    if(existing)return;
    data.records.unshift(S.makeRecord({title:'Skill 融合 · '+result.result.candidates[0].title,body,
      summary:'A/B/C 三方向方案，待执行与效果验证',kind:'document',project:'Skill 融合',source:'本机融合服务',
      tags:['Skill 融合','待验证'],evidence:{type:'fusion-proposal',packet:result.packet_sha256,value_verified:false}}));
    save();
  }
  function collision(){
    const list=skillChoices(),[a,b]=selectedSkills();
    return `<div class="page page-collision"><section class="page-heading"><div><span class="eyebrow">TWO SKILLS. ONE NEW POSSIBILITY.</span><h1>让真实能力，碰出下一步。</h1><p>不用先知道会碰出什么。选择两个 Skill，审查材料并探索候选方向，再决定新 Skill 值得解决什么、产出什么、怎样实现。</p></div><span class="local-pill">Skill 融合 · 来源可追溯</span></section>
    <section class="skill-library-note"><div><b>你的本地技能库 · ${list.length} 份</b><p>单份 SKILL.md 只用于初步探索；要分析真实实现，请带上完整代码、知识、模板和测试。</p></div><div class="fusion-actions"><button class="btn primary" data-action="package-collision">完整包碰撞（文件夹 / ZIP）</button><button class="text-btn" data-action="swap-skills" ${a&&b?'':'disabled'}>交换 A / B ⇄</button></div></section>
    <section class="collision-main"><div class="collision-stage" data-phase="ready" aria-busy="false"><div class="stage-label"><span>A → B / REVIEW THE CONNECTION</span><span id="collision-status" role="status">${canCollide()?'两份 Skill 已就绪。':'请导入并选择两份不同的 Skill。'}</span></div>
    <div class="versus skill-versus">${skillSlot('A',a,list)}<div class="versus-mark" aria-hidden="true">${icon('spark')}</div>${skillSlot('B',b,list)}</div>
    <ol class="collision-steps" aria-label="碰撞进度"><li data-step="ready" aria-current="step"><span>01</span>选好技能</li><li data-step="running"><span>02</span>核对声明</li><li data-step="result"><span>03</span>收下草稿</li></ol>
    <div class="collision-controls"><button class="collision-button" data-action="backend-collision" ${canCollide()?'':'disabled'}>${icon('collision')}<span>碰一碰</span></button><button class="btn subtle" data-action="collide" ${canCollide()?'':'disabled'}><span>声明初检（离线）</span></button><button class="text-btn" data-action="cancel-collision" hidden>取消</button></div><div id="collision-result" aria-live="polite"><div class="result-placeholder">${icon('spark')}<p>${a&&b&&!canCollide()?'A 与 B 需要不同的内容。':'碰一碰沿用所选 A/B，进入下方后端融合工作区；也可单独做离线声明初检。'}</p></div></div></div><p class="collision-disclaimer">离线初检只比较声明，不等于能力融合。后端从所选 Skill 或完整包提取依据、提出新机制和执行流程；外发材料前须单独确认范围与费用。</p></section><details class="collision-backend" id="collision-backend" ${state.fusionOpen?'open':''}><summary>Skill 融合工作区 · 完整包、方向与新 Skill 方案</summary><div id="fusion-workspace"></div></details></div>`;
  }
  function openBackendCollision(){
    const sources=selectedSkills();K.compose(...sources);
    window.SparkFusion.useSources(sources);
    state.fusionOpen=true;$('#collision-backend').open=true;
    $('#fusion-goal')?.focus();$('#collision-backend').scrollIntoView({block:'start',behavior:'instant'});
  }
  function openPackageCollision(){
    window.SparkFusion.usePackages();
    state.fusionOpen=true;$('#collision-backend').open=true;
    window.SparkFusion.activate();
    $('#fusion-status')?.focus();$('#collision-backend').scrollIntoView({block:'start',behavior:'instant'});
  }
  function collisionResult(c){
    return '<div class="collision-result"><div class="result-kicker">TWO REAL SOURCES · UNVERIFIED DRAFT</div><h3>'+esc(c.title)+'</h3><p>'+esc(c.copy)+'</p><div class="skill-matches"><h4>候选衔接 · A → B</h4>'+(c.matches.length?'<ul>'+c.matches.map(m=>'<li><b>'+esc(m.output.text)+'</b><span>A L'+m.output.line+' ↔ B L'+m.input.line+' · 声明同名，待验证</span></li>').join('')+'</ul>':'<p>没有同名声明。请补充输入输出或设计转换步骤。</p>')+'</div><details class="skill-warnings" open><summary>未决事项 · '+c.warnings.length+'</summary><ul>'+c.warnings.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul></details><details class="skill-draft"><summary>查看组合 Skill 草稿与验收清单</summary><div class="detail-body">'+S.markdown(c.body)+'</div></details><div class="next-step"><span>下一步</span><b>'+esc(c.next)+'</b></div><div class="result-actions"><button class="btn primary" data-action="save-collision">保存组合草稿</button><button class="btn subtle" data-action="download-collision">导出 SKILL.md 草稿</button><button class="text-btn" data-action="copy-collision">复制草稿</button></div></div>';
  }
  function evolution(){const st=stats();return '<div class="page"><section class="page-heading"><div><span class="eyebrow">MODEL CHANGE / HONEST REPORT</span><h1>换代重测</h1><p>模型是配置，不是代码。这里看环境变化之后，已有资料是没变、变了，还是挂了。</p></div><button class="btn subtle" data-action="retest">↻ 重新扫描本机</button></section><div class="evo-grid"><section class="evo-main"><div class="evo-banner"><div><span class="eyebrow">CURRENT FINGERPRINT</span><h2>重测报告示例</h2><p>以下为演示数据，尚未执行本机环境扫描。</p></div><span class="verified">DEMO</span></div><div class="fingerprint"><code>MINIMAX_MODEL=MiniMax-M3<br>ANTHROPIC_MODEL=claude-sonnet-4-5<br>CODEX_MODEL=gpt-5.6</code><div><span>指纹 b6068f93</span><span>示例时间 · 09:42</span></div></div><div class="evo-summary"><div><span>示例资料数</span><b>'+st.total+'</b></div><div><span>输出一致</span><b>'+Math.max(0,st.total-3)+'</b></div><div><span>需要人工判断</span><b>3</b></div></div><div class="section-head"><div><span class="eyebrow">RECENT REPORTS</span><h2>最近重测记录</h2></div><button class="text-btn" data-action="retest">刷新</button></div><div class="report-list"><div class="report"><span class="report-icon green">✓</span><div><b>Html九尾狐 · 联盟路由</b><small>输出指纹一致 · 没变</small></div><time>今天 09:44</time></div><div class="report"><span class="report-icon purple">✦</span><div><b>画语评测 · MCP 封装</b><small>新解锁 · 视觉能力可用</small></div><time>今天 09:44</time></div><div class="report"><span class="report-icon gold">↗</span><div><b>财税合规 · 进销项核验</b><small>变了 · 没有评分，不猜好坏</small></div><time>昨天 18:20</time></div></div></section><aside class="evo-aside"><div class="aside-card honest-card"><span class="quote-mark">“</span><h3>变了，不等于变好了。</h3><p>如果能力包没有在 verify 里报出 SCORE，我们只说输出变了，不替新模型编一个更好的结论。</p><small>这是闪光点对证据的最低承诺。</small></div><div class="aside-card"><span class="eyebrow">NEXT MILESTONE</span><h3>把重测挂回每一件成品</h3><p>下一版会在详情时间线上显示每次环境指纹、原始输出和变化原因。</p><button class="btn subtle full" data-nav="settings">查看跨端计划 <span>→</span></button></div></aside></div></div>'}
  function settings(){const st=stats(),theme=data.settings.theme,ma=S.modelAccess.read();let themes=[['paper','浅色','白色与雾灰，清楚而安静'],['forest','晴空','一点蓝色，让思绪更轻盈'],['night','深夜','低亮度，留给内容更多空间']].map(x=>'<button class="theme-choice '+(theme===x[0]?'active':'')+'" data-theme="'+x[0]+'"><span class="theme-dot '+x[0]+'"></span><span><b>'+x[1]+'</b><small>'+x[2]+'</small></span>'+(theme===x[0]?'<i>✓</i>':'')+'</button>').join('');return '<div class="page"><section class="page-heading"><div><span class="eyebrow">LOCAL BY DEFAULT</span><h1>数据与偏好</h1><p>资料浏览与方向计算可离线使用。主动导出或确认发送给远端模型时，所选资料会离开本机。</p></div><span class="local-pill"><i></i>本地模式</span></section><div class="settings-grid"><section class="settings-main"><div class="settings-card"><div class="settings-card-head"><div><span class="eyebrow">STORAGE</span><h3>这台设备上的空间</h3></div><span class="storage-size">'+(JSON.stringify(data).length/1024).toFixed(0)+' KB</span></div><div class="storage-meter"><i style="width:'+Math.min(96,Math.max(3,JSON.stringify(data).length/50000))+'%"></i></div><p class="muted">'+st.total.toLocaleString()+' 件资料 · '+st.projects.toLocaleString()+' 个项目 · '+data.activity.length+' 条操作记录</p><div class="settings-actions"><button class="btn subtle" data-action="export">↥ 导出完整备份</button><button class="btn subtle" data-action="import">↧ 导入备份</button><button class="btn subtle" data-action="export-previous">导出恢复前快照</button><button class="btn subtle danger" data-action="reset">恢复初始工作区</button></div></div><div class="settings-card"><div class="settings-card-head"><div><span class="eyebrow">MODEL ACCESS</span><h3>调用模型（本次会话）</h3></div><span class="storage-size" id="model-access-state">'+(ma.hasKey?'已配置':'未配置')+'</span></div><div class="model-access-fields"><label><span>接口地址</span><input type="url" data-model-access="endpoint" value="'+esc(ma.endpoint)+'" placeholder="https://…/v1/chat/completions" autocomplete="off" spellcheck="false"></label><label><span>模型</span><input type="text" data-model-access="model" value="'+esc(ma.model)+'" placeholder="例如 glm-4.7" autocomplete="off" spellcheck="false"></label><label><span>密钥</span><input type="password" id="model-access-key" value="" placeholder="'+(ma.hasKey?'本次会话已配置，可留空':'仅本次会话有效')+'" autocomplete="off" spellcheck="false"></label></div><p class="muted">填好后点「保存到本次会话」，碰撞台就会直接用这套配置。密钥只存在内存里，<b>不写入资料库、不进备份、不写磁盘</b>；关闭或刷新本页即失效，换接口地址时旧密钥自动作废。</p><p class="muted">用<b>本机模型</b>（Ollama / LM Studio 等）不需要密钥：接口地址填 <code>http://127.0.0.1:11434/v1/chat/completions</code>（Ollama 默认端口），模型名填已下载的模型名即可。</p><div class="settings-actions"><button class="btn subtle" data-action="save-model-access">保存到本次会话</button><button class="btn subtle danger" data-action="clear-model-access">清除</button></div></div><div class="settings-card"><div class="settings-card-head"><div><span class="eyebrow">BEHAVIOR</span><h3>使用习惯</h3></div></div><div class="setting-line"><div><b>轻量动效</b><small>保留碰撞时的呼吸感，低电量设备可关闭</small></div><button class="switch '+(data.settings.motion?'on':'')+'" data-setting="motion"><i></i></button></div><div class="setting-line"><div><b>轻音效</b><small>仅碰撞与保存时轻声提示，导航与选择始终静音</small></div><button class="switch '+(data.settings.sound?'on':'')+'" data-setting="sound"><i></i></button></div></div></section><aside class="settings-aside"><div class="settings-card"><span class="eyebrow">APPEARANCE</span><h3>挑一种你喜欢的光线</h3><div class="theme-list">'+themes+'</div></div><div class="settings-card about"><span class="eyebrow">ABOUT THIS VERSION</span><h3>本地版 '+S.VERSION+'</h3><p>资料浏览、方向计算与证据审查都可离线使用。桌面版内置本机融合服务，碰撞台可直接调用模型；纯离线 HTML 只保留资料浏览，模型调用需在浏览器中打开并连接本机服务。手机 PWA 安装需要 HTTPS 与对应浏览器支持；Android 和 iOS 原生安装包尚未交付。</p><div class="about-links"><button class="text-btn" data-action="open-readme">阅读本地说明 <span>↗</span></button><button class="text-btn" data-nav="evolution">查看换代重测 <span>→</span></button></div></div></aside></div></div>'}
  function detail(id){const r=find(id);if(!r)return;let ev=Object.entries(r.evidence||{}).slice(0,10).map(x=>'<div><span>'+esc(x[0])+'</span><b>'+esc(typeof x[1]==='string'?x[1]:JSON.stringify(x[1]))+'</b></div>').join('');$('#detail-content').innerHTML='<div class="detail-top"><div><span class="kind-mark '+tone(r)+'">'+kindIcon(r.kind)+'</span><span class="eyebrow">'+(KIND[r.kind]||r.kind)+' · '+status(r)+'</span></div><button class="close-btn" data-close="detail-dialog">×</button></div><h2>'+esc(r.title)+'</h2><p class="detail-summary">'+esc(r.summary||'还没有摘要。')+'</p><div class="detail-meta"><span>归档分 <b>'+(r.score==null?'待评':Number(r.score).toFixed(1))+'</b></span><span>项目 <b>'+esc(r.project||'未归位')+'</b></span><span>更新 <b>'+fmtDate(r.updated,true)+'</b></span></div><div class="detail-body">'+(K.isSkill(r)?'<p class="muted">以下行号对应碰撞结果的来源引用。</p><pre class="skill-source-code">'+esc(r.body.split(/\r?\n/).map((line,i)=>String(i+1).padStart(4,' ')+'  '+line).join('\n'))+'</pre>':S.markdown(r.body||'还没有正文。'))+'</div><div class="detail-evidence"><div class="section-head"><div><span class="eyebrow">TRACEABLE SOURCE</span><h3>来源依据</h3></div><button class="favorite detail-favorite '+(r.favorite?'on':'')+'" data-fav="'+esc(r.id)+'">'+(r.favorite?'★ 已收藏':'☆ 收藏')+'</button></div><div class="evidence-grid">'+ev+'</div></div><div class="detail-actions"><button class="btn primary" data-action="toggle-final" data-id="'+esc(r.id)+'">'+(r.status==='final'?'退回待处理':'标记为已定稿')+' <span>↗</span></button><button class="btn subtle" data-action="edit" data-id="'+esc(r.id)+'">'+(K.isSkill(r)?'创建新版本':'编辑')+'</button><button class="btn subtle danger" data-action="archive" data-id="'+esc(r.id)+'">归档</button></div>'+(r.parents.length?'<div class="lineage"><span class="eyebrow">BORN FROM</span>'+r.parents.map(p=>{const q=find(p);return q?'<button data-open="'+esc(q.id)+'">'+esc(q.title)+'</button>':''}).join('')+'</div>':'');$('#detail-dialog').showModal()}
  function editor(id){const f=$('#editor-form'),r=id&&find(id);f.reset();f.elements.id.value=r?.id||'';f.elements.title.value=r?.title||'';f.elements.project.value=r?.project||'';f.elements.tags.value=(r?.tags||[]).join(', ');f.elements.body.value=r?.body||'';$('#editor-heading').textContent=r?'编辑这束光':'收下一点灵感';$('#editor-dialog').showModal();f.elements.title.focus()}
  async function saveEditor(e){e.preventDefault();const f=e.target,title=f.elements.title.value.trim(),now=new Date().toISOString();if(!title)return;const old=f.elements.id.value,r=old&&find(old),fields={title,project:f.elements.project.value.trim(),tags:f.elements.tags.value.split(',').map(x=>x.trim()).filter(Boolean).slice(0,40),body:f.elements.body.value,summary:f.elements.body.value.split(/\n+/).find(Boolean)?.slice(0,220)||'',updated:now};if(r)Object.assign(r,fields);else{data.records.unshift(S.makeRecord({...fields,created:now,kind:'note',source:'手动记录',evidence:{createdBy:'local-editor'}}));activity('收下一点灵感','note')}
    // 保存失败就留在弹窗里，正文与焦点都不能丢——用户解除配额后可直接重试。
    if(await save())return;
    $('#editor-dialog').close();render();toast(old?'已更新 · 还在本地':'已收好 · 这束光加入了成品库')}
  function activity(title,kind){data.activity.unshift({title,kind,at:new Date().toISOString()});data.activity=data.activity.slice(0,120)}
  function collide(){
    if(collisionJob.snapshot().phase==='running')return;
    const [a,b]=selectedSkills(),result=K.compose(a,b);state.collisionMy=a.id;state.collisionOther=b.id;
    if(collisionJob.start(result,data.settings.motion&&!reduced.matches?820:0))sound('collide');
  }
  function paintCollision({phase,result}){
    state.lastCollision=result;
    if(state.route!=='collision'||!$('.collision-stage'))return;
    const busy=phase==='running'||phase==='saving',stage=$('.collision-stage');
    stage.dataset.phase=phase;stage.classList.toggle('is-colliding',phase==='running');stage.setAttribute('aria-busy',String(busy));
    if(phase==='running'&&window.SparkFX)window.SparkFX.burst(stage,820);
    const trigger=$('[data-action="collide"]'),cancel=$('[data-action="cancel-collision"]');
    const focusWasCancel=document.activeElement===cancel;
    trigger.disabled=busy||!canCollide();
    trigger.querySelector('span').textContent=busy?'正在初检…':phase==='result'?'重新初检':'声明初检（离线）';
    $('[data-action="backend-collision"]').disabled=busy||!canCollide();
    $$('.collision-steps li').forEach(el=>{if(el.dataset.step===(busy?'running':result?'result':'ready'))el.setAttribute('aria-current','step');else el.removeAttribute('aria-current')});
    cancel.hidden=!busy;
    if(busy)cancel.focus({preventScroll:true});else if(focusWasCancel)trigger.focus({preventScroll:true});
    $('#collision-status').textContent=({running:'正在连接两个方向…',result:'新方向已就绪',saving:'正在保存…',saved:'已收好',ready:'准备好，碰一碰。'})[phase];
    $('#collision-result').innerHTML=result?collisionResult(result):'<div class="result-placeholder">'+icon('spark')+'<p>'+(busy?'让两个方向靠近一点。':'两个方向，一个新的起点。')+'</p></div>';
    const saveButton=$('[data-action="save-collision"]');if(saveButton)saveButton.disabled=phase!=='result';
  }
  function saveCollision(){
    const saved=collisionJob.save(c=>{
      for(const source of c.sources){const original=find(source.id);if(!original||K.fingerprint(original.body)!==source.sha256)throw Error('来源 Skill 已变化，请重新碰撞。')}
      const now=new Date().toISOString();
      data.records.unshift(S.makeRecord({title:c.title,body:c.body,summary:c.copy,kind:'collision',project:'本地技能库',source:'双 Skill · 离线声明规则',created:now,updated:now,status:'inbox',tags:['组合草稿','未执行','待验证'],parents:c.parents,score:null,evidence:{method:c.method,sources:c.sources,createdAt:now,executed:false}}));
      activity('保存了双 Skill 组合草稿','collision');save();
    });
    if(saved){navigate('library');sound('save');toast('组合草稿已保存 · 双方来源一起保留')}
  }
  let skillSide='A',skillReadRevision=0,readingSkill=false,skillFileName='粘贴的 SKILL.md',reviewedSkill=null;
  function openSkillImport(side){
    stopSounds();collisionJob.cancel();skillSide=side==='B'?'B':'A';skillReadRevision++;readingSkill=false;reviewedSkill=null;skillFileName='粘贴的 SKILL.md';
    $('#skill-form').reset();$('#skill-import-heading').textContent='导入 Skill '+skillSide;$('#skill-preview').innerHTML='';$('#skill-error').textContent='';$('#skill-save').disabled=true;$('#skill-save').textContent='导入并选为 '+skillSide;$('#skill-dialog').showModal();$('#skill-text').focus();
  }
  function skillInput(){const f=$('#skill-form');return K.augment($('#skill-text').value,{inputs:f.elements.inputs.value,outputs:f.elements.outputs.value,constraints:f.elements.constraints.value})}
  function previewSkill(){
    if(readingSkill)throw Error('文件正在读取，请稍候');
    const body=skillInput(),contract=K.parse(body);reviewedSkill={body,contract};
    $('#skill-preview').innerHTML='<h3>'+esc(contract.name)+'</h3><p>'+esc(contract.description||'未声明用途')+'</p>'+Object.entries(K.LABELS).map(([key,label])=>'<h4>'+label+'</h4>'+contractList(contract.fields[key])).join('')+'<ul class="skill-warnings">'+contract.warnings.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul><p class="muted">只识别名称、简介及常见中英文标题下的声明；复杂 YAML 与代码块保留原文，未作完整解析。</p>';
    $('#skill-error').textContent='';$('#skill-save').disabled=false;
  }
  async function saveSkill(e){
    e.preventDefault();if(readingSkill||!reviewedSkill)throw Error('请先解析并检查预览');
    const body=skillInput();if(body!==reviewedSkill.body)throw Error('内容已变化，请重新预览');
    const fingerprint=K.fingerprint(body),existing=skillChoices().find(r=>r.body===body);
    let record=existing;
    if(!record){record=S.makeRecord({title:reviewedSkill.contract.name,body,summary:reviewedSkill.contract.description,kind:'document',project:'本地技能库',source:skillFileName,tags:['Skill','本地导入'],evidence:{type:'skill-source',schema:1,sha256:fingerprint}});data.records.unshift(record);activity('导入 Skill：'+record.title,'document');
      // 保存失败就不关弹窗、不选中、不跳转：原因写在弹窗里，草稿留在原地可重试。
      const failure=await save();
      if(failure){$('#skill-error').textContent=failure.message;return}}
    if(skillSide==='A')state.collisionMy=record.id;else state.collisionOther=record.id;
    $('#skill-dialog').close();navigate('collision');$(skillSide==='A'?'#collision-my':'#collision-other')?.focus();toast(existing?'相同内容已存在，已选中原有 Skill':'Skill 已导入 · 原文与指纹已保存');
  }
  function readSkillFile(file){
    const ticket=++skillReadRevision;readingSkill=false;reviewedSkill=null;$('#skill-save').disabled=true;$('#skill-preview').innerHTML='';
    if(!file)return;
    if(!/\.md$/i.test(file.name)){throw Error('请选择 UTF-8 的 SKILL.md 或 Markdown 文件')}
    if(file.size>K.MAX_BYTES)throw Error('单份 Skill 超过 256 KB，请缩小后导入');
    readingSkill=true;$('#skill-error').textContent='正在读取文件…';const reader=new FileReader();
    reader.onload=()=>{if(ticket!==skillReadRevision||!$('#skill-dialog').open)return;readingSkill=false;skillFileName=file.name;$('#skill-text').value=String(reader.result);$('#skill-error').textContent='文件已读取，请检查内容后解析预览。'};
    reader.onerror=reader.onabort=()=>{if(ticket!==skillReadRevision)return;readingSkill=false;$('#skill-error').textContent='文件读取失败，请重选；现有资料未改变。'};
    try{reader.readAsText(file,'UTF-8')}catch{reader.onerror()}
  }
  function downloadText(text,name,type='application/json'){
    const blob=new Blob([text],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function exportBackup(){
    if(workspace.status().recovery)throw Error('当前资料需要恢复，请先导出原始数据。');
    downloadText(JSON.stringify(S.exportPayload(data),null,2),'闪光点-本地备份-'+new Date().toISOString().slice(0,10)+'.json');
    toast('备份已生成，请保留下载文件');
  }
  function exportRaw(previous=false){
    // 恢复前快照的读取是异步的（桌面壳走 SQLite）；同一路径下未保存过则为 null。
    if(!previous){const raw=workspace.status().raw;emitRaw(raw,previous);return}
    workspace.previous().then(value=>{try{emitRaw(value.raw,previous)}catch(error){toast(error.message||'导出失败')}},error=>toast(error.message||'导出失败，请重试'));
  }
  function emitRaw(raw,previous){
    if(raw===undefined)throw Error('原始存储暂时无法读取，请先重试读取。');
    if(raw===null)throw Error('当时尚无本地存储，无需恢复原始文件。');
    let text=raw,extension='txt';
    if(previous){try{text=JSON.stringify(S.exportPayload(S.validateState(JSON.parse(raw))),null,2);extension='json'}catch{}}
    downloadText(text,previous?'闪光点-恢复前快照.'+extension:'闪光点-待恢复原始数据.txt',extension==='json'?'application/json':'text/plain');
    toast('原始快照已生成，请保留下载文件');
  }
  function ask(title,copy,yes){$('#confirm-title').textContent=title;$('#confirm-copy').textContent=copy;state.pendingConfirm=yes;$('#confirm-dialog').showModal()}
  // 模型接入只存在于内存：保存后立刻把密码框清空，密钥不留在 DOM 里。
  function saveModelAccess(){
    const endpoint=$('[data-model-access="endpoint"]').value.trim(),model=$('[data-model-access="model"]').value.trim();
    if(!endpoint)throw Error('请先填写接口地址');
    const key=$('#model-access-key').value;
    const before=S.modelAccess.read();
    S.modelAccess.set({endpoint,model,key});
    const after=S.modelAccess.read();
    $('#model-access-key').value='';
    render();
    if(before.endpoint&&before.endpoint!==endpoint&&!key)toast('接口地址已更换，旧密钥已作废 · 请重新填写');
    else toast(after.hasKey?'本次会话已配置 · 碰撞台可直接调用':'本次会话已保存端点与模型');
  }
  function clearModelAccess(){S.modelAccess.clear();render();toast('已清除本次会话的模型接入配置')}
  function reset(){
    const expected=workspace.status().raw;
    ask('恢复初始工作区？','当前资料将被替换。确认时会先保留一份原始快照；快照写入失败则不替换。建议先导出完整备份。',()=>{
      workspace.replace(S.initial(window.SPARK_SEED||{records:[]}),expected).then(snapshot=>{
        data=snapshot.state;
        finishReplacement('初始工作区已恢复；原始数据保留在恢复前快照中');
      }).catch(error=>{toast('恢复未完成，当前资料未替换：'+error.message);render()});
    });
  }
  function finishReplacement(message){
    try{collisionJob.cancel();stopSounds();render();toast(message)}
    catch{toast('资料已恢复，但界面刷新失败，请重新打开页面。')}
  }
  function recovery(){
    const snapshot=workspace.status();
    return '<section class="recovery-panel" aria-labelledby="recovery-heading"><span class="eyebrow">YOUR DATA COMES FIRST</span><h1 id="recovery-heading">先把你的资料保护好</h1><p>本地空间暂时无法读取，已暂停写入。原始存储没有被初始资料覆盖。</p><details class="recovery-detail"><summary>查看读取详情</summary><p class="recovery-reason">'+esc(snapshot.error)+'</p></details><ol><li>先导出原始数据，保留一份恢复线索。</li><li>选择之前的完整备份，检查内容后再确认恢复。</li></ol><div class="recovery-actions"><button class="btn primary" data-action="export-raw" '+(!snapshot.readable?'disabled':'')+'>导出原始数据</button><button class="btn subtle" data-action="import">选择备份恢复</button><button class="btn subtle" data-action="retry-load">重试读取</button><button class="btn subtle" data-action="export-previous">导出恢复前快照</button></div><button class="text-btn" data-action="reset">保留原始快照并恢复初始工作区</button></section>';
  }
  function retryLoad(){
    workspace.reload().then(snapshot=>{
      data=snapshot.state;pendingExternal=false;collisionJob.cancel();stopSounds();render();
      toast(workspace.status().recovery?'仍需恢复，原始资料已保留':'已重新读取本地空间');
    }).catch(error=>toast(error.message||'重新读取失败，请重试'));
  }
  function showRestorePreview(candidate,fileName){
    pendingRestore={state:candidate,baseline:workspace.status().raw};
    $('#restore-name').textContent=fileName;
    $('#restore-summary').textContent='备份格式 v'+candidate.version+' · '+candidate.records.length+' 件资料 · '+candidate.activity.length+' 条操作记录';
    $('#restore-impact').textContent=workspace.status().recovery?'确认后将替换当前待恢复的数据；先保存原始快照。':'确认后替换当前 '+data.records.length+' 件资料及偏好，不做合并；先保存原始快照。';
    $('#restore-error').textContent='';$('#restore-confirm').checked=false;$('#restore-apply').disabled=true;
    $('#toast').classList.remove('show');$('#restore-dialog').showModal();
  }
  function readRestoreFile(file){
    const revision=++restoreRevision;pendingRestore=null;readingBackup=false;
    if(!file)return;
    if(file.size>40000000){toast('备份超过 40 MB，尚未读取或替换资料');return}
    readingBackup=true;toast('正在检查备份，不会立即替换资料');
    const reader=new FileReader();
    const done=()=>{if(revision===restoreRevision){readingBackup=false;$('#restore-file').value=''}};
    reader.onload=()=>{if(revision!==restoreRevision)return;done();guarded(()=>showRestorePreview(S.readBackup(String(reader.result)),file.name))};
    reader.onerror=reader.onabort=()=>{if(revision!==restoreRevision)return;done();toast('文件读取失败，请重新选择；当前资料未更改')};
    try{reader.readAsText(file)}catch{reader.onerror()}
  }
  function toast(msg){const el=$('#toast');el.textContent=msg;el.classList.add('show');clearTimeout(window.__toast);window.__toast=setTimeout(()=>el.classList.remove('show'),2600)}
  function renderNav(){
    $('#desktop-nav').innerHTML=NAV.map(n=>'<button class="nav-item '+(state.route===n[0]?'active':'')+'" data-nav="'+n[0]+'" '+(state.route===n[0]?'aria-current="page"':'')+'><span class="nav-icon">'+icon(n[0])+'</span><span>'+n[1]+'</span><small>'+n[3]+'</small></button>').join('');
    $('#mobile-nav').innerHTML=NAV.map(n=>'<button class="mobile-item '+(state.route===n[0]?'active':'')+'" data-nav="'+n[0]+'" '+(state.route===n[0]?'aria-current="page"':'')+'>'+icon(n[0])+'<small>'+n[1]+'</small></button>').join('');
    $('#project-links').innerHTML=[...new Set(data.records.map(r=>r.project).filter(Boolean))].slice(0,5).map(p=>'<button class="project-link" data-project="'+esc(p)+'"><span></span>'+esc(p)+'</button>').join('');
  }
  function refreshControl(selector){render();$(selector)?.focus({preventScroll:true})}
  function render(){
    $('#view').innerHTML=workspace.status().recovery?recovery():state.route==='home'?home():state.route==='library'?library():state.route==='collision'?collision():state.route==='evolution'?evolution():settings();
    if(state.route==='collision'&&!workspace.status().recovery){window.SparkFusion?.mount(saveFusion);$('#collision-backend').ontoggle=event=>{state.fusionOpen=event.currentTarget.open;if(state.fusionOpen)window.SparkFusion?.activate()}}
    $('#view-name').textContent=(NAV.find(n=>n[0]===state.route)||NAV[0])[1];renderNav();$$('[data-nav]').forEach(el=>{if(el.dataset.nav===state.route)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current')});$$('[data-filter]').forEach(el=>el.setAttribute('aria-pressed',String(el.classList.contains('active'))));paintPreferences();$('#save-status').textContent=workspace.status().recovery?'恢复保护 · 写入已暂停':'本地空间 · 已就绪';
  }
  function paintPreferences(){
    document.body.dataset.theme=data.settings.theme;document.body.dataset.motion=data.settings.motion?'on':'off';document.body.dataset.sound=data.settings.sound?'on':'off';
    const toggle=$('.sound-toggle');toggle.disabled=workspace.status().recovery;toggle.classList.toggle('on',data.settings.sound);toggle.setAttribute('aria-pressed',String(data.settings.sound));toggle.innerHTML=icon('sound')+'<span>音效'+(data.settings.sound?'开':'关')+'</span>';
    $$('[data-setting]').forEach(el=>{const on=data.settings[el.dataset.setting];el.classList.toggle('on',on);el.setAttribute('aria-checked',String(on));el.setAttribute('role','switch');el.setAttribute('aria-label',el.dataset.setting==='sound'?'轻音效':'轻量动效')});
    $$('button[data-theme]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.theme===data.settings.theme)));
  }
  function bind(){
    // Delegation is installed once; rendered cards and dialogs inherit the same handler.
    document.addEventListener('click',e=>guarded(()=>{
      const el=e.target.closest('[data-nav],[data-fav],[data-action],[data-open],[data-filter],[data-project],[data-clear-project],[data-choice],button[data-setting],button[data-theme],[data-close]');
      if(!el||el.disabled)return;const d=el.dataset;
      if(d.nav){e.preventDefault();navigate(d.nav);return}
      if(d.close){document.getElementById(d.close).close();return}
      if(d.fav){const r=find(d.fav);if(!r)return;r.favorite=!r.favorite;activity((r.favorite?'收藏':'取消收藏')+'：'+r.title,r.kind);save();if($('#detail-dialog').open)detail(r.id);else{el.classList.toggle('on',r.favorite);el.textContent=r.favorite?'★':'☆';el.setAttribute('aria-pressed',String(r.favorite))}toast(r.favorite?'已收藏':'已取消收藏');return}
      if(d.action==='clear-filters'){state.filters={query:'',status:'all',kind:'',project:'',sort:'updated'};limit=48;refreshControl('#library-search');return}
      if(d.action){act(d.action,d.id);return}
      if(d.open){detail(d.open);return}
      if(d.filter){const same=d.filter==='kind'&&state.filters.kind===d.value;state.filters[d.filter]=same?'':d.value;limit=48;refreshControl('[data-filter="'+d.filter+'"][data-value="'+d.value+'"]');return}
      if(d.project!==undefined){navigate('library',{project:d.project});return}
      if(d.clearProject!==undefined){state.filters.project='';render();return}
      if(d.choice){stopSounds();collisionJob.cancel();state.collisionMy=d.choice;render();$$('[data-choice]').find(x=>x.dataset.choice===d.choice)?.focus({preventScroll:true});return}
      if(d.setting){data.settings[d.setting]=!data.settings[d.setting];if(d.setting==='sound')stopSounds();save();paintPreferences();return}
      if(d.theme){data.settings.theme=d.theme;save();refreshControl('button[data-theme="'+d.theme+'"]')}
    }));
    document.addEventListener('input',e=>{if(e.target.id==='library-search'&&!e.isComposing){state.filters.query=e.target.value;limit=48;clearTimeout(searchTimer);searchTimer=setTimeout(updateLibrary,120)}});
    document.addEventListener('compositionend',e=>{if(e.target.id==='library-search'){state.filters.query=e.target.value;limit=48;clearTimeout(searchTimer);updateLibrary()}});
    document.addEventListener('change',e=>guarded(()=>{
      if(e.target.id==='sort-select'){state.filters.sort=e.target.value;limit=48;updateLibrary()}
      if(e.target.id==='collision-other'||e.target.id==='collision-my'){stopSounds();collisionJob.cancel();state[e.target.id==='collision-my'?'collisionMy':'collisionOther']=e.target.value;refreshControl('#'+e.target.id)}
    }));
  }
  function act(name,id){
    if(name==='toggle-sound'){data.settings.sound=!data.settings.sound;stopSounds();save();paintPreferences();toast(data.settings.sound?'轻音效已开启 · 导航始终静音':'音效已关闭');return}
    if(name==='search'){navigate('library');$('#library-search').focus();return}
    if(name==='load-more'){limit+=48;updateLibrary();return}
    if(name==='cancel-collision'){stopSounds();collisionJob.cancel();return}
    if(name==='new')editor();
    if(name==='edit'){if(K.isSkill(find(id))){const source=find(id);$('#detail-dialog').close();openSkillImport('A');$('#skill-text').value=source.body;$('#skill-error').textContent='编辑后会另存为新版本，原来源快照保持不变。';return}$('#detail-dialog').close();editor(id)}
    if(name==='toggle-final'||name==='archive'){const r=find(id);if(r){r.status=name==='archive'?'archived':r.status==='final'?'inbox':'final';activity('更新资料：'+r.title,r.kind);save();$('#detail-dialog').close();render();toast('资料状态已保存')}}
    if(name==='import-skill')openSkillImport(id);
    if(name==='backend-collision')openBackendCollision();
    if(name==='package-collision')openPackageCollision();
    if(name==='swap-skills'){const [a,b]=selectedSkills();stopSounds();collisionJob.cancel();state.collisionMy=b?.id;state.collisionOther=a?.id;refreshControl('[data-action="swap-skills"]')}
    if(name==='download-collision'&&state.lastCollision)downloadText(state.lastCollision.body,'SKILL-draft.md','text/markdown;charset=utf-8');
    if(name==='collide')collide();if(name==='save-collision')saveCollision();
    if(name==='copy-collision'&&state.lastCollision){const c=state.lastCollision;if(!navigator.clipboard){toast('当前环境不支持复制，请从详情导出。');return}navigator.clipboard.writeText(c.body).then(()=>toast('摘要已复制'),()=>toast('复制未完成，请检查浏览器权限'))}
    if(name==='retest')toast('当前为报告示例，尚未接入本机模型重测。');
    if(name==='export-raw')exportRaw();if(name==='export-previous')exportRaw(true);if(name==='retry-load')retryLoad();if(name==='export')exportBackup();if(name==='import')$('#restore-file').click();if(name==='reset')reset();if(name==='save-model-access')saveModelAccess();if(name==='clear-model-access')clearModelAccess();
    if(name==='open-readme')toast('离线使用说明见安装包内 README.md');
  }
  $('#skill-form').addEventListener('submit',e=>{e.preventDefault();const p=saveSkill(e);if(p&&p.catch)p.catch(error=>{$('#skill-error').textContent=error.message})});
  $('#skill-preview-button').addEventListener('click',()=>{try{previewSkill()}catch(error){reviewedSkill=null;$('#skill-save').disabled=true;$('#skill-error').textContent=error.message}});
  $('#skill-form').addEventListener('input',()=>{reviewedSkill=null;$('#skill-save').disabled=true;$('#skill-preview').innerHTML='';if(readingSkill){skillReadRevision++;readingSkill=false;$('#skill-error').textContent='已使用手动输入，迟到的文件读取将忽略。'}});
  $('#skill-file').addEventListener('change',e=>{const file=e.target.files[0];e.target.value='';try{readSkillFile(file)}catch(error){$('#skill-error').textContent=error.message}});
  $('#skill-dialog').addEventListener('close',()=>{skillReadRevision++;readingSkill=false;reviewedSkill=null});
  $('#editor-form').addEventListener('submit',e=>{const p=saveEditor(e);if(p&&p.catch)p.catch(error=>{paintPreferences();toast(error.message||'操作未完成，请重试')})});
  $('#confirm-cancel').addEventListener('click',()=>$('#confirm-dialog').close());
  $('#confirm-yes').addEventListener('click',()=>guarded(()=>{const f=state.pendingConfirm;state.pendingConfirm=null;$('#confirm-dialog').close();f?.()}));
  $('#restore-file').addEventListener('change',e=>{const file=e.target.files[0];e.target.value='';readRestoreFile(file)});
  $('#restore-confirm').addEventListener('change',()=>{$('#restore-apply').disabled=!$('#restore-confirm').checked});
  $('#restore-apply').addEventListener('click',()=>{
    if(!pendingRestore||!$('#restore-confirm').checked)return;
    $('#restore-apply').disabled=true;
    workspace.replace(pendingRestore.state,pendingRestore.baseline).then(snapshot=>{
      data=snapshot.state;
      pendingRestore=null;pendingExternal=false;$('#restore-dialog').close();finishReplacement('恢复完成 · 替换前快照已保留');
    }).catch(error=>{
      $('#restore-error').textContent='恢复未完成，当前资料未替换：'+error.message;$('#restore-apply').disabled=false;
    });
  });
  $('#restore-dialog').addEventListener('close',()=>{pendingRestore=null;restoreRevision++;readingBackup=false});
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&!$('dialog[open]')){e.preventDefault();act('search')}
    if(e.key==='Escape'){stopSounds();if(collisionJob.snapshot().phase==='running')collisionJob.cancel();$$('dialog[open]').forEach(x=>x.close())}
  });
  window.addEventListener('hashchange',()=>{const route=location.hash.slice(1).split('?')[0];if(route==='fusion'||NAV.some(n=>n[0]===route))navigate(route)});
  window.addEventListener('storage',e=>{
    if(e.key!=='sparkroom-state-v1'&&e.key!==null)return;
    if($('dialog[open]')){pendingExternal=true;toast('另一窗口更新了资料，当前草稿保留；关闭编辑或预览后同步。');return}
    retryLoad();
  });
  $$('dialog').forEach(dialog=>dialog.addEventListener('close',()=>{if(pendingExternal&&!$('dialog[open]'))retryLoad()}));
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stopSounds();if(collisionJob.snapshot().phase==='running')collisionJob.cancel()}});
  window.addEventListener('pagehide',()=>{stopSounds();collisionJob.cancel()});
  reduced.addEventListener?.('change',()=>{if(reduced.matches&&collisionJob.snapshot().phase==='running')collisionJob.cancel()});
  if(!NAV.some(n=>n[0]===state.route))state.route='home';
  bind();render();
  // 桌面壳首次启动：把 WebView 里既有的 localStorage 状态搬进 SQLite，搬完才读正式数据。
  // 保留 localStorage 原件不删——它是用户数据，迁移出问题时的退路。
  async function boot(){
    if(desktopStore){
      try{
        if(await desktopStore.getItem(window.SparkStorage.KEY)===null){
          const legacy=localStorage.getItem(window.SparkStorage.KEY);
          if(legacy!==null)await desktopStore.setItem(window.SparkStorage.KEY,legacy);
        }
      }catch(error){toast('本机资料库读取失败：'+error.message)}
    }
    const snapshot=await workspace.reload();
    data=snapshot.state;render();
  }
  boot();
  if('serviceWorker' in navigator&&location.protocol!=='file:'&&!window.SPARK_STANDALONE){
    let registration,reloadPending=false,requestedUpdate=false;
    const banner=$('#update-banner'),button=$('#apply-update');
    function offer(){if(reloadPending||(registration?.waiting&&navigator.serviceWorker.controller)){banner.hidden=false;$('#update-message').textContent=reloadPending?'新版本已启用。请先保存编辑，再重新打开。':'新版本已就绪。你的本地资料会保留。'}}
    button.addEventListener('click',()=>{
      if($('dialog[open]')||readingBackup||readingSkill||collisionJob.snapshot().phase==='running'){toast('请先完成或关闭编辑、备份预览与碰撞，再更新。');return}
      if(reloadPending)location.reload();else if(registration?.waiting){requestedUpdate=true;registration.waiting.postMessage({type:'ACTIVATE'})}
    });
    navigator.serviceWorker.register('./sw.js').then(reg=>{registration=reg;offer();reg.addEventListener('updatefound',()=>reg.installing?.addEventListener('statechange',offer))}).catch(()=>{toast('离线缓存未更新，仍可继续使用当前页面。')});
    let hadController=Boolean(navigator.serviceWorker.controller);
    navigator.serviceWorker.addEventListener('controllerchange',()=>{if(!hadController){hadController=true;return}if(hadController){reloadPending=true;if(requestedUpdate&&!$('dialog[open]')&&!readingBackup&&!readingSkill&&collisionJob.snapshot().phase!=='running')location.reload();else offer()}});
  }
})();
