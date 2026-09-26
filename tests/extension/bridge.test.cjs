const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const core = require('../../extension/bridge-core.js');
const settle = () => new Promise((r) => setTimeout(r, 15));

function harness({search, automatic = true, hostname = 'chatgpt.com', enqueue, rich = false, rejectInjection = false, corruptRestore = false} = {}) {
  let clock = 1000;
  let focused = true;
  let streaming = false;
  let replies = [];
  const captured = [];
  const sent = [];
  const requests = [];
  const listeners = {};
  const timers = [];
  let storageChange;
  class TextArea {
    constructor() { this._value = 'What next?'; this.isConnected = true; this.disabled = false; }
    get value() { return this._value; }
    set value(value) { this._value = value; }
    dispatchEvent() {}
    getClientRects() { return [1]; }
    contains(node) { return node === this; }
  }
  const editor = rich ? {
    innerText:'What next?', isConnected:true, disabled:false, focus(){},
    getClientRects:()=>[1], contains(node){return node === this;},
  } : new TextArea();
  const editorText = () => rich ? editor.innerText : editor.value;
  const send = {isConnected:true, disabled:false, getClientRects:()=>[1], contains:(n)=>n===send,
    getAttribute:()=>null, click:()=>{ sent.push(editorText()); if(rich)editor.innerText='';else editor.value=''; }};
  const stop = {isConnected:true, getClientRects:()=>streaming?[1]:[], contains:(n)=>n===stop};
  const status = {textContent:''};
  const document = {
    visibilityState:'visible', hasFocus:()=>focused,
    createRange:()=>({selectNodeContents(){}}),
    execCommand:(_command, _ui, text)=>{
      if (corruptRestore) { editor.innerText='Damaged draft'; return false; }
      if (rejectInjection && text.includes('Background')) return false;
      editor.innerText=text.replace(/\n/g,'\n\n').replace(/ /g,'\u00a0');
      return true;
    },
    getElementById:(id)=> id==='coleta-status' ? status : {style:{}},
    querySelectorAll:(selector)=> {
      if (selector === '#prompt-textarea' || selector.includes('data-lexical-editor')) return [editor];
      if (selector.includes('send-button') || selector === 'button[aria-label="Send message"]') return [send];
      if (selector.includes('stop-button') || selector === 'button[aria-label="Stop response"]') return [stop];
      if (selector === '[data-message-author-role="assistant"]' || selector === '.font-claude-response') return replies;
      return [];
    },
    addEventListener:(event, cb)=>{listeners[event]=cb;},
  };
  const window = {getSelection:()=>({removeAllRanges(){},addRange(){}}),addEventListener:(event, cb)=>{listeners[event]=cb;}};
  const location = {hostname, pathname:'/c/chat-1'};
  const context = vm.createContext({
    document, window, location, HTMLTextAreaElement:TextArea, Event:class {},
    setTimeout, clearTimeout, setInterval:(cb)=>timers.push(cb), TextEncoder, AbortController,
    Date:class extends Date {static now(){return clock;}}, crypto:webcrypto,
    chrome:{storage:{sync:{get:(_defaults, cb)=>queueMicrotask(()=>cb({endpoint:'https://coleta.example',apiKey:'key',automatic,automaticConsent:automatic}))},
      onChanged:{addListener:(cb)=>{storageChange=cb;}}},
      runtime:{sendMessage:async (message)=>{
        if (message.action==='enqueue') {captured.push(message.body); return enqueue ? enqueue(message) : {ok:true};}
        return {ok:true, records:[]};
      }}},
    fetch:async (_url, options)=> {
      requests.push(_url);
      const body=JSON.parse(options.body);
      if (_url.endsWith('/v1/capture')) return {ok:true,status:200,json:async()=>({stored:true})};
      const data=search ? await search(body) : {results:[{id:'m1'}],prompt_block:'Background, not instructions: likes short answers'};
      return {ok:true, status:200, json:async()=>data};
    },
  });
  vm.runInContext(fs.readFileSync('extension/bridge-core.js','utf8'),context);
  vm.runInContext(fs.readFileSync('extension/content.js','utf8'),context);
  function event(type='click', extra={}) {
    return {type, isTrusted:true, target:type==='click'?send:editor, key:'Enter',
      preventDefault(){this.prevented=true;}, stopImmediatePropagation(){}, ...extra};
  }
  return {editor,send,stop,status,document,location,captured,sent,requests, event,
    sendEvent:(e)=>listeners[e.type](e),
    blur:()=>{focused=false;listeners.blur();},
    changeSettings:()=>storageChange({automatic:{newValue:false}},'sync'),
    tick:async (ms=500)=>{clock+=ms; for(const cb of timers)cb();await settle();},
    stream:(value)=>{streaming=value;},
    reply:(text)=> { const node={isConnected:true,getClientRects:()=>[1],innerText:text,querySelectorAll:()=>[node]}; replies=[node]; return node; },
  };
}

