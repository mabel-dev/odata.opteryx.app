#!/usr/bin/env python
"""Debug filter test results using test framework."""

import sys

import opteryx

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from tests.test_odata_battery import parse_odata_query_string

sys.path.insert(0, "/Users/justin/Nextcloud/opteryx-cloud/odata.opteryx")


tests = [
    (
        "$filter=number_of_moons gt 0 and mass gt 50&$top=10&$select=id,name,number_of_moons,mass",
        "number_of_moons gt 0 and mass gt 50",
    ),
    (
        "$filter=number_of_moons gt 0 or mass gt 100&$top=10&$select=id,name,number_of_moons,mass",
        "number_of_moons gt 0 or mass gt 100",
    ),
    (
        "$filter=distance_from_sun lt 500 and number_of_moons eq 0&$top=10&$select=id,name,distance_from_sun,number_of_moons",
        "distance_from_sun lt 500 and number_of_moons eq 0",
    ),
]

for query_string, description in tests:
    try:
        params = parse_odata_query_string(query_string)

        # Build logical plan
        builder = ODataLogicalPlanBuilder("$planets")
        plan = builder.build(
            filter_expr=params.get("filter_expr"),
            select_cols=params.get("select_cols"),
            orderby_expr=params.get("orderby_expr"),
            top=params.get("top"),
            skip=params.get("skip"),
        )

        # Execute
        arrow_table = opteryx.execute_logical_plan(plan)
        col_names = arrow_table.column_names
        columns_dict = arrow_table.to_pydict()

        print(f"\nFilter: {description}")
        print(f"Rows: {arrow_table.num_rows}")
        for i in range(arrow_table.num_rows):
            row = {col: columns_dict[col][i] for col in col_names}
            print(f"  {row}")
    except Exception as e:
        print(f"\nFilter: {description}")
        print(f"ERROR: {str(e)[:80]}")
