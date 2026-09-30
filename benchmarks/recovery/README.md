# Recovery evaluation scaffold

Run from a source checkout:

```powershell
python -m scripts.eval_jev_recovery --advisor fake
python -m scripts.eval_jev_recovery --advisor fake --threshold 0.85 --json
# Explicit opt-in: up to one paid Jev request per case (15 requests).
python -m scripts.eval_jev_recovery --advisor live --json
```

Default: fake, no network or credentials. The fake is a fixed rule-based double
with simulated confidence 0.8; it never receives acceptable labels. Its results
validate the harness and policy, not Jev quality. Live uses only LOOPRAIL settings
and the key corresponding to the chosen transport. Missing live configuration
fails before evaluation. JSON output contains bounded decisions, no raw payloads.

`cases.json` contains 15 small, human-authored scenarios. Required fields:
`id`, `goal`, `failure_type`, `tool_name`, `failure_count`, `available_tools`,
`remaining_budget`, `acceptable_actions`, `notes`. IDs are unique; types, enum,
counts, budget, names and nonempty unique acceptable sets are validated strictly.
The failure_type uses production's four projected categories. Goal carries the
scenario facts; notes and acceptable_actions stay local. Context uses precisely
the production projection; rich error text and raw tool arguments are excluded.
The scenarios assume the trigger already fired. Persistent unavailability is
included; transient errors alone do not activate the runtime detector.

Rows retain suggested and policy-selected actions, confidence, fallback reason,
and latency. Accepted means a non-fallback selected action belongs to that case's
acceptable set. BASELINE is not silently scored as an accepted semantic answer.
Invalid responses count schema/envelope failures, not all transport failures.
All totals and distributions are derived from the current run. Latency includes
the policy and advisor; fake timing is local overhead, not service timing.

Use this for failure-case regression, decision distribution, fallback observation
and exploratory threshold comparisons. Review disagreements and revise ambiguous
labels with documented reasoning. Collect representative live decisions on a
separate held-out corpus before calibration. Compare coverage, premature STOP,
latency and actual task outcomes against deterministic recovery. This tiny corpus
does not establish model accuracy, task-success improvement, or an optimal 0.65
threshold. It does not execute recovery tools; runtime integration tests cover
execution and persistence. No eval platform or additional Jev runtime lane exists.
