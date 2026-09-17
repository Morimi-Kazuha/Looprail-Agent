# CallEfficiency cost evaluation

This LooprailBench track measures how stable request prefixes affect estimated
Provider cost. The active Runtime subsystem is CallEfficiency, which records
one Call Record for each physical Provider attempt and supports offline
reduction.

The result below is a fixed workload measurement, not a cost promise for every
model, account, or production task.

## Question

How much can a stable request prefix reduce the estimated cost of a verified
successful Looprail task when the Provider's automatic prompt cache is
available?

The reference workload uses DeepSeek's normalized
`prompt_cache_hit_tokens` and `prompt_cache_miss_tokens` fields. It does not
claim that Looprail creates or controls the Provider's cache.

## Treatment axis

Each Comparison Block runs the same task under two policies. Automatic Provider
caching remains enabled in both arms.

| Policy | Request behavior | Role |
| --- | --- | --- |
| `prefix_disrupted` | Change the leading system and Tool Schema bytes before every Provider call | Negative control |
| `prefix_stable` | Preserve the ordinary request prefix | Treatment |

The disrupted arm is an experimental counterfactual, not a deployable
configuration. Each Trial uses a separate Provider user identity so that one
arm cannot warm another arm's cache.

## Workload and metrics

The frozen matrix has four workload classes, three cases per class, and three
repetitions:

| Workload class | Observable pressure | Shape per Trial |
| --- | --- | --- |
| `stable_dialogue` | Repeated stable system instructions | Six short Turns, no Tools |
| `long_history` | Growing conversation prefix | Six Turns after seeded history |
| `tool_accumulation` | Tool schemas and results accumulate | Six Turns, one verified Tool call per Turn |
| `intra_turn_tool_chain` | Tool results extend the prefix within one Turn | One Turn with a verified three-step Tool chain |

The primary metric is:

```text
cost_per_verified_success = sum(all valid Trial cost) / verified successes
```

Failed tasks remain in the numerator. A conservative cache hit rate is:

```text
cache_read / (cache_miss + cache_read)
```

A comparison is claim-eligible only when every planned block is valid, usage
reconciles, the requested model serves every call, all workload classes are
present, treatment success does not regress, and the confidence interval's
lower bound is positive.

## Recorded result

The fixed campaign completed 36 Comparison Blocks, 72 Trials, and 504 Provider
calls.

| Metric | Prefix disrupted | Prefix stable |
| --- | ---: | ---: |
| Valid Trials | 36 | 36 |
| Verified task pass rate | 100% | 100% |
| Conservative cache hit rate | 0% | 74.0478% |
| Estimated cost per verified success | $0.008356 | $0.002311 |

Stable prefixes reduced estimated cost per verified success by **72.3413%** in
this workload. The task-clustered paired estimate was **72.0750%**, with a 95%
interval of **68.8471% to 75.0961%**. The campaign used an estimated USD
0.384031.

## Evidence boundary

This result supports a workload-specific association between stable prefixes,
cache observations, and estimated cost. It does not prove that Looprail creates
the Provider cache, that another workload will achieve the same hit rate, or
that the estimate matches a Provider invoice.

Raw manifests, credentials, standalone reports, and local campaign output are
not published in this repository. Generated evidence stays outside Git.

## Offline reproduction

The replay path makes no Provider calls. With a separately trusted source
artifact and digest, it validates the artifact, recalculates each Trial from
the embedded price snapshot, and reruns the reducer:

```bash
uv run --frozen python -m benchmarks.looprailbench.packs.tokenwise_cost.replay \
  --source-report .looprail/evidence/tokenwise-cost-source/report.json \
  --expected-source-digest <trusted-source-digest> \
  --output .looprail/evidence/call-efficiency-replay/report.json
```

The expected digest must come from an independently trusted manifest; copying
it from the source report being checked does not establish provenance. An
`equivalent: true` result establishes artifact and reducer equivalence only; it
is not a new live Runtime result.

The live campaign runner is an explicitly paid operation behind
`--execute-paid-campaign`. It is not part of ordinary tests or the local
Runtime baseline and must be run only with separately authorized credentials
and a bounded budget.
