# Tracing Runtime overhead

This LooprailBench track measures the local Runtime cost of Tracing. It does
not test an external Provider and does not claim production latency.

## Workload

The fixed campaign runs 20 balanced blocks of 50 paired Turns: 1,000 pairs
and 2,000 total Turns. Every Turn crosses the shared Runtime Assembly and
Agent Loop, makes two deterministic local Provider calls, executes
`trace_lookup` once, and returns `TRACE_OK`.

The only treatment axis is `LOOPRAIL_TRACING=0` versus
`LOOPRAIL_TRACING=1`. Enabled Turns retain one terminal `session.turn` trace
joined to two `llm.call` spans and one `tool.call` span. The disabled arm emits
no trace bytes. Both arms must complete the same workload with identical
replies and call counts.

## Metrics and evidence

The aggregate reports P50 and P95 latency for both arms, relative overhead, a
block-clustered bootstrap 95% interval for the P95 ratio, and bytes per traced
Turn. Validity requires correctness, correlation, pair-count, arm-balance, and
disabled-no-output Gates to pass. There is no preregistered good-overhead
threshold; the result is an operational estimate rather than an optimization
claim.

The offline verifier rebuilds raw outcomes, the aggregate, claim eligibility,
the verifier report, and the file inventory. Generated manifests, raw traces,
and inventories remain outside Git.

## Recorded result

All 1,000 pairs and 2,000 Turns were valid. The enabled arm retained exactly
1,000 traces and 6,000 spans with 100% correlation; the disabled arm emitted
zero trace bytes. Tracing wrote 25,717.2 bytes per enabled Turn.

| Metric | Tracing off | Tracing on |
| --- | ---: | ---: |
| P50 Turn latency | 2.061208 ms | 4.284334 ms |
| P95 Turn latency | 2.912292 ms | 5.157333 ms |

The observed P95 difference was 2.245041 ms, or 77.0885% relative to the small
local baseline. The block-clustered relative P95 interval was -9.9969 to
101.9378%, so this result does not support a stable relative-overhead claim.
It does support exact trace correlation and an absolute local tax estimate.

## Reproduce or inspect a run

These targets plan, execute, and verify the tracing workload. Execution writes
evidence outside the public source tree:

```bash
make looprailbench-tracing-plan
make looprailbench-tracing-run
make looprailbench-tracing-verify
```

Set `LOOPRAIL_TRACING_OUTPUT` to select an evidence root. When verifying a run
bound to a different checkout, set `LOOPRAIL_TRACING_COMMIT` to the full commit
recorded by the retained manifest. Live or paid evaluation is not part of the
ordinary Runtime baseline.
