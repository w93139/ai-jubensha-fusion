const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
function compile(relative, resolve) {
 const filename = path.join(__dirname, '../src', relative);
 const {outputText} = ts.transpileModule(fs.readFileSync(filename,'utf8'), {fileName:filename,compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}});
 const module={exports:{}}; new Function('require','module','exports',outputText)(resolve,module,module.exports); return module.exports;
}
const row = (id, settled=false) => ({play_id:`play-${id}`,opening_session_id:`opening-${id}`,title:'虚构故事',character_name:'虚构角色',phase_label:settled?'已结束':'共同调查',settled,revision:3,updated_at:'2026-09-09T01:02:00Z'});
const tick = () => new Promise(resolve=>setImmediate(resolve));
const nodes = (tree,type) => Array.isArray(tree)?tree.flatMap(n=>nodes(n,type)):!React.isValidElement(tree)?[]:[...(tree.type===type?[tree]:[]),...nodes(tree.props.children,type)];
const press = (tree,text) => { const button=nodes(tree,'button').find(n=>n.props.children===text); assert.ok(button, text); return button.props.onClick(); };
function hookRunner() {
  const slots = [], effects = [], pending = []; let index = 0;
  const hooks = {
    useMemo(factory) { return factory(); },
    useCallback(callback) { return callback; },
    useSyncExternalStore(subscribe, read, server) { return server(); },
    useReducer(reducer, initial, init) { const [value, setValue] = hooks.useState(() => init ? init(initial) : initial); return [value, action => setValue(state => reducer(state, action))]; },
    useState(initial) {
      const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial;
      return [slots[slot], value => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value; }];
    },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
    useEffect(callback, deps) {
      const slot = index++;
      if (!effects[slot] || deps.some((value, offset) => value !== effects[slot].deps[offset])) pending.push(() => {
        effects[slot]?.cleanup?.(); effects[slot] = { deps, cleanup: callback() };
      });
    },
  };
  return { hooks, run(callback) { index = 0; const value = callback(); while (pending.length) pending.shift()(); return value; }, unmount() { for (const effect of effects) effect?.cleanup?.(); } };
}

