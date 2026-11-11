import test from 'node:test';
import assert from 'node:assert/strict';
import { CallSession, duration, formatDuration } from '../../static/js/calling/session.mjs';
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  let clock = 100000;
  const actions = [], media = { prepare: async()=>{}, close:()=>{}, connect: async(_,callbacks)=>{media.callbacks=callbacks;}, mute:async()=>{} };
  const call = { id:'call-1',conversation_id:7,caller_id:1,status:'inviting',revision:1,
    expires_at: new Date(200000).toISOString(), participants:[{user_id:1,status:'accepted',display_name:'Caller'},{user_id:2,status:'invited',display_name:'Susan'}] };
  const api = { start:async()=>structuredClone(call),action:async(id,action)=>{actions.push([id,action]);return {...structuredClone(call),revision:2};},connection:async()=>({}),get:async()=>call };
  const session = new CallSession({api,media,userId:1,now:()=>clock});
  return {session,api,media,call,actions,time:value=>{clock=value;}};
}
test('double start produces one invitation and never invents ringing or duration', async()=>{
  const {session,api,call}=fixture(); const response=deferred(); let starts=0;
  api.start=()=>{starts++; return response.promise;};
  const first=session.start(7,{name:'Susan'}); await flush(); await session.start(7,{});
  assert.equal(starts,1); assert.equal(session.state.phase,'initiating'); assert.equal(duration(session.state),0);
  response.resolve(call); await first;
  assert.equal(session.state.phase,'initiating'); assert.equal(session.pending,null);
  session.apply({...call,revision:2,participants:[call.participants[0],{...call.participants[1],status:'ringing'}]});
  assert.equal(session.state.phase,'ringing');
});
test('cancel during invitation creation cancels late server result and never revives UI',async()=>{
  const {session,api,call,actions}=fixture();const response=deferred(); api.start=()=>response.promise;
  const pending=session.start(7,{});await flush();await session.end();response.resolve(call);await pending;
  assert.equal(session.state.phase,'ended');assert.deepEqual(actions,[['call-1','cancel']]);
});
test('audio events alone start duration; minimize, restore and reconnect preserve state',async()=>{
  const {session,call,media,time}=fixture();await session.start(7,{name:'Susan'});
  session.apply({...call,status:'active',revision:3});await flush();
  assert.equal(session.state.phase,'connecting');assert.equal(duration(session.state),0);
  media.callbacks.ready();time(165000);await session.mute();session.minimize();session.restore();
  assert.equal(duration(session.state,165000),65);assert.equal(session.state.muted,true);
  media.callbacks.reconnecting();time(175000);assert.equal(duration(session.state,175000),75);
  media.callbacks.reconnected();assert.equal(session.state.phase,'connected');
  session.endLocal('ended');time(190000);assert.equal(duration(session.state,190000),75);
  assert.equal(formatDuration(3672),'1:01:12');
});
test('duplicate incoming answer is submitted once and stays connecting until real audio',async()=>{
  const {session,call,api}=fixture();session.userId='2';session.apply(call);
  const permission=deferred();session.media.prepare=()=>permission.promise;let accepts=0;
  api.action=async()=>{accepts++;return {...call,status:'active',revision:3};};
  const first=session.answer();await session.answer();assert.equal(session.state.phase,'connecting');
  permission.resolve();await first;await flush();assert.equal(accepts,1);assert.equal(session.state.connectedAt,null);
});
test('cancelled and expired invitations cannot be revived by late events',async()=>{
  const {session,call,time}=fixture();session.userId='2';session.apply(call);
  session.apply({...call,status:'cancelled',revision:3});session.apply({...call,status:'active',revision:4});
  assert.equal(session.state.outcome,'cancelled');assert.equal(session.state.connectedAt,null);
  const second=fixture();second.session.userId='2';second.session.apply(second.call);second.time(210000);second.session.tick();
  assert.equal(second.session.state.outcome,'no_answer');second.session.apply({...second.call,status:'active',revision:8});
  assert.equal(second.session.state.phase,'ended');
});
test('answer elsewhere dismisses local invitation without a leave request',async()=>{
  const {session,api,call,actions}=fixture();session.userId='2';session.apply(call);
  api.connection=async()=>{throw Object.assign(new Error(),{status:409,code:'answered_elsewhere'});};
  session.apply({...call,status:'active',revision:3});await flush();
  assert.equal(session.state.outcome,'elsewhere');assert.deepEqual(actions,[]);
});
test('a visible invitation acknowledges ringing once, and ignores old revisions',async()=>{
  const {session,call,actions}=fixture();session.userId='2';session.apply(call);
  await Promise.all([session.acknowledge(),session.acknowledge()]);assert.deepEqual(actions,[['call-1','ringing']]);
  session.apply({...call,revision:1});assert.equal(session.state.call.revision,2);
});
test('permission denial offers retry without creating an invitation',async()=>{
  const {session,media,api}=fixture();let starts=0;api.start=async()=>{starts++;};
  media.prepare=async()=>{throw Object.assign(new Error(),{name:'NotAllowedError',permissionBlocked:true});};
  await session.start(7,{});assert.equal(starts,0);assert.equal(session.state.phase,'permission');assert.equal(session.state.permissionBlocked,true);
});
test('decline is sent once while its response is pending',async()=>{
  const {session,call,api}=fixture();session.userId='2';session.apply(call);
  const response=deferred();let count=0;api.action=()=>{count++;return response.promise;};
  const first=session.decline();await session.decline();assert.equal(count,1);
  response.resolve({...call,status:'declined',revision:3});await first;assert.equal(session.state.outcome,'declined');
});
test('an ending request failure keeps an explicit retry available and stops local media',async()=>{
  const {session,api}=fixture();await session.start(7,{});api.action=async()=>{throw new Error('offline');};
  await session.end();assert.equal(session.state.phase,'end_failed');assert.equal(session.live,true);
});
test('acceptance winning cancellation is followed by an authorized leave',async()=>{
  const {session,api,call,actions}=fixture();await session.start(7,{});
  api.action=async(id,action)=>{actions.push([id,action]);if(action==='cancel')throw Object.assign(new Error(),{status:409});return {...call,status:'ended',revision:4};};
  api.get=async()=>({...call,status:'active',revision:3});await session.end();
  assert.deepEqual(actions,[['call-1','cancel'],['call-1','leave']]);assert.equal(session.state.phase,'ended');
});
test('late end response cannot reopen a dismissed outcome',async()=>{
  const {session,api,call}=fixture();await session.start(7,{});const response=deferred();api.action=()=>response.promise;
  const pending=session.end();session.apply({...call,status:'cancelled',revision:3});session.dismiss();
  response.resolve({...call,status:'cancelled',revision:3});await pending;assert.equal(session.state.phase,'idle');
});
test('microphone retry on an accepted outgoing call reconnects without accepting again',async()=>{
  const {session,call,media,actions}=fixture();await session.start(7,{});
  let denied=true;media.connect=async(_,callbacks)=>{if(denied)throw Object.assign(new Error(),{name:'NotAllowedError'});media.callbacks=callbacks;};
  session.apply({...call,status:'active',revision:3});await flush();assert.equal(session.state.phase,'permission');
  denied=false;await session.retryPermission();assert.equal(actions.length,0);media.callbacks.ready();assert.equal(session.state.phase,'connected');
});

