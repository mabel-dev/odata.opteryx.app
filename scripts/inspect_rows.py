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

queries = [
    "$filter=id add 1 gt 5 and id sub 1 lt 8",
    "$filter=id in (1,2,3) and mass gt 0.5",
    "$filter=indexof(name,'u') ge 0",
    "$filter=number_of_moons in (0,1,2)",
]

for qs in queries:
    params = mod.parse_odata_query_string(qs)
    res = mod.execute_odata_query("$planets", filter_expr=params.get("filter_expr"))
    print("\nQUERY:", qs)
    print("ROWS:", len(res["rows"]))
    for r in res["rows"]:
        print("  ", {k: r.get(k) for k in ["id", "name", "mass", "number_of_moons"]})
