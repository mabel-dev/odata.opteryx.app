"""Check a new odata revision before it takes traffic.

    python scripts/canary_check.py https://canary---odata-opteryx-app-git-....run.app

Run by .github/workflows/deploy.yaml against the revision it has just deployed
with `--no-traffic --tag canary`: the tag gives that revision its own URL while
every user is still on the previous one. Exit 0 promotes it; anything else
leaves traffic where it was.

The requests are anonymous, so this exercises what an anonymous caller can
reach - the `demo` workspace - and nothing that needs a token. That is why
Secret Manager stays in the startup gate (app/preflight.py) and is not left to
this check.

What it proves, in order:

1. the process is up                   /health/live
2. every dependency, from INSIDE the   /health - the full `health_checks` set,
   new revision                         including the catalog and data bucket
                                        the startup gate no longer probes
3. the service document                /api/v4/ lists the demo workspace
4. the metadata document               /api/v4/$metadata is an EDMX document
5. a real read through the engine      demo.geopolitics.countries: rows, count
6. a pushed-down filter and select     region eq 'Europe': only those rows, only
                                        those columns
7. the error path                      an unknown dataset is a 404, not a 500

Standard library only: the workflow runs it on the runner's own Python with
nothing installed.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# A new revision with min-instances 0 cold-starts on the first request, and
# that start runs the startup gate before the port opens.
FIRST_REQUEST_TIMEOUT = 120
REQUEST_TIMEOUT = 60
STARTUP_ATTEMPTS = 6

# A stable, small, anonymous-readable table. If it is ever moved or reshaped,
# change it here - the canary has to read SOMETHING real.
TABLE = "demo/geopolitics/countries"


class CanaryFailure(Exception):
    pass


def _get(base, path, params=None, timeout=REQUEST_TIMEOUT):
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "odata-deploy-canary"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _json(status, body, what):
    if status != 200:
        raise CanaryFailure(f"{what}: HTTP {status}: {body[:500]!r}")
    try:
        return json.loads(body)
    except ValueError as error:
        raise CanaryFailure(f"{what}: not JSON ({error}): {body[:200]!r}") from error


def check_live(base):
    last = None
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        try:
            status, body = _get(base, "/health/live", timeout=FIRST_REQUEST_TIMEOUT)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last = f"{type(error).__name__}: {error}"
        else:
            if status == 200:
                return "up"
            last = f"HTTP {status}: {body[:200]!r}"
        time.sleep(min(5 * attempt, 20))
    raise CanaryFailure(f"/health/live never answered 200 ({last})")


def check_health(base):
    payload = _json(*_get(base, "/health"), "/health")
    failed = [c for c in payload.get("checks", []) if not c.get("ok")]
    if payload.get("status") != "ok" or failed:
        raise CanaryFailure(f"/health is not ok: {json.dumps(failed or payload)[:800]}")
    names = sorted(c["name"] for c in payload["checks"])
    return ", ".join(names)


def check_service_document(base):
    payload = _json(*_get(base, "/api/v4/"), "service document")
    names = [entry.get("name", "") for entry in payload.get("value", [])]
    if not any(name.startswith("demo.") for name in names):
        raise CanaryFailure(f"service document lists no demo dataset: {names[:10]}")
    return f"{len(names)} datasets"


def check_metadata(base):
    status, body = _get(base, "/api/v4/$metadata")
    if status != 200:
        raise CanaryFailure(f"$metadata: HTTP {status}: {body[:300]!r}")
    if b"Edmx" not in body[:1000] or b"EntityContainer" not in body:
        raise CanaryFailure(f"$metadata is not an EDMX document: {body[:300]!r}")
    return f"{len(body)} bytes"


def check_read(base):
    payload = _json(
        *_get(base, f"/api/v4/{TABLE}", {"$top": "3", "$count": "true"}), f"read {TABLE}"
    )
    rows = payload.get("value")
    count = payload.get("@odata.count")
    if not rows or len(rows) > 3:
        raise CanaryFailure(f"read {TABLE}: expected 1-3 rows, got {rows!r:.300}")
    if not isinstance(count, int) or count < len(rows):
        raise CanaryFailure(f"read {TABLE}: @odata.count {count!r} for {len(rows)} rows")
    return f"{len(rows)} rows of {count}"


def check_filter(base):
    params = {
        "$filter": "region eq 'Europe'",
        "$select": "country_name_common,region",
        "$top": "5",
    }
    payload = _json(*_get(base, f"/api/v4/{TABLE}", params), f"filter {TABLE}")
    rows = payload.get("value") or []
    if not rows:
        raise CanaryFailure(f"filter {TABLE}: region eq 'Europe' returned no rows")
    wrong = [row for row in rows if row.get("region") != "Europe"]
    if wrong:
        raise CanaryFailure(f"filter {TABLE}: rows outside the filter: {wrong[:3]}")
    extra = {key for row in rows for key in row} - {"country_name_common", "region"}
    if extra:
        raise CanaryFailure(f"filter {TABLE}: columns outside $select: {sorted(extra)}")
    return f"{len(rows)} rows"


def check_not_found(base):
    status, body = _get(base, "/api/v4/demo/geopolitics/no_such_dataset_canary")
    if status != 404:
        raise CanaryFailure(f"unknown dataset: expected 404, got HTTP {status}: {body[:300]!r}")
    return "404"


CHECKS = (
    ("process up", check_live),
    ("dependencies", check_health),
    ("service document", check_service_document),
    ("metadata", check_metadata),
    ("read", check_read),
    ("filter + select", check_filter),
    ("unknown dataset", check_not_found),
)


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0]} <revision url>", file=sys.stderr)
        return 2
    base = argv[1]
    print(f"canary: {base}")
    for name, check in CHECKS:
        started = time.monotonic()
        try:
            detail = check(base)
        except CanaryFailure as failure:
            print(f"FAIL  {name}: {failure}")
            return 1
        except Exception as error:  # noqa: BLE001 - a crash in a check fails the canary
            print(f"FAIL  {name}: {type(error).__name__}: {error}")
            return 1
        print(f"ok    {name}: {detail} ({time.monotonic() - started:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
