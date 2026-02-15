# cf_speedtest

_Host a speedtest server on Cloudflare Workers and run tests using a Python library._

Custom speedtest platform on Cloudflare Workers. Includes a website and Python client.



## Worker (website)

```bash
cd website
npm install && npm run build
npx wrangler deploy
```

Optional: `npx wrangler secret put SPEEDTEST_PASSWORD` to password-protect.

## Python client

Requires a Worker URL (no default). Optional Basic Auth per call.

```python
from cf_speedtest import run_standard_test

# Full sequence (same as website)
results = run_standard_test("https://cf-speedtest.xxx.workers.dev")

# With Basic Auth and/or shorter run
results = run_standard_test(
    "https://cf-speedtest.xxx.workers.dev",
    measurement_sizes=[100_000, 1_000_000, 10_000_000],
    auth=("user", "password"),
    verbose=True,
)
# results["download_speed"], ["upload_speed"] in bps; ["ping_ms"], ["jitter_ms"]
```

**Install:** `pip install -e .` (from repo root) or `pip install git+https://github.com/<user>/<repo>.git`

**CLI example:**

```bash
python python/example_test.py --url https://cf-speedtest.xxx.workers.dev
python python/example_test.py --url https://... --user speedtest --password secret --full
python python/example_test.py --help
```
