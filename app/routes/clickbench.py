"""Internal ClickBench benchmark runner.

Not part of the public OData API: excluded from the OpenAPI schema and gated
behind the `data_admin` entitlement (see app.auth.permissions), not just
authentication, since it drives a heavy 43-query scan of a large dataset.

The query bodies below are mirrored from opteryx-core's
`tests/performance/clickbench/opteryx/runner.py` (`STATEMENTS`), which is the
source of truth for the ClickBench battery. That file lives in a sibling repo
and isn't guaranteed to be present at deploy time, so the statements are
copied here rather than imported at runtime -- keep the two in sync by hand
if the upstream battery changes.
"""

from __future__ import annotations

import json
import os
import sys
import time

# Self-contained (mirrors app/routes/v4/interface.py): ensures `import opteryx`
# below resolves to the full opteryx-core package rather than whatever a
# lightweight installed build provides, regardless of module import order.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../opteryx-core")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../opteryx-catalog"))
)

import opteryx
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.auth.deps import require_authentication
from app.auth.permissions import DATA_ADMIN_ENTITLEMENT
from app.auth.permissions import entitlements_from_claims
from app.logging import get_logger

logger = get_logger()

DEFAULT_CLICKBENCH_DATASET = "benchmarks.clickbench.hits"
MAX_ITERATIONS = 5