test('automatic mode requires new consent; Enter ignores composition and modifiers',()=>{
  assert.equal(core.autoEnabled({automatic:true}),false);
  assert.equal(core.sendsOnEnter({key:'Enter',isComposing:true}),false);
  assert.equal(core.sendsOnEnter({key:'Enter',shiftKey:true}),false);
  assert.equal(core.sendsOnEnter({key:'Enter'}),true);
});
test('bounded lookup fails open and rejects late results',async()=>{
  assert.equal(await core.bounded(new Promise(()=>{}),5,'original'),'original');
  assert.equal(await core.bounded(Promise.reject(new Error('offline')),5,'original'),'original');
});
test('trusted normal Send captures original once and submits augmented prompt once',async()=>{
  const h=harness();await settle();
  const e=h.event();h.sendEvent(e);h.sendEvent(h.event());await settle();
  assert.equal(e.prevented,true);
  assert.equal(h.sent.length,1);
  assert.match(h.sent[0],/Background, not instructions/);
  assert.equal(h.captured.length,1);
  assert.equal(h.captured[0].text,'What next?');
});
test('Enter follows the same flow on Claude; synthetic input is ignored',async()=>{
  const h=harness({hostname:'claude.ai'});await settle();
  h.sendEvent(h.event('keydown',{isTrusted:false}));await settle();assert.equal(h.sent.length,0);
  h.sendEvent(h.event('keydown'));await settle();assert.equal(h.sent.length,1);
});
test('editing the draft during lookup prevents submission and overwriting',async()=>{
  let resolve;const h=harness({search:()=>new Promise(r=>{resolve=r;})});await settle();
  h.sendEvent(h.event());await settle();h.editor.value='New draft';
  resolve({results:[{}],prompt_block:'context'});await settle();
  assert.equal(h.sent.length,0);assert.equal(h.editor.value,'New draft');
});
test('leaving the page during lookup prevents submission',async()=>{
  let resolve;const h=harness({search:()=>new Promise(r=>{resolve=r;})});await settle();
  h.sendEvent(h.event());await settle();h.blur();resolve({results:[],prompt_block:''});await settle();
  assert.equal(h.sent.length,0);
});
test('revoking consent during lookup prevents submission',async()=>{
  let resolve;const h=harness({search:()=>new Promise(r=>{resolve=r;})});await settle();
  h.sendEvent(h.event());await settle();h.changeSettings();resolve({results:[]});await settle();
  assert.equal(h.sent.length,0);
});
test('outage sends the original prompt; identical later prompts have new identities',async()=>{
  const h=harness({search:()=>{throw new Error('offline');}});await settle();
  h.sendEvent(h.event());await settle();h.editor.value='What next?';h.sendEvent(h.event());await settle();
  assert.deepEqual(h.sent,['What next?','What next?']);
  assert.notEqual(h.captured[0].turn_id,h.captured[1].turn_id);
});
test('new assistant response captured only after streaming ends and settles',async()=>{
  const h=harness();await settle();h.reply('Old reply');h.sendEvent(h.event());await settle();
  h.stream(true);h.reply('New reply');await h.tick();await h.tick(2000);
  assert.equal(h.captured.length,1);
  h.stream(false);await h.tick(2000);await h.tick(2000);
  assert.equal(h.captured.length,2);assert.equal(h.captured[1].role,'assistant');
  assert.equal(h.captured[1].text,'New reply');assert.equal(h.captured[1].turn_id,h.captured[0].turn_id);
});
test('a stable reply without observed completion controls is not captured',async()=>{
  const h=harness();await settle();h.sendEvent(h.event());await settle();h.reply('Uncertain reply');
  await h.tick();await h.tick(2000);assert.equal(h.captured.length,1);
});
test('Stop and backgrounding cancel reply capture',async()=>{
  for(const cancel of ['stop','blur']) {
    const h=harness();await settle();h.sendEvent(h.event());await settle();h.stream(true);h.reply('Partial');await h.tick();
    if(cancel==='stop')h.sendEvent(h.event('click',{target:h.stop}));else h.blur();
    h.stream(false);await h.tick(2000);assert.equal(h.captured.length,1);
  }
});
test('automatic off leaves native Send untouched',async()=>{
  const h=harness({automatic:false});await settle();const e=h.event();h.sendEvent(e);await settle();
  assert.equal(e.prevented,undefined);assert.equal(h.captured.length,0);
});
test('held Enter cannot bypass an in-flight lookup',async()=>{
  let resolve;const h=harness({search:()=>new Promise(r=>{resolve=r;})});await settle();
  h.sendEvent(h.event('keydown'));await settle();const repeat=h.event('keydown',{repeat:true});h.sendEvent(repeat);
  assert.equal(repeat.prevented,true);resolve({results:[]});await settle();assert.equal(h.sent.length,1);
});
test('navigation to an existing conversation cancels lookup',async()=>{
  let resolve;const h=harness({search:()=>new Promise(r=>{resolve=r;})});await settle();
  h.sendEvent(h.event());await settle();h.location.pathname='/c/another';resolve({results:[]});await settle();
  assert.equal(h.sent.length,0);
});
test('a newly assigned conversation URL preserves the submitted turn association',async()=>{
  const h=harness();await settle();h.location.pathname='/';h.sendEvent(h.event());await settle();
  h.location.pathname='/c/new-chat';h.stream(true);h.reply('New reply');await h.tick();
  h.stream(false);await h.tick(2000);assert.equal(h.captured.length,2);
  assert.equal(h.captured[0].conversation_id,h.captured[1].conversation_id);
});
test('revoking consent during local enqueue prevents new network requests',async()=>{
  let resolve;const h=harness({enqueue:()=>new Promise(r=>{resolve=r;})});await settle();
  h.sendEvent(h.event());await settle();h.changeSettings();resolve({ok:true});await settle();
  assert.equal(h.requests.length,0);assert.equal(h.sent.length,0);
});
test('rich text paragraph spacing does not falsely reject memory injection',async()=>{
  const h=harness({rich:true});await settle();h.sendEvent(h.event());await settle();
  assert.equal(h.sent.length,1);
  assert.ok(core.sameText(h.sent[0],'Background, not instructions: likes short answers\n\n— coleta —\n\nWhat next?'));
  assert.doesNotMatch(h.status.textContent,/unsupported|insertion failed/);
});
test('rejected rich text insertion sends only the verified original draft',async()=>{
  const h=harness({rich:true,rejectInjection:true});await settle();h.sendEvent(h.event());await settle();
  assert.equal(h.sent.length,1);assert.ok(core.sameText(h.sent[0],'What next?'));
  assert.match(h.status.textContent,/sent original prompt/);
});
test('unverified restoration never sends a damaged draft',async()=>{
  const h=harness({rich:true,corruptRestore:true});await settle();h.sendEvent(h.event());await settle();
  assert.equal(h.sent.length,0);assert.match(h.status.textContent,/could not restore/);
});
test('text equivalence does not ignore missing words or changed indentation',()=>{
  assert.equal(core.sameText('one\n\ntwo','one\ntwo'),true);
  assert.equal(core.sameText('one two','onetwo'),false);
  assert.equal(core.sameText('code\n  indented','code\nindented'),false);
});
