#!/usr/bin/env python3
"""Example script demonstrating cf_speedtest library usage"""

import sys
from cf_speedtest.speedtest import run_standard_test, silence_warnings

# Uncomment the line below to silence all warnings during the test
# silence_warnings()

def main():
    print("Starting Cloudflare Speedtest...")
    print("This follows the same sequence as the Node.js implementation.")
    print("This may take a few minutes...\n")
    
    # Define measurement sizes (in bytes) - same as check_speedtest.py
    # Note: The actual measurement sequence is hardcoded in run_standard_test
    # to match the Node.js defaultConfig, but we pass this for compatibility
    measurement_sizes = [
        100_000,
        1_000_000,
        10_000_000,
        25_000_000,
        100_000_000,
        250_000_000,
    ]
    
    try:
        # Run the speedtest
        # Parameters: measurement_sizes, percentile (0-100), verbose, testpatience (seconds)
        # The test follows this sequence (matching Node.js):
        # 1. Initial latency (1 packet)
        # 2. Initial download (100KB, 1 count)
        # 3. Main latency (20 packets)
        # 4. Download/Upload measurements with increasing sizes
        # 5. Early stopping if measurements take > 1000ms
        results = run_standard_test(
            measurement_sizes=measurement_sizes,
            percentile_val=90,
            verbose=True,
            testpatience=15
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
