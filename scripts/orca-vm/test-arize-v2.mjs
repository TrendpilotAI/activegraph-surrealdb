// Run after compiling arize-v2.ts into the directory supplied on argv[2].
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { pathToFileURL } from 'node:url'
const { legacyMessages } = await import(pathToFileURL(process.argv[2]).href)
const sid = 'ses_fixture'
const messages = legacyMessages([
  {id:'msg_user',type:'user',text:'private fixture prompt',time:{created:new Date(1000)}},
  {id:'msg_control',type:'model-switched',time:{created:1001}},
  {id:'msg_assistant',type:'assistant',model:{providerID:'mercor',id:'large-fixture'},
   time:{created:1100,completed:1500},cost:0.01,
   tokens:{input:11,output:4,reasoning:2,cache:{read:3,write:0}},
   content:[{type:'text',text:'fixture answer'},
    {type:'tool',id:'call_fixture',name:'shell',time:{created:1150,ran:1200,completed:1400},
     state:{status:'completed',input:{command:'printf fixture'},content:[{type:'text',text:'fixture'}]}}]},
],sid)
assert.equal(messages.length,2)
assert.equal(messages[0].info.time.created,1000)
assert.equal(messages[1].info.modelID,'large-fixture')
assert.equal(messages[1].info.providerID,'mercor')
assert.equal(messages[1].parts[1].callID,'call_fixture')
assert.equal(messages[1].parts[1].state.output,'fixture')
assert.deepEqual(messages[1].parts[1].state.time,{start:1200,end:1400})
const pending = legacyMessages([{id:'msg_pending',type:'assistant',time:{created:1600},
  content:[{type:'tool',id:'call_pending',name:'read',state:{status:'streaming',input:''}}]}],sid)
assert.equal(pending[0].parts[0].state.status,'pending')
assert.equal(pending[0].info.time.completed,undefined)
if (process.argv[3]) fs.writeFileSync(process.argv[3],JSON.stringify({type:'close',sessionID:sid,messages}))
console.log('OpenCode 2 snapshot conversion passed')
