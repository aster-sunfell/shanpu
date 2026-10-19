import http from 'k6/http';
import { Rate, Counter, Trend } from 'k6/metrics';
import { authHeaders } from './lib/auth.js';
import { seckillSummary } from './lib/summary.js';

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

const orderSuccess = new Counter('order_success_total');
const orderFail = new Counter('order_fail_total');
const duplicate = new Counter('seckill_duplicate_total');
const soldOut = new Counter('seckill_sold_out_total');
const successDuration = new Trend('success_req_duration');
const httpErrorRate = new Rate('http_error_rate');
const businessErrorRate = new Rate('business_error_rate');

export default function () {
  const baseUrl = __ENV.API_BASE || 'http://127.0.0.1:8081/api';
  const activityId = __ENV.ACTIVITY_ID || '2001';
  const res = http.post(`${baseUrl}/seckills/${activityId}/grab`, '{}', {
    headers: authHeaders(),
  });
  recordResult(res);
}

function recordResult(res) {
  httpErrorRate.add(res.status >= 400);
  let body = null;
  try {
    body = res.json();
  } catch (_) {
    orderFail.add(1);
    businessErrorRate.add(false);
    return;
  }

  const status = body && body.data ? body.data.status : '';
  if (res.status === 200 && body.code === 0 && status === 'PAID') {
    orderSuccess.add(1);
    successDuration.add(res.timings.duration);
    businessErrorRate.add(false);
    return;
  }

  orderFail.add(1);
  const businessError = res.status === 200 && (body.code === 40900 || body.code === 41000);
  businessErrorRate.add(businessError);
  if (body.code === 40900) {
    duplicate.add(1);
  } else if (body.code === 41000) {
    soldOut.add(1);
  }
}

export function handleSummary(data) {
  return seckillSummary(data, 'B');
}