# fmt:off
STATEMENTS = [
    "SELECT COUNT(*) FROM {DATASET};",
    "SELECT COUNT(*) FROM {DATASET} WHERE AdvEngineID <> 0;",
    "SELECT SUM(AdvEngineID), COUNT(*), AVG(ResolutionWidth) FROM {DATASET};",
    "SELECT AVG(UserID) FROM {DATASET};",
    "SELECT COUNT(DISTINCT UserID) FROM {DATASET};",
    "SELECT COUNT(DISTINCT SearchPhrase) FROM {DATASET};",
    "SELECT MIN(EventDate), MAX(EventDate) FROM {DATASET};",
    "SELECT AdvEngineID, COUNT(*) FROM {DATASET} WHERE AdvEngineID <> 0 GROUP BY AdvEngineID ORDER BY COUNT(*) DESC;",
    "SELECT RegionID, COUNT(DISTINCT UserID) AS u FROM {DATASET} GROUP BY RegionID ORDER BY u DESC LIMIT 10;",
    "SELECT RegionID, SUM(AdvEngineID), COUNT(*) AS c, AVG(ResolutionWidth), COUNT(DISTINCT UserID) FROM {DATASET} GROUP BY RegionID ORDER BY c DESC LIMIT 10;",
    "SELECT MobilePhoneModel, COUNT(DISTINCT UserID) AS u FROM {DATASET} WHERE MobilePhoneModel <> '' GROUP BY MobilePhoneModel ORDER BY u DESC LIMIT 10;",
    "SELECT MobilePhone, MobilePhoneModel, COUNT(DISTINCT UserID) AS u FROM {DATASET} WHERE MobilePhoneModel <> '' GROUP BY MobilePhone, MobilePhoneModel ORDER BY u DESC LIMIT 10;",
    "SELECT SearchPhrase, COUNT(*) AS c FROM {DATASET} WHERE SearchPhrase <> '' GROUP BY SearchPhrase ORDER BY c DESC LIMIT 10;",
    "SELECT SearchPhrase, COUNT(DISTINCT UserID) AS u FROM {DATASET} WHERE SearchPhrase <> '' GROUP BY SearchPhrase ORDER BY u DESC LIMIT 10;",
    "SELECT SearchEngineID, SearchPhrase, COUNT(*) AS c FROM {DATASET} WHERE SearchPhrase <> '' GROUP BY SearchEngineID, SearchPhrase ORDER BY c DESC LIMIT 10;",
    "SELECT UserID, COUNT(*) FROM {DATASET} GROUP BY UserID ORDER BY COUNT(*) DESC LIMIT 10;",
    "SELECT UserID, SearchPhrase, COUNT(*) FROM {DATASET} GROUP BY UserID, SearchPhrase ORDER BY COUNT(*) DESC LIMIT 10;",
    "SELECT UserID, SearchPhrase, COUNT(*) FROM {DATASET} GROUP BY UserID, SearchPhrase LIMIT 10;",
    "SELECT UserID, extract(minute FROM EventTime::TIMESTAMP[s]) AS m, SearchPhrase, COUNT(*) FROM {DATASET} GROUP BY UserID, extract(minute FROM EventTime::TIMESTAMP[s]), SearchPhrase ORDER BY COUNT(*) DESC LIMIT 10;",
    "SELECT UserID FROM {DATASET} WHERE UserID = 435090932899640449;",
    "SELECT COUNT(*) FROM {DATASET} WHERE URL LIKE '%google%';",
    "SELECT SearchPhrase, MIN(URL), COUNT(*) AS c FROM {DATASET} WHERE URL LIKE '%google%' AND SearchPhrase <> '' GROUP BY SearchPhrase ORDER BY c DESC LIMIT 10;",
    "SELECT SearchPhrase, MIN(URL), MIN(Title), COUNT(*) AS c, COUNT(DISTINCT UserID) FROM {DATASET} WHERE Title LIKE '%Google%' AND URL NOT LIKE '%.google.%' AND SearchPhrase <> '' GROUP BY SearchPhrase ORDER BY c DESC LIMIT 10;",
    "SELECT * FROM {DATASET} WHERE URL LIKE '%google%' ORDER BY EventTime LIMIT 10;",
    "SELECT SearchPhrase FROM {DATASET} WHERE SearchPhrase <> '' ORDER BY EventTime LIMIT 10;",
    "SELECT SearchPhrase FROM {DATASET} WHERE SearchPhrase <> '' ORDER BY SearchPhrase LIMIT 10;",
    "SELECT SearchPhrase FROM {DATASET} WHERE SearchPhrase <> '' ORDER BY EventTime, SearchPhrase LIMIT 10;",
    "SELECT CounterID, AVG(length(URL)) AS l, COUNT(*) AS c FROM {DATASET} WHERE URL <> '' GROUP BY CounterID HAVING COUNT(*) > 100000 ORDER BY l DESC LIMIT 25;",
    "SELECT REGEXP_REPLACE(Referer, b'^https?://(?:www\\.)?([^/]+)/.*$', r'\\1') AS k, AVG(length(Referer)) AS l, COUNT(*) AS c, MIN(Referer) FROM {DATASET} WHERE Referer <> '' GROUP BY REGEXP_REPLACE(Referer, b'^https?://(?:www\\.)?([^/]+)/.*$', r'\\1') HAVING COUNT(*) > 100000 ORDER BY l DESC LIMIT 25;",
    "SELECT SUM(ResolutionWidth), SUM(ResolutionWidth + 1), SUM(ResolutionWidth + 2), SUM(ResolutionWidth + 3), SUM(ResolutionWidth + 4), SUM(ResolutionWidth + 5), SUM(ResolutionWidth + 6), SUM(ResolutionWidth + 7), SUM(ResolutionWidth + 8), SUM(ResolutionWidth + 9), SUM(ResolutionWidth + 10), SUM(ResolutionWidth + 11), SUM(ResolutionWidth + 12), SUM(ResolutionWidth + 13), SUM(ResolutionWidth + 14), SUM(ResolutionWidth + 15), SUM(ResolutionWidth + 16), SUM(ResolutionWidth + 17), SUM(ResolutionWidth + 18), SUM(ResolutionWidth + 19), SUM(ResolutionWidth + 20), SUM(ResolutionWidth + 21), SUM(ResolutionWidth + 22), SUM(ResolutionWidth + 23), SUM(ResolutionWidth + 24), SUM(ResolutionWidth + 25), SUM(ResolutionWidth + 26), SUM(ResolutionWidth + 27), SUM(ResolutionWidth + 28), SUM(ResolutionWidth + 29), SUM(ResolutionWidth + 30), SUM(ResolutionWidth + 31), SUM(ResolutionWidth + 32), SUM(ResolutionWidth + 33), SUM(ResolutionWidth + 34), SUM(ResolutionWidth + 35), SUM(ResolutionWidth + 36), SUM(ResolutionWidth + 37), SUM(ResolutionWidth + 38), SUM(ResolutionWidth + 39), SUM(ResolutionWidth + 40), SUM(ResolutionWidth + 41), SUM(ResolutionWidth + 42), SUM(ResolutionWidth + 43), SUM(ResolutionWidth + 44), SUM(ResolutionWidth + 45), SUM(ResolutionWidth + 46), SUM(ResolutionWidth + 47), SUM(ResolutionWidth + 48), SUM(ResolutionWidth + 49), SUM(ResolutionWidth + 50), SUM(ResolutionWidth + 51), SUM(ResolutionWidth + 52), SUM(ResolutionWidth + 53), SUM(ResolutionWidth + 54), SUM(ResolutionWidth + 55), SUM(ResolutionWidth + 56), SUM(ResolutionWidth + 57), SUM(ResolutionWidth + 58), SUM(ResolutionWidth + 59), SUM(ResolutionWidth + 60), SUM(ResolutionWidth + 61), SUM(ResolutionWidth + 62), SUM(ResolutionWidth + 63), SUM(ResolutionWidth + 64), SUM(ResolutionWidth + 65), SUM(ResolutionWidth + 66), SUM(ResolutionWidth + 67), SUM(ResolutionWidth + 68), SUM(ResolutionWidth + 69), SUM(ResolutionWidth + 70), SUM(ResolutionWidth + 71), SUM(ResolutionWidth + 72), SUM(ResolutionWidth + 73), SUM(ResolutionWidth + 74), SUM(ResolutionWidth + 75), SUM(ResolutionWidth + 76), SUM(ResolutionWidth + 77), SUM(ResolutionWidth + 78), SUM(ResolutionWidth + 79), SUM(ResolutionWidth + 80), SUM(ResolutionWidth + 81), SUM(ResolutionWidth + 82), SUM(ResolutionWidth + 83), SUM(ResolutionWidth + 84), SUM(ResolutionWidth + 85), SUM(ResolutionWidth + 86), SUM(ResolutionWidth + 87), SUM(ResolutionWidth + 88), SUM(ResolutionWidth + 89) FROM {DATASET};",
    "SELECT SearchEngineID, ClientIP, COUNT(*) AS c, SUM(IsRefresh), AVG(ResolutionWidth) FROM {DATASET} WHERE SearchPhrase <> '' GROUP BY SearchEngineID, ClientIP ORDER BY c DESC LIMIT 10;",
    "SELECT WatchID, ClientIP, COUNT(*) AS c, SUM(IsRefresh), AVG(ResolutionWidth) FROM {DATASET} WHERE SearchPhrase <> '' GROUP BY WatchID, ClientIP ORDER BY c DESC LIMIT 10;",
    "SELECT WatchID, ClientIP, COUNT(*) AS c, SUM(IsRefresh), AVG(ResolutionWidth) FROM {DATASET} GROUP BY WatchID, ClientIP ORDER BY c DESC LIMIT 10;",
    "SELECT URL, COUNT(*) AS c FROM {DATASET} GROUP BY URL ORDER BY c DESC LIMIT 10;",
    "SELECT 1, URL, COUNT(*) AS c FROM {DATASET} GROUP BY 1, URL ORDER BY c DESC LIMIT 10;",
    "SELECT ClientIP, ClientIP - 1, ClientIP - 2, ClientIP - 3, COUNT(*) AS c FROM {DATASET} GROUP BY ClientIP, ClientIP - 1, ClientIP - 2, ClientIP - 3 ORDER BY c DESC LIMIT 10;",
    "SELECT URL, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-01'::DATE AND EventDate::DATE <= '2013-07-31'::DATE AND DontCountHits = 0 AND IsRefresh = 0 AND URL <> '' GROUP BY URL ORDER BY PageViews DESC LIMIT 10;",
    "SELECT Title, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-01'::DATE AND EventDate::DATE <= '2013-07-31'::DATE AND DontCountHits = 0 AND IsRefresh = 0 AND Title <> '' GROUP BY Title ORDER BY PageViews DESC LIMIT 10;",
    "SELECT URL, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-01'::DATE AND EventDate::DATE <= '2013-07-31'::DATE AND IsRefresh = 0 AND IsLink <> 0 AND IsDownload = 0 GROUP BY URL ORDER BY PageViews DESC LIMIT 10 OFFSET 1000;",
    "SELECT TraficSourceID, SearchEngineID, AdvEngineID, CASE WHEN (SearchEngineID = 0 AND AdvEngineID = 0) THEN Referer ELSE '' END AS Src, URL AS Dst, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-01'::DATE AND EventDate::DATE <= '2013-07-31'::DATE AND IsRefresh = 0 GROUP BY TraficSourceID, SearchEngineID, AdvEngineID, CASE WHEN (SearchEngineID = 0 AND AdvEngineID = 0) THEN Referer ELSE '' END, URL ORDER BY PageViews DESC LIMIT 10 OFFSET 1000;",
    "SELECT URLHash, EventDate, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-01'::DATE AND EventDate::DATE <= '2013-07-31'::DATE AND IsRefresh = 0 AND TraficSourceID IN (-1, 6) AND RefererHash = 3594120000172545465 GROUP BY URLHash, EventDate ORDER BY PageViews DESC LIMIT 10 OFFSET 100;",
    "SELECT WindowClientWidth, WindowClientHeight, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-01'::DATE AND EventDate::DATE <= '2013-07-31'::DATE AND IsRefresh = 0 AND DontCountHits = 0 AND URLHash = 2868770270353813622 GROUP BY WindowClientWidth, WindowClientHeight ORDER BY PageViews DESC LIMIT 10 OFFSET 10000;",
    "SELECT TRUNC(EventTime::TIMESTAMP[s], 'minute') AS M, COUNT(*) AS PageViews FROM {DATASET} WHERE CounterID = 62 AND EventDate::DATE >= '2013-07-14'::DATE AND EventDate::DATE <= '2013-07-15'::DATE AND IsRefresh = 0 AND DontCountHits = 0 GROUP BY TRUNC(EventTime::TIMESTAMP[s], 'minute') ORDER BY M LIMIT 10 OFFSET 1000;",
]
# fmt:on


