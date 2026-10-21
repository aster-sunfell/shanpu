import assert from 'node:assert/strict';
import test from 'node:test';
import { userIndex, userId, trafficClass, scheduledAt, phaseFor } from '../k6/lib/schedule.mjs';
const profile = {workload:'burst',baseRate:100,baseSeconds:20,rate:400,seconds:10,recoveryRate:100,recoverySeconds:30,scheduled:9000};
test('one pool covers exact disjoint phase budgets and excludes warmup user', () => {
  const counts = {baseline:0,burst:0,recovery:0};
  const used = new Set();
  for(let i=0;i<=24000;i++) {
    const index=userIndex(profile,'profile',i);
    if(index===null) continue;
    counts[phaseFor(profile,index).name]++;
    used.add(index);
  }
  assert.deepEqual(counts,{baseline:2000,burst:4000,recovery:3000});
  assert.equal(used.size,9000);
  assert.equal(userIndex(profile,'profile',24000),null);
});
test('schedule has instantaneous rate steps and a single consistent origin', () => {
  assert.equal(scheduledAt(profile,'profile',1999,1000),20990);
  assert.equal(scheduledAt(profile,'profile',2000,1000),21000);
  assert.equal(scheduledAt(profile,'profile',5999,1000),30997.5);
  assert.equal(scheduledAt(profile,'profile',6000,1000),31000);
  assert.equal(scheduledAt(profile,'profile',8999,1000),60990);
});
test('compact fixture IDs and mix are independent of scenario phases', () => {
  const pool={count:4,segments:[{index:0,userId:7,count:2},{index:2,userId:40,count:2}]};
  assert.deepEqual([0,1,2,3].map(i=>userId(pool,i)),[7,8,40,41]);
  assert.throws(()=>userId(pool,4));
  assert.equal(trafficClass({workload:'mixed'},0),'duplicate');
  assert.equal(trafficClass({workload:'mixed'},9),'eligible');
  assert.equal(trafficClass(profile,0),'eligible');
  assert.equal(userIndex({scheduled:10},'steady',10),null);
  assert.equal(userIndex({scheduled:10},'steady',-1),null);
});
