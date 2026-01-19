#!/usr/bin/env python3
"""Quick example of running the OData test battery."""

from test_odata_battery_example import QUICK_TEST_BATTERY
from test_odata_battery_example import run_battery

if __name__ == "__main__":
    print("=" * 80)
    print("OData Quick Test Battery Example")
    print("=" * 80)
    print()

    # Print the test battery format
    print("Test Battery Format: (dataset, query, expected_rows, expected_columns, expected_error)")
    print()
    print("Examples:")
    for i, (dataset, query, rows, cols, error) in enumerate(QUICK_TEST_BATTERY[:3], 1):
        print(f"  {i}. Dataset: {dataset}")
        print(f"     Query: {query}")
        print(f"     Expected: {rows} rows, {cols} columns, error={error}")
        print()

    print("=" * 80)
    print("Running Quick Battery")
    print("=" * 80)
    print()

    # Run the battery
    summary = run_battery(QUICK_TEST_BATTERY)

    print()
    print("=" * 80)
    print("Results")
    print("=" * 80)
    print(f"Total:   {summary['total']}")
    print(f"Passed:  {summary['passed']}")
    print(f"Failed:  {summary['failed']}")
    print(f"Time:    {summary['elapsed']:.2f}s")
    print()

    if summary["failed"] == 0:
        print("✅ All tests passed!")
    else:
        print(f"❌ {summary['failed']} test(s) failed")

        # Show failed tests
        failed_count = 0
        for result in summary["results"]:
            if not result["passed"]:
                failed_count += 1
                if failed_count <= 5:
                    print(f"\nTest #{result['test_num']}: {result['dataset']}")
                    print(f"  Query: {result['query']}")
                    print(f"  Expected: {result['expected']}")
                    print(
                        f"  Got: rows={result['actual_rows']}, columns={result['actual_columns']}, error={result['error']}"
                    )
                    print(f"  Reason: {result['reason']}")

        if summary["failed"] > 5:
            print(f"\n... and {summary['failed'] - 5} more failures")
