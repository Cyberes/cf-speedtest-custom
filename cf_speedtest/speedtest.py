"""Main speedtest implementation for Cloudflare speedtest"""

import time
import requests
from typing import List, Dict, Optional, Tuple
from .utils import percentile as _percentile

# Add small delay between requests to avoid rate limiting
# Note: Browser-based tests may have different rate limiting behavior
# The Node.js version doesn't have explicit delays, but browser timing is different
REQUEST_DELAY = 0.0  # No delay - match Node.js behavior

# Export percentile function for compatibility
percentile = _percentile


class RateLimitError(Exception):
    """Raised when Cloudflare rate limits the speedtest requests"""
    def __init__(self, status_code: int, retry_after: Optional[str] = None, message: str = ""):
        self.status_code = status_code
        self.retry_after = retry_after
        if not message:
            if status_code == 429:
                retry_msg = f" Retry-After: {retry_after} seconds." if retry_after else ""
                message = f"Rate limited (429) by Cloudflare.{retry_msg} Too many requests made too quickly."
            elif status_code == 403:
                message = "Request blocked (403) by Cloudflare. Rate limit exceeded or IP blocked."
            else:
                message = f"HTTP error {status_code} from Cloudflare."
        super().__init__(message)


# API endpoints
DOWNLOAD_API_URL = 'https://speed.cloudflare.com/__down'
UPLOAD_API_URL = 'https://speed.cloudflare.com/__up'

# Constants
ESTIMATED_HEADER_FRACTION = 0.005  # ~0.5% of packet header/payload size
ESTIMATED_SERVER_TIME = 10  # ms to discount from latency if not in headers
BANDWIDTH_MIN_REQUEST_DURATION = 10  # minimum duration (ms) for valid measurement
BANDWIDTH_FINISH_REQUEST_DURATION = 1000  # duration (ms) to reach for stopping further measurements
LATENCY_PERCENTILE = 0.5  # 50th percentile for latency


def get_server_time(response):
    """
    Extract server time from server-timing header.
    
    Args:
        response: requests.Response object
    
    Returns:
        Server time in milliseconds, or None if not found
    """
    server_timing = response.headers.get('server-timing')
    if server_timing:
        import re
        match = re.search(r'dur=([0-9.]+)', server_timing)
        if match:
            return float(match.group(1))
    return None


def measure_latency(num_packets: int = 20) -> List[float]:
    """
    Measure latency by performing GET requests with bytes=0.
    
    Args:
        num_packets: Number of latency measurements to perform
    
    Returns:
        List of latency measurements in milliseconds
    """
    latencies = []
    
    for _ in range(num_packets):
        try:
            start_time = time.time()
            response = requests.get(
                DOWNLOAD_API_URL,
                params={'bytes': '0'},
                timeout=30
            )
            end_time = time.time()
            
            if response.ok:
                # Calculate TTFB (time to first byte)
                # In Python, we approximate this as the total request time
                # minus server processing time
                server_time = get_server_time(response) or ESTIMATED_SERVER_TIME
                ttfb_ms = (end_time - start_time) * 1000
                ping_ms = max(0.01, ttfb_ms - server_time)
                latencies.append(ping_ms)
        except Exception:
            # Skip failed measurements
            continue
    
    return latencies


def measure_download_bandwidth(bytes_size: int, count: int, bypass_min_duration: bool = False) -> Tuple[List[Dict], float]:
    """
    Measure download bandwidth by performing GET requests.
    
    Args:
        bytes_size: Size of data to download in bytes
        count: Number of measurements to perform
        bypass_min_duration: If True, don't check for early stopping
    
    Returns:
        Tuple of (list of measurement results, minimum duration in ms)
        Each result contains:
        - bytes: size in bytes
        - bps: bits per second
        - duration: duration in milliseconds
        - ping: latency in milliseconds
    """
    results = []
    min_duration = float('inf')
    
    for i in range(count):
        try:
            # Add small delay between requests to avoid rate limiting
            if i > 0:
                time.sleep(REQUEST_DELAY)
            
            # Measure total request time
            request_start = time.time()
            response = requests.get(
                DOWNLOAD_API_URL,
                params={'bytes': str(bytes_size)},
                timeout=60,
                stream=True  # Use streaming to measure payload download time accurately
            )
            
            # Check for rate limiting or errors
            if not response.ok:
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    raise RateLimitError(429, retry_after)
                elif response.status_code == 403:
                    raise RateLimitError(403)
                else:
                    raise RateLimitError(response.status_code, message=f"HTTP error {response.status_code}: {response.reason}")
            
            # response.elapsed gives time from sending request to receiving headers (TTFB)
            headers_received = time.time()
            elapsed_ms = response.elapsed.total_seconds() * 1000
            
            # Stream content to measure actual payload download time
            content_start = time.time()
            content = b''
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
            content_received = time.time()
            
            total_time_ms = (content_received - request_start) * 1000
            payload_download_time_ms = (content_received - headers_received) * 1000
            
            if response.ok:
                server_time = get_server_time(response) or ESTIMATED_SERVER_TIME
                
                # In Node.js (from BandwidthEngine.js):
                # TTFB = responseStart - requestStart (elapsed_ms)
                # payloadDownloadTime = responseEnd - responseStart (measured via streaming)
                # ping = TTFB - server_time
                # duration = ping + payloadDownloadTime
                
                # Calculate like Node.js
                ttfb_ms = elapsed_ms
                payload_download_time_ms = max(1, payload_download_time_ms)  # min 1ms (from gePayloadDownload)
                ping_ms = max(0.01, ttfb_ms - server_time)
                download_duration_ms = ping_ms + payload_download_time_ms
                
                # Track minimum duration across all measurements for this size
                min_duration = min(min_duration, download_duration_ms)
                
                # Calculate bandwidth using transferSize (actual bytes transferred)
                # Node.js uses perf.transferSize which includes HTTP headers
                # In Python, len(content) gives the response body size
                # For bandwidth calculation, we use the actual bytes received
                transfer_size = len(content)
                # Formula from Node.js: bits = 8 * (transferSize || numBytes * (1 + ESTIMATED_HEADER_FRACTION))
                bits = 8 * (transfer_size if transfer_size > 0 else int(bytes_size * (1 + ESTIMATED_HEADER_FRACTION)))
                # Formula: bps = bits / (duration / 1000)
                bps = bits / (download_duration_ms / 1000) if download_duration_ms > 0 else 0
                
                results.append({
                    'bytes': bytes_size,
                    'bps': bps,
                    'duration': download_duration_ms,
                    'ping': ping_ms
                })
        except Exception:
            # Skip failed measurements
            continue
    
    return results, min_duration if min_duration != float('inf') else 0


