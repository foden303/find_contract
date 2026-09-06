"""Live, labeled benchmark for search selection quality and request cost.

Run from the repository root:
    python -m benchmarks.query_quality --provider ddg

The benchmark intentionally uses a temporary cold cache. Search engines are
non-deterministic, so this reports measurements instead of asserting them as a
test.
"""

import argparse
import asyncio
import json
import math
import tempfile
import time
from pathlib import Path

from core.cache import Cache
from core.config import Config, SEARCH_PROVIDERS
from core.pipeline import _select_candidates
from core.search import SearchClient, SearchError
from core.utils import extract_domain


CASES_PATH = Path(__file__).with_name("query_quality_cases.json")


def _matches(actual: str, expected: list[str]) -> bool:
    return any(
        actual == domain or actual.endswith("." + domain)
        for domain in expected
    )


async def run(provider: str, limit: int) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if limit > 0:
        cases = cases[:limit]

    rows = []
    latencies = []
    correct = 0
    with tempfile.TemporaryDirectory(prefix="finder-quality-") as temporary:
        config = Config(
            search_provider=provider,
            cache_path=str(Path(temporary) / "cache.db"),
            use_company_store=False,
        )
        cache = Cache(config.cache_path, config.cache_ttl)
        client = SearchClient(config, cache)
        try:
            for case in cases:
                telemetry = {}
                started = time.perf_counter()
                error = None
                candidates = []
                try:
                    candidates = await _select_candidates(
                        case["company"],
                        case["country"],
                        case["products"],
                        config,
                        client,
                        telemetry=telemetry,
                    )
                except SearchError as exc:
                    error = str(exc)
                elapsed = time.perf_counter() - started
                latencies.append(elapsed)
                actual = extract_domain(candidates[0].url) if candidates else ""
                expected = case.get(
                    "expected_domains", [case.get("expected_domain", "")]
                )
                matched = _matches(actual, expected)
                correct += int(matched)
                rows.append({
                    "company": case["company"],
                    "expected_domains": expected,
                    "actual_domain": actual,
                    "correct": matched,
                    "seconds": round(elapsed, 3),
                    "logical_queries": telemetry.get("logical_queries", 0),
                    "network_requests": telemetry.get("search_requests", 0),
                    "error": error,
                })
        finally:
            await client.aclose()
            cache.close()

    ordered = sorted(latencies)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "provider": provider,
        "cases": len(rows),
        "correct": correct,
        "accuracy_percent": round(correct * 100 / len(rows), 1) if rows else 0.0,
        "logical_queries": sum(row["logical_queries"] for row in rows),
        "network_requests": sum(row["network_requests"] for row in rows),
        "mean_seconds": round(sum(latencies) / len(latencies), 3) if rows else 0.0,
        "p95_seconds": round(ordered[p95_index], 3) if rows else 0.0,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=SEARCH_PROVIDERS, default="ddg")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Run only the first N labeled cases; zero runs every case.",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.provider, args.limit)), indent=2))


if __name__ == "__main__":
    main()
