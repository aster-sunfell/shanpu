import http from 'k6/http';
import exec from 'k6/execution';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import { userIndex, userId, trafficClass, scheduledAt, phaseFor, slotRate } from './schedule.mjs';

const config = JSON.parse(open(__ENV.POINT_CONFIG));
const pool = JSON.parse(open(__ENV.USER_FILE));
const rawurl = (value) => value.replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
function tokenFor(uid, index) {
  const header = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9';
  const body = rawurl(encoding.b64encode(JSON.stringify({ uid, uname: `${pool.prefix}_${index}`, iat: pool.issuedAt, exp: pool.issuedAt + 86400 })));
  const payload = `${header}.${body}`;
  return `${payload}.${rawurl(crypto.hmac('sha256', __ENV.JWT_SECRET, payload, 'base64'))}`;
}

export function optionsFor(plan) {
  if (config.plan !== plan) throw new Error(`Expected ${plan}, got ${config.plan}`);
  const scenario = (rate, seconds, startTime = '0s') => ({
    executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: `${seconds}s`,
    startTime, preAllocatedVUs: config.vus, maxVUs: config.vus,
    gracefulStop: `${config.requestTimeout + 2}s`,
  });
  const scenarios = config.workload === 'burst' ? {
    profile: scenario(slotRate(config), config.baseSeconds + config.seconds + config.recoverySeconds),
  } : { steady: scenario(config.rate, config.seconds) };
  return {
    scenarios,
  };
}

export function request() {
  const index = userIndex(config, exec.scenario.name, exec.scenario.iterationInTest);
  if (index === null) return;
  if (index >= pool.count - 1) throw new Error('User pool exhausted; warmup user must remain isolated');
  const uid = userId(pool, index);
  const token = tokenFor(uid, index);
  const phase = phaseFor(config, index);
  const requestId = `${phase.name}:${index - phase.offset}`;
  const startedAtMs = Date.now();
  const injectionEndMs = exec.scenario.startTime + 1000 * (config.baseSeconds + config.seconds + (config.recoverySeconds || 0));
  console.log(JSON.stringify({ type: 'start', requestId, index, userId: uid, trafficClass: trafficClass(config, index), scheduledAtMs: scheduledAt(config, exec.scenario.name, index, exec.scenario.startTime), startedAtMs, injectionEndMs }));
  const response = http.post(`${__ENV.API_BASE}/seckills/${config.activityId}/grab`, '{}', {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
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
  console.log(JSON.stringify({
    type: 'finish', requestId, index, startedAtMs, finishedAtMs, outcome,
    responseMs: response.timings.duration, httpStatus: response.status,
    errorCode: response.error_code || null, error: response.error || null,
    connectingMs: response.timings.connecting, blockedMs: response.timings.blocked,
    code: body ? body.code : null, state: state || null,
    orderId: body && body.data && body.data.orderId ? String(body.data.orderId) : null,
  }));
}