def measure_upload_bandwidth(bytes_size: int, count: int, bypass_min_duration: bool = False) -> Tuple[List[Dict], float]:
    """
    Measure upload bandwidth by performing POST requests.
    
    Args:
        bytes_size: Size of data to upload in bytes
        count: Number of measurements to perform
        bypass_min_duration: If True, don't check for early stopping
    
    Returns:
        Tuple of (list of measurement results, minimum duration in ms)
        Each result contains:
        - bytes: size in bytes
        - bps: bits per second
        - duration: duration in milliseconds
        - ping: latency in milliseconds
    """
    results = []
    min_duration = float('inf')
    
    # Generate content to upload
    content = b'0' * bytes_size
    
    for i in range(count):
        try:
            # Add small delay between requests to avoid rate limiting
            if i > 0:
                time.sleep(REQUEST_DELAY)
            
            # Measure total request time (includes sending data + receiving response)
            request_start = time.time()
            response = requests.post(
                UPLOAD_API_URL,
                data=content,
                timeout=60
            )
            
            # Check for rate limiting or errors
            if not response.ok:
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    raise RateLimitError(429, retry_after)
                elif response.status_code == 403:
                    raise RateLimitError(403)
                else:
                    raise RateLimitError(response.status_code, message=f"HTTP error {response.status_code}: {response.reason}")
            
            # Wait for response to complete
            _ = response.content
            request_end = time.time()
            
            # response.elapsed gives time from sending request to receiving headers (TTFB)
            elapsed_ms = response.elapsed.total_seconds() * 1000
            total_time_ms = (request_end - request_start) * 1000
            
            if response.ok:
                server_time = get_server_time(response) or ESTIMATED_SERVER_TIME
                
                # In Node.js (from BandwidthEngine.js):
                # Upload duration = TTFB = responseStart - requestStart
                # TTFB includes time to upload data + time to get first byte of response
                # In Python, response.elapsed is time from sending request to receiving headers
                # This should approximate TTFB (time to first byte)
                upload_duration_ms = max(1, elapsed_ms)
                
                # Track minimum duration across all measurements for this size
                min_duration = min(min_duration, upload_duration_ms)
                
                # Calculate bandwidth
                # Formula from Node.js: bits = 8 * numBytes * (1 + ESTIMATED_HEADER_FRACTION)
                bits = 8 * bytes_size * (1 + ESTIMATED_HEADER_FRACTION)
                # Formula: bps = bits / (duration / 1000)
                bps = bits / (upload_duration_ms / 1000) if upload_duration_ms > 0 else 0
                
                # ping = TTFB - server_time
                ping_ms = max(0.01, elapsed_ms - server_time)
                
                results.append({
                    'bytes': bytes_size,
                    'bps': bps,
                    'duration': upload_duration_ms,
                    'ping': ping_ms
                })
        except Exception:
            # Skip failed measurements
            continue
    
    return results, min_duration if min_duration != float('inf') else 0