test('a stale connection conflict uses the actual terminal outcome instead of claiming another-device answer',async()=>{
  const {session,call,api}=fixture();session.userId='2';session.apply(call);
  api.connection=async()=>{throw Object.assign(new Error(),{status:409,code:'call_not_active'});};
  api.get=async()=>({...call,status:'cancelled',revision:4});
  session.apply({...call,status:'active',revision:3});await flush();
  assert.equal(session.state.outcome,'cancelled');
});
test('a start socket event arriving before the HTTP response must not cancel its own invitation',async()=>{
  const {session,api,call,actions}=fixture();const response=deferred();api.start=()=>response.promise;
  const pending=session.start(7,{name:'Susan'});await flush();
  session.apply(structuredClone(call));
  response.resolve(structuredClone(call));await pending;
  assert.deepEqual(actions,[]);assert.equal(session.state.phase,'initiating');assert.equal(session.pending,null);
});

test('ringing arriving before the start response retains its newer state without cancelling',async()=>{
  const {session,api,call,actions}=fixture();const response=deferred();api.start=()=>response.promise;
  const pending=session.start(7,{});await flush();
  session.minimize();
  session.apply({...call,revision:2,participants:[call.participants[0],{...call.participants[1],status:'ringing'}]});
  response.resolve(call);await pending;
  assert.deepEqual(actions,[]);assert.equal(session.state.phase,'ringing');
  assert.equal(session.state.presentation,'minimized');assert.equal(session.state.call.revision,2);
});
test('rapid acceptance before the start response connects once without cancelling',async()=>{
  const {session,api,call,actions,media}=fixture();const response=deferred();api.start=()=>response.promise;
  let connects=0;media.connect=async()=>{connects++;};
  const pending=session.start(7,{});await flush();
  session.apply({...call,status:'active',revision:3});await flush();
  response.resolve(call);await pending;
  assert.deepEqual(actions,[]);assert.equal(connects,1);assert.equal(session.state.phase,'connecting');
});
test('reload recovery preserves the original clock, mute and minimized presentation',async()=>{
  const {session,call,media,time}=fixture();let preferences;
  media.connect=async(_,callbacks,prefs)=>{preferences=prefs;media.callbacks=callbacks;};
  session.restoreSaved({...call,status:'active',revision:3},{id:call.id,connectedAt:80000,muted:true,speaker:false,output:'default',presentation:'minimized'});
  await flush();media.callbacks.ready();time(125000);
  assert.equal(duration(session.state,125000),45);assert.equal(session.state.presentation,'minimized');
  assert.equal(preferences.muted,true);assert.equal(session.state.connectedAt,80000);
});
test('terminal server state cannot be revived by stored recovery hints',()=>{
  const {session,call}=fixture();session.restoreSaved({...call,status:'ended'},{id:call.id,connectedAt:80000,muted:true,presentation:'minimized'});
  assert.equal(session.state.phase,'idle');
});
test('recovery storage contains UI hints rather than tokens or profile data',async()=>{
  const {recoveryHint,validHint}=await import('../../static/js/calling/recovery.mjs');
  const {session}=fixture();await session.start(7,{name:'Private name'});
  const hint=recoveryHint(session.state);assert.equal(validHint(hint).id,'call-1');
  assert.equal('peer' in hint,false);assert.equal('participant_token' in hint,false);
  assert.equal(validHint({...hint,savedAt:Date.now()-86400001}),null);
});
test('ring alerts ignore unattached invitations and stop vibration/timers on acceptance',async()=>{
  const {CallAlerts}=await import('../../static/js/calling/alerts.mjs');
  const previous=navigator.vibrate;const vibration=[];navigator.vibrate=pattern=>{vibration.push(pattern);return true;};
  const alerts=new CallAlerts();
  try {
    alerts.update({phase:'incoming',call:null});assert.equal(alerts.timer,null);
    alerts.update({phase:'incoming',call:{id:'invite'}});assert.ok(alerts.timer);
    assert.deepEqual(vibration.at(-1),[250,120,250]);
    alerts.update({phase:'connecting',call:{id:'invite'}});assert.equal(alerts.timer,null);assert.equal(vibration.at(-1),0);
  } finally { alerts.stop();navigator.vibrate=previous; }
});
