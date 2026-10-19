import http from 'k6/http';
import { Rate, Counter, Trend } from 'k6/metrics';
import { cacheSummary } from './lib/summary.js';

const target = __ENV.CACHE_TARGET || 'activity';
const mode = __ENV.CACHE_MODE || 'on';

export const options = {
  scenarios: {
    fixed_100vu: {
      executor: 'constant-vus',
      vus: 100,
      duration: '30s',
      gracefulStop: '5s',
    },
  },
};

const success = new Counter('cache_success_total');
const duration = new Trend('cache_req_duration');
const httpErrorRate = new Rate('http_error_rate');

export default function () {
  const baseUrl = __ENV.API_BASE || 'http://127.0.0.1:8081/api';
  const path = target === 'product'
    ? `/products/${__ENV.PRODUCT_ID || '1'}`
    : `/seckills/${__ENV.ACTIVITY_ID || '3001'}`;
  const res = http.get(`${baseUrl}${path}`);
  httpErrorRate.add(res.status >= 400);

  let body = null;
  try {
    body = res.json();
  } catch (_) {
    return;
  }

  if (res.status === 200 && body.code === 0) {
    success.add(1);
    duration.add(res.timings.duration);
  }
}

export function handleSummary(data) {
  return cacheSummary(data, target, mode);
}
