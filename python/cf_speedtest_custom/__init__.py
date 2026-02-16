"""cf_speedtest_custom: Python client for Cloudflare-style speedtest (Worker)."""

from cf_speedtest_custom.speedtest import (
    SpeedtestResult,
    measure_latency,
    percentile,
    run_standard_test,
    silence_warnings,
)

__all__ = [
    "SpeedtestResult",
    "measure_latency",
    "percentile",
    "run_standard_test",
    "silence_warnings",
]
