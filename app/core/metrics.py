"""Prometheus metrics exporter for Hearth.

Exposes standard runtime gauges and counters in Prometheus exposition format.
Disabled by default; toggled via ``HEARTH_METRICS_ENABLED=true``.
"""

from __future__ import annotations

import os
import resource
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings

_SERVER_START_TIME = time.time()
_METRICS_LOCK = threading.Lock()
_REQUEST_COUNTS: dict[tuple[str, str], int] = {}


def record_http_request(method: str, status_code: int) -> None:
    """Increment the HTTP request counter for Prometheus."""
    key = (method.upper(), str(status_code))
    with _METRICS_LOCK:
        _REQUEST_COUNTS[key] = _REQUEST_COUNTS.get(key, 0) + 1


def generate_prometheus_metrics(settings: Settings) -> str:
    """Render application state as standard Prometheus text exposition format."""
    from app.services.transcode.session import get_session_manager

    lines: list[str] = []

    # Uptime
    uptime = time.time() - _SERVER_START_TIME
    lines.append("# HELP hearth_uptime_seconds Seconds elapsed since Hearth startup")
    lines.append("# TYPE hearth_uptime_seconds gauge")
    lines.append(f"hearth_uptime_seconds {uptime:.2f}")

    # Process RSS Memory
    # Linux ru_maxrss is in KiB; convert to bytes
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_bytes = usage.ru_maxrss * 1024
    except Exception:
        rss_bytes = 0

    lines.append("# HELP hearth_process_resident_memory_bytes Process memory resident set size")
    lines.append("# TYPE hearth_process_resident_memory_bytes gauge")
    lines.append(f"hearth_process_resident_memory_bytes {rss_bytes}")

    # CPU Count
    lines.append("# HELP hearth_system_cpu_count Host CPU count available to Hearth")
    lines.append("# TYPE hearth_system_cpu_count gauge")
    lines.append(f"hearth_system_cpu_count {os.cpu_count() or 1}")

    # Active Transcode Sessions
    manager = get_session_manager(settings)
    sessions = manager.list_sessions()
    hw_count = sum(1 for s in sessions if s.is_hardware)
    sw_count = sum(1 for s in sessions if not s.is_hardware)

    lines.append("# HELP hearth_transcode_sessions_active Active transcoding streams")
    lines.append("# TYPE hearth_transcode_sessions_active gauge")
    lines.append(f'hearth_transcode_sessions_active{{hwaccel="vaapi"}} {hw_count}')
    lines.append(f'hearth_transcode_sessions_active{{hwaccel="none"}} {sw_count}')

    # Database pool size
    lines.append("# HELP hearth_database_pool_size Configured database connection pool size")
    lines.append("# TYPE hearth_database_pool_size gauge")
    pool_size = getattr(settings, "db_pool_size", 50)
    lines.append(f"hearth_database_pool_size {pool_size}")

    # HTTP Requests Total
    lines.append(
        "# HELP hearth_http_requests_total Total HTTP requests handled by method and status"
    )
    lines.append("# TYPE hearth_http_requests_total counter")
    with _METRICS_LOCK:
        items = sorted(_REQUEST_COUNTS.items())

    if not items:
        lines.append('hearth_http_requests_total{method="GET",status="200"} 0')
    else:
        for (meth, code), count in items:
            lines.append(f'hearth_http_requests_total{{method="{meth}",status="{code}"}} {count}')

    return "\n".join(lines) + "\n"
