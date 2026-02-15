# cf_speedtest

_Host a speedtest server on Cloudflare Workers and run tests using a Python library._

Custom speedtest platform on Cloudflare Workers. Includes a website and Python client.



## Worker (website)

```bash
cd website
npm install && npm run build
npx wrangler deploy
```

Optional: `npx wrangler secret put SPEEDTEST_PASSWORD` to password-protect. Username is ignored by the server.



## Python client

```python
from cf_speedtest import run_standard_test

# No auth
results = run_standard_test("https://cf-speedtest.xxx.workers.dev")

# With optional password (same full sequence as website)
results = run_standard_test(
    "https://cf-speedtest.xxx.workers.dev",
    auth="secret",
    verbose=True,
)
# results["download_speed"], ["upload_speed"] in bps; ["ping_ms"], ["jitter_ms"]
```



### Install

`pip install -e .` (from repo root) or `pip install git+https://git.evulid.cc/cyberes/cf_speedtest_custom.git`



### CLI Example

```bash
python python/example_test.py --url https://cf-speedtest.xxx.workers.dev
python python/example_test.py --url https://... --password secret
python python/example_test.py --help
```
