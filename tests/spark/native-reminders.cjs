const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../static/js/spark-reminders.js'),'utf8');
async function check({denied=false,cancel=false,fails=false}={}) {
 const store=new Map([['spark-device-reminders','[99]']]);const calls=[];let interval;
 const plugin={checkPermissions:async()=>({display:denied?'denied':'granted'}),getPending:async()=>({notifications:[]}),cancel:async x=>calls.push(['cancel',x]),schedule:async x=>{calls.push(['schedule',x]);if(fails)throw Error('OS failure');}};
 const item={id:'reminder-uuid',notification_id:123,title:'Review',status:cancel?'cancelled':'scheduled',due_at:new Date(Date.now()+3600000).toISOString()};
 const context={console:{warn:()=>{}},Date,Set,JSON,decodeURIComponent,crypto:{randomUUID:()=> 'installation-uuid'},localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)},document:{hidden:false,querySelector:()=>null,cookie:'',addEventListener:()=>{}},setInterval:fn=>{interval=fn;},fetch:async(url,opts)=>{if(opts.method==='POST'){calls.push(['ack',url]);return{ok:true,json:async()=>({})}}return{ok:true,json:async()=>({reminders:[item]})}}};
 context.window={Capacitor:{isNativePlatform:()=>true,Plugins:{LocalNotifications:plugin}},addEventListener:()=>{}};
 vm.runInNewContext(source,context);await new Promise(r=>setImmediate(r));
 assert(calls.some(x=>x[0]==='cancel'),'stale native alerts cancelled');
 if(denied||cancel||fails)assert(!calls.some(x=>x[0]==='ack'),'no false phone ack');
 else {assert(calls.some(x=>x[0]==='ack'));assert.equal(calls.find(x=>x[0]==='schedule')[1].notifications[0].isExactNotification,false);}
 return {denied,cancel,fails,passed:true};
}
(async()=>{const result=[];for(const cfg of [{},{denied:true},{cancel:true},{fails:true}])result.push(await check(cfg));console.log(JSON.stringify(result,null,2));})();
