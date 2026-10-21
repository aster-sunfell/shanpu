// One fixed VU pool covers the complete profile. Planned indices map to disjoint
// phase/user ranges. Dropped arrivals invalidate the whole comparison point.
export function phaseFor(config, iteration) {
  if (config.workload !== 'burst') return {name: 'steady', offset: 0, start: 0, rate: config.rate};
  const base = config.baseRate * config.baseSeconds;
  const burst = config.rate * config.seconds;
  if (iteration < base) return {name: 'baseline', offset: 0, start: 0, rate: config.baseRate};
  if (iteration < base + burst) return {name: 'burst', offset: base, start: config.baseSeconds, rate: config.rate};
  return {name: 'recovery', offset: base + burst, start: config.baseSeconds + config.seconds, rate: config.recoveryRate};
}
export function slotRate(config) {
  return config.workload === 'burst' ? Math.max(config.baseRate, config.rate, config.recoveryRate) : config.rate;
}
export function userIndex(config, scenario, iteration) {
  if (!Number.isInteger(iteration) || iteration < 0) return null;
  if (config.workload !== 'burst') return iteration < config.scheduled ? iteration : null;
  // A constant-rate clock avoids ramping executor's missing first/last arrival.
  // Lower-rate phases select evenly spaced slots; unused slots send no HTTP.
  const clock = slotRate(config);
  let slot = iteration, offset = 0;
  for (const [rate, seconds] of [[config.baseRate, config.baseSeconds], [config.rate, config.seconds], [config.recoveryRate, config.recoverySeconds]]) {
    if (slot < clock * seconds) {
      const local = Math.floor(slot * rate / clock);
      if (local === Math.floor((slot - 1) * rate / clock)) return null;
      return offset + local;
    }
    slot -= clock * seconds;
    offset += rate * seconds;
  }
  return null;
}
export function scheduledAt(config, scenario, iteration, startTime) {
  const phase = phaseFor(config, iteration);
  return startTime + phase.start * 1000 + (iteration - phase.offset) * 1000 / phase.rate;
}
export function userId(pool, index) {
  if (!Number.isInteger(index) || index < 0 || index >= pool.count) throw new Error('User index outside fixture');
  let low = 0, high = pool.segments.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2), segment = pool.segments[mid];
    if (index < segment.index) high = mid - 1;
    else if (index >= segment.index + segment.count) low = mid + 1;
    else return segment.userId + index - segment.index;
  }
  throw new Error('User index not mapped to a fixture ID');
}
export function trafficClass(config, index) {
  return config.workload === 'mixed' && index % 10 !== 9 ? 'duplicate' : 'eligible';
}
