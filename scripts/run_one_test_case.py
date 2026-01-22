#!/usr/bin/env python3
import sys
from pathlib import Path
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

from tests.test_odata_battery import parse_odata_query_string, run_test_case

q = "$count=true&$top=0&$apply=groupby((n_regionkey),aggregate($count as NationCount))&$orderby=NationCount desc&$top=1"
print('parsed params:', parse_odata_query_string(q))
res = run_test_case('testdata.tpch.nation', q, None, None, 'Exception')
print('run_test_case result:', res)
