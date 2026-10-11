(function(root){
  'use strict';
  // Each request owns a generation. A resumed context must never replay an old gesture.
  function createAudio({Context,enabled,visible=()=>true,now=()=>Date.now()}){
    let context,generation=0,lastPlayed=-Infinity;
    const active=new Set();
    function stop(){
      generation++;
      for(const source of active){try{source.stop()}catch{}try{source.disconnect()}catch{}}
      active.clear();
    }
    async function play(kind){
      stop();
      if(!Context||!enabled()||!visible()||!['collide','save'].includes(kind))return;
      const ticket=generation,requested=now();
      if(requested-lastPlayed<180)return;
      try{
        if(!context||context.state==='closed')context=new Context();
        if(context.state==='suspended')await context.resume();
        if(ticket!==generation||!enabled()||!visible()||now()-requested>250||context.state!=='running')return;
        // A finite buffer ends even when the main thread is occupied. No oscillator loop.
        const duration=kind==='collide'?.16:.09,length=Math.ceil(context.sampleRate*duration);
        const buffer=context.createBuffer(1,length,context.sampleRate),samples=buffer.getChannelData(0);
        for(let i=0;i<length;i++){
          const t=i/context.sampleRate,x=i/(length-1),envelope=Math.sin(Math.PI*x)**2*Math.exp(-4*x);
          const frequency=kind==='collide'?220:660;
          samples[i]=.12*envelope*(Math.sin(2*Math.PI*frequency*t)+.18*Math.sin(2*Math.PI*frequency*2*t));
        }
        const source=context.createBufferSource();source.buffer=buffer;source.loop=false;
        source.connect(context.destination);active.add(source);
        source.onended=()=>{active.delete(source);source.disconnect()};
        source.start();lastPlayed=now();
      }catch{stop()}
    }
    return {play,stop};
  }

  function createCollision({changed=()=>{},setTimer=setTimeout,clearTimer=clearTimeout}={}){
    let phase='ready',result=null,timer=null,generation=0;
    const notify=()=>changed({phase,result});
    function cancel(){generation++;if(timer!==null)clearTimer(timer);timer=null;phase='ready';result=null;notify()}
    function start(value,duration){
      if(phase==='running'||phase==='saving')return false;
      const ticket=++generation;result=null;phase='running';notify();
      const finish=()=>{if(ticket!==generation)return;timer=null;result=value;phase='result';notify()};
      if(duration>0)timer=setTimer(finish,duration);else finish();
      return true;
    }
    function save(persist){
      if(phase!=='result'||!result)return false;
      phase='saving';notify();
      try{persist(result);phase='saved';notify();return true}
      catch(error){phase='result';notify();throw error}
    }
    return {start,cancel,save,snapshot:()=>({phase,result})};
  }
  const api={createAudio,createCollision};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.SparkRuntime=api;
})(typeof window!=='undefined'?window:globalThis);
