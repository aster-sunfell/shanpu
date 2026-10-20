// Arrival-rate executors can schedule at an exact duration boundary. Give each
// phase a bounded, disjoint user range; an extra boundary iteration sends nothing.
export function userIndex(config, scenario, iteration) {
  const budget = scenario === 'baseline' ? config.baseRate * config.baseSeconds : config.rate * config.seconds;
  if (!Number.isInteger(iteration) || iteration < 0 || iteration >= budget) return null;
  return (scenario === 'burst' ? config.baseRate * config.baseSeconds : 0) + iteration;
}

export function userId(pool, index) {
  if (!Number.isInteger(index) || index < 0 || index >= pool.count) throw new Error('User index outside fixture');
  let low = 0, high = pool.segments.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const segment = pool.segments[mid];
    if (index < segment.index) high = mid - 1;
    else if (index >= segment.index + segment.count) low = mid + 1;
    else return segment.userId + index - segment.index;
  }
  throw new Error('User index not mapped to a fixture ID');
}

export function trafficClass(config, index) {
  return config.workload === 'mixed' && index % 10 !== 9 ? 'duplicate' : 'eligible';
}

export function scheduledAt(config, scenario, iteration, startTime) {
  const rate = scenario === 'baseline' ? config.baseRate : config.rate;
  return startTime + iteration * 1000 / rate;
}
