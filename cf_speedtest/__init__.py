"""Cloudflare Speedtest - Minimal Python implementation"""

__version__ = "1.0.0"

from .speedtest import RateLimitError, run_standard_test, percentile

__all__ = ['RateLimitError', 'run_standard_test', 'percentile']
