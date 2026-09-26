const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
function harness() {
  let clock=1000;
  let settings={automatic:true,automaticConsent:true};
  let records={outbox:[]};
  let listener, change, alarm;
  let active=true;
  const keys=new Map();
  const database={transaction:()=>{
    const tx={objectStore:()=>({
      get:(key)=>{const request={};queueMicrotask(()=>{request.result=keys.get(key);request.onsuccess();});return request;},
      put:(value,key)=>{keys.set(key,value);queueMicrotask(()=>tx.oncomplete());},
    })};return tx;
  }};
  const context=vm.createContext({crypto:webcrypto, TextEncoder, TextDecoder, URL, Uint8Array, btoa, atob,
    Date:class extends Date {static now(){return clock;}},
    indexedDB:{open:()=>{const request={};queueMicrotask(()=>{request.result=database;request.onsuccess();});return request;}},
    chrome:{action:{onClicked:{addListener:()=>{}}},runtime:{onMessage:{addListener:(fn)=>listener=fn}},
      tabs:{get:async()=>({active,windowId:1})},windows:{get:async()=>({focused:active})},
      alarms:{create:()=>{},onAlarm:{addListener:(fn)=>alarm=fn}},
      storage:{sync:{get:async()=>settings},
        local:{get:async()=>structuredClone(records),set:async(value)=>{records={...records,...structuredClone(value)};},remove:async()=>{records={outbox:[]};}},
        onChanged:{addListener:(fn)=>change=fn},
      },
    },
  });
  vm.runInContext(fs.readFileSync('extension/background.js','utf8'),context);
  const sender={url:'https://claude.ai/chat/one',frameId:0,tab:{id:1}};
  const send=(action,extra={},source=sender)=>new Promise(resolve=>listener({type:'coleta-outbox',action,account:'a'.repeat(64),...extra},source,resolve));
  return {send,records:()=>records,keys, disable:()=>{settings.automatic=false;change({automatic:{newValue:false}},'sync');},
    expire:()=>{clock+=86_400_001;alarm({name:'coleta-expire-outbox'});},
    inactive:()=>{active=false;},settle:()=>vm.runInContext('serial',context)};
}
const body={text:'A private original message',turn_id:'s1',conversation_id:'c1',role:'user'};
test('outbox persists ciphertext with a nonextractable key and decrypts only for matching origin/account',async()=>{
  const h=harness();const added=await h.send('enqueue',{body});assert.equal(added.ok,true);
  assert.equal(JSON.stringify(h.records()).includes(body.text),false);
  assert.equal(h.keys.get('outbox').extractable,false);
  const pending=await h.send('pending');assert.equal(pending.records[0].body.text,body.text);
  assert.equal((await h.send('pending',{account:'b'.repeat(64)})).records.length,0);
  assert.equal((await h.send('pending',{}, {url:'https://chatgpt.com/c/one',frameId:0,tab:{id:1}})).records.length,0);
  await h.send('ack',{id:added.id});assert.equal((await h.send('pending')).records.length,0);
});
test('duplicate enqueue preserves one record and TTL; oversized records rejected',async()=>{
  const h=harness();await h.send('enqueue',{body});await h.send('enqueue',{body});
  assert.equal(h.records().outbox.length,1);
  assert.equal((await h.send('enqueue',{body:{...body,turn_id:'s2',text:'x'.repeat(100001)}})).ok,false);
});
test('untrusted origin, iframe, inactive window and revoked consent cannot read outbox',async()=>{
  const h=harness();await h.send('enqueue',{body});
  assert.equal((await h.send('pending',{}, {url:'https://evil.example',frameId:0,tab:{id:1}})).ok,false);
  assert.equal((await h.send('pending',{}, {url:'https://claude.ai',frameId:1,tab:{id:1}})).ok,false);
  h.inactive();assert.equal((await h.send('pending')).ok,false);
  h.disable();await h.settle();assert.equal(h.records().outbox.length,0);
  assert.equal((await h.send('pending')).ok,false);
});
test('retention alarm expires ciphertext without a provider page',async()=>{
  const h=harness();await h.send('enqueue',{body});h.expire();await h.settle();
  assert.equal(h.records().outbox.length,0);
});