router = APIRouter(
    prefix="/internal/clickbench",
    tags=["internal"],
    dependencies=[Depends(require_authentication)],
    include_in_schema=False,
)


def _require_data_admin(request: Request) -> dict:
    claims = getattr(request.state, "claims", None) or {}
    if DATA_ADMIN_ENTITLEMENT not in entitlements_from_claims(claims):
        raise HTTPException(status_code=403, detail="requires data_admin entitlement")
    return claims


def _run_one_query(query_id: str, statement: str, session_kwargs: dict, iterations: int) -> dict:
    """Run a single query for `iterations` warm runs. Blocking -- call via a threadpool.

    Never raises: engine/data errors are captured into the returned dict's
    "error" key so one bad query doesn't take down the whole streamed run.
    """
    times_ms = []
    error = None
    for _ in range(iterations):
        session = opteryx.session(**session_kwargs)
        try:
            start = time.perf_counter()
            for _ in session.execute_to_morsels(statement):
                pass
            times_ms.append((time.perf_counter() - start) * 1000)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        finally:
            session.close()

    entry = {"id": query_id, "sql": statement}
    if error is not None:
        entry["error"] = error
    else:
        entry["iterations_ms"] = times_ms
        entry["min_ms"] = min(times_ms)
        entry["avg_ms"] = sum(times_ms) / len(times_ms)
        entry["max_ms"] = max(times_ms)
    return entry


