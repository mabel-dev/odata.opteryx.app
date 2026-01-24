import importlib.util
import sys
from pathlib import Path

# Make repo root and app importable when running this script directly
_proj_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_proj_root))
sys.path.insert(0, str(_proj_root / "app"))
# Add workspace-level dev packages (same as tests do)
_workspace_root = _proj_root.parent
sys.path.insert(1, str(_workspace_root / "opteryx-core"))
sys.path.insert(1, str(_workspace_root / "pyiceberg-firestore-gcs"))

spec = importlib.util.spec_from_file_location(
    "test_odata_battery", str(_proj_root / "tests" / "test_odata_battery.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
run_test_case = module.run_test_case

cases = [
    ("$planets", "$filter=id add 1 gt 5 and id sub 1 lt 8"),
    ("$planets", "$filter=id in (1,2,3) and mass gt 0.5"),
    ("$planets", "$filter=indexof(name,'u') ge 0"),
    ("$planets", "$filter=number_of_moons in (0,1,2)"),
    ("$planets", "$filter=ceiling(mass) gt 1000"),
    ("$planets", "$filter=floor(mass) lt 1"),
    ("$planets", "$filter=ceiling(mass) eq 1"),
    ("$planets", "$filter=floor(mass) eq 0"),
    ("$planets", "$filter=floor(number_of_moons) lt 5"),
    ("$planets", "$filter=ceiling(mass) gt 1 and floor(number_of_moons) gt 0"),
    ("$planets", "$filter=(id eq 1 or id eq 2) and mass lt 1"),
    ("$planets", "$filter=((id eq 1 or id eq 2) and mass lt 1) or id eq 3"),
    ("$planets", "$filter=not(id gt 3 and mass lt 10)"),
    ("$planets", "$filter=((id lt 3) or (id gt 7)) and number_of_moons lt 10"),
    ("$planets", "$filter=not((id lt 3) and (mass gt 0.5))"),
]

for ds, qs in cases:
    res = run_test_case(ds, qs, None, None, None)
    print("\nQUERY:", qs)
    print("PASSED:", res["passed"])
    print("ACTUAL_ROWS:", res.get("actual_rows"))
    print("FULL_RESULT:", res)
    if res.get("rows"):
        for r in res["rows"]:
            print("  ", {k: r[k] for k in ("id", "name", "mass", "number_of_moons") if k in r})
    print("REASON:", res.get("reason") or res.get("error"))
