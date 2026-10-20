import exec from 'k6/execution';

const tokenFile = __ENV.TOKEN_FILE || '/runtime/bench-tokens.txt';
const tokens = open(tokenFile)
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean);

if (tokens.length === 0) {
  throw new Error(`token pool is empty: ${tokenFile}`);
}

export function authHeaders() {
  const idx = exec.scenario.iterationInTest % tokens.length;
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${tokens[idx]}`,
  };
}

export function tokenCount() {
  return tokens.length;
}
