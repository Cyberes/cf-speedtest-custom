# cf-speedtest-custom

_Host a speedtest server on Cloudflare Workers and run tests using a Python library._

Custom speedtest platform on Cloudflare Workers. Includes a website and Python client. A lot of work has been put into making sure it produces similar results to `speed.cloudflare.com`.

I used to run automated speedtests against `speed.cloudflare.com` but they have tightened their rate-limiting to the point that these automated runs are non-functional.


## Worker (website)

```bash
cd website
npm install && npm run build
npx wrangler deploy
```

Optional: `npx wrangler secret put SPEEDTEST_PASSWORD` to password-protect. Username is ignored by the server.



## Python client

```python
from cf_speedtest_custom import run_standard_test

# No auth
results = run_standard_test("https://cf-speedtest.xxx.workers.dev")

# With optional password (same full sequence as website)
results = run_standard_test(
    "https://cf-speedtest.xxx.workers.dev",
    auth="secret",
    verbose=True,
)
# results is a SpeedtestResult: .download_speed, .upload_speed (bps), .ping_ms, .jitter_ms, .latency_measurements, .client_ip, .colo
```



### Install

`pip install -e .` (from repo root) or `pip install git+https://git.evulid.cc/cyberes/cf-speedtest-custom`



### CLI Example

```bash
python python/example_test.py --url https://cf-speedtest.xxx.workers.dev
python python/example_test.py --url https://... --password secret
python python/example_test.py --help
```
