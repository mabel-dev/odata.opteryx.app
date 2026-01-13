#!/usr/bin/env python3
"""Quick script to test the OData read endpoint.

Usage examples:
  python scripts/test_odata.py --base http://localhost:8000 --workstream demo --collection public --dataset users
  python scripts/test_odata.py --base http://localhost:8000 --workstream demo --collection public --dataset users --token "$TOKEN"
"""

import argparse
import json
import sys

import requests


def parse_args():
    p = argparse.ArgumentParser(description="Test OData read endpoint")
    p.add_argument("--base", required=True, help="Base URL of service (e.g. http://localhost:8000)")
    p.add_argument("--workstream", required=True)
    p.add_argument("--collection", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--token", help="Bearer token for Authorization header")
    p.add_argument("--filter", help="$filter value to send (will be sent as $filter)")
    p.add_argument("--top", type=int, help="$top value")
    return p.parse_args()


def main():
    args = parse_args()
    base = args.base.rstrip("/")
    url = f"{base}/api/v1/{args.workstream}/{args.collection}/{args.dataset}"

    params = {}
    if args.filter:
        params["$filter"] = args.filter
    if args.top is not None:
        params["$top"] = str(args.top)

    headers = {"Accept": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
    except Exception as e:
        print("Request failed:", e, file=sys.stderr)
        sys.exit(2)

    print(f"HTTP {resp.status_code}")
    ct = resp.headers.get("content-type", "")
    if "json" in ct:
        try:
            obj = resp.json()
            print(json.dumps(obj, indent=2, sort_keys=True))
        except Exception:
            print(resp.text)
    else:
        print(resp.text)


if __name__ == "__main__":
    main()
