# Runtime scheduler evaluation

This LooprailBench track evaluates scheduler behavior with deterministic local
workloads. It isolates queueing and request-lifecycle behavior from Provider
and network variance. The results are engineering evidence, not a production
traffic model or a service-level objective.

## Reproduce the local workload

From the repository root, run:

```bash
make looprailbench-runtime-scheduler
```

The command writes evidence below
`.looprail/evidence/looprailbench-runtime-scheduler/`. Generated evidence is
local output and is not committed.

## Evaluated contracts

### Session head-of-line isolation

The control is a strict global FIFO with a fixed worker limit and per-session
serialization. The comparison policy uses session Lanes with the same USER
concurrency limit. Both policies receive the same ordered trace: a hot session
burst interleaved with short foreground Turns from other sessions.

The primary metric is foreground P95 queue wait. Paired repetitions alternate
policy order, and the comparison uses the median of paired reductions rather
than a percentile calculated from pooled repetitions.

### Foreground and background bulkheads

The control maps USER, CRON, and SUBAGENT work to one shared semaphore. The
comparison policy uses independent USER and Runtime-origin pools. Both policies
have the same total nominal capacity and receive the same idle and
background-saturated foreground trace.

The primary metric is loaded foreground P95 queue wait divided by idle
foreground P95 queue wait. This ratio reduces sensitivity to host timing
differences between repetitions.

### Accepted-request fate accounting

The request-fate workload covers normal execution, queued and running
cancellation, injection, interrupt, shutdown, origin limits, and rejection
after draining. The reducer requires zero lost requests, unexpected duplicate
executions, unresolved Handles, lifecycle contradictions, and pool-limit
violations.

This is an in-process guarantee from scheduler acceptance to terminal Handle
resolution. It does not claim exactly-once execution across process crashes or
external side effects.

## Evidence boundary

A result is reviewable only when the correctness Gates pass and the commit,
dependency lock, environment identity, workload, and clean-worktree binding
remain stable for the run. The local scheduler track does not require a live
Provider. Any separately approved live-provider experiment must retain its own
redacted receipts and must not be represented as a deterministic local result.
