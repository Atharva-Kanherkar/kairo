"""Path helpers with no dependencies (usable from Python 3.9 scripts)."""


def is_test_path(path: str) -> bool:
    """Heuristic: added or edited tests are allowed on a no-bug task; source edits are not."""
    parts = path.lower().split("/")
    name = parts[-1]
    return (any(p in ("test", "tests", "testing", "__tests__", "testdata") for p in parts[:-1])
            or name.startswith("test_") or name.endswith(("_test.py", "_test.go", "_tests.rs", ".test.ts", ".spec.ts"))
            or name == "conftest.py")
