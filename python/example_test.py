#!/usr/bin/env python3
"""Example: run a Cloudflare-style speedtest against your Worker."""

import argparse
import sys

import requests

from cf_speedtest_custom.speedtest import run_standard_test, silence_warnings


def main() -> int:
    p = argparse.ArgumentParser(description="Run speedtest against your Worker.")
    p.add_argument("--url", "-u", required=True, help="Worker URL (e.g. https://cf-speedtest.xxx.workers.dev)")
    p.add_argument("--password", help="Password (optional; use when Worker is password-protected)")
    p.add_argument("--quiet", "-q", action="store_true", help="Less output")
    p.add_argument("--no-warnings", action="store_true", help="Suppress urllib3/requests warnings")
    p.add_argument("--percentile", type=float, default=90, help="Bandwidth percentile 0–100 (default: 90)")
    p.add_argument("--timeout", type=int, default=15, help="Request timeout in seconds (default: 15)")
    args = p.parse_args()

    if args.no_warnings:
        silence_warnings()

    auth = None
    if args.password is not None:
        auth = ("", args.password)

    base_url = args.url.rstrip("/")
    print("Running...\n")

    try:
        results = run_standard_test(
            base_url,
            measurement_sizes=None,
            auth=auth,
            percentile_val=args.percentile,
            timeout=args.timeout,
            verbose=not args.quiet,
        )
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            print("Error: Server requires a password.", file=sys.stderr)
            print("Use --password <password>.", file=sys.stderr)
            return 1
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
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