def run_standard_test(
    measurement_sizes: List[int],
    percentile_val: float,
    verbose: bool = True,
    testpatience: int = 15
) -> Dict:
    """
    Run a standard speedtest matching the Node.js implementation sequence.
    
    Args:
        measurement_sizes: List of byte sizes to test (used to build measurement sequence)
        percentile_val: Percentile value (0-100) for bandwidth calculation
        verbose: Whether to print progress (ignored for now)
        testpatience: Timeout patience in seconds (not currently used)
    
    Returns:
        Dictionary with:
        - download_speed: Download speed in bytes per second
        - upload_speed: Upload speed in bytes per second
        - latency_measurements: List of latency measurements in milliseconds
    """
    # Convert percentile from 0-100 range to 0-1 range
    bandwidth_percentile = percentile_val / 100.0 if percentile_val > 1 else percentile_val
    
    # Build measurement sequence matching Node.js defaultConfig
    # Format: (type, bytes, count, bypass_min_duration)
    measurements = [
        ('latency', 0, 1, False),  # initial ttfb estimation
        ('download', 100_000, 1, True),  # initial download estimation
        ('latency', 0, 20, False),  # main latency measurement
        ('download', 100_000, 9, False),
        ('download', 1_000_000, 8, False),
        ('upload', 100_000, 8, False),
        # Skip packet loss
        ('upload', 1_000_000, 6, False),
        ('download', 10_000_000, 6, False),
        ('upload', 10_000_000, 4, False),
        ('download', 25_000_000, 4, False),
        ('upload', 25_000_000, 4, False),
        ('download', 100_000_000, 3, False),
        ('upload', 50_000_000, 3, False),
        ('download', 250_000_000, 2, False),
    ]
    
    # Collect all latency measurements
    all_latency_measurements = []
    download_results = []
    upload_results = []
    
    # Track if directions are finished (early stopping)
    download_finished = False
    upload_finished = False
    
    for mtype, bytes_size, count, bypass_min_duration in measurements:
        # Skip if direction is finished
        if mtype == 'download' and download_finished:
            continue
        if mtype == 'upload' and upload_finished:
            continue
        
        if mtype == 'latency':
            latencies = measure_latency(num_packets=count)
            all_latency_measurements.extend(latencies)
        
        elif mtype == 'download':
            measurements_list, min_duration = measure_download_bandwidth(
                bytes_size, count, bypass_min_duration
            )
            # Filter measurements that meet minimum duration
            valid_measurements = [
                m for m in measurements_list
                if m['duration'] >= BANDWIDTH_MIN_REQUEST_DURATION
            ]
            download_results.extend(valid_measurements)
            
            # Check for early stopping (if min duration > finish threshold and not bypassed)
            if (not bypass_min_duration and 
                min_duration > BANDWIDTH_FINISH_REQUEST_DURATION):
                download_finished = True
        
        elif mtype == 'upload':
            measurements_list, min_duration = measure_upload_bandwidth(
                bytes_size, count, bypass_min_duration
            )
            # Filter measurements that meet minimum duration
            valid_measurements = [
                m for m in measurements_list
                if m['duration'] >= BANDWIDTH_MIN_REQUEST_DURATION
            ]
            upload_results.extend(valid_measurements)
            
            # Check for early stopping (if min duration > finish threshold and not bypassed)
            if (not bypass_min_duration and 
                min_duration > BANDWIDTH_FINISH_REQUEST_DURATION):
                upload_finished = True
    
    # Use the main latency measurements (from the 20-packet measurement)
    # If we have multiple latency measurements, use the last one (the main 20-packet one)
    if len(all_latency_measurements) >= 20:
        # The main measurement is the last 20
        latency_measurements = all_latency_measurements[-20:]
    else:
        # Fallback to all measurements
        latency_measurements = all_latency_measurements
    
    if not latency_measurements:
        raise ValueError("Failed to measure latency")
    
    # Calculate final results
    download_bps = None
    if download_results:
        bps_values = [m['bps'] for m in download_results if m['bps']]
        if bps_values:
            # Filter to only use measurements with duration >= minimum (like Node.js)
            # Node.js filters: d.duration >= bandwidthMinRequestDuration
            valid_bps = [
                m['bps'] for m in download_results
                if m['bps'] and m['duration'] >= BANDWIDTH_MIN_REQUEST_DURATION
            ]
            if valid_bps:
                download_bps = percentile(valid_bps, bandwidth_percentile)
    
    upload_bps = None
    if upload_results:
        bps_values = [m['bps'] for m in upload_results if m['bps']]
        if bps_values:
            # Filter to only use measurements with duration >= minimum (like Node.js)
            valid_bps = [
                m['bps'] for m in upload_results
                if m['bps'] and m['duration'] >= BANDWIDTH_MIN_REQUEST_DURATION
            ]
            if valid_bps:
                upload_bps = percentile(valid_bps, bandwidth_percentile)
    
    # Convert from bits per second to bytes per second
    download_speed = (download_bps / 8) if download_bps else None
    upload_speed = (upload_bps / 8) if upload_bps else None
    
    if download_speed is None:
        raise ValueError("Failed to measure download speed")
    if upload_speed is None:
        raise ValueError("Failed to measure upload speed")
    
    return {
        'download_speed': download_speed,
        'upload_speed': upload_speed,
        'latency_measurements': latency_measurements
    }
