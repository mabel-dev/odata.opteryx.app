#!/usr/bin/env python3
"""Quick script to test the OData read endpoint.

Usage examples:
  # Using environment variables
  export ODATA_USERNAME="alice"
  export ODATA_PASSWORD="secret"
  export ODATA_BASE_URL="http://localhost:8000"
  python scripts/test_odata.py --workstream demo --collection public --dataset users

  # Using command line arguments
  python scripts/test_odata.py --base http://localhost:8000 --workstream demo --collection public --dataset users --token "$TOKEN"
  python scripts/test_odata.py --base https://odata.opteryx.app --workstream demo --collection public --dataset users --username alice --password secret

  # Test v4 endpoints
  python scripts/test_odata.py --api-version v4 --workstream personal --collection alice --dataset projects
"""

import argparse
import json
import os
import sys

import requests


def parse_args():
    p = argparse.ArgumentParser(description="Test OData read endpoint")
    p.add_argument(
        "--base",
        default=os.environ.get("ODATA_BASE_URL", "https://odata.opteryx.app"),
        help="Base URL of service (default: from ODATA_BASE_URL env var or https://odata.opteryx.app)",
    )
    p.add_argument(
        "--api-version",
        default="v1",
        choices=["v1", "v4"],
        help="API version to use (default: v1)",
    )
    p.add_argument("--workstream", required=True)
    p.add_argument("--collection", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--token", help="Bearer token for Authorization header")
    p.add_argument(
        "--username",
        default=os.environ.get("ODATA_USERNAME"),
        help="Username for HTTP Basic authentication (default: from ODATA_USERNAME env var)",
    )
    p.add_argument(
        "--password",
        default=os.environ.get("ODATA_PASSWORD"),
        help="Password for HTTP Basic authentication (default: from ODATA_PASSWORD env var)",
    )
    p.add_argument("--filter", help="$filter value to send (will be sent as $filter)")
    p.add_argument("--top", type=int, help="$top value")
    p.add_argument(
        "--test-service-doc",
        action="store_true",
        help="Test service document endpoint (GET /api/{version}/)",
    )
    p.add_argument(
        "--test-metadata",
        action="store_true",
        help="Test EDMX metadata endpoint (GET /api/{version}/$metadata)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    base = args.base.rstrip("/")

    # Test service document if requested
    if args.test_service_doc:
        url = f"{base}/api/{args.api_version}/"
        print(f"\n=== Testing Service Document: {url} ===")
        test_endpoint(url, args)
        return

    # Test metadata if requested
    if args.test_metadata:
        url = f"{base}/api/{args.api_version}/$metadata"
        print(f"\n=== Testing EDMX Metadata: {url} ===")
        test_endpoint(url, args)
        return

    # Test data endpoint
    url = f"{base}/api/{args.api_version}/{args.workstream}/{args.collection}/{args.dataset}"
    print(f"\n=== Testing Data Endpoint: {url} ===")

    params = {}
    if args.filter:
        params["$filter"] = args.filter
    if args.top is not None:
        params["$top"] = str(args.top)

    test_endpoint(url, args, params)


def test_endpoint(url: str, args, params: dict | None = None):
    """Test a single endpoint with authentication."""
    headers = {"Accept": "application/json"}
    auth = None

    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    elif args.username and args.password:
        auth = (args.username, args.password)
    else:
        print(
            "Warning: No authentication provided. Set ODATA_USERNAME/ODATA_PASSWORD env vars or use --username/--password",
            file=sys.stderr,
        )

    try:
        resp = requests.get(url, params=params, headers=headers, auth=auth, timeout=10)
    except requests.RequestException as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"HTTP {resp.status_code}")
    print(f"OData-Version: {resp.headers.get('OData-Version', 'Not present')}")

    ct = resp.headers.get("content-type", "")
    if "json" in ct:
        try:
            obj = resp.json()
            print(json.dumps(obj, indent=2, sort_keys=True))
        except ValueError:
            print(resp.text)
    elif "xml" in ct:
        print(resp.text[:1000])  # Print first 1000 chars of XML
        if len(resp.text) > 1000:
            print(f"... (truncated, total {len(resp.text)} characters)")
    else:
        print(resp.text)


if __name__ == "__main__":
    main()
