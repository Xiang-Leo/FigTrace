"""Measure real directory indexing and warm API queries, without rendering 5,000 previews."""

import argparse
import io
import statistics
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from figtrace.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="figtrace-benchmark-") as temp:
        base = Path(temp)
        sources = base / "sources"
        sources.mkdir()
        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), "#426846").save(buffer, "PNG")
        for i in range(args.count):
            (sources / f"figure-{i:05}.png").write_bytes(buffer.getvalue())
        app = create_app(base / "data", start_worker=False)
        library = app.state.library
        with TestClient(app, headers={"X-Figtrace-Request": "1"}) as client:
            job = client.post(
                "/api/roots", json={"path": str(sources), "project": "Benchmark"}
            ).json()
            started = time.perf_counter()
            library.scan(job["target"])
            elapsed = time.perf_counter() - started
            timings = []
            for i in range(30):
                started = time.perf_counter()
                response = client.get(
                    "/api/assets", params={"q": "figure-00", "page": 1}
                )
                assert response.status_code == 200 and len(
                    response.json()["items"]
                ) == min(48, args.count)
                timings.append((time.perf_counter() - started) * 1000)
            print(f"{args.count} local PNG files indexed: {elapsed:.2f}s")
            print(
                f"30 warm API queries (48 records/page): median {statistics.median(timings):.1f}ms; p95 {sorted(timings)[28]:.1f}ms"
            )
            print(
                "Excludes thumbnail conversion, browser rendering, and network-drive latency."
            )


if __name__ == "__main__":
    main()
