const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
function compile(file, imports) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname,file),'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','module','exports',code)(imports,module,module.exports); return module.exports;
}
function harness() {
  let token = 'fixture-user'; const sockets = [], posts = [], updates = [];
  const controller = new AbortController(), id = '84e33cbe-9c75-42ec-8848-a3053e3e52b1';
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url=url; this.readyState=0;this.bufferedAmount=0;this.sent=[];sockets.push(this); }
    open() {this.readyState=1;this.onopen?.();}
    send(data) {this.sent.push(data);}
    close() {this.closed=true;this.readyState=3;this.onclose?.();}
    event(value) {this.onmessage?.({data:JSON.stringify({request_id:id,...value})});}
  }
  const original = global.WebSocket; global.WebSocket=Socket;
  const imports = name => {
    if(name==='@/services/authService')return {default:{getToken:()=>token}};
    if(name==='@/stores/configStore')return {config:{api:{baseUrl:'https://speech.invalid'}}};
    return require(name);
  };
  const base = compile('../src/services/packageSpeechInputService.ts',imports);
  const api = compile('../src/services/packageRealtimeSpeechService.ts',name=>name==='@/services/packageSpeechInputService'?{...base,request:async (path,options)=>{posts.push({path,options});return {request_id:id,ticket:'x'.repeat(43),expires_in:30};}}:imports(name));
  const stream = api.startRealtimeSpeech({playId:'play-a',requestId:id,revision:4,channel:'PRIVATE',callId:'call-a',signal:controller.signal,onReady(){updates.push('ready');},onTranscript(text,confirmed){updates.push({text,confirmed});}});
  // Attach rejection handling immediately, mirroring the component.
  const completion = stream.result.then(value=>({value}),error=>({error}));
  return {stream,completion,controller,id,posts,sockets,updates,changeAuth(){token='other';},close(){stream.cancel();global.WebSocket=original;}};
}

test('realtime ticket travels in first frame; audio waits for READY; stop retains final result', async()=>{
  const h=harness();try{
    h.stream.push(new Int16Array([1,2]).buffer); await settle(); const ws=h.sockets[0];
    assert.ok(h.posts[0].path.includes('channel=PRIVATE&call_id=call-a'));
    assert.equal(h.posts[0].options.method,'POST'); assert.equal(h.posts[0].options.body,undefined);
    assert.ok(ws.url.startsWith('wss://speech.invalid/')); assert.ok(!ws.url.includes('?'));ws.open();
    assert.deepEqual(ws.sent,[JSON.stringify({ticket:'x'.repeat(43)})]);
    h.stream.stop();ws.event({type:'READY'});
    assert.ok(ws.sent[1] instanceof ArrayBuffer);assert.equal(ws.sent[2],JSON.stringify({type:'STOP'}));
    ws.event({type:'TRANSCRIPT',text:'先说',confirmed_text:''});ws.event({type:'TRANSCRIPT',text:'先说明。',confirmed_text:'先说明。'});
    ws.event({type:'RESULT',state:'OK',text:'先说明。',error_code:null});
    assert.equal((await h.completion).value.text,'先说明。');assert.equal(h.sockets.length,1);assert.equal(h.posts.length,1);assert.ok(ws.closed);
    assert.deepEqual(h.updates.slice(1),[{text:'先说',confirmed:''},{text:'先说明。',confirmed:'先说明。'}]);
  }finally{h.close();}
});

test('realtime disconnect, malformed results and auth changes fail without reconnect or draft mutations',async()=>{
  for(const mode of ['disconnect','wrong-id','oversize','auth','unexpected','backpressure']){
    const h=harness();try{
      await settle(); const ws=h.sockets[0];ws.open();ws.event({type:'READY'});
      if(mode==='disconnect')ws.close();
      if(mode==='wrong-id')ws.event({request_id:'other',type:'TRANSCRIPT',text:'secret',confirmed_text:''});
      if(mode==='oversize')ws.event({type:'TRANSCRIPT',text:'字'.repeat(6001),confirmed_text:''});
      if(mode==='auth'){h.changeAuth();ws.event({type:'TRANSCRIPT',text:'secret',confirmed_text:''});}
      if(mode==='unexpected')ws.event({type:'WRONG'});
      if(mode==='backpressure'){ws.bufferedAmount=160001;h.stream.push(new Int16Array([1]).buffer);}
      assert.ok((await h.completion).error);assert.deepEqual(h.updates,['ready']);assert.equal(h.posts.length,1);assert.equal(h.sockets.length,1);
    }finally{h.close();}
  }
});

test('realtime cancellation during ticket issuance makes no socket and startup queue is bounded',async()=>{
  const h=harness();try{h.controller.abort();await settle();assert.ok((await h.completion).error);assert.equal(h.sockets.length,0);}finally{h.close();}
  const bounded=harness();try{for(let n=0;n<11;n++)bounded.stream.push(new ArrayBuffer(16000));await settle();assert.ok((await bounded.completion).error);assert.equal(bounded.sockets.length,0);}finally{bounded.close();}
});

test('realtime partial is editable data while expired text is rejected',async()=>{
  for(const state of ['PARTIAL','EXPIRED']){
    const h=harness();try{await settle();const ws=h.sockets[0];ws.open();ws.event({type:'READY'});ws.event({type:'RESULT',state,text:'已完成一句。',error_code:'interrupted'});
      const outcome=await h.completion;assert.equal(Boolean(outcome.value),state==='PARTIAL');
    }finally{h.close();}
  }
});