@router.get("/run", include_in_schema=False)
def run_clickbench(
    request: Request,
    iterations: int = Query(2, ge=1, le=MAX_ITERATIONS),
    dataset: str = Query(DEFAULT_CLICKBENCH_DATASET),
) -> StreamingResponse:
    """Run the ClickBench query battery against `dataset`, streaming one NDJSON
    line per query as it completes.

    Internal diagnostic endpoint: not in the OpenAPI schema, requires the
    `data_admin` entitlement (not just authentication) since it drives a heavy
    43-query scan.

    Streamed rather than buffered: the full battery takes minutes against a
    100M-row dataset, and a single blocking response of that length was
    observed getting the serving instance killed mid-run in production
    (Cloud Run appears to treat a long-silent connection as unhealthy and
    recycle the instance, independent of the configured request timeout).
    Each query also runs via run_in_threadpool so the event loop stays free
    between queries instead of being pinned for the whole run.
    """
    claims = _require_data_admin(request)

    session_kwargs = dict(
        user=claims.get("identity"),
        # `data_admin` is the highest privilege tier this service has; a caller
        # who already cleared that gate is trusted with read access to the
        # benchmark dataset without also needing a separate token policy for it.
        # Opteryx's own can_perform_action() only recognizes reader/writer/owner
        # for READ (see opteryx.managers.permissions.ACTION_MAP) -- "admin" is
        # not a role it understands, only the data_admin *entitlement* above.
        access_policies=[{"pattern": "benchmarks.*", "role": "reader"}],
        billing_account=claims.get("billing_account"),
    )

    async def generate():
        yield (json.dumps({"type": "meta", "dataset": dataset, "iterations": iterations}) + "\n").encode(
            "utf-8"
        )

        passed = 0
        failed = 0
        sum_min_ms = 0.0
        start_suite = time.perf_counter()

        for index, statement_template in enumerate(STATEMENTS, start=1):
            query_id = f"q{index:02d}"
            statement = statement_template.replace("{DATASET}", dataset)

            entry = await run_in_threadpool(_run_one_query, query_id, statement, session_kwargs, iterations)
            if "error" in entry:
                failed += 1
            else:
                sum_min_ms += entry["min_ms"]
                passed += 1

            yield (json.dumps({"type": "query", **entry}) + "\n").encode("utf-8")

        took_ms = (time.perf_counter() - start_suite) * 1000

        logger.info(
            f"clickbench run by {claims.get('identity')}: dataset={dataset} "
            f"iterations={iterations} passed={passed} failed={failed} "
            f"sum_of_minimums_ms={sum_min_ms:.2f} took_ms={took_ms:.2f}"
        )

        yield (
            json.dumps(
                {
                    "type": "summary",
                    "passed": passed,
                    "failed": failed,
                    "sum_of_minimums_ms": sum_min_ms,
                    "took_ms": took_ms,
                }
            )
            + "\n"
        ).encode("utf-8")

    return StreamingResponse(generate(), media_type="application/x-ndjson")


__all__ = ["router"]

