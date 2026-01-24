import importlib.util
import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_repo_root = _scripts_dir.parent
sys.path.insert(0, str(_repo_root))
spec = importlib.util.spec_from_file_location(
    "test_odata_battery", str(_repo_root / "tests" / "test_odata_battery.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

cases = [
    ("$planets", "$filter=number_of_moons sub 5 gt 10", 2),
    ("$planets", "$filter=id add 1 gt 5 and id sub 1 lt 8", 2),
    ("$planets", "$filter=ceiling(mass) gt 1000", 2),
    ("$planets", "$filter=floor(mass) lt 1", 4),
    ("$planets", "$filter=ceiling(mass) eq 1", 4),
    ("$planets", "$filter=floor(mass) eq 0", 4),
    ("$planets", "$filter=floor(number_of_moons) lt 5", 5),
    ("$planets", "$filter=ceiling(mass) gt 1 and floor(number_of_moons) gt 0", 6),
    ("testdata.tpch.nation", "$filter=round(n_nationkey) mod 5 eq 0&$top=20", None),
    ("$planets", "$filter=indexof(name,'u') ge 0", 5),
    ("$planets", "$filter=number_of_moons in (0,1,2)", 5),
    ("$planets", "$filter=id in (1,2,3) and mass gt 0.5", 1),
    ("$planets", "$filter=(id eq 1 or id eq 2) and mass lt 1", 2),
    ("$planets", "$filter=((id eq 1 or id eq 2) and mass lt 1) or id eq 3", 3),
    ("$planets", "$filter=not(id gt 3 and mass lt 10)", 4),
    ("$planets", "$filter=((id lt 3) or (id gt 7)) and number_of_moons lt 10", 5),
    ("$planets", "$filter=not((id lt 3) and (mass gt 0.5))", 7),
]

for dataset, qs, expected in cases:
    print("\n=== QUERY:", dataset, qs)
    try:
        params = mod.parse_odata_query_string(qs)
        res = mod.execute_odata_query(dataset, filter_expr=params.get("filter_expr"))
        rows = res["rows"]
        print("Expected:", expected, "Got:", len(rows))
        for r in rows:
            print(
                "  ",
                {k: r.get(k) for k in ["id", "name", "mass", "number_of_moons", "n_nationkey"]},
            )
    except Exception as e:
        print("ERROR:", repr(e))

print("\nDone.")
