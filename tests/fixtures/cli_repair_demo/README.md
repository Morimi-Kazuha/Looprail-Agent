# CLI repair demo fixture

This tiny repository is copied into a temporary workspace by the CLI contract
tests. `calculator.py` contains a deliberately failing `add` implementation,
and `test_calculator.py` is the invariant test that the deterministic Provider
repairs it without editing the test.
