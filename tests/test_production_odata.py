#!/usr/bin/env python3
"""Test script for production OData service.

This script authenticates with the external auth service and tests the OData API.

Environment Variables:
    ODATA_USERNAME: Username for authentication (alias: CLIENT_ID)
    ODATA_PASSWORD: Password for authentication (alias: CLIENT_SECRET)
    ODATA_URL: Base URL of the OData service (default: https://odata.opteryx.app)
    AUTH_URL: Base URL of the auth service (default: https://authenticate.opteryx.app)

Examples:
    # Run full test suite
    export ODATA_USERNAME=testuser
    export ODATA_PASSWORD=testpass
    python test_production_odata.py

    # Test a specific URL
    export ODATA_USERNAME=testuser
    export ODATA_PASSWORD=testpass
    python test_production_odata.py "https://odata.opteryx.app/api/v4/public/examples/vulnerabilities?$top=1&$orderby=published_at%20desc"

    # Test a specific URL with custom base URL
    export ODATA_USERNAME=testuser
    export ODATA_PASSWORD=testpass
    export ODATA_URL=https://custom-odata.example.com
    python test_production_odata.py "https://custom-odata.example.com/api/v4/public/examples/vulnerabilities?$top=1"
"""

import os
import sys
from typing import Optional

import requests
from jose import jwt as jose_jwt
from orso.logging import get_logger

logger = get_logger()


def get_jwt_from_credentials(username: str, password: str, auth_url: str) -> str:
    """Authenticate with external auth service and get JWT.

    Args:
        username: Client ID / username
        password: Client secret / password
        auth_url: Base URL of the auth service

    Returns:
        str: JWT access token

    Raises:
        Exception: If authentication fails
    """
    token_endpoint = f"{auth_url.rstrip('/')}/token"
    timeout = float(os.environ.get("AUTH_SERVICE_TIMEOUT", "10"))

    print(f"🔐 Authenticating with: {token_endpoint}")

    data = {
        "grant_type": "client_credentials",
        "client_id": username,
        "client_secret": password,
    }

    try:
        response = requests.post(
            token_endpoint,
            params={"set_cookie": "false"},
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )

        if response.status_code != 200:
            print(f"❌ Authentication failed: {response.status_code}")
            print(f"   Response: {response.text}")
            sys.exit(1)

        response_data = response.json()
        access_token = response_data.get("access_token")

        if not access_token:
            print("❌ No access_token in auth service response")
            sys.exit(1)

        # Decode and display JWT info
        claims = jose_jwt.get_unverified_claims(access_token)
        identity = (
            claims.get("sub") or claims.get("email") or claims.get("preferred_username") or username
        )
        print(f"✅ Authentication successful for user: {identity}")

        return access_token

    except requests.Timeout:
        print(f"❌ Auth service timeout after {timeout}s")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"❌ Auth service connection error: {exc}")
        sys.exit(1)


