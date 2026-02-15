"""Main speedtest implementation for Cloudflare speedtest"""

import logging
import time
import requests
from typing import List, Dict, Optional, Tuple
from .utils import percentile as _percentile

# Create module-level logger (not root logger)
_logger = logging.getLogger(__name__)
_logger.setLevel(logging.WARNING)  # Default to WARNING level
# Add a null handler if no handlers exist to avoid using root logger
if not _logger.handlers:
    _handler = logging.NullHandler()
    _logger.addHandler(_handler)

# Request spacing to avoid rate limiting (inspired by Rust implementation)
REQUEST_DELAY = 0.1  # 100ms delay between requests (matching Rust: 100ms after errors)
REQUEST_DELAY_BETWEEN_SIZES = 0.5  # 500ms delay between different measurement sizes

# Retry configuration
MAX_RETRIES = 3  # Maximum retries per measurement
DEFAULT_RETRY_DELAY = 2.0  # Default delay in seconds if Retry-After header not present
MIN_BYTES_PER_REQ = 100_000  # 100 KB minimum (matching Rust: MIN_DOWNLOAD_BYTES_PER_REQ)

# Export percentile function for compatibility
percentile = _percentile


def set_log_level(level: int = logging.WARNING) -> None:
    """
    Set the logging level for the speedtest module.
    
    Args:
        level: Logging level (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)
              Use logging.NOTSET to disable all logging.
    
    Examples:
        >>> import logging
        >>> from cf_speedtest.speedtest import set_log_level
        >>> set_log_level(logging.ERROR)  # Only show errors
        >>> set_log_level(logging.NOTSET)  # Silence all warnings
    """
    _logger.setLevel(level)


