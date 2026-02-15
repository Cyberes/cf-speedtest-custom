#!/usr/bin/env python3
"""Example: run a Cloudflare-style speedtest against your Worker."""

import argparse
import sys
from cf_speedtest.speedtest import run_standard_test, silence_warnings

REDUCED_SIZES = [
    100_000,
    1_000_000,
    10_000_000,
    25_000_000,
    100_000_000,
    250_000_000,
]


def main() -> int:
    p = argparse.ArgumentParser(description="Run speedtest against your Worker.")
    p.add_argument("--url", "-u", required=True, help="Worker URL (e.g. https://cf-speedtest.xxx.workers.dev)")
    p.add_argument("--user", help="Basic auth username")
    p.add_argument("--password", help="Basic auth password")
    p.add_argument("--full", action="store_true", help="Full measurement sequence (same as website)")
    p.add_argument("--quiet", "-q", action="store_true", help="Less output")
    p.add_argument("--no-warnings", action="store_true", help="Suppress urllib3/requests warnings")
    p.add_argument("--percentile", type=float, default=90, help="Bandwidth percentile 0–100 (default: 90)")
    p.add_argument("--timeout", type=int, default=15, help="Request timeout in seconds (default: 15)")
    args = p.parse_args()

    if args.no_warnings:
        silence_warnings()

    auth = None
    if args.user and args.password:
        auth = (args.user, args.password)
    elif args.user or args.password:
        p.error("--user and --password must be given together")

    base_url = args.url.rstrip("/")
    print("Backend:", base_url)
    if args.full:
        print("Full sequence (same as website).")
    print("Running...\n")

    try:
        results = run_standard_test(
            base_url,
            measurement_sizes=None if args.full else REDUCED_SIZES,
            auth=auth,
            percentile_val=args.percentile,
            timeout=args.timeout,
            verbose=not args.quiet,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    dl = results["download_speed"] / 1_000_000
    ul = results["upload_speed"] / 1_000_000
    print("=" * 50)
    print("Results")
    print("=" * 50)
    print(f"Download: {dl:.2f} Mbps")
    print(f"Upload:   {ul:.2f} Mbps")
    print(f"Ping:     {results['ping_ms']:.2f} ms")
    print(f"Jitter:   {results['jitter_ms']:.2f} ms")
    lat = results["latency_measurements"]
    if lat:
        print(f"Latency:  {len(lat)} samples (avg {sum(lat)/len(lat):.2f} ms)")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
