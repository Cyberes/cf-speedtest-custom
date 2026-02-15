"""Cloudflare Speedtest - Minimal Python implementation"""

__version__ = "1.0.0"

from .speedtest import RateLimitError, run_standard_test, percentile, set_log_level, silence_warnings

__all__ = ['RateLimitError', 'run_standard_test', 'percentile', 'set_log_level', 'silence_warnings']