def silence_warnings() -> None:
    """
    Convenience function to silence all warnings from the speedtest module.
    
    Example:
        >>> from cf_speedtest.speedtest import silence_warnings
        >>> silence_warnings()  # No more warnings will be shown
    """
    _logger.setLevel(logging.CRITICAL + 1)  # Set to level above CRITICAL to silence all


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
    
    Implements adaptive sizing and retry logic inspired by Rust implementation:
    - When rate limited (429), reduces request size by half and retries
    - Retries with exponential backoff using Retry-After header
    - Has minimum size (100KB) that always works
    
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
    errors = 0
    
    for i in range(count):
        # Add delay between requests to avoid rate limiting
        if i > 0:
            time.sleep(REQUEST_DELAY)
        
        current_size = bytes_size
        retry_count = 0
        measurement_success = False
        
        while retry_count <= MAX_RETRIES and not measurement_success:
            try:
                # Measure total request time
                request_start = time.time()
                response = requests.get(
                    DOWNLOAD_API_URL,
                    params={'bytes': str(current_size)},
                    timeout=60,
                    stream=True  # Use streaming to measure payload download time accurately
                )
                
                # Check for rate limiting or errors
                if not response.ok:
                    if response.status_code == 429:
                        retry_after_str = response.headers.get('Retry-After')
                        retry_after = float(retry_after_str) if retry_after_str else DEFAULT_RETRY_DELAY
                        
                        # Adaptive sizing: reduce size by half (inspired by Rust implementation)
                        if current_size > MIN_BYTES_PER_REQ:
                            next_size = max(MIN_BYTES_PER_REQ, current_size // 2)
                            if next_size < current_size:
                                _logger.warning(
                                    f"Download: 429 from server, reducing bytes per request from {current_size:,} to {next_size:,}"
                                )
                                current_size = next_size
                                time.sleep(0.1)  # 100ms delay after error (matching Rust)
                                retry_count += 1
                                continue
                        
                        # If we can't reduce size further, wait and retry
                        if retry_count < MAX_RETRIES:
                            wait_time = retry_after * (2 ** retry_count)  # Exponential backoff
                            _logger.warning(
                                f"Download: Rate limited (429), waiting {wait_time:.1f}s before retry {retry_count + 1}/{MAX_RETRIES}"
                            )
                            time.sleep(wait_time)
                            retry_count += 1
                            continue
                        else:
                            # Max retries reached, raise error
                            raise RateLimitError(429, retry_after_str, 
                                f"Rate limited after {MAX_RETRIES} retries with size {current_size:,} bytes")
                    elif response.status_code == 403:
                        raise RateLimitError(403)
                    else:
                        raise RateLimitError(response.status_code, message=f"HTTP error {response.status_code}: {response.reason}")
                
                # response.elapsed gives time from sending request to receiving headers (TTFB)
                elapsed_ms = response.elapsed.total_seconds() * 1000
                
                # Read content (this blocks until all data is received)
                content = b''
                for chunk in response.iter_content(chunk_size=8192):
                    content += chunk
                content_received = time.time()
                
                # Total time from request start to content fully received
                total_time_ms = (content_received - request_start) * 1000
                
                # Payload download time = total time - TTFB
                payload_download_time_ms = max(1, total_time_ms - elapsed_ms)
                
                if response.ok:
                    server_time = get_server_time(response) or ESTIMATED_SERVER_TIME
                    
                    # Calculate like Node.js
                    ttfb_ms = elapsed_ms
                    payload_download_time_ms = max(1, payload_download_time_ms)
                    ping_ms = max(0.01, ttfb_ms - server_time)
                    download_duration_ms = ping_ms + payload_download_time_ms
                    
                    # Track minimum duration across all measurements for this size
                    min_duration = min(min_duration, download_duration_ms)
                    
                    # Calculate bandwidth using transferSize (actual bytes transferred)
                    transfer_size = len(content)
                    bits = 8 * (transfer_size if transfer_size > 0 else int(current_size * (1 + ESTIMATED_HEADER_FRACTION)))
                    bps = bits / (download_duration_ms / 1000) if download_duration_ms > 0 else 0
                    
                    results.append({
                        'bytes': current_size,  # Use actual size used (may be reduced)
                        'bps': bps,
                        'duration': download_duration_ms,
                        'ping': ping_ms
                    })
                    measurement_success = True
                    
            except RateLimitError as e:
                # If we've exhausted retries or can't reduce size further, track error and continue
                if retry_count >= MAX_RETRIES or current_size <= MIN_BYTES_PER_REQ:
                    errors += 1
                    _logger.warning(
                        f"Download measurement failed: {e} (size: {current_size:,} bytes)"
                    )
                    break  # Move to next measurement
                else:
                    # Continue retry loop
                    retry_count += 1
                    time.sleep(0.1)  # 100ms delay after error
            except Exception as e:
                # Skip other failed measurements
                errors += 1
                _logger.warning(
                    f"Download measurement error: {e} (size: {current_size:,} bytes)"
                )
                break  # Move to next measurement
    
    # Log errors if any occurred
    if errors > 0:
        _logger.warning(
            f"Download: {errors} request(s) failed out of {count} attempts"
        )
    
    # Return min_duration, or 0 if no successful measurements
    return results, min_duration if min_duration != float('inf') else 0


def measure_upload_bandwidth(bytes_size: int, count: int, bypass_min_duration: bool = False) -> Tuple[List[Dict], float]:
    """
    Measure upload bandwidth by performing POST requests.
    
    Implements adaptive sizing and retry logic inspired by Rust implementation:
    - When rate limited (429), reduces request size by half and retries
    - Retries with exponential backoff using Retry-After header
    - Has minimum size (100KB) that always works
    
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
    errors = 0
    
    for i in range(count):
        # Add delay between requests to avoid rate limiting
        if i > 0:
            time.sleep(REQUEST_DELAY)
        
        current_size = bytes_size
        retry_count = 0
        measurement_success = False
        
        while retry_count <= MAX_RETRIES and not measurement_success:
            try:
                # Generate content to upload
                content = b'0' * current_size
                
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
                        retry_after_str = response.headers.get('Retry-After')
                        retry_after = float(retry_after_str) if retry_after_str else DEFAULT_RETRY_DELAY
                        
                        # Adaptive sizing: reduce size by half (inspired by Rust implementation)
                        if current_size > MIN_BYTES_PER_REQ:
                            next_size = max(MIN_BYTES_PER_REQ, current_size // 2)
                            if next_size < current_size:
                                _logger.warning(
                                    f"Upload: 429 from server, reducing bytes per request from {current_size:,} to {next_size:,}"
                                )
                                current_size = next_size
                                time.sleep(0.1)  # 100ms delay after error (matching Rust)
                                retry_count += 1
                                continue
                        
                        # If we can't reduce size further, wait and retry
                        if retry_count < MAX_RETRIES:
                            wait_time = retry_after * (2 ** retry_count)  # Exponential backoff
                            _logger.warning(
                                f"Upload: Rate limited (429), waiting {wait_time:.1f}s before retry {retry_count + 1}/{MAX_RETRIES}"
                            )
                            time.sleep(wait_time)
                            retry_count += 1
                            continue
                        else:
                            # Max retries reached, raise error
                            raise RateLimitError(429, retry_after_str,
                                f"Rate limited after {MAX_RETRIES} retries with size {current_size:,} bytes")
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
                    
                    # Upload duration = TTFB (matching Node.js)
                    upload_duration_ms = max(1, elapsed_ms)
                    
                    # Track minimum duration across all measurements for this size
                    min_duration = min(min_duration, upload_duration_ms)
                    
                    # Calculate bandwidth
                    bits = 8 * current_size * (1 + ESTIMATED_HEADER_FRACTION)
                    bps = bits / (upload_duration_ms / 1000) if upload_duration_ms > 0 else 0
                    
                    # ping = TTFB - server_time
                    ping_ms = max(0.01, elapsed_ms - server_time)
                    
                    results.append({
                        'bytes': current_size,  # Use actual size used (may be reduced)
                        'bps': bps,
                        'duration': upload_duration_ms,
                        'ping': ping_ms
                    })
                    measurement_success = True
                    
            except RateLimitError as e:
                # If we've exhausted retries or can't reduce size further, track error and continue
                if retry_count >= MAX_RETRIES or current_size <= MIN_BYTES_PER_REQ:
                    errors += 1
                    _logger.warning(
                        f"Upload measurement failed: {e} (size: {current_size:,} bytes)"
                    )
                    break  # Move to next measurement
                else:
                    # Continue retry loop
                    retry_count += 1
                    time.sleep(0.1)  # 100ms delay after error
            except Exception as e:
                # Skip other failed measurements
                errors += 1
                _logger.warning(
                    f"Upload measurement error: {e} (size: {current_size:,} bytes)"
                )
                break  # Move to next measurement
    
    # Log errors if any occurred
    if errors > 0:
        _logger.warning(
            f"Upload: {errors} request(s) failed out of {count} attempts"
        )
    
    # Return min_duration, or 0 if no successful measurements
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
    
    # Track failed sizes for debugging
    failed_download_sizes = []
    failed_upload_sizes = []
    
    for idx, (mtype, bytes_size, count, bypass_min_duration) in enumerate(measurements):
        # Skip if direction is finished
        if mtype == 'download' and download_finished:
            continue
        if mtype == 'upload' and upload_finished:
            continue
        
        # Add delay between different measurement sizes (except first measurement)
        if idx > 0:
            time.sleep(REQUEST_DELAY_BETWEEN_SIZES)
        
        if mtype == 'latency':
            latencies = measure_latency(num_packets=count)
            all_latency_measurements.extend(latencies)
        
        elif mtype == 'download':
            try:
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
                # Only stop if we got valid measurements and min_duration exceeds threshold
                if (not bypass_min_duration and 
                    min_duration > 0 and  # Only if we got at least one measurement
                    min_duration > BANDWIDTH_FINISH_REQUEST_DURATION):
                    download_finished = True
            except RateLimitError as e:
                # Graceful degradation: log warning and continue with next size
                # Don't fail completely unless it's a critical measurement
                failed_download_sizes.append(bytes_size)
                if bytes_size == 100_000 and count == 1:
                    # Critical: initial download estimation failed
                    raise ValueError(f"Critical download measurement failed: {e}")
                else:
                    _logger.warning(
                        f"Download measurement skipped for size {bytes_size:,} bytes: {e}"
                    )
                    # Continue with next measurement instead of failing
        
        elif mtype == 'upload':
            try:
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
                # Only stop if we got valid measurements and min_duration exceeds threshold
                if (not bypass_min_duration and 
                    min_duration > 0 and  # Only if we got at least one measurement
                    min_duration > BANDWIDTH_FINISH_REQUEST_DURATION):
                    upload_finished = True
            except RateLimitError as e:
                # Graceful degradation: log warning and continue with next size
                failed_upload_sizes.append(bytes_size)
                _logger.warning(
                    f"Upload measurement skipped for size {bytes_size:,} bytes: {e}"
                )
                # Continue with next measurement instead of failing
    
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
    
    # Calculate final results with partial results support
    download_bps = None
    if download_results:
        # Filter to only use measurements with duration >= minimum (like Node.js)
        valid_bps = [
            m['bps'] for m in download_results
            if m['bps'] and m['duration'] >= BANDWIDTH_MIN_REQUEST_DURATION
        ]
        if valid_bps:
            # Require at least 3 measurements for reliable results, but allow fewer
            if len(valid_bps) < 3:
                _logger.warning(
                    f"Download: Only {len(valid_bps)} successful measurement(s), results may be less accurate"
                )
            download_bps = percentile(valid_bps, bandwidth_percentile)
    
    upload_bps = None
    if upload_results:
        # Filter to only use measurements with duration >= minimum (like Node.js)
        valid_bps = [
            m['bps'] for m in upload_results
            if m['bps'] and m['duration'] >= BANDWIDTH_MIN_REQUEST_DURATION
        ]
        if valid_bps:
            # Require at least 3 measurements for reliable results, but allow fewer
            if len(valid_bps) < 3:
                _logger.warning(
                    f"Upload: Only {len(valid_bps)} successful measurement(s), results may be less accurate"
                )
            upload_bps = percentile(valid_bps, bandwidth_percentile)
    
    # Convert from bits per second to bytes per second
    download_speed = (download_bps / 8) if download_bps else None
    upload_speed = (upload_bps / 8) if upload_bps else None
    
    # Enhanced error messages with context
    if download_speed is None:
        failed_msg = f" (failed sizes: {failed_download_sizes})" if failed_download_sizes else ""
        raise ValueError(f"Failed to measure download speed{failed_msg}. No successful measurements.")
    if upload_speed is None:
        failed_msg = f" (failed sizes: {failed_upload_sizes})" if failed_upload_sizes else ""
        raise ValueError(f"Failed to measure upload speed{failed_msg}. No successful measurements.")
    
    return {
        'download_speed': download_speed,
        'upload_speed': upload_speed,
        'latency_measurements': latency_measurements
    }
