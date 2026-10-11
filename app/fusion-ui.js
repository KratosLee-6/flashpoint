/* Model fusion UI: explicit request review, no persisted credentials, no package execution. */
(function(){
  'use strict';
  const esc=v=>window.Spark.escape(String(v??''));
  const state={token:'',connected:false,explorationSupported:false,uploadsSupported:false,reviewsSupported:false,uploads:{A:null,B:null},busy:false,pending:false,mode:'expert',packSources:[],message:'连接本机服务后开始。',error:'',goalError:'',prepared:null,inventory:null,selection:null,importedReview:null,source:null,probe:null,contextDirty:false,request:null,result:null,job:null,saved:false,materializeReport:null,materializeBusy:null,documents:null,
    form:{a:'',b:'',resourcesA:'',resourcesB:'',gapsA:'',gapsB:'',goal:'',budget:'160000',provider:'',endpoint:'',model:'',thinking:''}};
  let saveResult=null,connecting=false,transientKey='',busyFocusAction=null,recoveryRead=false,recoveryWarning='',pollTimer=null;
  // 融合服务地址基址：浏览器（启动脚本）走同源空串；桌面壳走 sidecar 端口，端口由 Rust 单一源提供。
  // null = 尚未解析，任何 /api/ 调用在解析前都不应发出。
  let apiBase=null;
  const isLoopbackBrowser=()=>location.protocol==='http:'&&location.hostname==='127.0.0.1';
  // 有 Tauri 桥即视为桌面壳：`window.__TAURI__` 只由 withGlobalTauri 注入。
  // 不能再要求 origin 是 tauri://——`tauri dev` 下页面跑在开发服务器上（http://127.0.0.1:1430），
  // 按 origin 判会把桌面壳误认成普通浏览器，融合服务直接连不上。
  const isDesktopShell=()=>Boolean(window.__TAURI__&&window.__TAURI__.core&&typeof window.__TAURI__.core.invoke==='function');
  async function resolveApiBase(){
    // 有 Tauri 桥就优先走 sidecar——`tauri dev` 下页面 origin 是开发服务器
    // （http://127.0.0.1:1430），若先判「回环浏览器」就会同源请求打到 dev server 上，
    // 融合服务完全连不上。桥的存在与否比 origin 形态更能说明「这是桌面壳」。
    if(isDesktopShell()){
      let base='';
      try{base=await window.__TAURI__.core.invoke('fusion_base_url')}catch{}
      if(typeof base!=='string'||!/^http:\/\/127\.0\.0\.1:\d{2,5}$/.test(base))throw Error('桌面壳未返回本机融合服务地址，请更新桌面版后重试。');
      return base;
    }
    if(isLoopbackBrowser())return '';
    throw Error('请使用“启动闪光点.cmd”或桌面版打开 App，在碰撞台连接后端融合服务。单文件离线版仍可浏览资料和做声明初检。');
  }
  const recoveryKey='spark-fusion-job-v1';
  const materialRoles={entry:'入口',implementation:'实现 / 提示词',knowledge:'知识 / 说明',template:'模板 / 素材',third_party:'第三方依赖候选',dependency:'依赖 / 许可',example_test:'样例 / 测试',other:'待分类'};
  const deliverables={items:null,invalid:0,busy:false,error:'',message:'尚未读取交付物；仅点击刷新后查询本机私有交付目录。'};
  const reviewLabels={A_only:'A 单独',B_only:'B 单独',mechanical_concatenation:'机械拼接',model_only:'仅模型',fusion:'融合',fusion_without_A:'融合移除 A',fusion_without_B:'融合移除 B'};
  // 能力结构轴的中文名（与服务端 capability.POLICY 一一对应，界面上不出现内部键名）
  const POLICY_AXIS={steps:'步骤',conditions:'条件',constraints:'约束',checks:'校验',deliverables:'交付物',evidence_refs:'依据',boundaries:'边界',audience:'受众'};
  const BRIDGE_STATUS={no_structural_overlap:'未达落点门槛',cross_domain_surface_found:'有跨域落点'};
  // 「没落点」有两种性质完全不同的机制，用一句话概括就是在骗人。实测 36 对真实包里的
  // 3 个负面案例全部是「差一个轴」，此时说「没有结构交集」会让用户以为材料不兼容。
  const NO_SURFACE_HINT={no_shared_axes:'两个包在结构轴上没有任何共同项——这不是材料不兼容，是它们确实写法不同。本层按构造给不出方向，需要模型探索或换一对材料。',below_threshold:'两边已经有共同结构轴，只是没到门槛。补上材料里的做法描述（步骤、约束、校验等）比换材料更可能见效。'};
  const reviewForm=()=>({decision:'needs_more_evidence',reason:'',quote_left:'',quote_right:'',confirmed:false});
  const reviews={items:null,detail:null,left:'',right:'',form:reviewForm(),receipt:null,saved:false,busy:false,error:'',message:'尚未读取原始实验。只在明确点击后核对本机冻结证据，不调用模型。'};
  let deliverableFocus=null;
  function downloadBlob(blob,name){
    const url=URL.createObjectURL(blob);
    const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function downloadJson(value,name){
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function rememberJob(id){
    try{if(id)sessionStorage.setItem(recoveryKey,id);else sessionStorage.removeItem(recoveryKey);recoveryWarning='';}
    catch{recoveryWarning='浏览器不允许保存任务编号：本页仍可使用，但刷新后可能无法找回任务。请等待结果并保存或导出。';}
  }
  function restoreJob(){
    if(recoveryRead)return;recoveryRead=true;
    try{
      const id=sessionStorage.getItem(recoveryKey);
      if(id&&/^[0-9a-f]{32}$/.test(id)){state.job=id;state.pending=true;state.message='正在找回刷新前的融合任务；只查询状态，不会重新发送模型请求。';}
      else if(id){rememberJob(null);recoveryWarning='保存的任务编号无效，未恢复或发送任何模型请求。';}
    }catch{recoveryWarning='浏览器不允许读取任务编号，无法自动恢复刷新前的任务。';}
  }
  function forgetJob(){clearTimeout(pollTimer);state.job=null;rememberJob(null);}
  function schedulePoll(delay){clearTimeout(pollTimer);if(state.job&&state.pending)pollTimer=setTimeout(poll,delay);}
  const root=()=>document.querySelector('#fusion-workspace');
  const lines=v=>v.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  function mounts(value){return lines(value).map(line=>{const i=line.indexOf('=');if(i<1)throw Error('关联资源请按“包内路径 = 本机文件或目录路径”填写，每行一项');return {mount:line.slice(0,i).trim(),path:line.slice(i+1).trim()}})}
  async function api(action,body){
    if(apiBase===null)throw Error('本机融合服务尚未就绪，请先连接本机服务。');
    const response=await fetch(apiBase+'/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Spark-Token':state.token},body:JSON.stringify(body),cache:'no-store'});
    let data;try{data=await response.json()}catch{throw Error('本机融合服务不可用，请通过启动脚本或桌面版打开 App')}
    if(!response.ok)throw Object.assign(Error(data.error?.startsWith('Context budget exceeded')?'完整请求超出字符预算，未截断也未发送。请先审查各文件正文及取舍理由，不要用扩大预算代替审查。':(data.error||'请求未完成')),{status:response.status});return data;
  }
  async function connect(){
    if(connecting)return;connecting=true;
    try{
      apiBase=await resolveApiBase();
      // 设置页已保存的端点/模型直接带进来，省去每次重打；密钥仍在内存，只在发送时取用。
      const access=window.Spark.modelAccess.read();
      if(access.endpoint&&!state.form.endpoint)state.form.endpoint=access.endpoint;
      if(access.model&&!state.form.model)state.form.model=access.model;
      restoreJob();
      if(state.job||recoveryWarning){const section=root()?.closest('#collision-backend');if(section)section.open=true}
      paint();
      const response=await fetch(apiBase+'/api/session',{cache:'no-store'});
      if(!response.ok)throw Error('请使用更新后的“启动闪光点.cmd”或桌面版启动本机融合服务。');
      const s=await response.json();
      if(!response.ok||s.service!=='spark-fusion')throw Error('当前页面未连接融合服务，请使用更新后的启动脚本。');
      if(state.token&&state.token!==s.token){state.prepared=null;state.inventory=null;state.selection=null;state.source=null;state.probe=null;state.request=null;state.result=null;transientKey='';state.pending=Boolean(state.job);}
      if(state.token!==s.token){deliverables.items=null;deliverables.invalid=0;deliverables.error='';deliverables.message='本机会话已连接，请手动刷新可下载 Skill。';}
      if(state.token&&state.token!==s.token){reviews.receipt=null;reviews.saved=false;reviews.message='本机会话已更换；当前意见须重新核对指纹与引文，或明确放弃后刷新证据。';}
      state.token=s.token;state.connected=true;state.error='';state.message='本机服务已连接 · 读取材料和方向计算无需联网';
      state.explorationSupported=s.capabilities?.exploration_without_goal===true;
      state.uploadsSupported=s.capabilities?.package_uploads===true;
      state.reviewsSupported=s.capabilities?.evidence_reviews===true;
      if(state.job&&state.pending){state.message='正在找回原融合任务；只查询状态，不会重新发送模型请求。';schedulePoll(100);}
      // 包源清单（curated + SkillHub 下载区）：只取名称与路径，供 A/B 输入的下拉选择。
      api('sources',{}).then(d=>{state.packSources=d.packs||[];if(!state.pending)paint()}).catch(()=>{});
    }catch(e){state.connected=false;state.error=e.message||'连接失败，请启动本机服务。'}finally{connecting=false;paint()}
  }
  function intentSummary(mode){
    return {explore:'自由探索（默认）：不用先知道结果，根据两个 Skill 发现候选方向。',focused:'按偏好探索：保留你的偏好或限制，仍由材料推导候选方向。'}[mode]||'此记录未标注探索模式，请按原始证据审查。';
  }
  function goalError(value){
    if(typeof value!=='string')return '偏好或限制须为文本；没有偏好可以留空，直接探索。';
    const length=Array.from(value).length;
    return length>6000?`偏好或限制不能超过 6000 个字符（当前 ${length} 个）。可以留空直接探索，Skill 正文请放在材料中。`:'';
  }
  function updateGoalFeedback(){
    const host=root();if(state.goalError)state.goalError=goalError(state.form.goal);
    host.querySelector('#fusion-goal').setAttribute('aria-invalid',String(Boolean(state.goalError)));
    const error=host.querySelector('#fusion-goal-error');error.textContent=state.goalError;error.hidden=!state.goalError;
    host.querySelector('#fusion-goal-count').textContent=`当前 ${Array.from(state.form.goal).length} / 6000 字符`;
    host.querySelector('#fusion-intent-mode').textContent=intentSummary(state.form.goal.trim()?'focused':'explore');
  }
  function field(name,label,placeholder='',multiline=false){
    const isGoal=name==='goal';
    const attrs=`id="fusion-${name}" data-fusion-field="${name}" ${state.busy||state.pending||(['a','b'].includes(name)&&state.uploads[name.toUpperCase()])?'disabled':''}${isGoal?` aria-describedby="fusion-goal-hint fusion-goal-error" aria-invalid="${Boolean(state.goalError)}"`:''}`;
    const mode=isGoal?`<p id="fusion-intent-mode" class="fusion-note">${esc(intentSummary(state.form.goal.trim()?'focused':'explore'))}</p>`:'';
    const feedback=isGoal?`<p id="fusion-goal-hint" class="fusion-note">可留空，直接探索；已有想法时再填写偏好的场景、产物形式或限制。最多 6000 个字符，无需粘贴完整 Skill 内容。<br><span id="fusion-goal-count">当前 ${Array.from(state.form.goal).length} / 6000 字符</span></p><p id="fusion-goal-error" class="fusion-field-error" role="alert" ${state.goalError?'':'hidden'}>${esc(state.goalError)}</p>`:'';
    return `<div class="fusion-field">${mode}<label for="fusion-${name}">${label}</label>${multiline?`<textarea ${attrs} rows="3" placeholder="${esc(placeholder)}">${esc(state.form[name])}</textarea>`:`<input ${attrs} value="${esc(state.form[name])}" placeholder="${esc(placeholder)}" autocomplete="off">`}${feedback}</div>`;
  }
  function details(title,value){return `<details><summary>${esc(title)}</summary><pre>${esc(typeof value==='string'?value:JSON.stringify(value,null,2))}</pre></details>`}
  function packagePicker(parent){
    const upload=state.uploads[parent],disabled=state.busy||state.pending?'disabled':'';
    const summary=upload?`${upload.name} · ${upload.kind==='zip'?'ZIP 材料包':upload.files.length+' 个已选文件'}；模型发送需另行确认。`:'也可以填写本机路径，或导入已有材料配置；无需重复打包。';
    return `<h3>${parent} · 完整材料包</h3><div class="fusion-actions"><button class="btn subtle" data-fusion="pick-package" data-parent="${parent}" data-kind="directory" ${disabled}>选择文件夹 ${parent}</button><button class="btn subtle" data-fusion="pick-package" data-parent="${parent}" data-kind="zip" ${disabled}>选择 ZIP ${parent}</button></div><input id="fusion-folder-${parent}" type="file" webkitdirectory multiple hidden data-upload-parent="${parent}" data-upload-kind="directory" ${disabled}><input id="fusion-zip-${parent}" type="file" accept=".zip,application/zip" hidden data-upload-parent="${parent}" data-upload-kind="zip" ${disabled}><p class="fusion-note fusion-upload-summary" id="fusion-upload-${parent}">${esc(summary)}</p>${upload?`<button class="text-btn" data-fusion="clear-upload" data-parent="${parent}" ${disabled}>改用本机路径 ${parent}</button>`:''}`;
  }
  function excludedUpload(name){
    const parts=name.toLowerCase().split('/');
    return parts.some(part=>['.git','.state','node_modules','.venv','venv','__pycache__','logs','credentials.json','secrets.json','state.json','pay-output.txt','payment-needed.txt','pay-attempt.json','.npmrc','.pypirc'].includes(part)||part.startsWith('.env'))||/\.(pem|key|p12|pfx|log|db|sqlite|sqlite3)$/i.test(name);
  }
  function uploadRows(files){
    if(!files.length||files.length>256)throw Error('目录需包含 1–256 个文件；大目录可改用本机路径清点，不会静默省略文件。');
    const roots=new Set(files.map(file=>file.webkitRelativePath.split('/')[0]));
    if(roots.size!==1||files.some(file=>!file.webkitRelativePath.includes('/')))throw Error('请通过“选择文件夹”选择一个完整目录。');
    const rows=files.map(file=>({file,path:file.webkitRelativePath.split('/').slice(1).join('/')}));
    let total=0;
    for(const row of rows){
      if(excludedUpload(row.path))continue;
      if(row.file.size>2*1024*1024)throw Error('单个文件不能超过 2 MB；未截断材料。');
      total+=row.file.size;
    }
    if(total>16*1024*1024)throw Error('每个材料包读取内容不能超过 16 MB；未截断材料。');
    return rows;
  }
  function fileContent(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);
      reader.onerror=reader.onabort=()=>reject(Error('无法读取所选文件，请重新选择后重试；未发送模型。'));reader.readAsDataURL(file);
    });
  }
  async function uploadPayload(){
    const uploads={};
    for(const parent of ['A','B']){
      const upload=state.uploads[parent];if(!upload)continue;
      if(upload.kind==='zip')uploads[parent]={kind:'zip',content:await fileContent(upload.files[0])};
      else{
        const files=[];
        for(const row of uploadRows(upload.files))files.push({path:row.path,content:excludedUpload(row.path)?null:await fileContent(row.file)});
        uploads[parent]={kind:'directory',files};
      }
    }
    return uploads;
  }
  function usePackages(){
    if(state.busy||state.pending)throw Error('当前任务结束前不能更换材料；请先查看原任务。');
    if(state.documents)action('package-input');
  }
  function useSources(records){
    if(state.busy||state.pending)throw Error('当前材料读取或融合任务结束前，不能替换 A/B');
    window.SparkSkills.compose(...records);
    const documents=Object.fromEntries(records.map((record,index)=>[index?'B':'A',{title:record.title,body:record.body,sha256:record.evidence.sha256}]));
    forgetJob();transientKey='';
    Object.assign(state,{documents,uploads:{A:null,B:null},inventory:null,selection:null,importedReview:null,source:null,probe:null,prepared:null,request:null,result:null,saved:false,contextDirty:false,error:'',goalError:''});
    Object.assign(state.form,{a:'',b:'',resourcesA:'',resourcesB:'',gapsA:'',gapsB:'',goal:''});
    state.message='已沿用碰撞台所选 A/B 的正文与指纹。不用预知结果，审查材料后即可探索；未读取关联文件，未发送模型。';paint();
  }
  function selectedDocuments(){
    return `<span class="eyebrow">01 / SELECTED SKILLS</span><h2>沿用碰撞台的两个 Skill</h2><div class="fusion-grid">${Object.entries(state.documents).map(([parent,document])=>`<article><h3>${parent} · ${esc(document.title)}</h3><p class="fusion-note">SKILL.md 正文 · ${document.body.length.toLocaleString()} 字符</p><code class="fusion-fingerprint">${esc(document.sha256)}</code></article>`).join('')}</div><p class="fusion-note">这里只接收已选正文，不会按文档中的路径自动读取磁盘或联网。若要理解脚本、资料和模板，请导入已有完整包配置，或切换完整包输入。</p><button class="btn subtle" data-fusion="package-input">改用完整 Skill 包</button>${field('goal','偏好或限制（选填）','不知道也可以留空；例如：优先探索学习场景，或只使用本地工具。',true)}${field('budget','请求字符预算（1000–1000000）','160000')}<button class="btn primary" data-fusion="prepare" ${!state.connected||state.busy||state.pending?'disabled':''}>${state.busy?'正在处理…':'读取所选 Skill 正文'}</button>`;
  }
  function contributionView(side,contribution){
    // 借「能力」（领域职能）与借「做法」（步骤/条件/约束/校验）不是一回事：
    // 只有借做法才可能落到第三个领域还不失真，所以这层必须让用户看见。
    const kinds={capability:'借了能力 · 领域职能',way_of_working:'借了做法 · 做事结构'};
    return `<section><h4>${esc(side)} 提供什么</h4><p class="fusion-note">${esc(kinds[contribution.kind]||'未声明借用类型')}</p><p>${esc(contribution.mechanism)}</p></section>`;
  }
  function materializeView(orientation){
    const busy=state.materializeBusy===orientation;
    const report=state.materializeReport&&state.materializeReport.orientation===orientation?state.materializeReport:null;
    const verdict=report?`<p><b>可运行：${report.runnable?'是':'否'}</b> · selftest 退出码 ${esc(String(report.selftest?.exit_code))}</p><p class="fusion-note">包体目录：<code>${esc(report.package_dir)}</code></p>${report.selftest?.stdout_tail?`<p class="fusion-note">自测输出：${esc(report.selftest.stdout_tail)}</p>`:''}${report.fix_rounds?.length?`<p class="fusion-note">修复回路：${report.fix_rounds.length} 轮（真实 traceback 定点修补）</p>`:''}<p class="fusion-note">${esc(report.value_note||'')}</p>`:'';
    return `<div class="fusion-materialize"><p class="fusion-note">物化＝把本方案生成真实文件包并运行自测——回答「能不能跑得动」；不证明「比父本好」。</p><label class="fusion-consent"><input id="fusion-materialize-consent" type="checkbox" ${busy||state.pending?'disabled':''}>我同意物化会再次发送材料并可能产生费用</label><button class="btn subtle" data-fusion="materialize" data-orientation="${esc(orientation)}" ${busy||state.pending?'disabled':''}>${busy?'正在物化并自测…':'物化并自测'}</button>${verdict}</div>`;
  }
  function candidateView(candidate){
    const orientations={A:'增强 A 的原场景',B:'增强 B 的原场景',C:'形成独立新场景'};
    const dispositions={hypothesis:'候选机制 · 待构建验证',blocked:'材料或实现存在阻塞',not_recommended:'不推荐融合'};
    return `<article class="fusion-candidate" data-orientation="${esc(candidate.orientation)}"><div class="fusion-candidate-heading"><span class="chip">${esc(candidate.orientation)} 向 · ${esc(orientations[candidate.orientation])}</span><span class="fusion-note">${esc(dispositions[candidate.disposition])}</span></div><h3>${esc(candidate.title)}</h3><h4>解决什么方向与问题</h4><p>${esc(candidate.problem)}</p><h4>融合机制 · 为什么不是拼接</h4><p>${esc(candidate.new_mechanism)}</p><div class="fusion-grid">${contributionView('A',candidate.contribution_a)}${contributionView('B',candidate.contribution_b)}</div><h4>具体执行流程</h4>${candidate.workflow.length?`<ol class="fusion-workflow">${candidate.workflow.map(step=>`<li>${esc(step)}</li>`).join('')}</ol>`:'<p>本方向尚无可执行流程，不构建新包。</p>'}<h4>新 Skill 要包含的内容</h4>${candidate.artifacts.length?`<ul class="fusion-artifacts">${candidate.artifacts.map(artifact=>`<li><code>${esc(artifact.path)}</code><span>${esc(artifact.purpose)}</span></li>`).join('')}</ul>`:'<p>尚无待构建产物。</p>'}<h4>怎样确认真的有新增能力</h4><ul>${candidate.evaluation.map(test=>`<li><strong>${esc(test.task)}</strong><p>${esc(test.success_criterion)}</p><p class="fusion-note">对照：A 单独 / B 单独 / 机械拼接 / 仅模型 / 融合</p></li>`).join('')}</ul><div class="fusion-grid">${candidate.ablation_checks.map(check=>`<section><h4>移除 ${esc(check.removed_parent)} 的反证检查</h4><p>${esc(check.task)}</p><p class="fusion-note">预计失去：${esc(check.expected_loss)}</p></section>`).join('')}</div>${candidate.blockers.length?`<section class="fusion-blockers"><h4>继续之前必须补齐</h4><ul>${candidate.blockers.map(blocker=>`<li>${esc(blocker)}</li>`).join('')}</ul></section>`:''}<p class="fusion-note">判断依据：${esc(candidate.rationale)}</p>${materializeView(candidate.orientation)}${details('逐项来源证据与完整方案',candidate)}</article>`;
  }
  function invalidateContext(){state.prepared=null;state.request=null;state.result=null;state.saved=false;state.materializeReport=null;state.materializeBusy=null;state.contextDirty=true;forgetJob();state.message='正文选择或审查理由已更改，请重新确认上下文；旧发送授权已撤销。';}
  function canSend(){return Boolean(state.probe?.basic_json_ok&&state.request&&root()?.querySelector('#fusion-consent')?.checked&&root()?.querySelector('#fusion-cost-consent')?.checked&&!state.busy&&!state.pending)}
  function matchingReview(report,review){
    return ['A','B'].every(parent=>{
      if(review?.package_sha256?.[parent]!==report.packages[parent].sha256)return false;
      const rows=review.selection?.[parent],expected=report.context_selection[parent].map(row=>row.path);
      return Array.isArray(rows)&&rows.length===expected.length&&new Set(rows.map(row=>row?.path)).size===expected.length&&rows.every(row=>row&&expected.includes(row.path)&&typeof row.include==='boolean'&&typeof row.reason==='string'&&row.reason.trim()&&row.reason.length<=1000&&Object.hasOwn(materialRoles,row.role)&&typeof row.classification_reason==='string'&&row.classification_reason.trim()&&row.classification_reason.length<=1000&&(row.path!=='SKILL.md'||row.include&&row.role==='entry'));
    });
  }
  function coverage(){
    if(!state.inventory)return '';
    const report=state.inventory,locked=state.busy||state.pending;
    const roles=materialRoles;
    const statuses={text:'已读取正文',unparsed:'未解析 · 仅清单',excluded:'安全排除 · 未读取'};
    const packages=Object.entries(report.packages).map(([parent,pkg])=>`<section class="fusion-coverage-parent"><h3>${parent} · ${pkg.files.length} 项材料</h3><p class="fusion-note">清单指纹 ${esc(pkg.sha256)}</p>${pkg.files.map((file,index)=>{
      const choice=state.selection[parent].find(row=>row.path===file.path),key=parent+'-'+index;
      return `<article class="fusion-file"><div><h4>${esc(file.path)}</h4><span class="chip">${esc(roles[choice?.role||file.role])}</span><p class="fusion-note">${esc(statuses[file.status])} · ${file.characters.toLocaleString()} 字符${file.lines?' · '+file.lines+' 行':''}<br>${esc(choice?.classification_reason||file.classification_basis)}</p><p class="fusion-file-budget">当前选择正文 ${choice?.include?file.body_characters.toLocaleString():0} 字符 · 完整正文成本 ${file.body_characters.toLocaleString()} 字符<br>上次计算的文件清单 ${file.inventory_characters.toLocaleString()} 字符；含行号及 JSON 转义，非 token。</p><p class="fusion-note">${esc(file.sha256||'安全排除项没有读取正文或计算哈希')}</p>${file.status==='text'?`<button id="fusion-source-${key}" class="btn subtle" data-fusion="source" data-parent="${parent}" data-path="${esc(file.path)}" ${state.busy?'disabled':''}>查看原文 ${esc(parent+' / '+file.path)}</button>`:''}</div><div>${choice?`<div class="fusion-field"><label for="fusion-role-${key}">材料分类 ${esc(parent+' / '+file.path)}</label><select id="fusion-role-${key}" data-context-parent="${parent}" data-context-path="${esc(file.path)}" data-context-field="role" ${locked||file.path==='SKILL.md'?'disabled':''}>${Object.entries(roles).map(([value,label])=>`<option value="${value}" ${choice.role===value?'selected':''}>${esc(label)}</option>`).join('')}</select></div><label class="fusion-field" for="fusion-classification-${key}"><span>分类依据 ${esc(parent+' / '+file.path)}</span><input id="fusion-classification-${key}" data-context-parent="${parent}" data-context-path="${esc(file.path)}" data-context-field="classification_reason" value="${esc(choice.classification_reason)}" maxlength="1000" ${locked?'disabled':''}></label><label class="fusion-consent"><input id="fusion-include-${key}" type="checkbox" data-context-parent="${parent}" data-context-path="${esc(file.path)}" ${choice.include?'checked':''} ${locked||file.path==='SKILL.md'?'disabled':''}>发送正文 ${esc(parent+' / '+file.path)}</label><label class="fusion-field" for="fusion-reason-${key}"><span>取舍理由 ${esc(parent+' / '+file.path)}</span><input id="fusion-reason-${key}" data-context-parent="${parent}" data-context-path="${esc(file.path)}" value="${esc(choice.reason)}" maxlength="1000" ${locked?'disabled':''}></label>`:`<p>${esc(file.reason)}</p>`}</div></article>`;
    }).join('')}${details(parent+' 的分类和覆盖统计',{roles:pkg.roles,delivery:pkg.summary})}<details class="fusion-references"><summary>${parent} 的引用与上下文关系（${pkg.references.length} 项字面引用）</summary><p>只核对显式本地引用；不追踪动态导入、网络资源或运行依赖。找到路径不代表依赖闭合。</p>${pkg.references.length?pkg.references.map(ref=>`<p><strong>${esc(ref.file)}:${ref.line}</strong> → ${esc(ref.target)}<br><span class="chip">${esc({selected:'正文已选',omitted:'目标正文未选',excluded:'安全排除',unparsed:'未解析',unresolved:'缺失引用'}[ref.context_status])}</span> ${esc(ref.resolved_path)}</p>`).join(''):'<p>未发现可识别的字面引用；不等于没有依赖。</p>'}</details></section>`).join('');
    const source=state.source;
    const excerpt=source?`<section class="fusion-source" aria-label="本机原文回查"><h3>原文 · ${esc(source.parent+' / '+source.path)}</h3><p class="fusion-note">${esc(source.sha256)}<br>字符 ${source.offset+1}–${source.end} / ${source.total_characters} · 从原文第 ${source.first_line} 行起；分段展示不改变实际发送的完整文件。</p><pre tabindex="0">${esc(source.text)}</pre><div class="fusion-actions">${source.offset?`<button class="btn subtle" data-fusion="source" data-parent="${source.parent}" data-path="${esc(source.path)}" data-offset="${Math.max(0,source.offset-12000)}">上一段原文</button>`:''}${source.next_offset!==null?`<button class="btn subtle" data-fusion="source" data-parent="${source.parent}" data-path="${esc(source.path)}" data-offset="${source.next_offset}">下一段原文</button>`:''}</div></section>`:'';
    return `<section class="fusion-panel fusion-coverage"><span class="eyebrow">02 / CONTEXT COVERAGE</span><h2>材料覆盖与发送选择</h2><p>全量清点之后再决定正文范围。分类仅为路径与格式提示，可修订分类和依据，不代表能力已理解。未选正文仍保留文件指纹和取舍理由，不能被模型当作已读证据。</p><p class="fusion-budget ${report.budget_ok?'':'over'}">上次计算：${report.request_characters.toLocaleString()} / ${report.context_limit_chars.toLocaleString()} 字符（非 token） · ${state.contextDirty?'选择待重新计算':report.budget_ok?'预算内，仍须确认范围':'超出预算 · 禁止发送，未截断'}<br>正文 ${report.budget_breakdown.selected_body_characters.toLocaleString()} + 其余 ${report.budget_breakdown.other_characters.toLocaleString()} = ${report.request_characters.toLocaleString()}；其余含清单、理由、规则、缺口与系统约束。改选后统计和引用状态须重新计算。</p><div class="fusion-actions"><button class="btn subtle" data-fusion="omit-dependencies" ${locked?'disabled':''}>采用第三方依赖排除建议</button><button class="btn primary" data-fusion="context" ${locked||!state.connected?'disabled':''}>确认上下文并计算方向</button><button class="btn subtle" data-fusion="export-coverage" ${locked||state.contextDirty?'disabled':''}>导出材料审查回执</button></div>${excerpt}${packages}${details('明确保留的材料缺口',{known_gaps:report.known_gaps,context_gaps:report.context_gaps})}</section>`;
  }
  function probeView(){return `<section class="fusion-probe"><h3>先检查连接（可选）</h3><p class="fusion-note">只发送固定的 JSON 测试句，不发送技能包。会产生一次模型请求，供应商可能计费；不自动重试。通过仅证明基础 JSON 响应，不证明长上下文、完整融合格式或语义质量。</p><label class="fusion-consent"><input id="fusion-probe-consent" type="checkbox" ${state.busy||state.pending?'disabled':''}>我同意连接检查可能产生供应商费用</label><button class="btn subtle" data-fusion="preflight" disabled>检查模型连接</button>${state.probe?`<p class="fusion-note" role="status">基础 JSON 检查通过 · ${esc(state.probe.model)} · ${esc(state.probe.elapsed_seconds)} 秒 · 未发送技能材料；发送完整请求仍需单独授权。</p>`:''}</section>`}
  // 结构落点（39 号文 §7）：与域无关的话语结构指纹带来的跨域桥接。
  // 与规则方向并存——规则给不了跨域结果时，这一层至少指得出「哪里能接上」。
  function collision(p){
    const col=p&&p.collision;if(!col)return '';
    const cs=col.candidates||[];
    const cards=cs.map(c=>{
      const A=(c.pair&&c.pair.a)||{},B=(c.pair&&c.pair.b)||{};
      return `<article class="fusion-hint"><span class="chip">${esc(c.collision_id)}</span>`
        +`<p><b>缺口</b>：${esc(c.gap||'')}</p>`
        +`<p class="fusion-note"><b>规则侧</b>：${esc(A.text||'')}</p>`
        +`<p class="fusion-note"><b>场景侧</b>：${esc(B.text||'')}</p>`
        +`<p class="fusion-note"><b>可核抓手</b>：${(c.anchors||[]).map(x=>`<code>${esc(x)}</code>`).join(' ')||'<span style="opacity:.7">本层没抽出能背书的标识符</span>'}</p>`
        +`<p class="fusion-note">${esc(c.why||'')}</p>`
        +((c.anchors_dropped||[]).length?`<p class="fusion-note">另有 ${c.anchors_dropped.length} 个词在母包条目里查不到，已剔除——<b>宁可少给抓手，不给来路不明的抓手</b>。</p>`:'')
        +`</article>`;}).join('');
    return `<section class="fusion-panel"><span class="eyebrow">02c / LOCAL COLLISION</span><h2>碰撞落点 · 由本地算出，不由模型猜</h2>`
      +`<p>这些落点是在<b>你按下生成之前</b>由本地算法算出来的：先找「哪一侧在哪个环境下没有规则可用」，再退化为「规则 × 动作」配对。模型只能在这些落点上做具体化，不能凭空发明方向。</p>`
      +`<p class="fusion-note">${esc(col.note||'')}</p>`
      +`<div class="fusion-grid">${cards||'<p class="fusion-note">本层没算出落点——<b>这不等于不许模型试</b>，只是没有可锚定的接口可用。</p>'}</div>`
      +`<p class="fusion-note">落点是<b>结构事实</b>，不是提案（<code>semantic_verified=false</code>）；它不判断两个包该不该融合。抓手必须能在母包条目里查到，否则宁可不给。</p></section>`;
  }
  function structure(p){
    const c=p&&p.capability;if(!c)return '';
    const axes=label=>`<div class="fusion-profile"><span class="chip">${label}</span>${(c[label].profile.has||[]).map(a=>`<span class="fusion-axis">${esc(POLICY_AXIS[a]||a)}</span>`).join('')||'<span class="fusion-note">未抽出结构标记</span>'}</div>`;
    const b=c.bridge;
    const surfaces=(b.surfaces||[]).map(s=>{const i=s.indexOf('：');return `<article class="fusion-hint"><span class="chip">${esc(i<0?s:s.slice(0,i))}</span><p>${esc(i<0?s:s.slice(i+1))}</p></article>`}).join('');
    const f=c.failures;
    const gaps=(f&&f.gaps||[]).map(g=>{const t=(g.from_side&&g.from_side.text)||'';const i=t.indexOf('：');return `<article class="fusion-hint"><span class="chip">${esc(g.side)} 的 ${esc(g.scenario_term)}</span><p>${esc(i<0?t:t.slice(0,i))}</p><p class="fusion-note">${esc(g.why||'')}</p></article>`}).join('');
    const failureBlock=f?`<h3 style="margin:1.1em 0 .4em">未覆盖面 · 哪一侧在哪个环境下没有规则可用</h3><p class="fusion-note">双向检查了 ${f.checked} 个环境场景词，覆盖 ${f.covered} 个，未覆盖 ${f.gap_count} 个${f.weak_terms?`（另有 ${f.weak_terms} 个局部变量名不是环境场景，未计入）`:''}。</p><div class="fusion-grid">${gaps||`<p class="fusion-note">${esc(f.note||'')}</p>`}</div><p class="fusion-note">${esc(f.note||'')}</p>`:'';
    return `<section class="fusion-panel"><span class="eyebrow">02b / STRUCTURAL SURFACES</span><h2>结构落点 · 跨域碰撞在哪里接得上</h2><p>不按共同词，按两边「做事的方式」：步骤、条件、约束、校验、交付物、依据、边界、受众。领域越远越有戏——法律与汽修可以没有一句共同词，但都有步骤和约束。</p><div class="fusion-grid">${axes('A')}${axes('B')}</div><p class="fusion-note">判定：<b>${esc(BRIDGE_STATUS[b.status]||b.status)}</b> · 共同结构轴 ${b.shared_axis_count} 个 · 落点 ${b.surface_count} 处 · 重叠度 ${b.structural_overlap}</p><p class="fusion-note">${esc(b.note||'')}</p><div class="fusion-grid">${surfaces||`<p class="fusion-note">${esc(NO_SURFACE_HINT[b.no_surface_reason]||NO_SURFACE_HINT.no_shared_axes)}</p>`}</div>${failureBlock}<p class="fusion-note">结构落点是<b>给模型的指针</b>，不是提案。它不判断两个包是否真能碰撞（<code>semantic_verified=false</code>），最终判断仍由你或模型做出。<b>未覆盖不等于有问题</b>——它只说明这一侧在该环境下没有可援引的规则。</p></section>`;
  }
  function materials(){
    const p=state.prepared;if(!p)return '';
    const inventories=Object.entries(p.packages).map(([label,pkg])=>`<article><h3>${label} · ${pkg.files.length} 项材料</h3>${details('文件清单、哈希与解析状态',pkg.files)}${details('字面引用核查',pkg.references)}</article>`).join('');
    const hints=p.rule_directions.suggestions.map(d=>`<article class="fusion-hint"><span class="chip">${esc(d.orientation)} 向 · 优先级 ${d.priority_points}</span><h3>${esc(d.label)}</h3><p>${esc(d.exploration_question)}</p><p class="fusion-note">${d.state==='blocked_materials'?'有材料缺口':'待模型判断'}</p>${details('计分依据与原文证据',{score:d.score_breakdown,evidence:d.evidence,blockers:d.blockers})}</article>`).join('');
    const structureBlock=structure(p);
    const collisionBlock=collision(p);
    return `<section class="fusion-panel"><span class="eyebrow">02 / DIRECTIONS</span><h2>先找到值得探索的方向</h2><p id="fusion-direction-intent" class="fusion-note">${esc(intentSummary(p.intent_mode))}</p><p>这些分数是规则排序，不代表能力评级或成功概率。模型可以否决所有建议。</p><div class="fusion-grid">${inventories}</div><div class="fusion-grid">${hints||'<p>规则没有匹配方向——两边没有出现规则需要的共同词。仍可让模型基于材料探索，并解释判断依据。</p>'}</div>${details('已知材料缺口',p.known_gaps)}<p class="fusion-note">请求约 ${p.request_characters.toLocaleString()} 字符（非 token 估算）；不支持解析的文件仅发送清单。</p></section>${collisionBlock}${structureBlock}`;
  }
  // ── 厂商目录（2026-10-02）───────────────────────────────
  // 目录是**纯本地说明书**：只说明「端点长什么样、参数有什么坑」，
  // 不联网、不调 API、不含任何凭据。选厂商只负责**填**端点与模型，
  // 用户随时可以改——目录漏了新厂商也不会把人挡在外面。
  const PROVIDERS=[
    ['deepseek','DeepSeek（深度求索）','https://api.deepseek.com/v1/chat/completions',['deepseek-chat','deepseek-reasoner'],'openai',''],
    ['zhipu','智谱 GLM（通用 API）','https://open.bigmodel.cn/api/paas/v4/chat/completions',['glm-4.7','glm-4.5-air','glm-5.3'],'openai','始终思考型模型不能关闭推理，只能选强度（low/high/max）'],
    ['zhipu-coding','智谱 GLM（Coding Plan 套餐）','https://open.bigmodel.cn/api/coding/paas/v4/chat/completions',['glm-5.3','glm-5-Turbo','glm-4.7'],'openai','买了 Coding Plan 必须用这个端点——用通用端点会报「余额不足」，那是端点不对，不是余额问题'],
    ['moonshot','Kimi / 月之暗面','https://api.moonshot.cn/v1/chat/completions',['moonshot-v1-8k','kimi-k2-0905-preview'],'openai',''],
    ['qwen','通义千问（DashScope 兼容模式）','https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',['qwen-plus','qwen-max','qwen-turbo'],'openai',''],
    ['doubao','豆包（火山方舟）','https://ark.cn-beijing.volces.com/api/v3/chat/completions',[],'openai','模型名要填控制台里的推理接入点 ID（形如 ep-…），不是模型代号'],
    ['minimax','MiniMax（国际站）','https://api.minimax.io/v1/chat/completions',['MiniMax-M3','MiniMax-M2.7'],'openai','已弃用 max_tokens 改叫 max_completion_tokens（已自动改名）；**请把「推理设置」选成「关闭」**——默认的自适应思考会把输出预算吃光（实测关闭后 181 秒写完，不关则六次全被截断），而且它的 reasoning_tokens 恒为 0，用量读数上看不出来'],
    ['minimax-cn','MiniMax（国内站）','https://api.minimax.cn/v1/chat/completions',['MiniMax-M3'],'openai','国内账号用这个端点，国际站的 key 打这里会 401；**同样请把「推理设置」选成「关闭」**，否则输出预算会被思考吃光'],
    ['openrouter','OpenRouter（聚合）','https://openrouter.ai/api/v1/chat/completions',['deepseek/deepseek-chat','anthropic/claude-sonnet-4'],'openai','模型名格式是「厂商/模型」'],
    ['siliconflow','硅基流动 SiliconFlow（聚合）','https://api.siliconflow.cn/v1/chat/completions',['deepseek-ai/DeepSeek-V3'],'openai','模型名格式是「厂商/模型」'],
    ['anthropic','Anthropic Claude','https://api.anthropic.com/v1/messages',['claude-sonnet-4-5','claude-opus-4-1','claude-haiku-4-5'],'anthropic','用 x-api-key 鉴权，没有 response_format——靠提示词约束它只回 JSON'],
    ['gemini','Google Gemini','https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',['gemini-2.5-pro','gemini-2.5-flash'],'gemini','模型名在路径里；system 提示词单独放 systemInstruction'],
    ['ollama','Ollama（本地）','http://127.0.0.1:11434/v1/chat/completions',['qwen2.5:7b','llama3.1:8b'],'openai','不需要密钥；模型名用「名称:标签」，先 ollama pull'],
    ['lmstudio','LM Studio（本地）','http://127.0.0.1:1234/v1/chat/completions',[],'openai','不需要密钥；先在 Local Server 页启动服务'],
    ['vllm','vLLM / 任意自建兼容服务','http://127.0.0.1:8000/v1/chat/completions',[],'openai','不需要密钥；端口按你的服务改'],
  ];
  const PROVIDER_BY_ID=Object.fromEntries(PROVIDERS.map(p=>[p[0],p]));
  function providerView(){
    const cur=state.form.provider||'';
    const picked=PROVIDER_BY_ID[cur];
    return `<label class="fusion-field" for="fusion-provider"><span>服务商（选一个自动填好端点与模型名）</span><select id="fusion-provider" data-fusion-field="provider" ${state.busy||state.pending?'disabled':''}>`
      +`<option value="">— 不选，自己填端点 —</option>`
      +PROVIDERS.map(p=>`<option value="${esc(p[0])}" ${cur===p[0]?'selected':''}>${esc(p[1])}</option>`).join('')
      +`</select></label>`
      +(picked&&picked[5]?`<p class="fusion-note"><b>${esc(picked[1])}</b>：${esc(picked[5])}</p>`:'')
      +`<p class="fusion-note">协议：<code>${esc(picked?picked[4]:'按端点自动判断')}</code>。选厂商只是帮你<b>填</b>端点与模型名——两项都可以再改，接不上别的服务随时切「不选」。</p>`;
  }
  function model(){if(!state.prepared)return '';return `<section class="fusion-panel"><span class="eyebrow">03 / MODEL REVIEW</span><h2>让模型理解，再做判断</h2>${providerView()}<div class="fusion-grid">${field('endpoint','模型端点','https://服务商/v1/chat/completions')}${field('model','模型名','填写服务商或本机已安装的模型名')}</div><label class="fusion-field" for="fusion-thinking"><span>推理（仅部分模型支持）</span><select id="fusion-thinking" data-fusion-field="thinking" ${state.busy||state.pending?'disabled':''}><option value="" ${!state.form.thinking?'selected':''}>按服务商默认</option><option value="disabled" ${state.form.thinking==='disabled'?'selected':''}>关闭推理（始终思考型模型不可选）</option><option value="enabled" ${state.form.thinking==='enabled'?'selected':''}>开启推理（用服务商默认强度）</option><option value="low" ${state.form.thinking==='low'?'selected':''}>推理强度 · 低</option><option value="high" ${state.form.thinking==='high'?'selected':''}>推理强度 · 中</option><option value="max" ${state.form.thinking==='max'?'selected':''}>推理强度 · 高</option></select></label><p class="fusion-note">推理型模型会先把输出预算花在推理链上，导致返回被截断或正文为空（2026-10-01 实测 glm-4.7：关掉推理 2.5 秒正常返回，不关则被截断）。<b>始终思考型模型（glm-5.3 等）不接受「关闭推理」</b>，请改选推理强度。不选则不发送该字段，保持服务商默认行为；选了会出现在下面的完整请求里，可发送前核对。</p><label class="fusion-field" for="fusion-key"><span>API 密钥（仅本次请求；本机模型可留空）</span><input id="fusion-key" type="password" value="${esc(transientKey)}" autocomplete="off" ${state.busy||state.pending?'disabled':''}></label><p class="fusion-note">已连接后端融合服务，可直接预览与发送。本机模型（Ollama / LM Studio 等）不需要密钥，接口地址填回环地址即可；未在「数据与偏好 → 调用模型」保存配置时，也可在此临时填写。点击发送会将下面预览中的源文本发送至所填端点。HTTPS 用于联网服务，数字回环 HTTP 可用于本机模型。不会自动重试。</p><button class="btn subtle" data-fusion="preview" ${state.busy||state.pending?'disabled':''}>查看完整请求</button>${state.request?details('完整发送内容 · 含源文件正文',state.request.messages):''}<label class="fusion-consent"><input id="fusion-consent" type="checkbox" ${!state.request||state.busy||state.pending?'disabled':''}>我已检查请求内容及端点，允许发送本次材料</label><div class="fusion-actions"><button class="btn primary" data-fusion="run" disabled>发送给模型</button>${state.pending?'<button class="btn subtle" data-fusion="cancel">停止等待并丢弃返回结果</button>':''}</div><p class="fusion-note">停止等待不能撤回已发送内容或保证停止供应商计费；底层请求结束前不重复发送。</p></section>`}
  // 产出文件名的扩展名是**系统推断**，不是模型原话——必须让用户看见并能推翻。
  function artifactNamesView(artifactNames){
    if(!artifactNames||!artifactNames.inferred||!artifactNames.inferred.length)return '';
    const rows=artifactNames.inferred.map(i=>`<li><code>${esc(i.to)}</code> — ${esc(i.basis)}（模型原文写的是 <code>${esc(i.from)}</code>）</li>`).join('');
    const miss=artifactNames.unresolved&&artifactNames.unresolved.length
      ?`<p class="fusion-note">另有 ${artifactNames.unresolved.length} 个文件名没有依据可依据扩展名，保持原样未作修改。</p>`:'';
    return `<details><summary>扩展名由系统推断（${artifactNames.inferred.length} 处）</summary>`+
      `<p class="fusion-note">模型把文件名写到点为止就停了，这里按各产出的用途推断补全。**这是推断，不是模型原文**——如与你的预期不符，请以产物用途为准。</p>`+
      `<ul>${rows}</ul>${miss}</details>`;
  }
  // 证据重锚定留痕：修了多少、没能修多少。它只修**转写误差**，修不动的照旧被契约拒。
  function reanchorView(reanchor){
    if(!reanchor||!reanchor.applied)return '';
    const rejected=(reanchor.rejected||[]).length;
    return `<p class="fusion-note">引用重锚定：修正 ${reanchor.fixed} 处${rejected?` · <b>未能重锚 ${rejected} 处（照旧交回契约把关，未放宽任何校验）</b>`:''}</p>`;
  }
  function results(){
    if(!state.result)return '';
    const result=state.result.result;
    const spark=state.result.spark;
    return `<section class="fusion-panel"><span class="eyebrow">04 / NEW SKILL PLANS</span><h2>融合后的 Skill 方案 · 待验证</h2><p class="fusion-note">分析服务：${esc(state.result.model)} · 本次请求 ${esc(state.result.elapsed_seconds)} 秒</p><p>来源与格式校验已通过。以下展示目标、机制和构建计划，不是已经生成或验收的新 Skill 包；尚未执行，也未证明效果。</p>${details('双方能力画像',result.profiles)}${details('规则方向的采用与否决',result.direction_reviews)}${details('每个来源保留与舍弃什么',(result.profiles||[]).map(p=>({来源:p.parent,...p.retention})))}<p id="fusion-result-intent" class="fusion-note">${esc(intentSummary(state.result.intent_mode))} 候选问题尚待你选择与审查，不代表已验证的新能力。</p>${reanchorView(state.result.reanchor)}${artifactNamesView(state.result.artifact_names)}${spark?sparkView(spark):''}<div class="fusion-candidates">${result.candidates.map(candidateView).join('')}</div><div class="fusion-actions"><button class="btn primary" data-fusion="save" ${state.saved?'disabled':''}>${state.saved?'已保存到成品库':'保存方案到成品库'}</button><button class="btn subtle" data-fusion="export">导出方案 JSON</button></div></section>`;
  }
  // 火花读数：模型认领的类别 + 从结果本身可核对出的风险。类别是认领，不是结论。
  // 借用类型与能力层的接缝：说「借了做法」的那些证据行，能力层在上面留没留下结构指纹。
  function borrowedView(spark){
    const rows=(spark.candidates||[]).filter(c=>c.borrowed&&(c.borrowed.A||c.borrowed.B));
    if(!rows.length)return '';
    const cell=(parent,read)=>read?`<p><b>${esc(parent)}</b>：${esc(read.kind==='way_of_working'?'借了做法':'借了能力')}${read.attested_axes.length?` · 证据落在 ${read.attested_axes.map(a=>esc(POLICY_AXIS[a]||a)).join('、')}`:''}${read.shared_axes.length?` · 其中双方共有 ${read.shared_axes.map(a=>esc(POLICY_AXIS[a]||a)).join('、')}`:''}${read.unattested_evidence?` · ${read.unattested_evidence} 条证据本层没留下指纹`:''}</p>`:'';
    return `<div class="fusion-borrowed"><p class="eyebrow">借的东西 · 与能力层对照</p>${rows.map(c=>`<article><p><b>${esc(c.title)}</b></p>${cell('A',c.borrowed.A)}${cell('B',c.borrowed.B)}</article>`).join('')}<p class="fusion-note">${esc((spark.capability_linkage||{}).caveat||'')}</p></div>`;
  }
  function sparkView(spark){
    const chips=(spark.claimed_class_labels||[]).map(t=>`<span class="chip">认领：${esc(t)}</span>`).join('');
    const flagged=(spark.candidates||[]).filter(c=>c.risk_count);
    const fills=(spark.candidates||[]).filter(c=>c.gaps_to_fill&&c.gaps_to_fill.length);
    // 新颖性与借用可核验（2026-10-02）。**分母不足时显示「不可测」而不是 0%**——
    // 0 会被读成「测出来是零」。实测同一批产出用英文命名 vs 中文命名，
    // 新颖率差 5 倍，所以它只用来报告，永不用来下结论。
    const noveltyRows=(spark.candidates||[]).map(c=>{
      const n=c.novelty||{};
      if(!n.markers && n.note!==undefined && /未做/.test(n.note||''))
        return `<article><p><b>${esc(c.title)}</b></p><p class="fusion-note">未做新颖性核对</p></article>`;
      const val=n.measurable ? `${Math.round(n.ratio*100)}%`
                             : '<b>不可测</b>（标识符不足门槛 '+esc(String(n.min_denominator||''))+'）';
      const bv=c.borrow_verification||{};
      const bval=!bv.checked?'未做核对'
        :(bv.ratio===null?'自述里没有可核的具体指认':`${Math.round(bv.ratio*100)}% 能在母包核到`);
      return `<article><p><b>${esc(c.title)}</b></p>`
        +`<p class="fusion-note">新颖：${val}　·　新造标识符 ${n.new_marker_count} 个 / 共 ${n.marker_count} 个</p>`
        +`<p class="fusion-note">借用自述可核验：${esc(bval)}</p>`
        +(n.note?`<p class="fusion-note">${esc(n.note)}</p>`:'')
        +`</article>`;}).join('');
    // 2026-10-01 实测：换一对毫不相干的材料重跑，通过时的五类分布**完全相同**
    //（{beats_A:1, beats_B:1, independent_usable:1}）。契约强制「恰好 A/B/C 三个候选」，
    // 模型唯一的选择自由只是「哪个方向认领哪一类」，而它每次都选同一个映射——
    // **所以这组分布是契约结构的产物，不携带关于材料的信息。**
    // 界面上必须把这件事说清楚，否则「比 A 好」会被读成结论，而它只是一句认领。
    const classCaveat = chips
      ? '<p class="fusion-note">这些类别是**模型自认，不是结论**。实测换材料重跑，分布不变——' +
        '契约强制恰好三个候选，模型只是按方向对号入座，认领不携带关于材料的信息。' +
        '融合到底有没有用，只有对照实验能回答（A 单独 / B 单独 / 机械拼接 / 仅模型 / 融合）。</p>'
      : '';
    return `<div class="fusion-spark"><p class="eyebrow">SPARK READ / 火花读数</p><div class="fusion-profile">${chips||'<span class="fusion-note">未认领类别</span>'}</div>${classCaveat}<p class="fusion-note">${esc(spark.meaning)} ${esc(spark.reason_unverified||'')}</p>${borrowedView(spark)}${noveltyRows?`<div class="fusion-borrowed"><p class="eyebrow">新颖性与借用可核验</p>${noveltyRows}<p class="fusion-note">新颖比率<b>只用来报告，永不用来下结论</b>：实测同一批产出用英文命名与中文命名差 5 倍，标识符不足门槛时显示「不可测」而不是 0%——0 会被读成「测出来是零」。</p></div>`:""}${fills.length?`<div class="fusion-fill">${fills.map(c=>`<article><p><b>${esc(c.title)}</b> · 待补缺口：${c.gaps_to_fill.map(esc).join('、')}</p><label class="fusion-consent"><input id="fusion-fill-consent" type="checkbox" ${state.busy||state.pending?'disabled':''}>我同意为补足再次发送材料并可能产生费用</label><button class="btn subtle" data-fusion="fill" data-title="${esc(c.title)}" ${state.busy||state.pending?'disabled':''}>${state.fillBusy===c.title?'正在补足…':'让 AI 补足'}</button></article>`).join('')}<p class="fusion-note">补足后由同一个核对器复评，并仍然标注为「未验证」——补完不等于验证过。</p></div>`:''}${flagged.length?details('可核对出的风险（'+flagged.length+' 个候选）',flagged.map(c=>({方案:c.title,风险:c.risks.map(r=>r.detail)}))):'<p class="fusion-note">本次结果没有可核对出的结构性风险。</p>'}</div>`;
  }
  function comparisonView(evidence){
    if(!evidence.comparison?.length)return '';
    const labels={A_only:'仅 A',B_only:'仅 B',mechanical_concatenation:'机械拼接',model_only:'仅模型',fusion:'融合 Skill',fusion_without_A:'融合 · 移除 A',fusion_without_B:'融合 · 移除 B'};
    return `<div class="fusion-comparison"><table><caption>独立模型辅助审阅（非人审）</caption><thead><tr><th scope="col">实验臂</th><th scope="col">通过 / 6</th><th scope="col">功能分 / 30</th><th scope="col">硬失败题数</th></tr></thead><tbody>${evidence.comparison.map(row=>`<tr><th scope="row">${esc(labels[row.arm]||row.arm)}</th><td>${esc(row.passed)} / 6</td><td>${esc(row.functional_total)} / 30</td><td>${esc(row.failures)}</td></tr>`).join('')}</tbody></table><p class="fusion-note">功能分为三道功能题的合计；硬失败仅指明确命中冻结的失败条件，其他不足反映在通过数中。分数不是人工验证，也不证明融合收益。</p></div>`;
  }
  function reviewDraft(){return !reviews.saved&&(reviews.form.confirmed||reviews.form.decision!=='needs_more_evidence'||['reason','quote_left','quote_right'].some(key=>reviews.form[key].trim()))}
  function reviewAnswer(side){
    const row=reviews.detail.runs.find(item=>item.id===reviews[side]);
    return `<article class="fusion-review-answer"><label for="review-${side}">${side==='left'?'左侧':'右侧'}对照组</label><select id="review-${side}" data-review-side="${side}" data-review-nav ${reviews.busy||reviewDraft()?'disabled':''}>${reviews.detail.runs.map(item=>`<option value="${esc(item.id)}" ${item.id===row.id?'selected':''}>${esc(reviewLabels[item.arm]||item.arm)} · ${esc(item.id)}</option>`).join('')}</select><h4>原始回答 · ${esc(row.id)}</h4><p class="fusion-note">模型判定：${row.passed_by_model_reviewer?'通过':'未通过'}，不是人工结论。</p><code class="fusion-fingerprint">回答 SHA-256 ${esc(row.response_sha256)}</code><pre class="fusion-review-response">${esc(row.response)}</pre>${details('独立模型评分、理由和引文',row.assessment)}${details('该组实际提供的文件及指纹',row.provided_files)}${details('会话自述读取记录（非执行审计）',{reported:row.reported_read_files,warnings:row.metadata_warnings})}</article>`;
  }
  function reviewsView(){
    const detail=reviews.detail,locked=reviews.busy||reviewDraft(),remoteLocked=locked||!state.connected||!state.reviewsSupported;
    return `<section id="fusion-reviews" class="fusion-panel" aria-labelledby="fusion-reviews-title"><span class="eyebrow">TASK EVIDENCE / HUMAN REVIEW</span><h2 id="fusion-reviews-title">逐题证据与人工审查</h2><p>看原题、七组原始回答、模型判据与来源窗口，再记录你同意、有异议或还缺什么证据。人工意见另存，不改写冻结实验，也不会自动把实验包标成已验证。</p>${state.connected&&!state.reviewsSupported?'<p class="fusion-note">当前本机服务版本不支持逐题证据。请保留已有任务结果，再用更新后的启动脚本重启服务。</p>':''}<button id="review-refresh" class="btn subtle" data-fusion="review-refresh" data-review-nav ${remoteLocked?'disabled':''}>刷新逐题证据</button><p id="review-status" class="fusion-note" role="status" tabindex="-1" aria-live="polite">${esc(reviews.message)}</p>${reviews.error?`<p class="fusion-error" role="alert">${esc(reviews.error)}</p>`:''}${reviews.items&&!reviews.items.length?'<p>暂无可完整核对的逐题证据；不会用摘要或模拟回答补齐。</p>':''}<div class="fusion-review-list">${(reviews.items||[]).map(item=>`<article><h3>${esc(item.title)} · ${esc(item.version)}</h3><p>${esc(item.decision)}</p><p class="fusion-note">${item.tasks.length} 个冻结任务 · 仅模型评审，人工验收仍待完成。</p><code class="fusion-fingerprint">受测 ZIP SHA-256 ${esc(item.zip_sha256)}</code><div class="fusion-actions">${item.tasks.map(task=>`<button class="btn subtle" data-fusion="review-task" data-id="${esc(item.id)}" data-task="${esc(task.id)}" data-review-nav ${remoteLocked?'disabled':''}>${esc(task.id+' · '+task.name)}${task.fusion_passed_by_model?'':' · 融合模型判未过'}</button>`).join('')}</div></article>`).join('')}</div>${detail?`<div class="fusion-review-task"><h3>${esc(detail.task.id+' · '+(detail.task.name||'冻结任务'))}</h3><h4>冻结原题</h4><pre>${esc(detail.task.input)}</pre>${details('预先定义的观察项与失败条件',{required:detail.task.required_observations,failures:detail.task.failure_conditions})}${details('冻结评分规则',detail.scoring)}<div class="fusion-review-columns">${reviewAnswer('left')}${reviewAnswer('right')}</div><details class="fusion-review-sources"><summary>来源核对窗口 · 不代表每组都获得这些材料</summary><p>${esc(detail.citation_limit)}</p>${detail.source_windows.length?detail.source_windows.map(window=>`<article><h4>${esc(window.path)}</h4><code class="fusion-fingerprint">SHA-256 ${esc(window.material_sha256)}</code><pre>${esc(window.numbered_excerpt)}</pre></article>`).join(''):'<p>本批没有来源窗口；不要把缺少证据写为已核对。</p>'}</details>${details('实验限制与未知项',detail.limitations)}<form id="review-form"><h4>记录这道题、这两份回答的人工意见</h4><p class="fusion-note">只记录你填写的意见，不验证审阅者身份；不代表领域专家验收、全部七组已审或融合收益成立。更换题目/组别前请先保存或明确放弃当前意见。</p><label for="review-decision">对当前模型判定的意见</label><select id="review-decision" data-review-field="decision" ${reviews.busy?'disabled':''}>${[['needs_more_evidence','需要补充证据'],['disagree_with_model','我有异议'],['agree_with_model','我认可这次判定']].map(([value,label])=>`<option value="${value}" ${reviews.form.decision===value?'selected':''}>${label}</option>`).join('')}</select>${[['quote_left','左侧原回答精确短引（必填）'],['quote_right','右侧原回答精确短引（必填）'],['reason','人工理由与待补证据（必填）']].map(([key,label])=>`<label for="review-${key}">${label}</label><textarea id="review-${key}" data-review-field="${key}" rows="3" ${reviews.busy?'disabled':''}>${esc(reviews.form[key])}</textarea>`).join('')}<label class="fusion-consent"><input id="review-confirmed" type="checkbox" data-review-field="confirmed" ${reviews.form.confirmed?'checked':''} ${reviews.busy?'disabled':''}>我已对照原题、所选两份回答与判据；来源窗口之外仍保持未核实</label><div class="fusion-actions"><button class="btn primary" type="button" data-fusion="review-validate" ${reviews.busy||!state.connected||!state.reviewsSupported?'disabled':''}>核对引文并生成审查记录</button><button class="btn subtle" type="button" data-fusion="review-discard" ${reviews.busy?'disabled':''}>放弃当前未保存意见</button></div>${reviews.receipt?`<div id="review-receipt"><p class="fusion-note">记录绑定原回答、证据报告及 ZIP 指纹；未修改模型评分或验收状态。</p><div class="fusion-actions"><button class="btn primary" type="button" data-fusion="review-save" ${reviews.saved?'disabled':''}>${reviews.saved?'已保存审查意见':'保存审查意见到成品库'}</button><button class="btn subtle" type="button" data-fusion="review-export">导出审查意见 JSON</button></div></div>`:''}</form></div>`:''}</section>`;
  }
  function bindReviews(){
    const panel=root()?.querySelector('#fusion-reviews');if(!panel)return;
    panel.querySelector('form')?.addEventListener('submit',event=>event.preventDefault());
    panel.oninput=event=>{
      const key=event.target.dataset.reviewField;if(!key)return;
      reviews.form[key]=event.target.type==='checkbox'?event.target.checked:event.target.value;
      reviews.receipt=null;reviews.saved=false;reviews.error='';
      reviews.message='意见已修改，请重新核对引文；尚未保存本次修改。';
      panel.querySelector('#review-status').textContent=reviews.message;
      panel.querySelector('[role="alert"]')?.remove();
      panel.querySelectorAll('[data-review-nav]').forEach(element=>element.disabled=reviewDraft()||reviews.busy||(Boolean(element.dataset.fusion)&&(!state.connected||!state.reviewsSupported)));
      panel.querySelector('#review-receipt')?.remove();
    };
    panel.onchange=event=>{
      const side=event.target.dataset.reviewSide;if(!side)return;
      if(reviewDraft()){event.target.value=reviews[side];return;}
      reviews[side]=event.target.value;reviews.form=reviewForm();reviews.receipt=null;reviews.saved=false;paintReviews();
    };
  }
  function paintReviews(){
    const panel=root()?.querySelector('#fusion-reviews');if(!panel)return;
    const hadFocus=panel.contains(document.activeElement),focusId=hadFocus?document.activeElement.id:null;
    if(reviews.busy&&focusId&&focusId!=='review-status')reviews.focusId=focusId;
    const restoreId=!reviews.busy&&focusId==='review-status'&&reviews.focusId?reviews.focusId:focusId;
    if(!reviews.busy)reviews.focusId=null;
    panel.outerHTML=reviewsView();bindReviews();
    const target=restoreId?document.getElementById(restoreId):null;
    if(hadFocus)(target&&!target.disabled?target:document.getElementById('review-status'))?.focus({preventScroll:true});
  }
  async function reviewAction(name,options){
    if(reviews.busy)return;reviews.error='';
    try{
      if(['review-refresh','review-task','review-validate'].includes(name)&&(!state.connected||!state.reviewsSupported))throw Error('当前服务未提供逐题证据接口；请保留任务结果，再用更新后的启动脚本重启服务。');
      if(['review-refresh','review-task'].includes(name)&&reviewDraft())throw Error('请先保存或明确放弃当前人工意见，避免更换证据后丢失。');
      if(name==='review-refresh'){
        reviews.busy=true;reviews.items=null;reviews.detail=null;reviews.receipt=null;paintReviews();
        const result=await api('reviews',{});reviews.items=result.experiments;
        reviews.message=`已核对 ${result.experiments.length} 批冻结证据；${result.invalid_count} 批缺失或未通过完整性检查。选择任务后才展示原始回答。`;
      }else if(name==='review-task'){
        const item=reviews.items.find(entry=>entry.id===options.id);if(!item)throw Error('请先刷新证据列表。');
        reviews.busy=true;reviews.detail=null;reviews.receipt=null;paintReviews();
        reviews.detail=await api('reviews/task',{id:item.id,task:options.task,evidence_sha256:item.evidence_sha256});
        reviews.left=reviews.detail.runs.find(row=>row.arm==='fusion').id;reviews.right=reviews.detail.runs.find(row=>row.arm==='A_only').id;
        reviews.form=reviewForm();reviews.saved=false;reviews.message='原题、原回答、评分与来源已加载；这不是新的模型调用，也未执行包内代码。';
      }else if(name==='review-discard'){
        reviews.form=reviewForm();reviews.receipt=null;reviews.saved=false;reviews.message='已明确放弃当前表单意见；原始实验与已保存记录没有修改。';
      }else if(name==='review-validate'){
        reviews.busy=true;reviews.receipt=null;paintReviews();
        reviews.receipt=await api('reviews/note',{id:reviews.detail.id,task:reviews.detail.task.id,evidence_sha256:reviews.detail.evidence_sha256,left:reviews.left,right:reviews.right,...reviews.form});
        reviews.message='精确引文与指纹已核对；请保存或导出你的审查意见。尚未完成人工验收。';
      }else if(name==='review-save'){
        if(!reviews.receipt)throw Error('请先核对引文并生成审查记录。');
        saveResult(reviews.receipt);reviews.saved=true;reviews.message='人工意见已另存到成品库；原模型判定、实验包和未验收状态保持不变。';
      }else if(name==='review-export'){
        if(!reviews.receipt)throw Error('请先核对引文并生成审查记录。');
        downloadJson(reviews.receipt,'fusion-review-'+reviews.receipt.task_id+'.json');reviews.message='已导出单题配对审查意见；不代表整批已审核或融合收益已验证。';
      }
    }catch(error){reviews.error=error.message||'证据读取或审查记录未完成；未改写原始实验。';reviews.message='本次操作未完成；原始实验与既有审查记录未修改。';}
    finally{reviews.busy=false;paintReviews();}
  }
  function deliverablesView(){
    const labels={experimental_unverified:'实验包 · 未验证',rejected:'实验包 · 已否决',inconclusive:'实验包 · 结论不充分',reviewed_smoke_pass:'实验包 · 冒烟记录（非收益验证）'};
    const items=deliverables.items||[];
    return `<section id="fusion-downloads" class="fusion-downloads" aria-labelledby="fusion-downloads-title"><span class="eyebrow">LOCAL SKILL DELIVERABLES</span><h2 id="fusion-downloads-title">本地已构建的实验 Skill</h2><p>这里只列出私有交付目录中经清单登记的已构建实验 Skill ZIP，不是候选方案的 JSON 导出。“发送给模型”不会自动编译或打包当前候选。浏览页面不会自动读取交付物，也不会调用模型或执行包内代码。</p><p class="fusion-deliverable-warning">尚未证明融合收益。AI 独立审查不等于人工验证；任何冒烟或审查通过记录都不代表收益已验证。</p><button id="fusion-refresh-deliverables" class="btn subtle" data-fusion="refresh-deliverables" ${deliverables.busy?'disabled':''}>刷新可下载 Skill</button><p id="fusion-download-status" class="fusion-note" role="status" aria-live="polite" tabindex="-1" aria-busy="${deliverables.busy}">${esc(deliverables.message)}</p>${deliverables.error?`<p class="fusion-error" role="alert">${esc(deliverables.error)}</p>`:''}${deliverables.invalid?`<p class="fusion-note">${esc(deliverables.invalid)} 项未通过格式、完整性或安全校验，未提供下载。</p>`:''}${deliverables.items&&!items.length?'<p>暂无通过完整性校验的可下载 Skill；生成方案不会自动生成 ZIP。</p>':''}<div class="fusion-deliverables">${items.map(item=>`<article class="fusion-deliverable" data-deliverable-id="${esc(item.id)}"><div class="fusion-candidate-heading"><span class="chip">${esc(labels[item.status]||'实验包 · 未验证')}</span><span class="fusion-note">版本 ${esc(item.version)}</span></div><h3>${esc(item.title)}</h3><p>${esc(item.description)}</p><h4>融合机制</h4><p>${esc(item.mechanism)}</p><h4>执行流程</h4>${item.workflow.length?`<ol class="fusion-workflow">${item.workflow.map(step=>`<li>${esc(step)}</li>`).join('')}</ol>`:'<p>未提供执行流程，请先人工审查。</p>'}<h4>限制与缺口</h4>${item.limitations.length?`<ul>${item.limitations.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:'<p>未提供限制说明，不代表没有限制。</p>'}<h4>证据记录 · 未经人工验证</h4><p class="fusion-note">清单声明的状态（非人工结论）：${esc(item.evidence.status)}</p><p class="fusion-evidence-conclusion">${esc(item.evidence.summary)}</p>${comparisonView(item.evidence)}<p class="fusion-note">报告声明：${esc(item.evidence.task_count)} 个任务 · ${esc(item.evidence.arm_count)} 个实验臂 · ${esc(item.evidence.run_count)} 次运行。数量不代表质量或融合收益。</p><p class="fusion-deliverable-warning">尚未证明融合收益 · AI 独立审查不等于人工验证</p><p class="fusion-note">后端已核对 ZIP 字节与包内逐文件 SHA-256；只证明完整性，不代表安全或效果验收。${esc(item.file_count)} 个文件 · ${esc(item.size_bytes.toLocaleString())} 字节</p><code class="fusion-fingerprint">SHA-256 ${esc(item.sha256)}</code><div class="fusion-actions"><button id="fusion-download-${esc(item.id)}" class="btn primary" data-fusion="download-deliverable" data-id="${esc(item.id)}" ${deliverables.busy||!state.connected?'disabled':''}>下载 Skill ZIP</button>${item.evidence_available?`<button id="fusion-evidence-${esc(item.id)}" class="btn subtle" data-fusion="download-evidence" data-id="${esc(item.id)}" ${deliverables.busy||!state.connected?'disabled':''}>下载证据 JSON</button>`:'<span class="fusion-note">没有可下载的独立证据报告。</span>'}</div></article>`).join('')}</div></section>`;
  }
  function paintDeliverables(){
    const panel=root()?.querySelector('#fusion-downloads');if(!panel)return;
    const active=panel.contains(document.activeElement)?document.activeElement:null;
    const focusId=active?.id;
    panel.outerHTML=deliverablesView();
    let target=focusId?root().querySelector('#'+CSS.escape(focusId)):null;
    if(target?.disabled){deliverableFocus=focusId;target=root().querySelector('#fusion-download-status');}
    if(focusId==='fusion-download-status'&&!deliverables.busy&&deliverableFocus){target=root().querySelector('#'+CSS.escape(deliverableFocus))||target;deliverableFocus=null;}
    if(target)target.focus({preventScroll:true});
  }
  async function deliverableAction(name,options){
    if(deliverables.busy)return;
    deliverables.error='';
    try{
      if(apiBase===null||!state.connected||!state.token)throw Error('下载需要本机融合服务。请使用“启动闪光点.cmd”或桌面版打开 App 并重新连接；离线版或非本机页面不能读取交付物。');
      deliverables.busy=true;deliverables.message=name==='refresh-deliverables'?'正在核对本机交付物的 ZIP 与文件指纹…':'正在重新校验下载内容…';
      if(name==='refresh-deliverables'){deliverables.items=null;deliverables.invalid=0;}
      paintDeliverables();
      if(name==='refresh-deliverables'){
        const data=await api('deliverables',{});
        if(!Array.isArray(data.deliverables))throw Error('本机服务未返回交付物列表，请更新启动服务后重试。');
        deliverables.items=data.deliverables;deliverables.invalid=data.invalid_count;
        deliverables.message=`已读取 ${data.deliverables.length} 个实际 ZIP；尚未证明融合收益。`;
      }else{
        const item=deliverables.items?.find(row=>row.id===options.id);
        if(!item||!/^[a-z0-9][a-z0-9-]{0,63}$/.test(item.id))throw Error('交付物列表已变化，请刷新后重试。');
        const evidence=name==='download-evidence';
        const response=await fetch(apiBase+'/api/deliverables/'+(evidence?'evidence':'download'),{method:'POST',headers:{'Content-Type':'application/json','X-Spark-Token':state.token},body:JSON.stringify({id:item.id}),cache:'no-store'});
        if(!response.ok){let data;try{data=await response.json()}catch{}throw Object.assign(Error(data?.error||'下载未完成，请刷新后重试。'),{status:response.status});}
        if(response.headers.get('Content-Type')?.split(';')[0]!==(evidence?'application/json':'application/zip'))throw Error('本机服务未返回正确的文件类型，未下载。');
        const blob=await response.blob();
        if(evidence){
          if(blob.size>512*1024)throw Error('证据报告超过大小限制，未下载。');
          JSON.parse(await blob.text());
        }else{
          if(blob.size!==item.size_bytes||response.headers.get('X-Spark-SHA256')!==item.sha256)throw Error('ZIP 与列表中的指纹或大小不一致，请刷新后重试；未下载。');
          const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer())),byte=>byte.toString(16).padStart(2,'0')).join('');
          if(digest!==item.sha256)throw Error('ZIP 指纹不一致，未下载；请刷新并检查交付物。');
        }
        downloadBlob(blob,item.id+(evidence?'-evidence.json':'.zip'));
        deliverables.message=`已发起${evidence?'证据 JSON':'Skill ZIP'}下载；AI 独立审查不等于人工验证，尚未证明融合收益。`;
      }
    }catch(error){
      deliverables.error=error instanceof TypeError?'无法连接本机下载服务，请检查启动服务后重新连接；离线浏览不会提供 ZIP。':error.message||'下载未完成，请刷新后重试。';
      deliverables.message='未提供新的下载；不会自动重试或调用模型。';
      deliverables.items=null;deliverables.invalid=0;
    }finally{deliverables.busy=false;paintDeliverables();}
  }
  function paint(){
    const host=root();if(!host)return;
    const active=host.contains(document.activeElement)?document.activeElement:null;
    const oldDetails=[...host.querySelectorAll('details')];
    const opened=oldDetails.map(el=>el.open);
    const focusedSummary=active?.tagName==='SUMMARY'?oldDetails.indexOf(active.parentElement):-1;
    const focusId=active?.id,focusAction=active?.dataset.fusion;
    if(focusId!=='fusion-status')busyFocusAction=null;
    const selection=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd,active.selectionDirection]:null;
    if(state.mode==='quick'){
      // **快速碰撞**：选两个包 → 单次确认 → 自动精简 + 融合。专家模式一键切回。
      const modes=`<div class="fusion-actions" style="margin:0 0 12px"><button class="btn subtle" data-fusion="set-mode" data-mode="quick">快速碰撞（选两个包即可）</button><button class="btn subtle" data-fusion="set-mode" data-mode="expert">专家模式 ✓</button></div>`;
      host.innerHTML=`<div class="page fusion-page"><datalist id="fusion-pack-sources">${(state.packSources||[]).map(p=>`<option value="${esc(p.path)}">${esc(p.name+' · '+(p.source==='skillhub'?'SkillHub':'本地'))}</option>`).join('')}</datalist><header class="fusion-header"><span class="eyebrow">SKILL FUSION / 模型融合</span><h1>从两种能力，<br><span>找到新的可能。</span></h1>${modes}</header><div id="fusion-status" class="fusion-status" tabindex="-1" role="status" aria-live="polite" ${state.pending?'aria-busy="true"':''}>${esc(state.message)}</div>${state.error?`<p class="fusion-error" role="alert">${esc(state.error)}</p>`:''}${!state.connected?'<button class="btn subtle" data-fusion="connect">重新连接本机服务</button>':''}<section class="fusion-panel fusion-quick"><canvas class="fusion-aurora" aria-hidden="true"></canvas><div class="fusion-quick-body"><span class="eyebrow">QUICK COLLISION / 快速碰撞</span><h2>选两个包，其余自动</h2><p class="fusion-note">自动精简上下文（SKILL.md 必留，逐文件留痕）；模型与接口来自「数据与偏好 → 调用模型」。审查轨迹全部保留，可随时切到专家模式逐步审查。</p><div class="fusion-grid">${field('quick-a','Skill A（路径，或下拉选择包源）','可下拉：本地 curated / SkillHub 包源')}${field('quick-b','Skill B','同上')}</div>${field('quick-goal','偏好或限制（选填）','留空＝自由探索，不预设结果',true)}<label class="fusion-field" for="fusion-thinking"><span>推理（MiniMax-M3 建议关闭；始终思考型模型必选强度）</span><select id="fusion-thinking" data-fusion-field="thinking" ${state.busy||state.pending?'disabled':''}><option value="" ${!state.form.thinking?'selected':''}>按服务商默认</option><option value="disabled" ${state.form.thinking==='disabled'?'selected':''}>关闭推理（推荐）</option><option value="enabled" ${state.form.thinking==='enabled'?'selected':''}>开启推理</option><option value="low" ${state.form.thinking==='low'?'selected':''}>推理强度 · 低</option><option value="high" ${state.form.thinking==='high'?'selected':''}>推理强度 · 中</option><option value="max" ${state.form.thinking==='max'?'selected':''}>推理强度 · 高</option></select></label><label class="fusion-consent"><input id="fusion-quick-consent" type="checkbox" ${state.busy||state.pending?'disabled':''}>我确认：碰撞会把所选材料发送给我配置的模型，并可能产生费用</label><div class="fusion-actions"><button class="btn primary" data-fusion="quick-run" ${!state.connected||state.busy||state.pending?'disabled':''}>${state.pending?'碰撞进行中…':'开始碰撞'}</button></div><p class="fusion-note">碰撞产出的是方案（不是已经运行的程序）；方案审查页可对每个候选「物化并自测」——生成真实文件包并运行自测，回答「能不能跑得动」。</p></div></section>${state.result?results():''}${state.pending?'<p class="fusion-note">模型判断通常需要几分钟；可以切页面，回来这里看进度。</p>':''}</div>`;
      if(window.SparkFX&&window.SparkFX.aurora)window.SparkFX.aurora(root().querySelector('.fusion-aurora'));
      for(const p of ['quick-a','quick-b'])host.querySelector('#fusion-'+p)?.setAttribute('list','fusion-pack-sources');
      return;
    }
    host.innerHTML=`<div class="page fusion-page"><datalist id="fusion-pack-sources">${(state.packSources||[]).map(p=>`<option value="${esc(p.path)}">${esc(p.name+' · '+(p.source==='skillhub'?'SkillHub':'本地'))}</option>`).join('')}</datalist><header class="fusion-header"><span class="eyebrow">SKILL FUSION / 模型融合</span><h1>从两种能力，<br><span>找到新的可能。</span></h1><p>带上完整材料，先看方向，再让模型解释双方如何产生新的作用。</p><ol class="fusion-steps" aria-label="融合步骤"><li class="active">1 材料</li><li class="${state.prepared?'active':''}">2 方向</li><li class="${state.pending?'active':''}">3 模型判断</li><li class="${state.result?'active':''}">4 方案审查</li></ol><div class="fusion-actions" style="margin:14px 0 0"><button class="btn subtle" data-fusion="set-mode" data-mode="quick">快速碰撞（选两个包即可）</button><button class="btn subtle" disabled>专家模式 ✓</button></div></header><div id="fusion-status" class="fusion-status" tabindex="-1" role="status" aria-live="polite" ${state.pending?'aria-busy="true"':''}>${esc(state.message)}</div>${state.error?`<p class="fusion-error" role="alert">${esc(state.error)}</p>`:''}${!state.connected?'<button class="btn subtle" data-fusion="connect">重新连接本机服务</button>':''}<section class="fusion-panel"><span class="eyebrow">01 / SOURCE MATERIALS</span><h2>完整材料，才有理解的基础</h2><p>填写本机 Skill 目录或 ZIP 路径${(state.packSources||[]).length?'；输入框可下拉选择本地 curated 包或已下载的 SkillHub 包':''}。目录内需要 SKILL.md，脚本只读取，不会执行。</p><div class="fusion-grid">${['A','B'].map(p=>`<article>${field(p.toLowerCase(),'Skill '+p+' 路径','C:\\Skills\\'+p)}${field('resources'+p,p+' 的关联资源（可选）','lib = C:\\Project\\lib\nprompts-data.js = C:\\Project\\prompts-data.js',true)}<p class="fusion-note">每行：包内路径 = 本机文件或目录路径。只读取明确关联的资源。</p>${field('gaps'+p,p+' 的已知缺口（可选）','每行一项，模型必须保留',true)}</article>`).join('')}</div>${field('goal','偏好或限制（选填）','不知道也可以留空；例如：优先探索学习场景，或只使用本地工具。',true)}${field('budget','请求字符预算（1000–1000000）','160000')}<p class="fusion-note">全量清点后再审查发送范围；所选正文超预算会阻断发送，不会截断材料。字符数不等于 token 数，请根据所用模型的上下文容量设置。</p><button class="btn primary" data-fusion="prepare" ${!state.connected||state.busy||state.pending?'disabled':''}>${state.busy?'正在处理…':'读取完整材料清单'}</button></section>${materials()}${model()}${results()}</div>`;
    for(const p of ['a','b'])host.querySelector('#fusion-'+p)?.setAttribute('list','fusion-pack-sources');
    host.querySelector('.fusion-header h1').outerHTML='<h2>两个 Skill，形成怎样的新能力？</h2>';
    host.querySelector('.fusion-header p').textContent='后端依次核查材料、提取双方能力、审查互补与冲突，再给出目标、机制、流程和验收条件。模型只辅助分析，不是被融合的对象。';
    host.querySelectorAll('.fusion-steps li')[2].textContent='3 机制分析';
    host.querySelector('.fusion-page').insertAdjacentHTML('beforeend',deliverablesView());
    host.querySelector('.fusion-page').insertAdjacentHTML('beforeend',reviewsView());bindReviews();
    if(state.documents)host.querySelector('.fusion-panel').innerHTML=selectedDocuments();
    else{
      host.querySelector('.fusion-panel > p').textContent='选择含 SKILL.md 的完整文件夹或 ZIP，连同脚本、知识、模板与测试一起清点。ZIP 可有一层外目录；无 SKILL.md 的普通项目暂不能直接当作 Skill，可通过关联资源补入。文件仅送到本机服务；不会执行脚本、安装依赖或自动发送模型。';
      for(const parent of ['A','B'])host.querySelector('#fusion-'+parent.toLowerCase()).closest('article').insertAdjacentHTML('afterbegin',packagePicker(parent));
    }
    host.querySelector('.fusion-panel').insertAdjacentHTML('afterend',coverage());
    host.querySelector('.fusion-panel h2').insertAdjacentHTML('afterend',`<label class="fusion-field" for="fusion-source-map"><span>导入已有材料配置 JSON（不含密钥）</span><input id="fusion-source-map" type="file" accept=".json,application/json" ${state.busy||state.pending?'disabled':''}></label><p class="fusion-note">复用已定位的目录与显式资源映射；导入只填写表单，不读取源包或发送模型。</p>`);
    const modelPanel=host.querySelector('#fusion-key')?.closest('.fusion-panel');
    if(modelPanel){
      modelPanel.querySelector('h2').textContent='后端分析服务 · 辅助生成 Skill 方案';
      const consent=modelPanel.querySelector('.fusion-consent');
      consent.insertAdjacentHTML('beforebegin',probeView());
      modelPanel.querySelector('.fusion-probe h3').textContent='先检查连接，再授权材料与费用';
      consent.insertAdjacentHTML('afterend',`<label class="fusion-consent"><input id="fusion-cost-consent" type="checkbox" ${!state.request||!state.probe?.basic_json_ok||state.busy||state.pending?'disabled':''}>我已核对供应商计费，接受本次模型请求费用（未设置费用上限）</label><p class="fusion-note">字符预算不是 token 或金额估算；本机未设置扣费硬上限。请核对服务商单价和模型容量。连接检查不证明融合质量，且不会自动授权发送材料。</p>`);
    }
    if(recoveryWarning)host.querySelector('#fusion-status').insertAdjacentHTML('afterend',`<p class="fusion-note" role="status">${esc(recoveryWarning)}</p>`);
    if(state.pending&&!state.prepared)host.querySelector('#fusion-status').insertAdjacentHTML('afterend',`<section class="fusion-panel"><h2>恢复中的融合任务</h2><p>仅恢复本标签页的任务编号，不保存材料正文、端点、密钥或发送授权。本机服务仍在运行且任务未被替换时，可以找回结果。</p><button class="btn subtle" data-fusion="cancel" ${!state.connected||state.busy?'disabled':''}>停止等待并丢弃返回结果</button><p class="fusion-note">停止等待不能撤回已发送内容或保证停止供应商计费；底层请求结束前不重复发送。</p></section>`);
    const newDetails=[...host.querySelectorAll('details')];
    newDetails.forEach((el,i)=>{if(oldDetails[i]?.querySelector('summary')?.textContent===el.querySelector('summary')?.textContent)el.open=opened[i]});
    let target=focusId?host.querySelector('#'+CSS.escape(focusId)):focusAction?host.querySelector('[data-fusion="'+CSS.escape(focusAction)+'"]'):focusedSummary>=0?newDetails[focusedSummary]?.querySelector('summary'):null;
    if(focusId==='fusion-status'&&busyFocusAction){
      const trigger=host.querySelector('[data-fusion="'+CSS.escape(busyFocusAction)+'"]');
      if(trigger&&!trigger.disabled){target=trigger;busyFocusAction=null;}
      else if(!state.busy&&!state.pending)busyFocusAction=null;
    }
    if(target?.disabled){busyFocusAction=focusAction||null;target=host.querySelector('#fusion-status');}
    if(target){target.focus({preventScroll:true});if(selection&&target.setSelectionRange)target.setSelectionRange(...selection)}
    host.onclick=e=>{const button=e.target.closest('[data-fusion]');if(button&&!button.disabled)action(button.dataset.fusion,button.dataset)};
    host.oninput=e=>{
      if(e.target.dataset.contextParent){
        const row=state.selection[e.target.dataset.contextParent].find(item=>item.path===e.target.dataset.contextPath);
        if(e.target.type==='checkbox')row.include=e.target.checked;else row[e.target.dataset.contextField||'reason']=e.target.value;
        invalidateContext();paint();return;
      }
      if(e.target.id==='fusion-key'){
        transientKey=e.target.value;
        for(const id of ['fusion-consent','fusion-cost-consent']){const consent=host.querySelector('#'+id);if(consent)consent.checked=false;}
        const send=host.querySelector('[data-fusion="run"]');if(send)send.disabled=true;
      }
      if(e.target.dataset.fusionField){
        const name=e.target.dataset.fusionField;state.form[name]=e.target.value;
        // 选厂商 = **帮你填**端点与模型名。两项都只是建议，用户随后可以随意改；
        // 换成「不选」就回到完全手填，不受目录限制。
        if(name==='provider'){
          const picked=PROVIDER_BY_ID[state.form.provider];
          if(picked){
            state.form.endpoint=picked[2];
            state.form.model=(picked[3]&&picked[3][0])||'';
            // 换服务商等于换端点，**旧密钥必须作废**——沿用它等于把 A 家的密钥发给 B 家。
            transientKey='';
            const ep=host.querySelector('#fusion-endpoint')||document.getElementById('fusion-endpoint');
            const md=host.querySelector('#fusion-model')||document.getElementById('fusion-model');
            const ky=host.querySelector('#fusion-key')||document.getElementById('fusion-key');
            if(ep)ep.value=picked[2];
            if(md)md.value=state.form.model;
            if(ky)ky.value='';
          }
        }
        if(name==='goal')updateGoalFeedback();
        // 推理开关会改变发出去的请求体，因此和端点/模型一样必须作废旧的连接检查结果。
        if(['endpoint','model','thinking'].includes(name)){state.probe=null;host.querySelector('.fusion-probe [role="status"]')?.remove();}
        if(name==='endpoint'){transientKey='';const key=host.querySelector('#fusion-key');if(key)key.value='';}
        if(!['endpoint','model','thinking'].includes(name)){
          forgetJob();
          state.importedReview=null;
          state.inventory=null;state.selection=null;state.source=null;state.probe=null;state.prepared=null;state.request=null;state.result=null;state.saved=false;
          // Leave the active input in place; remove stale downstream controls immediately.
          host.querySelectorAll('.fusion-panel').forEach((panel,i)=>{if(i>0&&panel.id!=='fusion-reviews')panel.remove()});
          state.message='材料已更改，请重新读取并计算方向。';host.querySelector('.fusion-status').textContent=state.message;
        }
        const consent=host.querySelector('#fusion-consent');if(consent)consent.checked=false;
        const cost=host.querySelector('#fusion-cost-consent');if(cost){cost.checked=false;cost.disabled=!state.probe?.basic_json_ok;}
        const probeConsent=host.querySelector('#fusion-probe-consent');if(probeConsent)probeConsent.checked=false;
        const probeButton=host.querySelector('[data-fusion="preflight"]');if(probeButton)probeButton.disabled=true;
        const send=host.querySelector('[data-fusion="run"]');if(send)send.disabled=true;
      }
    };
    host.onchange=e=>{if(e.target.dataset.uploadParent&&e.target.files.length)action('choose-package',{parent:e.target.dataset.uploadParent,kind:e.target.dataset.uploadKind,files:Array.from(e.target.files)});if(e.target.id==='fusion-source-map'&&e.target.files[0])action('source-map',{file:e.target.files[0]});if(['fusion-consent','fusion-cost-consent'].includes(e.target.id))host.querySelector('[data-fusion="run"]').disabled=!canSend();if(e.target.id==='fusion-probe-consent')host.querySelector('[data-fusion="preflight"]').disabled=!e.target.checked||state.busy||state.pending};
  }
  // 端点是数字回环（本机模型，如 Ollama / LM Studio）时不需要密钥——服务端本就允许空密钥；
  // 其余端点必须有。这是我加「模型接入」时一度堵死的能力。
  function resolveKey(endpoint){
    const key=transientKey||window.Spark.modelAccess.takeKey(endpoint);
    if(key)return key;
    if(/^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\//.test(endpoint||''))return '';
    throw Error('请填写密钥，或在「数据与偏好 → 调用模型」里保存本次会话的模型接入配置');
  }
  async function action(name,options={}){
    if(name.startsWith('review-')){await reviewAction(name,options);return;}
    if(['refresh-deliverables','download-deliverable','download-evidence'].includes(name)){await deliverableAction(name,options);return;}
    if(name==='connect'){connect();return}
    if(name==='set-mode'){state.mode=options.mode||'quick';state.message=state.mode==='quick'?'快速碰撞：选两个包，其余自动。专家逐步审查在「专家模式」。':'专家模式：逐层审查材料、上下文与请求。';paint();return}
    if(state.busy && name!=='quick-run')return;
    state.error='';
    if(name==='quick-run'){
      // **快速碰撞**（2026-10-11）：选两个包 → 自动精简 → 一次确认 → 融合。
      // 审查轨迹不消失：auto_select 的逐文件 reason 在服务端留痕，结果页照常
      // 展示 spark 读数与物化按钮；专家模式保留完整逐步审查。
      const a=String(state.form['quick-a']||'').trim();
      const b=String(state.form['quick-b']||'').trim();
      const goal=String(state.form['quick-goal']||'').trim();
      if(!a||!b)throw Error('请选择两个 Skill 包（可从下拉选择本地或 SkillHub 包源）');
      if(!root().querySelector('#fusion-quick-consent')?.checked)throw Error('请先确认：碰撞会把所选材料发送给你配置的模型，并可能产生费用');
      const access=window.Spark.modelAccess.read();
      if(!access.endpoint||!access.model)throw Error('请先在「数据与偏好 → 调用模型」里配置接口地址与模型（一次配置，长期使用）');
      const key=window.Spark.modelAccess.takeKey(access.endpoint);
      if(!key)throw Error('该接口需要密钥：请到「数据与偏好 → 调用模型」重新保存一次');
      state.busy=true;state.pending=true;state.result=null;state.saved=false;state.materializeReport=null;state.materializeBusy=null;
      state.message='正在读取两个包并自动精简上下文（SKILL.md 必留）；随后发送给模型。';paint();
      const report=await api('inventory',{a,b,goal,auto_select:true,lean_budget:30000});
      state.prepared=report;state.form.endpoint=access.endpoint;state.form.model=access.model;
      state.job=crypto.randomUUID().replaceAll('-','');rememberJob(state.job);
      const sent=await api('run',{request_id:state.job,packet_sha256:report.packet_sha256,endpoint:access.endpoint,model:access.model,api_key:key,thinking:state.form.thinking||null});
      state.job=sent.id;state.message='已发送给模型；你可以切换页面或刷新，回到这里查看进度。';schedulePoll(800);paint();
    }
    if(state.busy)return;
    if(name==='pick-package'){
      if(!state.pending)root().querySelector(`#fusion-${options.kind==='zip'?'zip':'folder'}-${options.parent}`).click();
      return;
    }
    if(name==='prepare'){
      state.goalError=goalError(state.form.goal);
      if(state.goalError){
        state.message='请检查选填的偏好或限制；尚未读取材料或发送模型。';paint();
        const goal=root().querySelector('#fusion-goal');goal.focus();goal.scrollIntoView({block:'center'});return;
      }
      if(!state.form.goal.trim()&&!state.explorationSupported){state.error='当前本机服务仍是旧版，尚不支持自由探索。请在当前任务结束后重启本机服务并重新连接；无需编造目标。';paint();return;}
      if(Object.values(state.uploads).some(Boolean)&&!state.uploadsSupported){state.error='当前本机服务尚不支持文件夹 / ZIP 选择导入。请在当前任务结束后重启服务并重新连接；所选文件尚未读取或发送。';paint();return;}
    }
    try{
      if(name==='choose-package'||name==='clear-upload'){
        if(state.pending)throw Error('任务结束前不能更换完整材料包');
        const parent=options.parent;
        if(name==='choose-package'){
          if(options.kind==='directory')uploadRows(options.files);
          else if(options.files.length!==1||!options.files[0].name.toLowerCase().endsWith('.zip')||options.files[0].size>16*1024*1024)throw Error('请选择不超过 16 MB 的 ZIP 包；其他压缩格式暂不支持。');
        }
        forgetJob();transientKey='';Object.assign(state,{documents:null,inventory:null,selection:null,importedReview:null,source:null,probe:null,prepared:null,request:null,result:null,saved:false,contextDirty:false});
        state.uploads[parent]=name==='clear-upload'?null:{kind:options.kind,files:options.files,name:options.kind==='directory'?options.files[0].webkitRelativePath.split('/')[0]:options.files[0].name};
        state.form[parent.toLowerCase()]='';state.form['resources'+parent]='';state.form['gaps'+parent]='';
        state.message='材料选择已更新，旧发送授权已撤销。尚未读取文件正文；请选好 A/B 后点击读取完整材料清单。';
      }else if(name==='source-map'){
        if(state.pending)throw Error('任务结束前不能更换材料配置');
        if(options.file.size>128*1024)throw Error('材料配置文件不得超过 128 KB');
        state.busy=true;paint();
        const config=JSON.parse(await options.file.text());
        if(!config||Array.isArray(config)||Object.keys(config).some(key=>!['a','b','goal','resources','known_gaps','max_context_chars','context_review'].includes(key))||!['a','b'].every(key=>typeof config[key]==='string'&&config[key].trim()))throw Error('材料配置仅允许 A/B 路径、目标、资源映射、缺口、预算与审查意见，不接受端点或密钥');
        const preference=config.goal??'';const invalidGoal=goalError(preference);if(invalidGoal)throw Error('材料配置：'+invalidGoal);
        const form={...state.form,a:config.a,b:config.b,goal:preference,budget:String(config.max_context_chars??160000)};
        for(const parent of ['A','B']){
          const resources=config.resources?.[parent]??[],gaps=config.known_gaps?.[parent]??[];
          if(!Array.isArray(resources)||resources.some(row=>!row||typeof row.mount!=='string'||typeof row.path!=='string'||/[\r\n=]/.test(row.mount)||/[\r\n]/.test(row.path))||!Array.isArray(gaps)||gaps.some(gap=>typeof gap!=='string'||/[\r\n]/.test(gap)))throw Error('资源映射或缺口格式不正确');
          form['resources'+parent]=resources.map(row=>row.mount+' = '+row.path).join('\n');form['gaps'+parent]=gaps.join('\n');
        }
        forgetJob();Object.assign(state,{form,uploads:{A:null,B:null},goalError:'',documents:null,inventory:null,selection:null,importedReview:config.context_review||null,source:null,probe:null,prepared:null,request:null,result:null,saved:false});
        transientKey='';state.message='已有材料配置已填入，尚未读取文件。请核对后点击读取完整材料清单。';
      }else if(name==='package-input'){
        if(state.pending)throw Error('任务结束前不能更换材料');
        forgetJob();transientKey='';Object.assign(state,{documents:null,inventory:null,selection:null,importedReview:null,source:null,probe:null,prepared:null,request:null,result:null,saved:false});
        state.message='已切换完整包输入。请选择文件夹 / ZIP、填写本机路径或导入已有映射，再重新审查；旧发送授权已撤销。';
      }else if(name==='export-coverage'){
        if(state.contextDirty)throw Error('请重新计算后再导出材料回执');
        downloadJson({type:'material-context-review',context_confirmed:Boolean(state.prepared),export_sends_no_model_request:true,report:state.inventory},'material-context-review.json');
      }else if(name==='prepare'){
        forgetJob();state.busy=true;state.inventory=null;state.selection=null;state.source=null;state.probe=null;state.prepared=null;state.request=null;state.result=null;state.saved=false;state.message='正在本机完整清点材料，不会因发送预算省略文件…';paint();
        const f=state.form;
        const uploads=state.documents?{}:await uploadPayload();
        const data=state.documents
          ?await api('documents',{documents:Object.fromEntries(Object.entries(state.documents).map(([parent,document])=>[parent,{body:document.body,sha256:document.sha256}])),goal:f.goal,max_context_chars:Number(f.budget)})
          :await api(Object.keys(uploads).length?'packages':'inventory',{a:f.a,b:f.b,...(Object.keys(uploads).length?{uploads}:{}),goal:f.goal,max_context_chars:Number(f.budget),resources:{A:mounts(f.resourcesA),B:mounts(f.resourcesB)},known_gaps:{A:lines(f.gapsA),B:lines(f.gapsB)}});
        state.inventory=data;state.selection=structuredClone(data.context_selection);state.contextDirty=false;state.message='全量清单已准备好。请审查每项正文及取舍理由，再确认上下文。';
        if(state.importedReview){
          if(matchingReview(data,state.importedReview)){state.selection=structuredClone(state.importedReview.selection);state.contextDirty=true;state.message='材料指纹一致，已载入已有分类与取舍意见；请回查并确认上下文，尚未授权模型发送。';}
          else state.message='已有审查与当前材料指纹或清单不一致，未套用旧选择；请重新审查完整清单。';
        }
      }else if(name==='context'){
        state.busy=true;state.prepared=null;state.request=null;state.result=null;forgetJob();paint();
        const data=await api('context',{inventory_sha256:state.inventory.inventory_sha256,selection:state.selection});
        state.inventory=data;state.contextDirty=false;state.prepared=data.budget_ok?data:null;
        state.message=data.budget_ok?'上下文已确认，方向只依据选中正文计算。请检查完整请求后再发送。':'所选正文仍超出预算，未截断也未发送。请根据材料用途重新审查整文件取舍，不要用扩大预算代替材料审查。';
      }else if(name==='omit-dependencies'){
        for(const rows of Object.values(state.selection))for(const row of rows)if(row.role==='third_party'){
          Object.assign(row,{include:false,reason:'采用依赖排除建议：本轮仅发送清单；第三方实现与运行依赖尚未验证 / Dependency inventory only, not verified'});
        }
        invalidateContext();
      }else if(name==='source'){
        state.busy=true;paint();state.source=await api('source',{inventory_sha256:state.inventory.inventory_sha256,parent:options.parent,path:options.path,offset:Number(options.offset||0)});
      }else if(name==='preflight'){
        if(!root()?.querySelector('#fusion-probe-consent')?.checked)throw Error('请先确认连接检查可能产生费用');
        const key=resolveKey(state.form.endpoint);transientKey='';state.probe=null;state.busy=true;paint();
        state.probe=await api('preflight',{endpoint:state.form.endpoint,model:state.form.model,api_key:key,allow_billable_probe:true,thinking:state.form.thinking||null});
        state.message='基础连接检查通过，未发送技能材料；请单独审查并授权完整请求。';      }else if(name==='preview'){
        state.busy=true;state.request=await api('preview',{packet_sha256:state.prepared.packet_sha256});state.message='完整请求已展开，检查后再允许发送。';
      }else if(name==='run'){
        if(!canSend())throw Error('请先检查连接、审查请求，并分别确认材料发送和费用');
        // 密钥只从「本次会话已配置」取用（设置页录入，内存中）；表单里的临时密钥优先。
        const input=root().querySelector('#fusion-key');
        const key=resolveKey(state.form.endpoint);
        input.value='';transientKey='';
        state.job=crypto.randomUUID().replaceAll('-','');rememberJob(state.job);state.busy=true;state.pending=true;state.result=null;state.saved=false;state.materializeReport=null;state.materializeBusy=null;state.message='模型正在综合判断；你可以切换页面或刷新，回到这里查看进度。';paint();
        const job=await api('run',{request_id:state.job,packet_sha256:state.prepared.packet_sha256,endpoint:state.form.endpoint,model:state.form.model,api_key:key,thinking:state.form.thinking||null});
        state.job=job.id;schedulePoll(500);
      }else if(name==='cancel'){
        await api('cancel',{id:state.job});state.message='已停止接收结果，等待底层请求结束；不会自动重试。';
      }else if(name==='fill'){
        // 第 5 类补足：带缺口清单再问模型一次；补完由服务端同一个核对器复评。
        const title=options.title;
        if(!root()?.querySelector('#fusion-fill-consent')?.checked)throw Error('请先确认补足请求会再次发送材料并可能产生费用');
        const key=window.Spark.modelAccess.takeKey(state.form.endpoint);
        if(!key)throw Error('补足需要密钥：请在「数据与偏好 → 调用模型」里保存本次会话的模型接入配置');
        state.fillBusy=title;paint();
        const job=await api('fill',{title,packet_sha256:state.prepared.packet_sha256,endpoint:state.form.endpoint,model:state.form.model,api_key:key,thinking:state.form.thinking||null});
        state.fillReport={...job,title};
        state.message='补足请求已发出；结果回来后会重新判定该方案属于哪一类。';
      }else if(name==='materialize'){
        // 机主裁定（2026-10-07）：「能跑得动就说明是正常的」——把方案生成真实
        // 文件包并运行 selftest；只回答「跑不跑得动」，不证明「比父本好」。
        const orientation=options.orientation;
        if(!root()?.querySelector('#fusion-materialize-consent')?.checked)throw Error('请先确认物化会再次发送材料并可能产生费用');
        const key=window.Spark.modelAccess.takeKey(state.form.endpoint);
        if(!key)throw Error('物化需要密钥：请在「数据与偏好 → 调用模型」里保存本次会话的模型接入配置');
        state.materializeBusy=orientation;paint();
        const job=await api('materialize',{orientation,packet_sha256:state.prepared.packet_sha256,endpoint:state.form.endpoint,model:state.form.model,api_key:key,thinking:state.form.thinking||null});
        state.message='物化请求已发出：生成包体后自动运行 selftest，通常需要几分钟。';paint();
        let status=job;
        while(status.status==='running'){await new Promise(r=>setTimeout(r,1500));status=await api('job',{id:job.id});}
        if(status.status==='completed'){state.materializeReport={...status.result,orientation};state.message='物化完成：包体已生成并运行自测。可运行只说明「跑得动」，不证明比父本好。';}
        else{state.error=status.error||'物化失败';state.message='物化失败，未产生可运行包体；可检查配置后重试。'}
        state.materializeBusy=null;paint();
      }else if(name==='save'){
        saveResult(state.result);state.saved=true;forgetJob();state.message='方案已保存到成品库，可随资料备份导出。';
      }else if(name==='export'){
        const url=URL.createObjectURL(new Blob([JSON.stringify(state.result,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='skill-fusion-proposal.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }
    }catch(e){state.error=e.message||'操作未完成';if(name==='run'){
        if(e.status){state.pending=false;forgetJob();}
        else{state.message='发送回执未收到，正在按请求编号查询原任务；不会自动重复发送。';schedulePoll(500);}
      }}finally{state.busy=false;paint()}
  }
  async function poll(){
    const id=state.job;if(!id||!state.pending)return;
    try{
      const job=await api('job',{id});
      if(state.job!==id)return;
      if(job.status==='completed'){state.error='';state.result=job.result;state.pending=false;state.message='模型方案已返回，来源与格式校验通过。请审查实际可用性。'}
      else if(job.status==='failed'){state.pending=false;forgetJob();state.error=job.error;state.message='本次请求失败，未保存结果；可以检查配置后手动重试。'}
      else if(job.status==='cancelled'&&!job.transport_pending){state.pending=false;forgetJob();state.message='请求已结束，返回结果已丢弃。'}
      schedulePoll(900);paint();
    }catch(e){
      if(state.job!==id)return;
      if(e.status===400){state.pending=false;forgetJob();state.message='原任务无法恢复；未自动发送模型请求。';state.error='本机服务没有可恢复的任务，可能已重启或材料已被替换。请重新准备材料并审查后手动发送。';}
      else{state.connected=false;state.error='任务状态连接中断，请重新连接本机服务。'+(e.message||'');}
      paint()
    }
  }
  window.SparkFusion={useSources,usePackages,resolveApiBase,activate(){if(!state.connected)connect()},mount(onSave){
    saveResult=onSave;restoreJob();
    const section=root()?.closest('#collision-backend');
    if(section&&(state.job||recoveryWarning))section.open=true;
    paint();if(section?.open&&!state.connected)connect();
  }};
})();