def test_odata_service_document(jwt_token: str, odata_url: str) -> bool:
    """Test the OData service document endpoint.

    Args:
        jwt_token: JWT token for authentication
        odata_url: Base URL of the OData service

    Returns:
        bool: True if test passed

    Raises:
        Exception: If test fails
    """
    endpoint = f"{odata_url.rstrip('/')}/api/v4/"
    headers = {"Authorization": f"Bearer {jwt_token}"}

    print(f"\n📋 Testing OData Service Document: {endpoint}")

    try:
        response = requests.get(endpoint, headers=headers, timeout=10)

        if response.status_code != 200:
            print(f"❌ Service document request failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False

        data = response.json()
        datasets = data.get("value", [])
        print("✅ Service document retrieved successfully")
        print(f"   Found {len(datasets)} dataset(s)")
        for dataset in datasets[:5]:  # Show first 5
            print(f"      - {dataset.get('name', 'Unknown')}")
        if len(datasets) > 5:
            print(f"      ... and {len(datasets) - 5} more")

        return True

    except requests.RequestException as exc:
        print(f"❌ Service document request error: {exc}")
        return False


def test_odata_metadata(jwt_token: str, odata_url: str) -> bool:
    """Test the OData metadata endpoint.

    Args:
        jwt_token: JWT token for authentication
        odata_url: Base URL of the OData service

    Returns:
        bool: True if test passed
    """
    endpoint = f"{odata_url.rstrip('/')}/api/v4/$metadata"
    headers = {"Authorization": f"Bearer {jwt_token}"}

    print(f"\n📋 Testing OData Metadata: {endpoint}")

    try:
        response = requests.get(endpoint, headers=headers, timeout=10)

        if response.status_code != 200:
            print(f"❌ Metadata request failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False

        # Should return XML
        if "<?xml" not in response.text and "edmx" not in response.text.lower():
            print("⚠️  Response doesn't look like EDMX metadata")
            print(f"   First 200 chars: {response.text[:200]}")
            return False

        print("✅ Metadata retrieved successfully")
        print(f"   Response size: {len(response.text)} bytes")

        return True

    except requests.RequestException as exc:
        print(f"❌ Metadata request error: {exc}")
        return False


def test_odata_query_with_options(
    jwt_token: str, odata_url: str, dataset_path: Optional[str] = None
) -> bool:
    """Test OData query with various options.

    Args:
        jwt_token: JWT token for authentication
        odata_url: Base URL of the OData service
        dataset_path: Optional dataset path (will get from service document if not provided)

    Returns:
        bool: True if test passed
    """
    headers = {"Authorization": f"Bearer {jwt_token}"}

    # If no dataset provided, try to get one from the service document
    if not dataset_path:
        try:
            response = requests.get(
                f"{odata_url.rstrip('/')}/api/v4/",
                headers=headers,
                timeout=10,
            )
            if response.status_code == 200:
                datasets = response.json().get("value", [])
                if datasets:
                    dataset_path = datasets[0].get("url")
                    print("\n📊 Testing OData Query using first available dataset")
                else:
                    print("\n⚠️  No datasets available, skipping query test")
                    return True
            else:
                print("\n⚠️  Could not retrieve dataset list, skipping query test")
                return True
        except Exception as exc:
            print(f"\n⚠️  Could not retrieve dataset list: {exc}, skipping query test")
            return True

    if not dataset_path:
        print("\n⚠️  No dataset path available, skipping query test")
        return True

    endpoint = f"{odata_url.rstrip('/')}/api/v4/{dataset_path}"

    # Test with $top parameter
    params = {"$top": "10"}
    print(f"   Endpoint: {endpoint}")
    print(f"   Parameters: {params}")

    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=10)

        if response.status_code != 200:
            print(f"❌ Query request failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False

        data = response.json()
        records = data.get("value", [])
        print("✅ Query executed successfully")
        print(f"   Records returned: {len(records)}")
        if records:
            print(f"   First record keys: {list(records[0].keys())}")

        return True

    except requests.RequestException as exc:
        print(f"❌ Query request error: {exc}")
        return False


def test_odata_with_filter(jwt_token: str, odata_url: str) -> bool:
    """Test OData query with filter on public.github.events.

    Tests filtering by actor_login eq 'Copilot' with count=true and top=0.

    Args:
        jwt_token: JWT token for authentication
        odata_url: Base URL of the OData service

    Returns:
        bool: True if test passed
    """
    headers = {"Authorization": f"Bearer {jwt_token}"}
    dataset_path = "public/github/events"
    endpoint = f"{odata_url.rstrip('/')}/api/v4/{dataset_path}"

    print("\n🔍 Testing OData Filter on public.github.events")
    print(f"   Endpoint: {endpoint}")

    # Test with filter for Copilot and count
    params = {
        "$filter": "actor_login eq 'Copilot'",
        "$count": "true",
        "$top": "0",
    }
    print("   Filter: actor_login eq 'Copilot'")
    print("   Count: true, Top: 0")

    try:
        import time

        start_time = time.time()
        response = requests.get(endpoint, params=params, headers=headers, timeout=120)
        elapsed_time = time.time() - start_time
        print(f"   Request completed in {elapsed_time:.2f} seconds")

        if response.status_code != 200:
            print(f"❌ Filter request failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False

        data = response.json()
        count = data.get("@odata.count")
        records = data.get("value", [])
        print("✅ Filter executed successfully")
        if count is not None:
            print(f"   Count: {count}")
        print(f"   Records returned: {len(records)}")

        return True

    except requests.RequestException as exc:
        print(f"❌ Filter request error: {exc}")
        return False


def test_health_endpoint(odata_url: str) -> bool:
    """Test the health check endpoint (no auth required).

    Args:
        odata_url: Base URL of the OData service

    Returns:
        bool: True if test passed
    """
    endpoint = f"{odata_url.rstrip('/')}/health"

    print(f"\n❤️  Testing Health Endpoint: {endpoint}")

    try:
        response = requests.get(endpoint, timeout=10)

        if response.status_code != 200:
            print(f"❌ Health check failed: {response.status_code}")
            return False

        print("✅ Health check passed")
        return True

    except requests.RequestException as exc:
        print(f"❌ Health check error: {exc}")
        return False


def test_custom_url(jwt_token: str, url: str) -> bool:
    """Test a custom OData URL with the provided JWT.

    Args:
        jwt_token: JWT token for authentication
        url: Full URL to test (including query parameters)

    Returns:
        bool: True if test passed
    """
    headers = {"Authorization": f"Bearer {jwt_token}"}

    print("\n🎯 Testing Custom URL")
    print(f"   URL: {url}")

    try:
        import time

        start_time = time.time()
        response = requests.get(url, headers=headers, timeout=120)
        elapsed_time = time.time() - start_time
        print(f"   Request completed in {elapsed_time:.2f} seconds")

        if response.status_code != 200:
            print(f"❌ Request failed with status: {response.status_code}")
            print(f"   Response: {response.text[:500]}")
            return False

        data = response.json()
        count = data.get("@odata.count")
        records = data.get("value", [])

        print("✅ Request executed successfully")
        if count is not None:
            print(f"   Count: {count}")
        print(f"   Records returned: {len(records)}")

        if records:
            print(f"   First record has {len(records[0])} fields")
            # Show first 3 fields
            first_record = records[0]
            for i, (key, value) in enumerate(first_record.items()):
                if i >= 3:
                    break
                # Truncate long values
                val_str = str(value)[:50] if len(str(value)) > 50 else str(value)
                print(f"      {key}: {val_str}")
            if len(first_record) > 3:
                print(f"      ... and {len(first_record) - 3} more fields")

        return True

    except requests.RequestException as exc:
        print(f"❌ Request error: {exc}")
        return False


def main():
    """Run all tests or test a specific URL."""
    # Parse command-line arguments
    if len(sys.argv) > 1:
        custom_url = sys.argv[1]
        test_custom = True
    else:
        custom_url = None
        test_custom = False

    # Get configuration from environment
    username = os.environ.get("CLIENT_ID") or os.environ.get("ODATA_USERNAME") or "bastian"
    password = (
        os.environ.get("CLIENT_SECRET")
        or os.environ.get("ODATA_PASSWORD")
        or "opt_AFMxwYdUDFHKHg3iBV6FeD9_ImMINuG1CIj3msdaF7A_01"
    )
    odata_url = os.environ.get("ODATA_URL", "https://odata.opteryx.app")
    auth_url = os.environ.get("AUTH_URL", "https://authenticate.opteryx.app")

    if not username or not password:
        print("❌ Missing credentials!")
        print("   Please set CLIENT_ID and CLIENT_SECRET (or ODATA_USERNAME and ODATA_PASSWORD)")
        print("   environment variables")
        sys.exit(1)

    print("=" * 80)
    if test_custom:
        print("🧪 OData Custom URL Test")
    else:
        print("🧪 OData Service Production Test Suite")
    print("=" * 80)
    print(f"OData Service URL: {odata_url}")
    print(f"Auth Service URL: {auth_url}")
    print(f"Username: {username}")
    print()

    # Step 1: Get JWT from auth service
    jwt_token = get_jwt_from_credentials(username, password, auth_url)

    # Step 2: Run tests
    if test_custom:
        # Test custom URL
        result = test_custom_url(jwt_token, custom_url)
        print("\n" + "=" * 80)
        print("📊 Test Result")
        print("=" * 80)
        if result:
            print("✅ PASS - Custom URL test succeeded")
            return 0
        else:
            print("❌ FAIL - Custom URL test failed")
            return 1
    else:
        # Run full test suite
        results = []

        # Test health endpoint (doesn't require JWT)
        results.append(("Health Endpoint", test_health_endpoint(odata_url)))

        # Test OData endpoints (require JWT)
        results.append(("Service Document", test_odata_service_document(jwt_token, odata_url)))
        results.append(("Metadata", test_odata_metadata(jwt_token, odata_url)))
        results.append(("Query", test_odata_query_with_options(jwt_token, odata_url)))
        results.append(("Filter", test_odata_with_filter(jwt_token, odata_url)))

        # Summary
        print("\n" + "=" * 80)
        print("📊 Test Summary")
        print("=" * 80)

        passed = sum(1 for _, result in results if result)
        total = len(results)

        for test_name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status:8} {test_name}")

        print()
        print(f"Passed: {passed}/{total}")

        if passed == total:
            print("\n🎉 All tests passed!")
            return 0
        else:
            print(f"\n⚠️  {total - passed} test(s) failed")
            return 1


if __name__ == "__main__":
    sys.exit(main())
