(function(root){
  'use strict';
  const MAX_BYTES=256000,MAX_CHARS=180000;
  const LABELS={inputs:'输入',outputs:'输出',constraints:'约束',steps:'步骤',dependencies:'依赖'};
  const aliases={inputs:/^(inputs?|输入)(?:\s|$|[（(/:：])|^输入与参数$/,outputs:/^(outputs?|输出)(?:\s|$|[（(/:：])/,constraints:/^(constraints?|restrictions?|约束|限制)(?:\s|$|[（(/:：])/,steps:/^(steps?|workflow|instructions?|步骤|工作流|执行步骤|调用流程)(?:\s|$|[（(/:：])/,dependencies:/^(dependencies|requirements|依赖|依赖项|依赖安装)(?:\s|$|[（(/:：])/};
  function parse(text){
    if(typeof text!=='string'||!text.trim())throw Error('请提供非空的 SKILL.md 内容');
    if(text.length>MAX_CHARS||new TextEncoder().encode(text).length>MAX_BYTES)throw Error('单份 Skill 超过大小限制（180,000 字符 / 256 KB）');
    if(/[\u0000\uFFFD]/.test(text))throw Error('请选择有效的 UTF-8 Markdown 文件');
    const lines=text.replace(/^\uFEFF/,'').split(/\r?\n/),fields=Object.fromEntries(Object.keys(LABELS).map(k=>[k,[]])),warnings=[];
    let name='',description='',start=0;
    if(lines[0].trim()==='---'){
      const end=lines.findIndex((line,i)=>i>0&&line.trim()==='---');
      if(end<0)throw Error('文件头缺少结束的 ---');
      for(let i=1;i<end;i++){
        const match=lines[i].match(/^(name|description):\s*(.*)$/);if(!match)continue;
        let value=match[2].trim();
        if(/^[|>][-+]?$/.test(value)){value='';while(i+1<end&&/^\s+\S/.test(lines[i+1]))value+=(value?' ':'')+lines[++i].trim()}
        else value=value.replace(/^(['"])(.*)\1$/,'$2');
        if(match[1]==='name')name=value;else description=value;
      }
      start=end+1;
    }
    let section=null,level=0,fence=null;
    for(let i=start;i<lines.length;i++){
      const line=lines[i],marker=line.match(/^\s{0,3}(`{3,}|~{3,})/);
      if(marker){if(!fence)fence=marker[1];else if(marker[1][0]===fence[0]&&marker[1].length>=fence.length)fence=null;continue}
      if(fence)continue;
      const heading=line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
      if(heading){
        if(!name&&heading[1].length===1)name=heading[2];
        const title=heading[2].toLowerCase().trim(),kind=Object.keys(aliases).find(key=>aliases[key].test(title));
        if(kind){section=kind;level=heading[1].length}else if(heading[1].length<=level)section=null;
        continue;
      }
      if(!description&&!section&&line.trim())description=line.trim();
      if(section&&line.trim()){
        const text=line.trim().replace(/^(?:[-*+]\s+|\d+[.)]\s+)/,'').trim();
        if(text.length>280||fields[section].length>=6){warnings.push(LABELS[section]+'有超长或超量声明，未用于自动比较；请查看原文');continue}
        fields[section].push({text,line:i+1,quote:line.trim()});
      }
    }
    if(!name.trim()||name.length>160)throw Error('请用 name 文件头或一级标题提供 Skill 名称（不超过 160 字符）');
    for(const key of ['inputs','outputs','constraints'])if(!fields[key].length)warnings.push('未识别'+LABELS[key]+'声明，需要补充或人工确认');
    if(/\]\((?:\.\.?\/|scripts\/|references\/)/.test(text))warnings.push('引用的脚本或资料尚未导入，也不会执行');
    return {name,description:description.slice(0,1000),fields,warnings:[...new Set(warnings)]};
  }
  const normalize=s=>s.normalize('NFKC').trim().replace(/^`([^`]+)`$/,'$1').replace(/\s+/g,' ');
  function isSkill(record){return record?.kind==='document'&&record?.evidence?.type==='skill-source'&&record?.evidence?.schema===1}
  function compose(a,b){
    if(!isSkill(a)||!isSkill(b))throw Error('请选择两份已导入的 Skill');
    if(a.id===b.id||a.body===b.body)throw Error('请选择两份内容不同的 Skill');
    if(a.evidence.sha256!==fingerprint(a.body)||b.evidence.sha256!==fingerprint(b.body))throw Error('来源指纹不一致，请重新导入 Skill');
    const A=parse(a.body),B=parse(b.body),matches=[];
    for(const output of A.fields.outputs)for(const input of B.fields.inputs)if(normalize(output.text)===normalize(input.text))matches.push({output,input});
    const missing=B.fields.inputs.filter(input=>!matches.some(m=>m.input===input));
    const warnings=[...A.warnings.map(w=>'A：'+w),...B.warnings.map(w=>'B：'+w)];
    if(!matches.length)warnings.push('没有找到 A 输出与 B 输入的同名声明；暂不能认定可以直接串联');
    if(missing.length)warnings.push('B 尚有 '+missing.length+' 项输入需要外部补充');
    warnings.push('同名声明不代表格式、语义或运行环境兼容；约束与依赖仍须逐项核对');
    const refs=(side,items)=>items.map(x=>'- '+side+' L'+x.line+'：'+x.text).join('\n')||'- 未声明；待补充';
    const title=(A.name+' × '+B.name+' · 组合草稿').slice(0,160);
    const summary=matches.length?'找到 '+matches.length+' 组同名输入/输出声明，可作为 A → B 的候选衔接点。':'两份真实 Skill 已读取，但尚无可直接衔接的同名声明。';
    const sections=[`---\nname: spark-combination-${a.evidence.sha256.slice(0,8)}-${b.evidence.sha256.slice(0,8)}\ndescription: ${JSON.stringify(title+'，未执行的组合草稿，使用前须完成验收。')}\n---`, `# ${title}`,'> 本地声明规则分析 · 未调用模型 · 未执行 Skill · 待验证草稿',`## 组合目标\n先执行 A「${A.name}」，检查产物后再交给 B「${B.name}」。此顺序是候选流程，不代表已验证兼容。`,`## 原始用途\n- A：${A.description||'未声明'}\n- B：${B.description||'未声明'}`,
      '## 候选衔接\n'+(matches.map(m=>`- A L${m.output.line}「${m.output.text}」 ↔ B L${m.input.line}「${m.input.text}」：仅声明同名`).join('\n')||'- 无同名声明，先确认转换步骤或补全输入输出。'),
      '## 尚需补齐的 B 输入\n'+refs('B',missing),
      '## 输入\n'+refs('A',A.fields.inputs)+'\n'+refs('B',missing),
      '## 输出\n'+refs('B',B.fields.outputs),
      '## 约束\n'+refs('A',A.fields.constraints)+'\n'+refs('B',B.fields.constraints),
      '## 依赖\n'+refs('A',A.fields.dependencies)+'\n'+refs('B',B.fields.dependencies),
      '## 原始步骤线索\n'+refs('A',A.fields.steps)+'\n'+refs('B',B.fields.steps),
      '## 待验证工作流\n1. 核对 A/B 的全部原文、约束和外部依赖。\n2. 准备 A 的输入和 B 额外需要的输入。\n3. 人工运行 A，检查候选衔接产物的真实格式与内容。\n4. 必要时编写并测试转换步骤，再人工运行 B。\n5. 对 B 的结果按原技能要求验收；失败时记录原因，不标记成功。',
      '## 验收清单\n- [ ] A 的输入、依赖和约束均已满足\n- [ ] A 的输出符合 B 实际输入要求\n- [ ] B 的额外输入已补齐\n- [ ] 两侧约束已人工检查，没有未解决的冲突\n- [ ] 使用真实样例执行并保留证据',
      '## 未决事项\n'+warnings.map(w=>'- '+w).join('\n'),
      `## 来源\n- A：${a.id} / SHA-256 ${a.evidence.sha256||'未记录'}\n- B：${b.id} / SHA-256 ${b.evidence.sha256||'未记录'}\n- 行号对应本次保存的完整来源文本；人工补充如有追加，也包含在来源文本中。`];
    return {title,copy:summary,next:missing.length?'先补齐 B 的缺失输入，再核对候选衔接。':'先核对声明、约束与真实产物，再执行最小样例。',body:sections.join('\n\n'),parents:[a.id,b.id],sources:[{id:a.id,sha256:a.evidence.sha256||null},{id:b.id,sha256:b.evidence.sha256||null}],matches,warnings,contracts:[A,B],method:'skill-declarations-v1'};
  }
  function augment(text,notes){
    let out=text;for(const key of ['inputs','outputs','constraints'])if(notes[key]?.trim()){
      if(notes[key].length>1600)throw Error('每项补充不超过 1,600 字符');
      out+='\n\n## '+LABELS[key]+'（人工补充）\n'+notes[key].trim().split(/\r?\n/).map(line=>'- '+line.replace(/^[-*+]\s+/,'' )).join('\n');
    }
    parse(out);return out;
  }
  // SHA-256 over UTF-8; synchronous so backup validation can verify source snapshots.
  function fingerprint(text){
    const bytes=new TextEncoder().encode(text),length=Math.ceil((bytes.length+9)/64)*64,data=new Uint8Array(length);data.set(bytes);data[bytes.length]=128;
    const view=new DataView(data.buffer);view.setUint32(length-8,Math.floor(bytes.length/0x20000000));view.setUint32(length-4,(bytes.length*8)>>>0);
    const K=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const hash=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19],w=new Uint32Array(64),rotate=(x,n)=>(x>>>n)|(x<<(32-n));
    for(let offset=0;offset<length;offset+=64){
      for(let i=0;i<16;i++)w[i]=view.getUint32(offset+i*4);
      for(let i=16;i<64;i++){const x=w[i-15],y=w[i-2];w[i]=(w[i-16]+(rotate(x,7)^rotate(x,18)^(x>>>3))+w[i-7]+(rotate(y,17)^rotate(y,19)^(y>>>10)))>>>0}
      let [a,b,c,d,e,f,g,h]=hash;
      for(let i=0;i<64;i++){const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0,t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;h=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0}
      [a,b,c,d,e,f,g,h].forEach((v,i)=>hash[i]=(hash[i]+v)>>>0);
    }
    return hash.map(n=>n.toString(16).padStart(8,'0')).join('');
  }

  const api={fingerprint,MAX_BYTES,parse,compose,isSkill,augment,normalize,LABELS};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SparkSkills=api;
})(typeof window==='undefined'?globalThis:window);