function harness({library=async()=>({items:[],has_more:false}),releases=async()=>[],recordsOnly=false}={}) {
 const runner=hookRunner(); let current=true, invalidate; let state={isAuthenticated:true,isLoading:false,user:{id:'a'}};
 const module=compile('components/PlayHome.tsx',name=>{
  if(name==='react')return {...React,...runner.hooks};
  if(name==='next/link')return {default:({children,...props})=>React.createElement('a',props,children)};
  if(name==='@/components/AppLayout')return {default:({children})=>children};
  if(name==='@/stores/authStore')return {useAuthStore:()=>state};
  if(name==='@/services/packagePlayService')return {default:{library},watchPackagePlayAuth:callback=>{invalidate=()=>{current=false;callback();};return {isCurrent:()=>current,dispose(){}};}};
  if(name==='@/services/packagePreviewService')return {default:{releases}};
  return require(name);
 });
 return { render:()=>runner.run(()=>module.PlayerLibrary({recordsOnly})), home:()=>runner.run(()=>module.default({recordsOnly})), invalidate:()=>invalidate(), state:value=>state=value,unmount:runner.unmount };
}
test('homepage starts with loading then an honest empty state, with no game creation',async()=>{
 let calls=0;const h=harness({library:async()=>{calls++;return {items:[],has_more:false};}});
 try {assert.match(renderToStaticMarkup(h.render()),/正在读取游戏记录/);await tick();const html=renderToStaticMarkup(h.render());assert.match(html,/还没有开始过游戏/);assert.match(html,/href="\/play\/package-preview"/);assert.equal(calls,1);} finally{h.unmount();}
});
test('latest ongoing record continues its exact play, settled record opens ending',async()=>{
 for(const settled of [false,true]){const h=harness({library:async()=>({items:[row('saved',settled)],has_more:false})});try{h.render();await tick();const html=renderToStaticMarkup(h.render());assert.match(html,/href="\/play\/package-play\?play=play-saved"/);assert.match(html,settled?/查看结局/:/继续游戏/);assert.doesNotMatch(html,/M3|Token|费用上限/);}finally{h.unmount();}}
});
test('library failure is distinct from empty and retry makes a new readonly request',async()=>{
 let calls=0;const h=harness({library:async()=>{if(++calls===1)throw Error('PRIVATE');return {items:[row('restored')],has_more:false};}});try{h.render();await tick();let tree=h.render();assert.match(renderToStaticMarkup(tree),/暂时无法读取游戏记录/);assert.doesNotMatch(renderToStaticMarkup(tree),/还没有开始|PRIVATE/);await press(tree,'重试读取记录');assert.match(renderToStaticMarkup(h.render()),/play-restored/);assert.equal(calls,2);}finally{h.unmount();}
});
test('paged records preserve earlier rows, deduplicate shifted rows, and reject concurrent clicks',async()=>{
 let resolvePage;const calls=[];const h=harness({recordsOnly:true,library:async(offset,limit,signal)=>{calls.push({offset,limit,signal});return offset===0?{items:[row('a'),row('b')],has_more:true}:new Promise(resolve=>resolvePage=resolve);}});
 try{h.render();await tick();const tree=h.render();const promise=press(tree,'加载更多记录');press(tree,'加载更多记录');assert.equal(calls.length,2);assert.equal(calls[1].offset,2);resolvePage({items:[row('b'),row('c')],has_more:false});await promise;await tick();const html=renderToStaticMarkup(h.render());assert.equal((html.match(/play-b"/g)||[]).length,1);assert.match(html,/play-a/);assert.match(html,/play-c/);assert.doesNotMatch(html,/加载更多记录/);}finally{h.unmount();}
});
test('token invalidation clears loaded content, aborts requests and ignores late responses',async()=>{
 let resolve,signal;const h=harness({library:async(o,l,s)=>{signal=s;return new Promise(r=>resolve=r);},releases:async()=>[{id:1,title:'当前故事',characters:[],player_count:5}]});
 try{h.render();await tick();h.invalidate();assert.equal(signal.aborted,true);resolve({items:[row('secret')],has_more:false});await tick();const html=renderToStaticMarkup(h.render());assert.match(html,/登录状态已变化/);assert.doesNotMatch(html,/secret|当前故事|虚构故事/);}finally{h.unmount();}
});
test('unmount aborts pending library and no stale data can be applied',async()=>{
 let signal;const h=harness({library:async(o,l,s)=>{signal=s;return new Promise(()=>{});}});h.render();h.unmount();assert.equal(signal.aborted,true);
});
test('guests get login return path and no private library component',()=>{
 const h=harness();h.state({isAuthenticated:false,isLoading:false,user:null});const tree=h.home();const html=renderToStaticMarkup(tree);assert.match(html,/登录并继续/);assert.match(html,/returnUrl=%2F/);assert.doesNotMatch(html,/我的游戏记录|正在读取游戏记录/);h.unmount();
});
test('new game catalogue displays title only and points to explicit release selection',async()=>{
 const h=harness({releases:async()=>[{id:42,title:'虚构故事',content_version:'m3-internal',player_count:5,characters:[{id:'a',name:'甲'}]}]});try{h.render();await tick();const html=renderToStaticMarkup(h.render());assert.match(html,/package-preview\?release_id=42/);assert.doesNotMatch(html,/m3-internal/);assert.match(html,/5 个角色/);}finally{h.unmount();}
});
test('catalogue errors cannot be presented as no available stories',async()=>{
 const h=harness({releases:async()=>{throw Error('PRIVATE');}});try{h.render();await tick();const html=renderToStaticMarkup(h.render());assert.match(html,/暂时无法读取可玩的剧本/);assert.doesNotMatch(html,/暂时没有可玩的剧本|PRIVATE/);}finally{h.unmount();}
});
function service(token=()=> 'synthetic-token') {return compile('services/packagePlayService.ts',name=>name==='@/stores/configStore'?{config:{api:{baseUrl:'https://fixture.invalid'}}}:name==='@/services/authService'?{default:{getToken:token}}:require(name));}
test('library transport authenticates no-store, uses only GET and validates metadata',async t=>{
 const s=service();let call;t.mock.method(global,'fetch',async(...args)=>{call=args;return{ok:true,json:async()=>({success:true,data:{items:[row('saved')],has_more:false}})};});const controller=new AbortController();assert.equal((await s.default.library(12,12,controller.signal)).items.length,1);assert.equal(call[0],'https://fixture.invalid/api/fusion/package-play-library?offset=12&limit=12');assert.equal(call[1].method,undefined);assert.equal(call[1].cache,'no-store');assert.equal(call[1].headers.Authorization,'Bearer synthetic-token');assert.equal(call[1].signal,controller.signal);
 for(const args of [[-1,12],[0,51],[NaN,12]])await assert.rejects(s.default.library(...args));
});
test('malformed library IDs, duplicate rows and incompatible state fail closed',()=>{
 const check=service().checkedPlayLibrary;
 for(const bad of [{items:[{...row('a'),play_id:'javascript:bad'}],has_more:false},{items:[row('a'),row('a')],has_more:false},{items:[{...row('a'),settled:true}],has_more:false},{items:[],has_more:true},{items:[{...row('a'),updated_at:'bad'}],has_more:false}])assert.throws(()=>check(bad));
});
test('library discards a response after identity changes while reading JSON',async t=>{
 let token='a';const s=service(()=>token);t.mock.method(global,'fetch',async()=>({ok:true,json:async()=>{token='b';return{success:true,data:{items:[row('secret')],has_more:false}};}}));await assert.rejects(s.default.library(),error=>error.status===401);
});
test('legacy homepage aliases use current flow with no old scripts request or redirect',()=>{
 for(const name of ['pages/index.tsx','pages/script-center.tsx','pages/play/index.tsx']){const source=fs.readFileSync(path.join(__dirname,'../src',name),'utf8');assert.match(source,/@\/components\/PlayHome/);assert.doesNotMatch(source,/hasVisitedHome|ScriptsService|fusionGameService/);}
});
test('mobile menu can close from its trigger with Escape and hidden links become inert', t=>{
 const runner=hookRunner();let focused=false,prevented=false;
 const original=global.document;global.document={querySelector:()=>({focus(){focused=true;}})};t.after(()=>{if(original===undefined)delete global.document;else global.document=original;});
 const C=compile('components/AppLayout.tsx',name=>{
  if(name==='react')return {...React,...runner.hooks};
  if(name==='next/router')return {useRouter:()=>({pathname:'/',push(){}})};
  if(name==='next/link')return {default:()=>null};
  if(name==='@/stores/authStore')return {useAuthStore:()=>({isAuthenticated:true})};
  if(name==='@/lib/utils')return {cn:(...p)=>p.filter(Boolean).join(' ')};
  if(name==='@/components/DockBar'||name==='@/components/UserMenu')return {default:()=>null};
  if(name==='@/components/ui/button')return {Button:'button'};
  return require(name);
 }).default;
 try{let tree=runner.run(()=>C({children:'fixture'}));let nav=nodes(tree,'div').find(n=>n.props['aria-label']==='手机导航');assert.equal(nav.props.inert,true);
 nodes(tree,'button').find(n=>n.props['aria-label']==='打开导航').props.onClick();tree=runner.run(()=>C({children:'fixture'}));assert.equal(nodes(tree,'div').find(n=>n.props['aria-label']==='手机导航').props.inert,false);
 tree.props.onKeyDown({key:'Escape',preventDefault(){prevented=true;}});tree=runner.run(()=>C({children:'fixture'}));assert.equal(nodes(tree,'div').find(n=>n.props['aria-label']==='手机导航').props.inert,true);assert.ok(focused&&prevented);
 }finally{runner.unmount();}
});
