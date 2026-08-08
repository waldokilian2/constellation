"""Constellation regression runner — stdlib only (repo convention: no extra deps).

Usage: python tests/run_tests.py [module ...]
Default: run every test_*.py in tests/.
"""
from __future__ import annotations
import importlib.util, pathlib, sys

TESTS = pathlib.Path(__file__).resolve().parent

sys.path.insert(0, str(TESTS.parent))  # repo root — engine.* must be importable

def main():
    modules = sys.argv[1:] or sorted(p.stem for p in TESTS.glob("test_*.py"))
    failed = 0
    for name in modules:
        spec = importlib.util.spec_from_file_location(name, TESTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for fn_name in sorted(dir(mod)):
            if fn_name.startswith("test_") and callable(getattr(mod, fn_name)):
                try:
                    getattr(mod, fn_name)()
                    print(f"PASS {name}::{fn_name}")
                except Exception as e:
                    failed += 1
                    print(f"FAIL {name}::{fn_name}: {e}")
    print("DONE" if not failed else f"{failed} FAILURE(S)")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())