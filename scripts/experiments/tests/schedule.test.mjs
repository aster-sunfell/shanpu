import assert from 'node:assert/strict';
import test from 'node:test';
import { userIndex } from '../k6/lib/schedule.mjs';

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
