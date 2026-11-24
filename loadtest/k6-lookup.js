// Compare the two layers of the API:
//   MODE=cached  -> /v1/customers/{id}/transactions (Redis cache-aside, high-TPS path)
//   MODE=direct  -> same endpoint with cache=false (every request runs a Trino query)
//   MODE=page    -> /v1/transactions keyset page over a 7-day window (Trino, partition-pruned)
//   MODE=warmup  -> requests every id in 1..HOT_CUSTOMERS once, so a following MODE=cached
//                   run measures a warm cache (100% hits) instead of the cold-start misses
// Run:  make loadtest MODE=cached VUS=20 DURATION=60s
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const KEY = __ENV.API_KEY || 'dev-analyst-key';
const MODE = __ENV.MODE || 'cached';
// Hot set of customers: with a cache, repeated ids become hits after the first miss.
const HOT = parseInt(__ENV.HOT_CUSTOMERS || '1000');
const cacheHits = new Counter('cache_hits');

const VUS = parseInt(__ENV.VUS || '10');
export const options = {
  scenarios: {
    load: MODE === 'warmup'
      ? { executor: 'shared-iterations', vus: VUS, iterations: HOT, maxDuration: '15m' }
      : { executor: 'constant-vus', vus: VUS, duration: __ENV.DURATION || '60s' },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: { http_req_failed: ['rate<0.01'] },
};

function isoDate(d) { return d.toISOString().slice(0, 10); }

export default function () {
  const headers = { 'X-API-Key': KEY };
  let url;
  if (MODE === 'page') {
    const to = new Date(); const from = new Date(Date.now() - 7 * 86400e3);
    url = `${BASE}/v1/transactions?date_from=${isoDate(from)}&date_to=${isoDate(to)}&limit=100`;
  } else {
    const id = MODE === 'warmup' ? exec.scenario.iterationInTest + 1 : 1 + Math.floor(Math.random() * HOT);
    url = `${BASE}/v1/customers/${id}/transactions?limit=20&cache=${MODE !== 'direct'}`;
  }
  const res = http.get(url, { headers, tags: { mode: MODE } });
  check(res, { 'status 200': (r) => r.status === 200 });
  if (res.headers['X-Cache'] === 'HIT') cacheHits.add(1);
}

export function handleSummary(data) {
  const m = data.metrics;
  const d = m.http_req_duration.values;
  const line = {
    mode: MODE, vus: parseInt(__ENV.VUS || '10'), duration: __ENV.DURATION || '60s',
    requests: m.http_reqs.values.count, tps: +m.http_reqs.values.rate.toFixed(1),
    p50_ms: +d.med.toFixed(1), p95_ms: +d['p(95)'].toFixed(1), p99_ms: +d['p(99)'].toFixed(1),
    error_rate: m.http_req_failed.values.rate, cache_hits: m.cache_hits ? m.cache_hits.values.count : 0,
  };
  return {
    stdout: `\n${JSON.stringify(line)}\n`,
    [`/results/${MODE}-${Date.now()}.json`]: JSON.stringify(line, null, 2),
  };
}
