"""Hidden behavior verifier. Run in a fresh isolated interpreter, outside agent cwd."""

# ruff: noqa: S101 -- standalone behavior verifier, invoked without -O
import importlib.util
import json
import sys
from pathlib import Path


def verify(root, task):
    root = Path(root)
    assert (root / "sentinel.txt").read_text() == "preserve this file\n"
    assert (root / "review.md").stat().st_size > 20
    assert (root / "test_billing.py").stat().st_size > 30
    spec = importlib.util.spec_from_file_location("billing_under_test", root / task["target"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for value in (0, 1, 19, 99, 100, 101, 199, 12345, 99999):
        expected_fee = value * task["rate"] // 100
        assert module.fee(value) == expected_fee
        assert module.total(value) == value + expected_fee
    if task["negative"]:
        for function in (module.fee, module.total):
            try:
                function(-1)
            except ValueError:
                pass
            else:
                raise AssertionError("negative input accepted")
    # Import into a separate module namespace; client must actually call the API.
    sys.path.insert(0, str(root))
    client_spec = importlib.util.spec_from_file_location("client_under_test", root / "client.py")
    client = importlib.util.module_from_spec(client_spec)
    client_spec.loader.exec_module(client)
    assert client.checkout(12345) == 12345 + 12345 * task["rate"] // 100
    if task["event"] == "rename":
        assert not (root / "billing.py").exists()


if __name__ == "__main__":
    verify(sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
