/**
 * Cloudflare-official-style speedtest: sequential single requests,
 * same measurement sequence, 90th percentile bandwidth, median latency.
 * Matches speedtest/src (defaultConfig + BandwidthEngine + MeasurementCalculations).
 *
 * Measurement methodology (no correction factors):
 * - Download: payload bytes (Resource Timing transferSize, else requested bytes) / payload time (responseEnd - responseStart). No added latency or factors.
 * - Upload: XHR upload progress events; instantaneous bps = (bytes delta) / (time delta) between consecutive events, 90th percentile per request. If fewer than 2 progress events, one sample = payload bytes / round-trip time (no /2).
 * - Ping: TTFB minus server-reported processing time (Server-Timing). If no Server-Timing or dur < 1, use 0 so reported value = TTFB.
 * - Jitter: mean of |latency[i] - latency[i-1]|.
 */
(function(global) {
  var BANDWIDTH_FINISH_REQUEST_DURATION = 1000;
  var BANDWIDTH_MIN_REQUEST_DURATION = 10;
  var BANDWIDTH_PERCENTILE = 0.9;
  var LATENCY_PERCENTILE = 0.5;

  // Exact CF defaultConfig order (without packetLoss): latency 1, download 100k bypass, latency 20, then dl/ul
  var MEASUREMENTS = [
    { type: 'latency', numPackets: 1 },
    { type: 'download', bytes: 1e5, count: 1, bypassMinDuration: true },
    { type: 'latency', numPackets: 20 },
    { type: 'download', bytes: 1e5, count: 9 },
    { type: 'download', bytes: 1e6, count: 8 },
    { type: 'upload', bytes: 1e5, count: 8 },
    { type: 'upload', bytes: 1e6, count: 6 },
    { type: 'download', bytes: 1e7, count: 6 },
    { type: 'upload', bytes: 1e7, count: 4 },
    { type: 'download', bytes: 2.5e7, count: 4 },
    { type: 'upload', bytes: 2.5e7, count: 4 },
    { type: 'download', bytes: 1e8, count: 3 },
    { type: 'upload', bytes: 5e7, count: 3 },
    { type: 'download', bytes: 2.5e8, count: 2 }
  ];

  function percentile(vals, perc) {
    if (!vals || vals.length === 0) return 0;
    var sorted = vals.slice().sort(function(a, b) { return a - b; });
    var idx = (vals.length - 1) * perc;
    var rem = idx % 1;
    if (rem === 0) return sorted[Math.round(idx)];
    var lo = sorted[Math.floor(idx)];
    var hi = sorted[Math.ceil(idx)];
    return lo + (hi - lo) * rem;
  }

  function getServerTime(r) {
    var st = r.headers.get('server-timing');
    if (st) {
      var m = st.match(/dur=([0-9.]+)/);
      if (m) {
        var dur = parseFloat(m[1]);
        if (dur >= 1) return dur;
      }
    }
    return null;
  }

  function getEffectiveServerTime(r) {
    var t = getServerTime(r);
    return (t != null && t >= 1) ? t : 0;
  }

  function CFSpeedtest(baseUrl) {
    this.baseUrl = (baseUrl || '').replace(/\/$/, '');
    this._state = -1;
    this._latencyTimings = [];
    this._down = {};
    this._up = {};
    this._finished = { down: false, up: false };
    this._aborted = false;
    this._controller = null;
    this._currentXhr = null;
    this.onupdate = null;
    this.onend = null;
  }

  CFSpeedtest.prototype.getState = function() {
    return this._state;
  };

  CFSpeedtest.prototype._emit = function(data) {
    if (this.onupdate) this.onupdate(data);
  };

  CFSpeedtest.prototype._doFetch = function(url, opts) {
    var self = this;
    if (!this._controller) this._controller = new AbortController();
    var options = { signal: this._controller.signal, credentials: 'include' };
    if (opts) { if (opts.method) options.method = opts.method; if (opts.body !== undefined) options.body = opts.body; }
    return fetch(url, options).then(function(r) {
      if (r.status === 401) throw new Error('Password required. Reload the page and enter the password when prompted (leave username blank).');
      if (!r.ok) throw new Error(r.statusText);
      return r;
    });
  };

  CFSpeedtest.prototype._runLatency = function(numPackets) {
    var self = this;
    var url = this.baseUrl + '/__down?bytes=0';
    var remaining = numPackets;

    function one() {
      if (self._aborted || remaining <= 0) return self._next();
      var reqUrl = url + (url.indexOf('?') >= 0 ? '&' : '?') + 'r=' + Math.random();
      return self._doFetch(reqUrl).then(function(r) {
        var effectiveServerTime = getEffectiveServerTime(r);
        return r.text().then(function() {
          var perf = performance.getEntriesByName(reqUrl).slice(-1)[0];
          var ttfb = perf ? (perf.responseStart - perf.requestStart) : 0;
          var ping = Math.max(0.01, ttfb - effectiveServerTime);
          self._latencyTimings.push({ ping: ping });
          remaining--;
          var pings = self._latencyTimings.map(function(t) { return t.ping; });
          self._emit({ testState: 2, pingStatus: percentile(pings, LATENCY_PERCENTILE).toFixed(2), jitterStatus: self._jitter(self._latencyTimings).toFixed(2), clientIp: self._clientIp || '', colo: self._colo || '', dlStatus: '', ulStatus: '', dlProgress: 0, ulProgress: 0, pingProgress: (numPackets - remaining) / numPackets });
          one();
        });
      }).catch(function(err) {
        if (self._aborted) return;
        self._next();
      });
    }
    one();
  };

  CFSpeedtest.prototype._jitter = function(timings) {
    var pings = timings.map(function(t) { return t.ping; });
    if (pings.length < 2) return 0;
    var sum = 0;
    for (var i = 1; i < pings.length; i++) sum += Math.abs(pings[i] - pings[i - 1]);
    return sum / (pings.length - 1);
  };

  CFSpeedtest.prototype._runDownload = function(bytes, count, bypassMinDuration) {
    var self = this;
    var done = 0;
    var minDuration = Infinity;
    var total = count;

    function one() {
      if (self._aborted) return self._next();
      if (done >= count) {
        if (!bypassMinDuration && minDuration > BANDWIDTH_FINISH_REQUEST_DURATION)
          self._finished.down = true;
        return self._next();
      }
      var reqUrl = self.baseUrl + '/__down?bytes=' + bytes + '&r=' + Math.random();
      return self._doFetch(reqUrl).then(function(r) {
        var effectiveServerTime = getEffectiveServerTime(r);
        return r.text().then(function(body) {
          var perf = performance.getEntriesByName(reqUrl).slice(-1)[0];
          if (!perf) { done++; return one(); }
          var ttfb = perf.responseStart - perf.requestStart;
          var payloadTime = Math.max(perf.responseEnd - perf.responseStart, 1);
          var ping = Math.max(0.01, ttfb - effectiveServerTime);
          var transferSize = perf.transferSize || bytes; // transferSize 0 (e.g. Safari): use requested bytes, no factor
          var bps = (8 * transferSize) / (payloadTime / 1000);
          if (!self._down[bytes]) self._down[bytes] = { timings: [], numMeasurements: count };
          self._down[bytes].timings.push({ bps: bps, duration: payloadTime, ping: ping });
          self._down[bytes].timings = self._down[bytes].timings.slice(-count);
          minDuration = Math.min(minDuration, payloadTime);
          done++;
          var pts = self._bandwidthPoints(self._down);
          var cur = pts.length ? percentile(pts.filter(function(d) { return d.duration >= BANDWIDTH_MIN_REQUEST_DURATION; }).map(function(d) { return d.bps; }).filter(Boolean), BANDWIDTH_PERCENTILE) : 0;
          var mbps = (cur / 1e6).toFixed(2);
          self._emit({ testState: 1, dlStatus: mbps, ulStatus: '', pingStatus: '', jitterStatus: '', clientIp: self._clientIp || '', colo: self._colo || '', dlProgress: done / total, ulProgress: 0 });
          one();
        });
      }).catch(function(err) {
        if (self._aborted) return;
        self._next();
      });
    }
    one();
  };

  function makeUploadBody(bytes) {
    try {
      return new Blob([new Uint8Array(bytes)]);
    } catch (e) {
      return new Array(bytes + 1).join('0').slice(0, bytes);
    }
  }

  CFSpeedtest.prototype._runUpload = function(bytes, count, bypassMinDuration) {
    var self = this;
    var url = this.baseUrl + '/__up';
    var body = makeUploadBody(bytes);
    var done = 0;
    var minDuration = Infinity;
    var total = count;

    function one() {
      if (self._aborted) return self._next();
      if (done >= count) {
        if (!bypassMinDuration && minDuration > BANDWIDTH_FINISH_REQUEST_DURATION)
          self._finished.up = true;
        return self._next();
      }
      var reqUrl = url + (url.indexOf('?') >= 0 ? '&' : '?') + 'r=' + Math.random();
      var sendStart = 0;
      var progressSamples = [];
      var xhr = new XMLHttpRequest();
      self._currentXhr = xhr;

      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) progressSamples.push({ loaded: e.loaded, time: Date.now() });
      };

      var onDone = function() {
        self._currentXhr = null;
        if (self._aborted) return self._next();
        var bps = 0;
        var duration = Math.max(Date.now() - sendStart, 1);
        if (progressSamples.length >= 2) {
          var rates = [];
          for (var i = 1; i < progressSamples.length; i++) {
            var dt = (progressSamples[i].time - progressSamples[i - 1].time) / 1000;
            if (dt > 0) {
              var dB = progressSamples[i].loaded - progressSamples[i - 1].loaded;
              if (dB > 0) rates.push((8 * dB) / dt);
            }
          }
          bps = rates.length ? percentile(rates, BANDWIDTH_PERCENTILE) : (8 * bytes) / (duration / 1000);
        } else {
          bps = (8 * bytes) / (duration / 1000);
        }
        if (!self._up[bytes]) self._up[bytes] = { timings: [], numMeasurements: count };
        self._up[bytes].timings.push({ bps: bps, duration: duration });
        self._up[bytes].timings = self._up[bytes].timings.slice(-count);
        minDuration = Math.min(minDuration, duration);
        done++;
        var pts = self._bandwidthPoints(self._up);
        var cur = pts.length ? percentile(pts.filter(function(d) { return d.duration >= BANDWIDTH_MIN_REQUEST_DURATION; }).map(function(d) { return d.bps; }).filter(Boolean), BANDWIDTH_PERCENTILE) : 0;
        var mbps = (cur / 1e6).toFixed(2);
        self._emit({ testState: 3, dlStatus: '', ulStatus: mbps, pingStatus: '', jitterStatus: '', clientIp: self._clientIp || '', colo: self._colo || '', dlProgress: 1, ulProgress: done / total });
        one();
      };

      xhr.onload = function() {
        onDone();
      };
      xhr.onerror = xhr.onabort = function() {
        self._currentXhr = null;
        if (self._aborted) return self._next();
        one();
      };

      xhr.open('POST', reqUrl, true);
      xhr.withCredentials = true;
      sendStart = Date.now();
      xhr.send(body);
    }
    one();
  };

  CFSpeedtest.prototype._bandwidthPoints = function(dir) {
    var out = [];
    Object.keys(dir).forEach(function(bytes) {
      var t = dir[bytes].timings || [];
      t.forEach(function(x) { out.push(x); });
    });
    return out;
  };

  CFSpeedtest.prototype._next = function() {
    if (this._aborted) {
      this._state = 5;
      if (this.onend) this.onend(true);
      return;
    }
    var idx = this._measIdx + 1;
    while (idx < MEASUREMENTS.length) {
      var m = MEASUREMENTS[idx];
      if (m.type === 'download' && this._finished.down) { idx++; continue; }
      if (m.type === 'upload' && this._finished.up) { idx++; continue; }
      break;
    }
    this._measIdx = idx;
    if (idx >= MEASUREMENTS.length) {
      this._state = 4;
      this._emitFinal();
      if (this.onend) this.onend(false);
      return;
    }
    var meas = MEASUREMENTS[idx];
    if (meas.type === 'latency') this._runLatency(meas.numPackets);
    else if (meas.type === 'download') this._runDownload(meas.bytes, meas.count, meas.bypassMinDuration);
    else if (meas.type === 'upload') this._runUpload(meas.bytes, meas.count, meas.bypassMinDuration);
  };

  CFSpeedtest.prototype._emitFinal = function() {
    var latPts = this._latencyTimings.map(function(t) { return t.ping; });
    var pingMs = latPts.length ? percentile(latPts, LATENCY_PERCENTILE) : 0;
    var jitterMs = latPts.length >= 2 ? this._jitter(this._latencyTimings) : 0;
    var downPts = this._bandwidthPoints(this._down).filter(function(d) { return d.duration >= BANDWIDTH_MIN_REQUEST_DURATION && d.bps; }).map(function(d) { return d.bps; });
    var upPts = this._bandwidthPoints(this._up).filter(function(d) { return d.duration >= BANDWIDTH_MIN_REQUEST_DURATION && d.bps; }).map(function(d) { return d.bps; });
    var dlMbps = downPts.length ? (percentile(downPts, BANDWIDTH_PERCENTILE) / 1e6).toFixed(2) : '0';
    var ulMbps = upPts.length ? (percentile(upPts, BANDWIDTH_PERCENTILE) / 1e6).toFixed(2) : '0';
    this._emit({ testState: 4, dlStatus: dlMbps, ulStatus: ulMbps, pingStatus: pingMs.toFixed(2), jitterStatus: jitterMs.toFixed(2), clientIp: this._clientIp || '', colo: this._colo || '', dlProgress: 1, ulProgress: 1 });
  };

  CFSpeedtest.prototype._getIP = function() {
    var self = this;
    return this._doFetch(this.baseUrl + '/getIP').then(function(r) { return r.json(); }).then(function(d) {
      self._clientIp = (d.ip || '') + ' ' + (d.org || '') + ' ' + (d.country || '');
      self._colo = d.colo ? ('CF: ' + d.colo) : '';
    }).catch(function() {});
  };

  CFSpeedtest.prototype.start = function() {
    var self = this;
    this._controller = new AbortController();
    this._state = 3;
    this._aborted = false;
    this._down = {};
    this._up = {};
    this._finished = { down: false, up: false };
    this._latencyTimings = [];
    this._measIdx = -1;
    performance.clearResourceTimings();
    if (typeof performance.setResourceTimingBufferSize === 'function')
      performance.setResourceTimingBufferSize(10000);
    this._getIP().then(function() {
      self._next();
    }).catch(function() {
      self._next();
    });
  };

  CFSpeedtest.prototype.abort = function() {
    this._aborted = true;
    this._state = 5;
    if (this._currentXhr) try { this._currentXhr.abort(); } catch (e) {}
    if (this._controller) this._controller.abort();
  };

  global.CFSpeedtest = CFSpeedtest;
})(typeof window !== 'undefined' ? window : this);
