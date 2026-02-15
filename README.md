# cf_speedtest_custom

_Host a speedtest server on Cloudflare Workers and run tests using a Python library._

Custom speedtest platform on Cloudflare Workers. A lot of work was put into getting it to be close to the official `speed.cloudflare.com` test.

## Server and Website Install

Worker is located in the `website/` directory.

1. Install and build:

   ```bash
   npm install
   npm run build
   ```

3. Deploy:
   ```bash
   npx wrangler deploy
   ```

If you want to password protect the speedtest, set a password via:

```bash
npx wrangler secret put SPEEDTEST_PASSWORD
```

## Python client

Default backend is `https://speed.cloudflare.com`. To use **your** Worker and optional Basic Auth:

```python
from cf_speedtest.speedtest import configure, run_standard_test

configure(
    base_url="https://cf-speedtest.your-subdomain.workers.dev",
    auth=("speedtest", "your_speedtest_password"),
)
results = run_standard_test([100_000, 1_000_000, 10_000_000], percentile_val=90)
print("Download (bytes/s):", results["download_speed"])
print("Upload (bytes/s):", results["upload_speed"])
```

Or pass per-call overrides:

```python
results = run_standard_test(
    [100_000, 1_000_000],
    90,
    base_url="https://cf-speedtest.xxx.workers.dev",
    auth=("speedtest", "mypass"),
)
```

Install the package (from repo root):

```bash
pip install -e .
```

Example script with env-based config:

```bash
export CF_SPEEDTEST_URL="https://cf-speedtest.xxx.workers.dev"
export CF_SPEEDTEST_USER="speedtest"
export CF_SPEEDTEST_PASS="your_password"
python example_test.py
```
