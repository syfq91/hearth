#!/usr/bin/env python3
"""Hearth load test simulation.

Simulates 15 concurrent users:
- 3 concurrent transcodes (HLS fMP4 delivery, playlist polling, segment streaming)
- 6 direct plays (HTTP range-request streaming)
- 1 background scan (library scan enqueuing and monitoring)
- 6 API browsing users (libraries, items, search, home, watch progress)

Target metrics:
- API p95 latency under 300 ms
- 0 dropped segments
- Flat memory growth over time
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("hearth.load_test")


@dataclass
class LatencyRecord:
    timestamp: float
    category: str  # "api", "stream", "hls_playlist", "hls_segment"
    endpoint: str
    status_code: int
    elapsed_ms: float


@dataclass
class MemorySample:
    timestamp: float
    rss_mb: float


@dataclass
class LoadTestReport:
    duration_seconds: float
    total_requests: int
    api_requests: int
    api_p50_ms: float
    api_p90_ms: float
    api_p95_ms: float
    api_p99_ms: float
    api_max_ms: float
    api_avg_ms: float
    dropped_segments: int
    successful_segments: int
    direct_play_bytes: int
    scans_triggered: int
    initial_rss_mb: float
    peak_rss_mb: float
    final_rss_mb: float
    memory_slope_mb_per_hour: float
    target_p95_met: bool
    target_dropped_segments_met: bool
    target_memory_growth_met: bool
    all_targets_met: bool
    error_count: int
    errors: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        status = "PASSED" if self.all_targets_met else "FAILED"
        p95_status = "PASS" if self.target_p95_met else "FAIL"
        dropped_status = "PASS" if self.target_dropped_segments_met else "FAIL"
        mem_status = "PASS" if self.target_memory_growth_met else "FAIL"
        return f"""### Hearth Load Test Results ({status})

- **Duration**: {self.duration_seconds:.1f} s
- **Total Requests**: {self.total_requests} (API: {self.api_requests})
- **API Latency**:
  - Avg: {self.api_avg_ms:.2f} ms
  - p50: {self.api_p50_ms:.2f} ms
  - p90: {self.api_p90_ms:.2f} ms
  - **p95**: {self.api_p95_ms:.2f} ms (Target: < 300 ms — {p95_status})
  - p99: {self.api_p99_ms:.2f} ms
  - Max: {self.api_max_ms:.2f} ms
- **Video Segments**:
  - Successful: {self.successful_segments}
  - **Dropped**: {self.dropped_segments} (Target: 0 — {dropped_status})
- **Direct Play Streaming**: {self.direct_play_bytes / (1024 * 1024):.2f} MB delivered
- **Background Scans**: {self.scans_triggered} scans completed
- **Memory Profile**:
  - Initial RSS: {self.initial_rss_mb:.2f} MB
  - Peak RSS: {self.peak_rss_mb:.2f} MB
  - Final RSS: {self.final_rss_mb:.2f} MB
  - **Growth Rate**: {self.memory_slope_mb_per_hour:.2f} MB/h (Target: <50 MB/h — {mem_status})
