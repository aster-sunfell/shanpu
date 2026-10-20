import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Trend } from 'k6/metrics';
import { userIndex } from './schedule.mjs';

const config = JSON.parse(open(__ENV.POINT_CONFIG));
const users = new SharedArray('isolated-users', () => JSON.parse(open(__ENV.USER_FILE)));
const outcomes = new Counter('request_outcomes');
const latency = new Trend('response_duration', true);

export function optionsFor(plan) {
  if (config.plan !== plan) throw new Error(`Expected ${plan}, got ${config.plan}`);
  const scenario = (rate, seconds, startTime = '0s') => ({
    executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: `${seconds}s`,
    startTime, preAllocatedVUs: config.vus, maxVUs: config.vus,
    gracefulStop: `${config.requestTimeout + 2}s`,
  });
  const scenarios = config.workload === 'burst' ? {
    baseline: scenario(config.baseRate, config.baseSeconds),
    burst: scenario(config.rate, config.seconds, `${config.baseSeconds}s`),
  } : { steady: scenario(config.rate, config.seconds) };
  return {
    scenarios, summaryTrendStats: ['min', 'med', 'p(95)', 'p(99)', 'max'],
    thresholds: { dropped_iterations: ['count==0'] },
  };
}

export function request() {
  const index = userIndex(config, exec.scenario.name, exec.scenario.iterationInTest);
  if (index === null) return;
  if (index >= users.length - 1) throw new Error('User pool exhausted; token reuse is forbidden');
  const user = users[index];
  const requestId = `${exec.scenario.name}:${exec.scenario.iterationInTest}`;
  const startedAtMs = Date.now();
  const injectionEndMs = exec.scenario.startTime + 1000 * (exec.scenario.name === 'baseline'
    ? config.baseSeconds + config.seconds : config.seconds);
  console.log(JSON.stringify({ type: 'start', requestId, index, startedAtMs, injectionEndMs }));
  const response = http.post(`${__ENV.API_BASE}/seckills/${config.activityId}/grab`, '{}', {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${user.token}` },
    timeout: `${config.requestTimeout}s`, tags: { name: 'seckill-grab' },
  });
  const finishedAtMs = Date.now();
  let body;
  try { body = response.json(); } catch (_) { body = null; }
  const state = body && body.data && body.data.status;
  let outcome = 'internal_error';
  if (response.status === 0) outcome = response.error_code === 1050 ? 'timeout' : 'network_error';
  else if (response.status === 429) outcome = 'rate_limited';
  else if (response.status === 401 || response.status === 403) outcome = 'unauthorized';
  else if (response.status === 200 && body && body.code === 0
      && state === (config.path === 'C' ? 'PENDING' : 'PAID') && body.data.orderId) outcome = 'accepted';
  else if (response.status === 200 && body && body.code === 40900) outcome = 'duplicate';
  else if (response.status === 200 && body && body.code === 41000) outcome = 'sold_out';
  outcomes.add(1, { outcome });
  latency.add(response.timings.duration, { outcome });
  console.log(JSON.stringify({
    type: 'finish', requestId, index, startedAtMs, finishedAtMs, outcome,
    responseMs: response.timings.duration, httpStatus: response.status,
    code: body ? body.code : null, state: state || null,
    orderId: body && body.data && body.data.orderId ? String(body.data.orderId) : null,
  }));
}

export function summary(data) {
  return { [`${__ENV.RESULT_DIR}/k6-summary.json`]: JSON.stringify(data, null, 2) };
}
