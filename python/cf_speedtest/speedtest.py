"""
Cloudflare-style speedtest client. Same measurement sequence and formulas as
speedtest-cf.js so results match the website.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)

BANDWIDTH_FINISH_REQUEST_DURATION_MS = 1000
BANDWIDTH_MIN_REQUEST_DURATION_MS = 10
BANDWIDTH_PERCENTILE = 0.9
LATENCY_PERCENTILE = 0.5

MEASUREMENTS = [
    {"type": "latency", "num_packets": 1},
    {"type": "download", "bytes": 100_000, "count": 1, "bypass_min_duration": True},
    {"type": "latency", "num_packets": 20},
    {"type": "download", "bytes": 100_000, "count": 9, "bypass_min_duration": False},
    {"type": "download", "bytes": 1_000_000, "count": 8, "bypass_min_duration": False},
    {"type": "upload", "bytes": 100_000, "count": 8, "bypass_min_duration": False},
    {"type": "upload", "bytes": 1_000_000, "count": 6, "bypass_min_duration": False},
    {"type": "download", "bytes": 10_000_000, "count": 6, "bypass_min_duration": False},
    {"type": "upload", "bytes": 10_000_000, "count": 4, "bypass_min_duration": False},
    {"type": "download", "bytes": 25_000_000, "count": 4, "bypass_min_duration": False},
    {"type": "upload", "bytes": 25_000_000, "count": 4, "bypass_min_duration": False},
    {"type": "download", "bytes": 100_000_000, "count": 3, "bypass_min_duration": False},
    {"type": "upload", "bytes": 50_000_000, "count": 3, "bypass_min_duration": False},
    {"type": "download", "bytes": 250_000_000, "count": 2, "bypass_min_duration": False},
]


def percentile(values: Sequence[float], perc: float) -> float:
    """Linear-interpolation percentile (matches speedtest-cf.js). perc in 0–1 or 0–100."""
    if not values:
        return 0.0
    if perc > 1:
        perc = perc / 100.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    idx = (n - 1) * perc
    rem = idx % 1
    if rem == 0:
        return sorted_vals[int(round(idx))]
    lo = sorted_vals[int(idx)]
    hi = sorted_vals[min(int(idx) + 1, n - 1)]
    return lo + (hi - lo) * rem


def silence_warnings() -> None:
    """Suppress urllib3/requests warnings."""
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass


def _server_time_ms(r: requests.Response) -> float:
    st = r.headers.get("Server-Timing") or r.headers.get("server-timing") or ""
    m = re.search(r"dur=([0-9.]+)", st)
    if not m:
        return 0.0
    try:
        dur = float(m.group(1))
        return dur if dur >= 1 else 0.0
    except ValueError:
        return 0.0


def _upload_body(size: int) -> bytes:
    if size <= 0:
        return b""
    chunk = 256 * 1024
    if size <= chunk:
        return bytes((i * 31) & 0xFF for i in range(size))
    body = bytearray()
    while len(body) < size:
        take = min(chunk, size - len(body))
        for i in range(take):
            body.append((i * 31) & 0xFF)
    return bytes(body[:size])


def _fetch(
    method: str,
    url: str,
    auth: Optional[Tuple[str, str]],
    timeout: int,
    stream: bool = False,
    data: Optional[bytes] = None,
) -> requests.Response:
    r = requests.request(
        method, url, auth=auth, timeout=timeout, stream=stream, data=data
    )
    r.raise_for_status()
    return r


def _get_ip(base_url: str, auth: Optional[Tuple[str, str]], timeout: int) -> Dict[str, str]:
    try:
        r = _fetch("GET", f"{base_url}/getIP", auth, timeout)
        d = r.json()
        return {k: d.get(k, "") for k in ("ip", "country", "colo", "org")}
    except Exception as e:
        logger.debug("getIP failed: %s", e)
        return {"ip": "", "country": "", "colo": "", "org": ""}


def measure_latency(
    base_url: str,
    num_packets: int = 20,
    auth: Optional[Tuple[str, str]] = None,
    timeout: int = 15,
) -> List[float]:
    """Run latency probes; return list of ping times in ms."""
    base_url = base_url.rstrip("/")
    out: List[float] = []
    for _ in range(num_packets):
        url = f"{base_url}/__down?bytes=0&r={time.perf_counter()}"
        try:
            t0 = time.perf_counter()
            r = _fetch("GET", url, auth, timeout)
            r.content
            ttfb_ms = (time.perf_counter() - t0) * 1000
            server_ms = _server_time_ms(r)
            ping = max(0.01, ttfb_ms - server_ms) if server_ms >= 1 else max(0.01, ttfb_ms)
            out.append(ping)
        except Exception as e:
            logger.debug("Latency probe failed: %s", e)
    return out


def _jitter(pings: List[float]) -> float:
    if len(pings) < 2:
        return 0.0
    return sum(abs(pings[i] - pings[i - 1]) for i in range(1, len(pings))) / (len(pings) - 1)


def _run_full(
    base_url: str,
    auth: Optional[Tuple[str, str]],
    timeout: int,
    verbose: bool,
) -> Dict[str, Any]:
    base_url = base_url.rstrip("/")
    info = _get_ip(base_url, auth, timeout)
    if verbose:
        logger.info("getIP: %s", info.get("ip") or "(none)")

    latencies: List[float] = []
    down: Dict[int, List[Dict[str, float]]] = {}
    up: Dict[int, List[Dict[str, float]]] = {}
    finished_dl = False
    finished_ul = False

    def all_points(d: Dict[int, List[Dict[str, float]]]) -> List[Dict[str, float]]:
        return [p for timings in d.values() for p in timings]

    def do_latency(n: int) -> None:
        for _ in range(n):
            url = f"{base_url}/__down?bytes=0&r={time.perf_counter()}"
            try:
                t0 = time.perf_counter()
                r = _fetch("GET", url, auth, timeout)
                r.content
                ttfb_ms = (time.perf_counter() - t0) * 1000
                server_ms = _server_time_ms(r)
                ping = max(0.01, ttfb_ms - server_ms) if server_ms >= 1 else max(0.01, ttfb_ms)
                latencies.append(ping)
            except Exception as e:
                logger.debug("Latency probe failed: %s", e)

    def do_download(bytes_req: int, count: int, bypass: bool) -> None:
        nonlocal finished_dl
        min_dur = float("inf")
        for _ in range(count):
            url = f"{base_url}/__down?bytes={bytes_req}&r={time.perf_counter()}"
            try:
                r = _fetch("GET", url, auth, timeout, stream=True)
                t0 = time.perf_counter()
                chunks = list(r.iter_content(chunk_size=65536))
                payload_ms = max((time.perf_counter() - t0) * 1000, 1)
                n = sum(len(c) for c in chunks) or bytes_req
                bps = (8 * n) / (payload_ms / 1000)
                down.setdefault(bytes_req, [])
                down[bytes_req].append({"bps": bps, "duration": payload_ms})
                down[bytes_req] = down[bytes_req][-count:]
                min_dur = min(min_dur, payload_ms)
            except Exception as e:
                logger.debug("Download failed: %s", e)
        if not bypass and min_dur > BANDWIDTH_FINISH_REQUEST_DURATION_MS:
            finished_dl = True
        if verbose:
            pts = [p["bps"] for p in all_points(down) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
            logger.info("Download progress: %.2f Mbps", (percentile(pts, BANDWIDTH_PERCENTILE) / 1e6) if pts else 0)

    def do_upload(bytes_req: int, count: int, bypass: bool) -> None:
        nonlocal finished_ul
        min_dur = float("inf")
        body = _upload_body(bytes_req)
        for _ in range(count):
            url = f"{base_url}/__up?r={time.perf_counter()}"
            try:
                t0 = time.perf_counter()
                _fetch("POST", url, auth, timeout, data=body)
                dur_ms = max((time.perf_counter() - t0) * 1000, 1)
                bps = (8 * bytes_req) / (dur_ms / 1000)
                up.setdefault(bytes_req, [])
                up[bytes_req].append({"bps": bps, "duration": dur_ms})
                up[bytes_req] = up[bytes_req][-count:]
                min_dur = min(min_dur, dur_ms)
            except Exception as e:
                logger.debug("Upload failed: %s", e)
        if not bypass and min_dur > BANDWIDTH_FINISH_REQUEST_DURATION_MS:
            finished_ul = True
        if verbose:
            pts = [p["bps"] for p in all_points(up) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
            logger.info("Upload progress: %.2f Mbps", (percentile(pts, BANDWIDTH_PERCENTILE) / 1e6) if pts else 0)

    for m in MEASUREMENTS:
        if m["type"] == "download" and finished_dl:
            continue
        if m["type"] == "upload" and finished_ul:
            continue
        if m["type"] == "latency":
            do_latency(m["num_packets"])
        elif m["type"] == "download":
            do_download(m["bytes"], m["count"], m.get("bypass_min_duration", False))
        elif m["type"] == "upload":
            do_upload(m["bytes"], m["count"], m.get("bypass_min_duration", False))

    dl_pts = [p["bps"] for p in all_points(down) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
    ul_pts = [p["bps"] for p in all_points(up) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
    return {
        "latency_measurements": latencies,
        "ping_ms": percentile(latencies, LATENCY_PERCENTILE) if latencies else 0.0,
        "jitter_ms": _jitter(latencies) if len(latencies) >= 2 else 0.0,
        "download_speed": percentile(dl_pts, BANDWIDTH_PERCENTILE) if dl_pts else 0.0,
        "upload_speed": percentile(ul_pts, BANDWIDTH_PERCENTILE) if ul_pts else 0.0,
        "client_ip": f"{info.get('ip', '')} {info.get('org', '')} {info.get('country', '')}".strip(),
        "colo": f"CF: {info['colo']}" if info.get("colo") else "",
    }


def _run_reduced(
    base_url: str,
    measurement_sizes: Sequence[int],
    auth: Optional[Tuple[str, str]],
    timeout: int,
    percentile_pct: float,
    verbose: bool,
) -> Dict[str, Any]:
    base_url = base_url.rstrip("/")
    latencies = measure_latency(base_url, num_packets=3, auth=auth, timeout=timeout)
    p = percentile_pct / 100.0 if percentile_pct > 1 else percentile_pct
    down_pts: List[Dict[str, float]] = []
    up_pts: List[Dict[str, float]] = []

    for size in measurement_sizes:
        try:
            r = _fetch("GET", f"{base_url}/__down?bytes={size}&r={time.perf_counter()}", auth, timeout, stream=True)
            t0 = time.perf_counter()
            chunks = list(r.iter_content(chunk_size=65536))
            payload_ms = max((time.perf_counter() - t0) * 1000, 1)
            n = sum(len(c) for c in chunks) or size
            down_pts.append({"bps": (8 * n) / (payload_ms / 1000), "duration": payload_ms})
        except Exception as e:
            logger.debug("Download failed: %s", e)
        try:
            t0 = time.perf_counter()
            _fetch("POST", f"{base_url}/__up?r={time.perf_counter()}", auth, timeout, data=_upload_body(size))
            dur_ms = max((time.perf_counter() - t0) * 1000, 1)
            up_pts.append({"bps": (8 * size) / (dur_ms / 1000), "duration": dur_ms})
        except Exception as e:
            logger.debug("Upload failed: %s", e)

    dl_bps = [x["bps"] for x in down_pts if x["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and x["bps"]]
    ul_bps = [x["bps"] for x in up_pts if x["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and x["bps"]]
    info = _get_ip(base_url, auth, timeout)
    return {
        "latency_measurements": latencies,
        "ping_ms": percentile(latencies, LATENCY_PERCENTILE) if latencies else 0.0,
        "jitter_ms": _jitter(latencies) if len(latencies) >= 2 else 0.0,
        "download_speed": percentile(dl_bps, p) if dl_bps else 0.0,
        "upload_speed": percentile(ul_bps, p) if ul_bps else 0.0,
        "client_ip": f"{info.get('ip', '')} {info.get('org', '')} {info.get('country', '')}".strip(),
        "colo": f"CF: {info['colo']}" if info.get("colo") else "",
    }


def run_standard_test(
    base_url: str,
    measurement_sizes: Optional[Sequence[int]] = None,
    auth: Optional[Tuple[str, str]] = None,
    percentile_val: float = 90,
    timeout: int = 15,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Run speedtest. base_url is required (your Worker URL).
    If measurement_sizes is None, runs the full sequence (same as website).
    Returns dict: download_speed (bps), upload_speed (bps), latency_measurements, ping_ms, jitter_ms, client_ip, colo.
    """
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        raise ValueError("base_url is required.")
    if measurement_sizes is None:
        return _run_full(base_url, auth, timeout, verbose)
    return _run_reduced(base_url, measurement_sizes, auth, timeout, percentile_val, verbose)