- **Error Count**: {self.error_count}
"""


class LoadTestMetricsCollector:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.records: list[LatencyRecord] = []
        self.memory_samples: list[MemorySample] = []
        self.dropped_segments: int = 0
        self.successful_segments: int = 0
        self.direct_play_bytes: int = 0
        self.scans_triggered: int = 0
        self.errors: list[str] = []

    async def record_latency(
        self,
        category: str,
        endpoint: str,
        status_code: int,
        elapsed_ms: float,
    ) -> None:
        rec = LatencyRecord(
            timestamp=time.time(),
            category=category,
            endpoint=endpoint,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
        )
        async with self.lock:
            self.records.append(rec)

    async def record_segment(self, dropped: bool = False) -> None:
        async with self.lock:
            if dropped:
                self.dropped_segments += 1
            else:
                self.successful_segments += 1

    async def record_direct_play_bytes(self, num_bytes: int) -> None:
        async with self.lock:
            self.direct_play_bytes += num_bytes

    async def record_scan(self) -> None:
        async with self.lock:
            self.scans_triggered += 1

    async def record_memory(self, rss_mb: float) -> None:
        sample = MemorySample(timestamp=time.time(), rss_mb=rss_mb)
        async with self.lock:
            self.memory_samples.append(sample)

    async def record_error(self, message: str) -> None:
        async with self.lock:
            self.errors.append(message)
            if len(self.errors) > 100:
                self.errors.pop(0)

    def generate_report(
        self,
        duration_seconds: float,
        warmup_seconds: float = 0.0,
    ) -> LoadTestReport:
        t_start = self.records[0].timestamp if self.records else 0.0
        api_latencies = [
            r.elapsed_ms
            for r in self.records
            if r.category == "api"
            and r.status_code < 500
            and (r.timestamp - (r.elapsed_ms / 1000.0) - t_start >= warmup_seconds)
        ]
        api_latencies.sort()

        def percentile(values: list[float], pct: float) -> float:
            if not values:
                return 0.0
            k = (len(values) - 1) * (pct / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return values[int(k)]
            d0 = values[int(f)] * (c - k)
            d1 = values[int(c)] * (k - f)
            return d0 + d1

        api_p50 = percentile(api_latencies, 50.0)
        api_p90 = percentile(api_latencies, 90.0)
        api_p95 = percentile(api_latencies, 95.0)
        api_p99 = percentile(api_latencies, 99.0)
        api_max = max(api_latencies) if api_latencies else 0.0
        api_avg = (sum(api_latencies) / len(api_latencies)) if api_latencies else 0.0

        initial_rss = self.memory_samples[0].rss_mb if self.memory_samples else 0.0
        peak_rss = max((s.rss_mb for s in self.memory_samples), default=initial_rss)
        final_rss = self.memory_samples[-1].rss_mb if self.memory_samples else initial_rss

        # Linear regression for memory growth rate (MB per hour)
        slope_mb_per_hour = 0.0
        if len(self.memory_samples) >= 2:
            t0 = self.memory_samples[0].timestamp
            ts = [s.timestamp - t0 for s in self.memory_samples]
            ys = [s.rss_mb for s in self.memory_samples]
            n = len(ts)
            mean_t = sum(ts) / n
            mean_y = sum(ys) / n
            denom = sum((t - mean_t) ** 2 for t in ts)
            if denom > 0:
                numer = sum((ts[i] - mean_t) * (ys[i] - mean_y) for i in range(n))
                slope_per_sec = numer / denom
                slope_mb_per_hour = slope_per_sec * 3600.0

        target_p95 = api_p95 < 300.0 if api_latencies else True
        target_dropped = self.dropped_segments == 0
        # Flat growth: for short benchmark runs (< 30s) verify RSS drift < 50 MB;
        # for longer/soak runs verify hourly slope < 50 MB/hour.
        if duration_seconds < 30.0:
            target_memory = (final_rss - initial_rss) < 50.0
        else:
            target_memory = slope_mb_per_hour < 50.0

        all_met = target_p95 and target_dropped and target_memory and (len(self.errors) == 0)

        return LoadTestReport(
            duration_seconds=duration_seconds,
            total_requests=len(self.records),
            api_requests=len(api_latencies),
            api_p50_ms=api_p50,
            api_p90_ms=api_p90,
            api_p95_ms=api_p95,
            api_p99_ms=api_p99,
            api_max_ms=api_max,
            api_avg_ms=api_avg,
            dropped_segments=self.dropped_segments,
            successful_segments=self.successful_segments,
            direct_play_bytes=self.direct_play_bytes,
            scans_triggered=self.scans_triggered,
            initial_rss_mb=initial_rss,
            peak_rss_mb=peak_rss,
            final_rss_mb=final_rss,
            memory_slope_mb_per_hour=max(0.0, slope_mb_per_hour),
            target_p95_met=target_p95,
            target_dropped_segments_met=target_dropped,
            target_memory_growth_met=target_memory,
            all_targets_met=all_met,
            error_count=len(self.errors),
            errors=self.errors[:10],
        )


def _get_process_rss_mb() -> float:
    """Read current process VmRSS in MB from Linux /proc/self/status."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    kb = float(parts[1])
                    return kb / 1024.0
    except Exception:
        pass
    return 0.0


async def _memory_sampler_loop(
    stop_event: asyncio.Event,
    metrics: LoadTestMetricsCollector,
    interval: float = 1.0,
) -> None:
    while not stop_event.is_set():
        rss = _get_process_rss_mb()
        await metrics.record_memory(rss)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)


