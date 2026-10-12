# Python website PageSpeed bot

Requires Python 3.10 or newer. Uses only the standard library; no pip dependencies are needed.

## Setup

Install Python with its command available on PATH. Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set `PAGESPEED_API_KEY`. Enable the PageSpeed Insights API in the key's Google Cloud project. Google setup instructions: https://developers.google.com/speed/docs/insights/v5/get-started

Keep the key local. Environment variables override values in `.env`.

## Run

Launch the browser dashboard:

```powershell
python ui_server.py
```

Open **http://127.0.0.1:8000**, enter your website URL, choose mobile/desktop and a page limit, then click **Analyze website**. Results appear as audits finish. Search URLs, filter devices, and choose 10/25/50 results per page. **Insights** opens metrics, opportunities, field data, and the full Lighthouse response. Export CSV or JSON directly from the dashboard.

The API key stays in `.env` on the Python backend. One audit runs at a time. UI reports are stored in `reports/ui/`, and past reports appear in audit history after a restart. Keep the server open while audits run. Use `python ui_server.py --port 8080` for a different port. This dashboard runs locally on loopback; it is not deployed to a hosting service.

The command-line bot remains available:

```powershell
python pagespeed_bot.py https://example.com --max-pages 100
```

Discover pages without an API key or audit requests:

```powershell
python pagespeed_bot.py https://example.com --discover-only
```

Resume using saved successful audit responses:

```powershell
python pagespeed_bot.py https://example.com --resume
```

Other options: `--strategy mobile|desktop|both`, `--output reports`, `--urls-file extra-urls.txt` (one URL per line), `--env-file .env`.

## Reports

The **Comparison CSV** dashboard button downloads `performance_comparison.csv`: one row per URL with separate Mobile and Desktop Performance, FCP, SI, TBT, LCP, CLS, audit dates/timestamps, links, status, and errors. FCP/SI/LCP are formatted in seconds, TBT in milliseconds, and CLS stays unitless. Original precise numeric measurements remain in `results.csv` and JSON. Audit dates are UTC dates from Google's response, including the original dates when resuming.

`results.csv` also includes page name, website, audit date/timestamp, and PageSpeed launch links. These links run a new analysis and may show different measurements; the API does not supply saved PageSpeed share URLs. `pagespeed_link_type` identifies this distinction and `saved_analysis_url` remains blank. Saved raw JSON is the evidence for the bot's actual measurements.

For `nationalag.com` and `www.nationalag.com`, the bot automatically prioritizes the 14 pages in the NAF comparison sheet, including Farm Input Loans. Missing, blocked, or non-HTML required pages appear as explicit failed rows with blank metrics. Use `--report-profile naf` for a National Ag staging host, or `--report-profile none` to disable this list. The page limit must accommodate the required list. Select both devices for a complete comparison; a single-device run marks the other device as not audited.

Competitors can be audited by entering their website URLs separately; the bot cannot infer equivalent competitor pages. Existing historical measurements in the workbook are not replaced or recreated.

- `reports/results.csv`: performance, accessibility, best practices, SEO scores, FCP, LCP, TBT, CLS, Speed Index, and audit errors.
- `reports/results.json`: metrics, failed audits, warnings, and any returned field data.
- `reports/raw/`: complete API responses with all returned Lighthouse details.
- `reports/urls.txt`: discovered HTML pages.
- `reports/discovery.json`: warnings and whether discovery reached the URL limit.

Timing metrics use milliseconds; CLS is unitless. Missing values remain blank/null. Results are saved after every audit. Individual failures produce a nonzero exit code. Resume retains original audit dates; omit it for fresh measurements. Use a different output folder per run to retain history.

## Discovery and quota

The bot discovers same-origin pages through robots.txt sitemaps, XML sitemap indexes, and static HTML links. It respects robots.txt rules and HTML crawl delays. The default discovery limit is 100 URLs; increase it as needed. XML sitemap discovery is limited to 100 files; compressed sitemap files are not supported.

JavaScript-only routes, authenticated pages, and hidden routes cannot be automatically discovered. Supply known extra URLs with `--urls-file`; they must still be same-origin and robots-allowed. Subdomains and www/non-www hosts are separate origins. Start with the website's final canonical URL. Discovery cannot guarantee an inventory of every possible page.

Both strategies use two API requests per page. Audits run sequentially, with retry/backoff for transient failures and rate limits. Field data may be absent. Lab TBT is not real-user INP, and scores vary between runs.

## Schedule and test

Windows Task Scheduler: program = your installed `python.exe`, arguments = `pagespeed_bot.py https://example.com --output reports`, **Start in** = this project directory.

```powershell
python -m unittest discover -s tests -v
```
