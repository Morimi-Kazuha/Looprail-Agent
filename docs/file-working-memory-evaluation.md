# File Working Memory evaluation

## Claim

The evaluation verifies that a trusted file observation can reach a later
Windows CLI session as grounded context. It does **not** demonstrate fewer
reads of the target source or a stable token or cost reduction. File Working
Memory remains disabled by default.

## Frozen task and verifier

The offline benchmark defines 12 synthetic invoice tasks in
`benchmarks/looprailbench/file_working/tasks.py`. It generates isolated task
workspaces and records a manifest; the independent verifier in
`benchmarks/looprailbench/file_working/verify.py` checks executable behavior,
exact arithmetic, imports, tests, and the sentinel file. The clean-clone test
`tests/test_file_working_public_benchmark.py` verifies these fixtures without
starting a model. They are distinct from provider-billed measurements.

## One real A/B pair

Trial A had FWM off and Trial B had it on. Each trial completed six real SDK
model requests, and both independent verifiers passed. This is one exploratory
pair, not a statistically powered experiment.

| Observed metric | A: off | B: on | Reading |
| --- | ---: | ---: | --- |
| Session B `read_file` calls, all files | 2 | 1 | One fewer call in this pair |
| Session B `billing.py` reads | 1 | 1 | No reduction for the target source |
| Session B total tokens | 17,198 | 17,253 | B used 55 more tokens (+0.32%) |
| Session B provider cost | $0.002475744 | $0.003594768 | B cost $0.001119024 more (+45.20%) |
| Full-trial total tokens | 33,247 | 32,124 | B used 1,123 fewer tokens (-3.38%) |
| Full-trial provider cost | $0.006420396 | $0.005326368 | B cost $0.001094028 less (-17.04%) |
| Independent verifier | pass | pass | Both task outcomes verified |

The B Session B request contained the validated cross-session file evidence,
but the model later read `billing.py` again. The difference in aggregate
`read_file` calls came from an additional `sentinel.txt` read in A, not from
avoiding the target-source read. The full-trial token and cost differences
cannot be attributed to FWM. Session behavior and remote prompt-cache usage
differed between the two arms.

The two trials had 12 completed real SDK requests in total and $0.011746764 of
provider-confirmed cost. These are observed results from this pair, not an
estimate of typical savings. A conservative budget-ledger reservation is a
separate safety measure and is not the provider bill.

## Evidence boundary

- **Real provider A/B:** one pair, 12 requests, two passing independent
  verifiers, with the outcomes reported above.
- **Deterministic offline contracts:** validate receipt provenance, scope and
  hash checks, invalidation, context admission, host gating, and the synthetic
  independent verifier. They make no live requests and prove no cost
  reduction.
- **Cost estimates:** no estimated savings are claimed. Provider cost above is
  reported from the completed trial usage records; conservative ledger
  reservations are not interchangeable with it.
- **Historical results:** earlier failures and intermediate ledger records
  remain local and are not combined with this final pair as if they were
  successful trials.

The original provider traces, authorization material, host-state snapshots,
budget ledgers, machine-specific reports, and historical campaign runners that
depend on them are retained locally and excluded from this public repository.
The public task constructor, verifier, and deterministic regression tests are
the reproducible sources included here; they do not recreate private provider
evidence.