async def _timed_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    category: str,
    metrics: LoadTestMetricsCollector,
    **kwargs: Any,
) -> httpx.Response:
    t0 = time.perf_counter()
    resp = await client.request(method, url, **kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # Strip query params for clean endpoint aggregation
    endpoint = urlparse(url).path
    await metrics.record_latency(category, endpoint, resp.status_code, elapsed_ms)
    return resp


async def run_transcode_user(
    user_id: str,
    client: httpx.AsyncClient,
    token: str,
    item_id: str,
    stop_event: asyncio.Event,
    metrics: LoadTestMetricsCollector,
) -> None:
    """Simulates a user playing video with transcode requirements."""
    headers = {"Authorization": f"Bearer {token}"}
    session_id: str | None = None

    user_num = int(user_id.split("_")[-1]) if "_" in user_id else 0
    await asyncio.sleep(user_num * 0.1)

    try:
        # Request playback info with low bitrate profile forcing transcode
        force_transcode_profile = {
            "capabilities": {
                "preset": "chrome",
                "containers": ["mp4"],
                "video_codecs": ["h264"],
                "audio_codecs": ["aac"],
                "max_bitrate": 50000,
                "hls_fmp4": True,
            }
        }
        res = await _timed_request(
            client,
            "POST",
            f"/api/v1/items/{item_id}/playback-info",
            category="playback_setup",
            metrics=metrics,
            headers=headers,
            json=force_transcode_profile,
        )
        if res.status_code != 200:
            await metrics.record_error(f"Transcode playback-info failed: {res.status_code}")
            return

        data = res.json()
        hls_url = data.get("hls_url")
        session_id = data.get("session_id")
        if not hls_url or not session_id:
            # If server decided direct play or no session
            return

        # Fetch master playlist
        res_master = await _timed_request(
            client,
            "GET",
            hls_url,
            category="hls_playlist",
            metrics=metrics,
            headers=headers,
        )
        if res_master.status_code != 200:
            await metrics.record_error(f"Failed to fetch master.m3u8: {res_master.status_code}")
            return

        media_m3u8_url = f"/api/v1/hls/{session_id}/media.m3u8"
        fetched_segments: set[str] = set()

        while not stop_event.is_set():
            res_media = await _timed_request(
                client,
                "GET",
                media_m3u8_url,
                category="hls_playlist",
                metrics=metrics,
                headers=headers,
            )
            if res_media.status_code == 200:
                lines = res_media.text.splitlines()
                # Find segments
                candidates: list[str] = []
                for line in lines:
                    line = line.strip()
                    seg_rel: str | None = None
                    if line.startswith("#EXT-X-MAP:"):
                        m = re.search(r'URI="([^"]+)"', line)
                        if m:
                            seg_rel = m.group(1).split("?")[0]
                    elif line.endswith(".m4s") or ".m4s?" in line:
                        seg_rel = line.split("?")[0]
                    if seg_rel and seg_rel not in fetched_segments:
                        candidates.append(seg_rel)

                # Fetch at most 2 segments per cycle to emulate realistic player buffer ahead
                for seg_rel in candidates[:2]:
                    fetched_segments.add(seg_rel)
                    seg_url = f"/api/v1/hls/{session_id}/{seg_rel}"
                    res_seg = await _timed_request(
                        client,
                        "GET",
                        seg_url,
                        category="hls_segment",
                        metrics=metrics,
                        headers=headers,
                    )
                    if res_seg.status_code == 200 and len(res_seg.content) > 0:
                        await metrics.record_segment(dropped=False)
                    else:
                        await metrics.record_segment(dropped=True)
                        await metrics.record_error(
                            f"Segment dropped: {seg_url} status={res_seg.status_code}"
                        )
                    await asyncio.sleep(0.05)

            with contextlib.suppress(asyncio.TimeoutError):
                poll_timeout = 1.6 + random.uniform(0.1, 0.5)
                await asyncio.wait_for(stop_event.wait(), timeout=poll_timeout)

    except Exception as exc:
        await metrics.record_error(f"Transcode user {user_id} error: {exc}")
    finally:
        if session_id:
            with contextlib.suppress(Exception):
                await client.post(f"/api/v1/hls/{session_id}/stop", headers=headers)


async def run_direct_play_user(
    user_id: str,
    client: httpx.AsyncClient,
    token: str,
    item_id: str,
    file_id: str,
    stop_event: asyncio.Event,
    metrics: LoadTestMetricsCollector,
) -> None:
    """Simulates a user direct-playing media with HTTP range chunking and progress reporting."""
    headers = {"Authorization": f"Bearer {token}"}
    chunk_size = 524288  # 512 KB chunks
    offset = 0
    step = 0
    user_num = int(user_id.split("_")[-1]) if "_" in user_id else 0
    await asyncio.sleep(user_num * 0.15)
    last_progress_time = time.time()
    progress_interval = 7.0 + (user_num * 1.2)

    try:
        while not stop_event.is_set():
            range_header = f"bytes={offset}-{offset + chunk_size - 1}"
            res = await _timed_request(
                client,
                "GET",
                f"/api/v1/stream/{file_id}",
                category="stream",
                metrics=metrics,
                headers={**headers, "Range": range_header},
            )
            if res.status_code in (200, 206):
                received_len = len(res.content)
                await metrics.record_direct_play_bytes(received_len)
                if received_len < chunk_size:
                    # Looped back to start
                    offset = 0
                else:
                    offset += received_len
            else:
                await metrics.record_error(f"Direct play stream error: {res.status_code}")
                offset = 0

            # Periodically report playback progress (interleaved across users)
            now = time.time()
            if now - last_progress_time >= progress_interval:
                last_progress_time = now
                pos_ms = int((offset / 500000.0) * 1000)
                await _timed_request(
                    client,
                    "POST",
                    f"/api/v1/items/{item_id}/progress",
                    category="playback",
                    metrics=metrics,
                    headers=headers,
                    json={"position_ms": pos_ms, "duration_ms": 60000, "state": "playing"},
                )

            sleep_interval = 1.2 + random.uniform(0.2, 0.8)
            step += 1
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_interval)
    except Exception as exc:
        await metrics.record_error(f"Direct play user {user_id} error: {exc}")


