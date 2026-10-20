function metricValue(data, name, field, fallback = Number.NaN) {
  const metric = data.metrics[name];
  if (!metric || !metric.values || metric.values[field] === undefined) {
    return fallback;
  }
  return metric.values[field];
}

function fixed(value, digits = 3) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) {
    return 'NA';
  }
  return Number(value).toFixed(digits);
}

export function seckillSummary(data, scenario) {
  const success = metricValue(data, 'order_success_total', 'count', 0);
  const iterations = metricValue(data, 'iterations', 'count', 0);
  const line = [
    'K6_SUMMARY',
    `scenario=${scenario}`,
    `success=${fixed(success, 0)}`,
    `iterations=${fixed(iterations, 0)}`,
    `success_rps=${fixed(metricValue(data, 'order_success_total', 'rate', 0), 3)}`,
    `p50=${fixed(metricValue(data, 'success_req_duration', 'p(50)'), 3)}`,
    `p95=${fixed(metricValue(data, 'success_req_duration', 'p(95)'), 3)}`,
    `p99=${fixed(metricValue(data, 'success_req_duration', 'p(99)'), 3)}`,
    `http_error_rate=${fixed(metricValue(data, 'http_error_rate', 'rate'), 6)}`,
    `business_error_rate=${fixed(metricValue(data, 'business_error_rate', 'rate'), 6)}`,
  ].join(' ');
  return { stdout: `${line}\n` };
}

export function cacheSummary(data, target, mode) {
  const success = metricValue(data, 'cache_success_total', 'count', 0);
  const iterations = metricValue(data, 'iterations', 'count', 0);
  const line = [
    'K6_D_SUMMARY',
    `target=${target}`,
    `mode=${mode}`,
    `success=${fixed(success, 0)}`,
    `iterations=${fixed(iterations, 0)}`,
    `rps=${fixed(metricValue(data, 'cache_success_total', 'rate', 0), 3)}`,
    `p50=${fixed(metricValue(data, 'cache_req_duration', 'p(50)'), 3)}`,
    `p95=${fixed(metricValue(data, 'cache_req_duration', 'p(95)'), 3)}`,
    `p99=${fixed(metricValue(data, 'cache_req_duration', 'p(99)'), 3)}`,
    `http_error_rate=${fixed(metricValue(data, 'http_error_rate', 'rate'), 6)}`,
  ].join(' ');
  return { stdout: `${line}\n` };
}
