"""
Cloudflare-style speedtest client. Same measurement sequence and formulas as
speedtest-cf.js where comparable. No correction factors. Download = payload
bytes / payload time. Upload = chunked body; we record (time, offset) per chunk
and use average bps over full send period (first to last sample) so the result
reflects network speed rather than kernel acceptance spikes. Ping = TTFB minus
server time (or TTFB). Jitter = mean of |latency[i]-latency[i-1]|.

Auth: 401 is always re-raised (do not swallow in _get_ip or measurement loops)
so the script fails fast with a clear message instead of appearing to hang.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Union

import requests


@dataclass(frozen=True)
class SpeedtestResult:
    """Result of a full speedtest run. All speeds in bits per second (bps)."""

    download_speed: float  # bps
    upload_speed: float  # bps
    latency_measurements: Tuple[float, ...]  # ping samples in ms
    ping_ms: float
    jitter_ms: float
    client_ip: str
    colo: str

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


# Chunk size for upload: time between yields reflects when the library is ready
# for more data (previous chunk sent). 256KB gives ~20ms intervals at 100 Mbps.
UPLOAD_CHUNK_BYTES = 256 * 1024


def _upload_body_chunked(
    body: bytes, samples: List[Tuple[float, int]]
) -> Iterator[bytes]:
    """Yield body in chunks; record (time, cumulative_bytes) before each yield. The library asks for the next chunk when the previous has been sent, so time deltas reflect upload speed."""
    offset = 0
    n = len(body)
    while offset < n:
        samples.append((time.perf_counter(), offset))
        end = min(offset + UPLOAD_CHUNK_BYTES, n)
        chunk = body[offset:end]
        offset = end
        yield chunk


def _upload_bps_from_samples(
    samples: List[Tuple[float, int]], bytes_req: int
) -> Optional[float]:
    """Average upload bps over the full send period (first to last sample). We use total
    bytes / send duration instead of 90th percentile of instantaneous rates, because
    our samples reflect when the library asked for the next chunk (socket accepted
    data), not when data left the network—so instantaneous rates can be inflated by
    kernel buffer acceptance. Average over send period is a better proxy for network speed."""
    if len(samples) < 2:
        return None
    send_duration_sec = samples[-1][0] - samples[0][0]
    if send_duration_sec <= 0:
        return None
    return (8 * bytes_req) / send_duration_sec


def _normalize_auth(auth: Optional[Union[str, Tuple[str, str]]]) -> Optional[Tuple[str, str]]:
    """Accept password-only (str) or (_, password) tuple; server only checks password."""
    if auth is None:
        return None
    if isinstance(auth, str):
        return ("", auth)
    return auth


def _fetch(
    method: str,
    url: str,
    auth: Optional[Tuple[str, str]],
    timeout: int,
    stream: bool = False,
    data: Optional[bytes] = None,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    if session is None:
        r = requests.request(
            method, url, auth=auth, timeout=timeout, stream=stream, data=data
        )
    else:
        r = session.request(
            method, url, auth=auth, timeout=timeout, stream=stream, data=data
        )
    if r.status_code == 401:
        try:
            r.content  # consume body so connection is released (avoids pool hang)
        except Exception:
            pass
        raise requests.HTTPError(
            "401 Unauthorized: server requires a password. Use auth='<password>'.",
            response=r,
        )
    r.raise_for_status()
    return r


def _get_ip(
    base_url: str,
    auth: Optional[Tuple[str, str]],
    timeout: int,
    session: Optional[requests.Session] = None,
) -> Dict[str, str]:
    try:
        r = _fetch("GET", f"{base_url}/getIP", auth, timeout, session=session)
        d = r.json()
        return {k: d.get(k, "") for k in ("ip", "country", "colo", "org")}
    except requests.HTTPError:
        raise  # fail fast with clear "password required" message (do not swallow)
    except Exception as e:
        logger.debug("getIP failed: %s", e)
        return {"ip": "", "country": "", "colo": "", "org": ""}


def measure_latency(
    base_url: str,
    num_packets: int = 20,
    auth: Optional[Union[str, Tuple[str, str]]] = None,
    timeout: int = 15,
) -> List[float]:
    """Run latency probes; return list of ping times in ms."""
    auth = _normalize_auth(auth)
    base_url = base_url.rstrip("/")
    out: List[float] = []
    with requests.Session() as session:
        for _ in range(num_packets):
            url = f"{base_url}/__down?bytes=0&r={time.perf_counter()}"
            r = None
            try:
                t0 = time.perf_counter()
                r = _fetch("GET", url, auth, timeout, stream=True, session=session)
                ttfb_ms = (time.perf_counter() - t0) * 1000
                server_ms = _server_time_ms(r)
                ping = max(0.01, ttfb_ms - server_ms) if server_ms >= 1 else max(0.01, ttfb_ms)
                out.append(ping)
            except requests.HTTPError:
                raise
            except Exception as e:
                logger.debug("Latency probe failed: %s", e)
            finally:
                if r is not None:
                    try:
                        r.content
                    except Exception:
                        pass
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
) -> SpeedtestResult:
    base_url = base_url.rstrip("/")
    latencies: List[float] = []
    down: Dict[int, List[Dict[str, float]]] = {}
    up: Dict[int, List[Dict[str, float]]] = {}
    finished_dl = False
    finished_ul = False

    def all_points(d: Dict[int, List[Dict[str, float]]]) -> List[Dict[str, float]]:
        return [p for timings in d.values() for p in timings]

    def do_latency(n: int, session: requests.Session) -> None:
        for _ in range(n):
            url = f"{base_url}/__down?bytes=0&r={time.perf_counter()}"
            r = None
            try:
                t0 = time.perf_counter()
                r = _fetch("GET", url, auth, timeout, stream=True, session=session)
                ttfb_ms = (time.perf_counter() - t0) * 1000
                server_ms = _server_time_ms(r)
                ping = max(0.01, ttfb_ms - server_ms) if server_ms >= 1 else max(0.01, ttfb_ms)
                latencies.append(ping)
            except requests.HTTPError:
                raise
            except Exception as e:
                logger.debug("Latency probe failed: %s", e)
            finally:
                if r is not None:
                    try:
                        r.content
                    except Exception:
                        pass

    def do_download(bytes_req: int, count: int, bypass: bool, session: requests.Session) -> None:
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
            except requests.HTTPError:
                raise
            except Exception as e:
                logger.debug("Download failed: %s", e)
        if not bypass and min_dur > BANDWIDTH_FINISH_REQUEST_DURATION_MS:
            finished_dl = True
        if verbose:
            pts = [p["bps"] for p in all_points(down) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
            logger.info("Download progress: %.2f Mbps", (percentile(pts, BANDWIDTH_PERCENTILE) / 1e6) if pts else 0)

    def do_upload(bytes_req: int, count: int, bypass: bool, session: requests.Session) -> None:
        nonlocal finished_ul
        min_dur = float("inf")
        body = _upload_body(bytes_req)
        for _ in range(count):
            url = f"{base_url}/__up?r={time.perf_counter()}"
            try:
                samples: List[Tuple[float, int]] = []
                chunked = _upload_body_chunked(body, samples)
                t0 = time.perf_counter()
                _fetch("POST", url, auth, timeout, data=chunked)
                dur_ms = max((time.perf_counter() - t0) * 1000, 1)
                bps_from_samples = _upload_bps_from_samples(samples, bytes_req)
                bps = bps_from_samples if bps_from_samples is not None else (8 * bytes_req) / (dur_ms / 1000)
                up.setdefault(bytes_req, [])
                up[bytes_req].append({"bps": bps, "duration": dur_ms})
                up[bytes_req] = up[bytes_req][-count:]
                min_dur = min(min_dur, dur_ms)
            except requests.HTTPError:
                raise
            except Exception as e:
                logger.debug("Upload failed: %s", e)
        if not bypass and min_dur > BANDWIDTH_FINISH_REQUEST_DURATION_MS:
            finished_ul = True
        if verbose:
            pts = [p["bps"] for p in all_points(up) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
            logger.info("Upload progress: %.2f Mbps", (percentile(pts, BANDWIDTH_PERCENTILE) / 1e6) if pts else 0)

    with requests.Session() as session:
        info = _get_ip(base_url, auth, timeout, session=session)
        if verbose:
            logger.info("getIP: %s", info.get("ip") or "(none)")

        for m in MEASUREMENTS:
            if m["type"] == "download" and finished_dl:
                continue
            if m["type"] == "upload" and finished_ul:
                continue
            if m["type"] == "latency":
                do_latency(m["num_packets"], session)
            elif m["type"] == "download":
                do_download(m["bytes"], m["count"], m.get("bypass_min_duration", False), session)
            elif m["type"] == "upload":
                do_upload(m["bytes"], m["count"], m.get("bypass_min_duration", False), session)

    dl_pts = [p["bps"] for p in all_points(down) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
    ul_pts = [p["bps"] for p in all_points(up) if p["duration"] >= BANDWIDTH_MIN_REQUEST_DURATION_MS and p["bps"]]
    return SpeedtestResult(
        latency_measurements=tuple(latencies),
        ping_ms=percentile(latencies, LATENCY_PERCENTILE) if latencies else 0.0,
        jitter_ms=_jitter(latencies) if len(latencies) >= 2 else 0.0,
        download_speed=percentile(dl_pts, BANDWIDTH_PERCENTILE) if dl_pts else 0.0,
        upload_speed=percentile(ul_pts, BANDWIDTH_PERCENTILE) if ul_pts else 0.0,
        client_ip=f"{info.get('ip', '')} {info.get('org', '')} {info.get('country', '')}".strip(),
        colo=f"Server: {info['colo']}" if info.get("colo") else "",
    )


def run_standard_test(
    base_url: str,
    auth: Optional[Union[str, Tuple[str, str]]] = None,
    timeout: int = 15,
    verbose: bool = False,
) -> SpeedtestResult:
    """
    Run the full speedtest (same sequence as the website).
    base_url is required (your Worker URL).
    auth is optional: password string or (_, password) tuple; server only checks password.
    Returns SpeedtestResult with download_speed/upload_speed in bps, ping_ms, jitter_ms, client_ip, colo.
    """
    if not base_url or not str(base_url).strip():
        raise ValueError("base_url is required.")
    base_url = base_url.strip().rstrip("/")
    auth = _normalize_auth(auth)
    return _run_full(base_url, auth, timeout, verbose)
