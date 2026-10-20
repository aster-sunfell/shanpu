// Arrival-rate executors can schedule at an exact duration boundary. Give each
// phase a bounded, disjoint user range; an extra boundary iteration sends nothing.
export function userIndex(config, scenario, iteration) {
  const budget = scenario === 'baseline' ? config.baseRate * config.baseSeconds : config.rate * config.seconds;
  if (!Number.isInteger(iteration) || iteration < 0 || iteration >= budget) return null;
  return (scenario === 'burst' ? config.baseRate * config.baseSeconds : 0) + iteration;
}
