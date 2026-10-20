import assert from 'node:assert/strict';
import test from 'node:test';
import { userIndex, userId, trafficClass, scheduledAt } from '../k6/lib/schedule.mjs';

test('burst phase boundary cannot reuse a baseline user or the warmup user', () => {
  const config = { baseRate: 2, baseSeconds: 2, rate: 10, seconds: 2 };
  const used = [];
  // Exercise the extra iteration observed at the duration boundary of both phases.
  for (const [phase, attempts] of [['baseline', 5], ['burst', 21]]) {
    for (let iteration = 0; iteration < attempts; iteration++) {
      const index = userIndex(config, phase, iteration);
      if (index !== null) used.push(index);
    }
  }
  assert.equal(used.length, 24);
  assert.equal(new Set(used).size, 24);
  assert.equal(Math.max(...used), 23);
  assert.equal(userIndex(config, 'baseline', 4), null);
  assert.equal(userIndex(config, 'burst', 20), null);
});

test('steady traffic stays bounded even if the executor schedules extra iterations', () => {
  const config = { rate: 5, seconds: 2 };
  assert.equal(userIndex(config, 'steady', 9), 9);
  assert.equal(userIndex(config, 'steady', 10), null);
  assert.equal(userIndex(config, 'steady', -1), null);
});


test('compact ID ranges do not reuse users across gaps; mix classes stay deterministic', () => {
  const pool = {count: 4, segments: [{index: 0, userId: 7, count: 2}, {index: 2, userId: 40, count: 2}]};
  assert.deepEqual([0,1,2,3].map(i => userId(pool,i)), [7,8,40,41]);
  assert.throws(() => userId(pool,4));
  assert.equal(trafficClass({workload:'mixed'}, 0), 'duplicate');
  assert.equal(trafficClass({workload:'mixed'}, 9), 'eligible');
  assert.equal(trafficClass({workload:'steady'}, 0), 'eligible');
});


test('planned timestamps use the rate and origin of each phase', () => {
  const config = {baseRate: 2, rate: 10};
  assert.equal(scheduledAt(config,'baseline',3,1000),2500);
  assert.equal(scheduledAt(config,'burst',3,3000),3300);
});
