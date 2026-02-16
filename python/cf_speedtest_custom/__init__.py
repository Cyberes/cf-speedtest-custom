"""cf_speedtest_custom: Python client for Cloudflare-style speedtest (Worker)."""

from cf_speedtest_custom.speedtest import (
    measure_latency,
    percentile,
    run_standard_test,
    silence_warnings,
    SpeedtestResult,
)

__all__ = [
    "measure_latency",
    "percentile",
    "run_standard_test",
    "silence_warnings",
    "SpeedtestResult",
]
