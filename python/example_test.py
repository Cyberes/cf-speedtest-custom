#!/usr/bin/env python3
"""Example script demonstrating cf_speedtest library usage"""

import os
import sys
from cf_speedtest.speedtest import run_standard_test, configure, silence_warnings

# Uncomment to silence all warnings during the test
# silence_warnings()

def main():
    print("Starting Cloudflare Speedtest...")
    print("This follows the same sequence as the Node.js implementation.")
    print("This may take a few minutes...\n")

    # Optional: use your own Worker and Basic Auth (set env vars or call configure())
    base_url = os.environ.get("CF_SPEEDTEST_URL")  # e.g. https://cf-speedtest.xxx.workers.dev
    auth_user = os.environ.get("CF_SPEEDTEST_USER")  # e.g. speedtest
    auth_pass = os.environ.get("CF_SPEEDTEST_PASS")
    if base_url:
        configure(base_url=base_url, auth=(auth_user, auth_pass) if auth_user and auth_pass else None)
        print("Using backend:", base_url)

    measurement_sizes = [
        100_000,
        1_000_000,
        10_000_000,
        25_000_000,
        100_000_000,
        250_000_000,
    ]

    try:
        results = run_standard_test(
            measurement_sizes=measurement_sizes,
            percentile_val=90,
            verbose=True,
            testpatience=15,
        )
        
        # Display results
        print("\n" + "="*50)
        print("Speedtest Results")
        print("="*50)
        
        download_mbps = results['download_speed'] / 1_000_000
        upload_mbps = results['upload_speed'] / 1_000_000
        
        print(f"Download Speed: {download_mbps:.2f} Mbps ({results['download_speed']:.0f} bytes/sec)")
        print(f"Upload Speed:   {upload_mbps:.2f} Mbps ({results['upload_speed']:.0f} bytes/sec)")
        
        # Calculate latency statistics
        latencies = results['latency_measurements']
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            min_latency = min(latencies)
            max_latency = max(latencies)
            
            print(f"\nLatency Measurements: {len(latencies)} samples")
            print(f"  Average: {avg_latency:.2f} ms")
            print(f"  Minimum: {min_latency:.2f} ms")
            print(f"  Maximum: {max_latency:.2f} ms")
        
        print("="*50)
        
        return 0
        
    except Exception as e:
        print(f"\nError running speedtest: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
