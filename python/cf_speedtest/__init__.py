"""cf_speedtest: Python client for Cloudflare-style speedtest (Worker)."""

from cf_speedtest.speedtest import (
    measure_latency,
    percentile,
    run_standard_test,
    silence_warnings,
)

__all__ = ["measure_latency", "percentile", "run_standard_test", "silence_warnings"]