async def run_background_scanner(
    client: httpx.AsyncClient,
    admin_token: str,
    library_id: str,
    stop_event: asyncio.Event,
    metrics: LoadTestMetricsCollector,
    interval: float = 5.0,
) -> None:
    """Simulates periodic background library scans."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    await asyncio.sleep(0.4)
    try:
        while not stop_event.is_set():
            res = await _timed_request(
                client,
                "POST",
                f"/api/v1/libraries/{library_id}/scan",
                category="scan",
                metrics=metrics,
                headers=headers,
            )
            if res.status_code in (200, 202):
                await metrics.record_scan()
            else:
                await metrics.record_error(f"Background scan trigger returned {res.status_code}")

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
    except Exception as exc:
        await metrics.record_error(f"Background scanner error: {exc}")


async def run_browsing_user(
    user_id: str,
    client: httpx.AsyncClient,
    token: str,
    library_id: str,
    item_id: str,
    stop_event: asyncio.Event,
    metrics: LoadTestMetricsCollector,
) -> None:
    """Simulates an active user browsing libraries, search, home, and progress."""
    headers = {"Authorization": f"Bearer {token}"}

    endpoints_cycle = [
        ("GET", "/api/v1/libraries", None),
        ("GET", f"/api/v1/libraries/{library_id}/items", None),
        ("GET", f"/api/v1/items/{item_id}", None),
        ("GET", "/api/v1/home/continue-watching", None),
        ("GET", "/api/v1/home/recently-added", None),
        ("GET", "/api/v1/search?q=movie", None),
    ]

    # Stagger user starts and entry points across the catalog
    user_num = int(user_id.split("_")[-1]) if "_" in user_id else 0
    step = user_num
    await asyncio.sleep((user_num % 6) * 0.1)

    try:
        while not stop_event.is_set():
            method, path, body = endpoints_cycle[step % len(endpoints_cycle)]
            step += 1

            kwargs: dict[str, Any] = {"headers": headers}
            if body:
                kwargs["json"] = body

            res = await _timed_request(
                client,
                method,
                path,
                category="api",
                metrics=metrics,
                **kwargs,
            )
            if res.status_code >= 500:
                await metrics.record_error(f"Browse request {path} returned {res.status_code}")

            think_time = 1.2 + random.uniform(0.2, 1.0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=think_time)
    except Exception as exc:
        await metrics.record_error(f"Browsing user {user_id} error: {exc}")


async def execute_load_test(
    client: httpx.AsyncClient,
    admin_token: str,
    user_tokens: list[str],
    library_id: str,
    item_id: str,
    file_id: str,
    duration_seconds: float,
    metrics: LoadTestMetricsCollector | None = None,
    warmup_seconds: float = 2.0,
) -> LoadTestReport:
    """Run the complete 15-user simulation for the given duration."""
    if metrics is None:
        metrics = LoadTestMetricsCollector()

    stop_event = asyncio.Event()

    # Initial memory sample
    await metrics.record_memory(_get_process_rss_mb())

    # Users allocation: 3 transcode, 6 direct play, 6 browsing = 15 users
    assert len(user_tokens) >= 15, f"Expected 15 user tokens, got {len(user_tokens)}"

    tasks: list[asyncio.Task[None]] = []

    # Memory sampler task
    tasks.append(asyncio.create_task(_memory_sampler_loop(stop_event, metrics, interval=0.5)))

    # 3 Concurrent Transcode Users (Tokens 0, 1, 2)
    for i in range(3):
        t = asyncio.create_task(
            run_transcode_user(
                f"transcoder_{i + 1}",
                client,
                user_tokens[i],
                item_id,
                stop_event,
                metrics,
            )
        )
        tasks.append(t)

    # 6 Concurrent Direct Play Users (Tokens 3, 4, 5, 6, 7, 8)
    for i in range(6):
        t = asyncio.create_task(
            run_direct_play_user(
                f"direct_player_{i + 1}",
                client,
                user_tokens[3 + i],
                item_id,
                file_id,
                stop_event,
                metrics,
            )
        )
        tasks.append(t)

    # 1 Background Scan Task (Admin Token)
    tasks.append(
        asyncio.create_task(
            run_background_scanner(
                client,
                admin_token,
                library_id,
                stop_event,
                metrics,
                interval=4.0,
            )
        )
    )

    # 6 Concurrent API Browsing Users (Tokens 9, 10, 11, 12, 13, 14)
    for i in range(6):
        t = asyncio.create_task(
            run_browsing_user(
                f"browser_{i + 1}",
                client,
                user_tokens[9 + i],
                library_id,
                item_id,
                stop_event,
                metrics,
            )
        )
        tasks.append(t)

    try:
        await asyncio.sleep(duration_seconds)
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Final memory sample
        await metrics.record_memory(_get_process_rss_mb())

    return metrics.generate_report(duration_seconds, warmup_seconds=warmup_seconds)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Hearth 15-User Load Test Simulation")
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL of Hearth server (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Duration in seconds (default: 30s; 7200s for full 2-hour soak)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to output results JSON file",
    )
    args = parser.parse_args()

    print(f"Starting Hearth load test against {args.url} for {args.duration:.1f}s...")
    async with httpx.AsyncClient(base_url=args.url, timeout=30.0) as client:
        # Check health
        res = await client.get("/api/v1/health")
        if res.status_code != 200:
            print(f"Error: Hearth server at {args.url} is not healthy: {res.text}")
            sys.exit(1)

        # Log in as admin or setup
        admin_auth = {"username": "admin", "password": "correct horse"}
        setup_res = await client.post("/api/v1/auth/setup", json=admin_auth)
        if setup_res.status_code == 201:
            admin_tokens = setup_res.json()
        else:
            login_res = await client.post("/api/v1/auth/login", json=admin_auth)
            if login_res.status_code != 200:
                print(f"Failed to authenticate admin: {login_res.text}")
                sys.exit(1)
            admin_tokens = login_res.json()

        admin_token = admin_tokens["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Get or create library and items
        libs_res = await client.get("/api/v1/libraries", headers=headers)
        libs = libs_res.json().get("items", [])
        if not libs:
            print("No libraries found. Please create a library with media before load testing.")
            sys.exit(1)
        library_id = libs[0]["id"]

        items_res = await client.get(f"/api/v1/libraries/{library_id}/items", headers=headers)
        items = items_res.json().get("items", [])
        if not items:
            print("No items found in library. Please scan media first.")
            sys.exit(1)
        item_id = items[0]["id"]

        item_detail = (await client.get(f"/api/v1/items/{item_id}", headers=headers)).json()
        files = item_detail.get("files", [])
        if not files:
            print("No media files found on item.")
            sys.exit(1)
        file_id = files[0]["id"]

        # Provision users: 1 admin + 14 users = 15 total enabled users
        user_tokens: list[str] = [admin_token]
        for i in range(1, 15):
            username = f"load_user_{i:02d}"
            pw = "correct horse"
            # create user
            await client.post(
                "/api/v1/users",
                headers=headers,
                json={
                    "username": username,
                    "password": pw,
                    "role": "user",
                    "library_ids": [library_id],
                },
            )
            # login user
            u_login = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": pw},
            )
            if u_login.status_code == 200:
                user_tokens.append(u_login.json()["access_token"])
            else:
                print(f"Failed to log in {username}: {u_login.text}")
                sys.exit(1)

        # Run test
        report = await execute_load_test(
            client=client,
            admin_token=admin_token,
            user_tokens=user_tokens,
            library_id=library_id,
            item_id=item_id,
            file_id=file_id,
            duration_seconds=args.duration,
        )

        print(report.to_markdown())

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
            print(f"Results saved to {args.output}")

        if not report.all_targets_met:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
