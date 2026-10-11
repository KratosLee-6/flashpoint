(function (root) {
  'use strict';
  const VERSION = 1;
  const Skills=typeof module!=='undefined'&&module.exports?require('./skills.js'):root.SparkSkills;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uid = () => 'spark-' + (globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2));
  const plain = (s, max = 1000) => typeof s === 'string' && s.length <= max;
  function validateRecord(r) {
    if (!r || typeof r !== 'object' || !plain(r.id,180) || !r.id || !plain(r.title,300) || !r.title.trim()) throw Error('资料缺少有效的编号或标题');
    for (const [key,max] of [['body',200000],['summary',4000],['project',200],['source',500],['kind',30],['created',100],['updated',100]]) if (!plain(r[key],max)) throw Error('资料字段不完整：'+key);
    if (!['session','document','note','collision','file'].includes(r.kind)) throw Error('资料类型不支持');
    if (!['inbox','final','archived'].includes(r.status) || typeof r.favorite !== 'boolean') throw Error('资料状态无效');
    if (!Array.isArray(r.tags) || r.tags.length>40 || !r.tags.every(t=>plain(t,180))) throw Error('标签格式无效');
    if (!Array.isArray(r.parents) || r.parents.length>10 || !r.parents.every(t=>plain(t,180))) throw Error('来源关系无效');
    if (r.score !== null && (!Number.isFinite(r.score) || r.score<0 || r.score>10)) throw Error('归档分须为 0–10 或空值');
    if (!r.evidence || typeof r.evidence !== 'object' || Array.isArray(r.evidence) || JSON.stringify(r.evidence).length>30000) throw Error('来源依据无效');
    if(r.evidence.type==='skill-source'){if(!Skills.isSkill(r)||r.evidence.sha256!==Skills.fingerprint(r.body))throw Error('Skill 来源内容与指纹不一致');Skills.parse(r.body)}
    if(r.evidence.method==='skill-declarations-v1'){const refs=r.evidence.sources;if(r.kind!=='collision'||r.parents.length!==2||new Set(r.parents).size!==2||!Array.isArray(refs)||refs.length!==2||r.evidence.executed!==false||!refs.every((ref,i)=>ref&&ref.id===r.parents[i]&&/^[a-f0-9]{64}$/.test(ref.sha256)))throw Error('双 Skill 来源记录不完整')}
    return r;
  }
  function validateState(s) {
    if (!s || s.version!==VERSION || !Array.isArray(s.records) || s.records.length>20000) throw Error('备份版本或资料列表不正确');
    s.records.forEach(validateRecord);
    const ids = new Set(s.records.map(r=>r.id));
    if(ids.size!==s.records.length) throw Error('备份含重复资料编号');
    const visiting=new Set(),done=new Set(),records=new Map(s.records.map(r=>[r.id,r]));
    function visit(id,depth=0){if(depth>200)throw Error('来源关系层级过深');if(visiting.has(id))throw Error('来源关系形成循环');if(done.has(id))return;visiting.add(id);for(const p of records.get(id).parents){if(!ids.has(p))throw Error('备份缺少碰撞的原始资料');visit(p,depth+1)}visiting.delete(id);done.add(id)}
    ids.forEach(id=>visit(id));
    for(const record of s.records)if(record.evidence.method==='skill-declarations-v1')for(const ref of record.evidence.sources){const source=records.get(ref.id);if(!Skills.isSkill(source)||source.evidence.sha256!==ref.sha256)throw Error('组合草稿的来源版本不匹配')}
    if(!Array.isArray(s.activity)||s.activity.length>500||!s.activity.every(a=>a&&plain(a.title,500)&&plain(a.at,100))) throw Error('活动记录无效');
    if(!s.settings || !['paper','forest','night'].includes(s.settings.theme) || typeof s.settings.motion!=='boolean' || typeof s.settings.sound!=='boolean') throw Error('偏好设置无效');
    if(!Number.isInteger(s.revision)||s.revision<0) throw Error('数据版本号无效');
    return s;
  }
  function makeRecord(fields={}) {const now=new Date().toISOString();return validateRecord({id:uid(),title:'未命名灵感',body:'',summary:'',kind:'note',project:'',source:'手动记录',created:now,updated:now,status:'inbox',favorite:false,tags:[],parents:[],score:null,evidence:{},...fields});}
  function initial(seed) {return {version:VERSION,revision:0,records:seed.records.map(r=>({...r,favorite:false,status:'inbox'})),activity:[],settings:{theme:'paper',motion:true,sound:false}};}
  function select(records,filters={}) {
    const term=String(filters.query||'').trim().toLocaleLowerCase();
    let out=records.filter(r=>(filters.status==='archived'?r.status==='archived':r.status!=='archived') &&
      (!filters.kind||r.kind===filters.kind) && (!filters.project||r.project===filters.project) &&
      (filters.status!=='final'||r.status==='final') && (filters.status!=='inbox'||r.status==='inbox') &&
      (filters.status!=='favorites'||r.favorite) && (!term||[r.title,r.body,r.summary,r.project,r.source,...r.tags].join(' ').toLocaleLowerCase().includes(term)));
    return out.sort(filters.sort==='score'?(a,b)=>(b.score??-1)-(a.score??-1):filters.sort==='title'?(a,b)=>a.title.localeCompare(b.title,'zh'): (a,b)=>b.updated.localeCompare(a.updated));
  }
  function stats(records) {const live=records.filter(r=>r.status!=='archived');return {total:live.length,sessions:live.filter(r=>r.kind==='session').length,documents:live.filter(r=>r.kind==='document').length,final:live.filter(r=>r.status==='final').length,favorites:live.filter(r=>r.favorite).length,projects:new Set(live.map(r=>r.project).filter(Boolean)).size,collisions:live.filter(r=>r.kind==='collision').length};}
  function exportPayload(state) {return {format:'sparkroom-backup',exportedAt:new Date().toISOString(),state:validateState(state)};}
  function readBackup(text) {if(typeof text!=='string')throw Error('备份内容必须是文本');if(new TextEncoder().encode(text).byteLength>40000000)throw Error('备份超过 40 MB');let value;try{value=JSON.parse(text)}catch{throw Error('文件不是有效的 JSON')};if(!value || value.format!=='sparkroom-backup')throw Error('请选择闪光点导出的完整备份');return validateState(value.state);}
  function markdown(text) {
    const lines=String(text).replace(/\r/g,'').split('\n');let code=false,html='';
    for(const line of lines){if(line.startsWith('```')){html+=code?'</code></pre>':'<pre><code>';code=!code;continue;}if(code){html+=escape(line)+'\n';continue;}
      let safe=escape(line).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
      const h=line.match(/^(#{1,4})\s/);if(h){html+=`<h${h[1].length+1}>${safe.slice(h[1].length+1)}</h${h[1].length+1}>`;}
      else if(/^[-*] /.test(line))html+='<p class="md-bullet">• '+safe.slice(2)+'</p>';
      else if(line.startsWith('> '))html+='<blockquote>'+safe.slice(5)+'</blockquote>';
      else if(/^---+$/.test(line))html+='<hr>';
      else if(line.trim())html+='<p>'+safe+'</p>';
    }return html+(code?'</code></pre>':'');
  }
  // 模型接入（39 号文 B3）：设置页录入，碰撞工作区取用。
  // 硬约束：只存在于本机内存，**不写入资料库、不写入 localStorage/sessionStorage、
  // 不进日志、不随备份导出**。换端点即作废密钥——旧密钥不跨端点复用。
  let access=null;
  const modelAccess={
    read(){return access?{endpoint:access.endpoint,model:access.model,hasKey:Boolean(access.key)}:{endpoint:'',model:'',hasKey:false}},
    set({endpoint,model,key}){
      const next={endpoint:String(endpoint||'').trim(),model:String(model||'').trim(),key:String(key||'')};
      if(access&&access.endpoint&&access.endpoint!==next.endpoint)next.key=''; // 换端点不沿用旧密钥
      access=next.endpoint||next.model||next.key?next:null;
      return modelAccess.read();
    },
    takeKey(endpoint){return access&&access.endpoint===endpoint?access.key:''},
    clear(){access=null}
  };
  const api={VERSION,escape,uid,validateRecord,validateState,makeRecord,initial,select,stats,exportPayload,readBackup,markdown,modelAccess};
  if(typeof module!=='undefined' && module.exports)module.exports=api;else root.Spark=api;
})(typeof window==='undefined'?globalThis:window);
