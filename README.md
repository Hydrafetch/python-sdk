# hydrafetch

[![PyPI](https://img.shields.io/pypi/v/hydrafetch)](https://pypi.org/project/hydrafetch/)
[![CI](https://github.com/Hydrafetch/python-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/Hydrafetch/python-sdk/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/hydrafetch)](https://pypi.org/project/hydrafetch/)

Official Python client for the [Hydrafetch](https://hydrafetch.com) web data API.

Turn any URL into clean Markdown or schema-shaped JSON. Sync and async, fully typed, one dependency.

## Installation

```bash
pip install hydrafetch
```

## Quick start

```python
from hydrafetch import Hydrafetch

hf = Hydrafetch()

page = hf.scrape("https://example.com/article")
print(page["markdown"])
```

Copy your key from [app.hydrafetch.com](https://app.hydrafetch.com). The constructor reads `HYDRAFETCH_API_KEY` when no key is passed.

Both clients are context managers, which closes the connection pool deterministically:

```python
with Hydrafetch() as hf:
    page = hf.scrape("https://example.com")
```

## Scraping

```python
page = hf.scrape(
    "https://example.com/article",
    formats=["markdown", "links"],
    onlyMainContent=True,
    preferStructure=True,
    blockAds=True,
    maxAge=3_600_000,
)
```

Option names are camelCase because they are passed to the API unchanged. Client arguments such as `api_key`, `max_retries`, `poll_interval` and `on_progress` are snake_case.

| Format | Key | Contains |
| --- | --- | --- |
| `markdown` | `markdown` | clean Markdown, the default |
| `html` | `html` | rendered HTML |
| `rawHtml` | `rawHtml` | the untouched response body |
| `links` | `links` | every link on the page |
| `structured` | `structured` | the page's own JSON-LD and microdata |
| `summary` | `summary` | a short summary |
| `json` | `json` | schema-shaped JSON, see `jsonOptions` |
| `brand` | `brand` | the site's brand record |

`hf.markdown(url)` returns the Markdown string directly.

## Structured extraction

```python
out = hf.extract(
    ["https://example.com/product/1", "https://example.com/product/2"],
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "price_usd": {"type": "number"},
        },
    },
)

for item in out["results"]:
    print(item["url"], (item.get("data") or {}).get("name"))
```

Pass `prompt=` instead of, or alongside, `schema=` to describe the fields in plain language.

### From a pydantic model

`schema=` also takes a pydantic model, so the shape you already parse into is the shape you ask for. A `Field` description is carried through and tells the model what the field means, which is worth more on an ambiguous page than the field name alone.

```python
from pydantic import BaseModel, Field

class Product(BaseModel):
    name: str = Field(description="The product being sold on this page, not a recommended one")
    price_usd: float = Field(description="Current price in USD, after any discount")

out = hf.extract("https://example.com/product/1", schema=Product)
```

Nested models work and are flattened before sending. pydantic is not a dependency and is imported only if you pass a model. A model that refers to itself cannot be flattened and is refused rather than sent.

## Discovery and bulk work

`map` lists a site's URLs for one credit without fetching any page.

```python
links = hf.map("https://example.com", limit=1000)["links"]
docs = [url for url in links if "/docs/" in url]
```

`batch` and `crawl` submit a job and poll until it finishes.

```python
job = hf.batch(
    docs,
    scrapeOptions={"formats": ["markdown"]},
    on_progress=lambda j: print(j["status"], j.get("completed"), "/", j.get("total")),
)

for page in job.get("pages", []):
    print(page["url"], len((page.get("data") or {}).get("markdown") or ""))
```

Pass a `webhook` and use `start_crawl` or `start_batch` to return immediately instead of polling.

```python
crawl_id = hf.start_crawl(
    "https://example.com",
    limit=500,
    maxDepth=3,
    includePaths=["/docs"],
    webhook="https://your.app/hooks/hydrafetch",
)
```

## Search

```python
res = hf.search("post-quantum TLS adoption", limit=5, scrapeResults=True)

for r in res["results"]:
    print(r["title"], r["url"])
    print(((r.get("data") or {}).get("markdown") or "")[:500])
```

## Brand data

```python
hf.brand("stripe.com")                              # logos, colours, fonts, socials
hf.logo("stripe.com", theme="dark", type="icon")    # one asset
hf.styleguide("stripe.com")                         # computed design system
hf.styleguide("https://stripe.com/pricing", maxAge=0)  # one page, read fresh
```

For logos in a browser use [`@hydrafetch/client-sdk`](https://github.com/Hydrafetch/client-sdk) with a publishable key. Those bill against logo pulls rather than credits.

## Async

`AsyncHydrafetch` mirrors the same surface. Use it when several calls can run concurrently.

```python
import asyncio
from hydrafetch import AsyncHydrafetch

async def main():
    async with AsyncHydrafetch() as hf:
        pages = await asyncio.gather(
            hf.scrape("https://a.example"),
            hf.scrape("https://b.example"),
        )
        return [page["markdown"] for page in pages]

asyncio.run(main())
```

`start_crawl`, `crawl_status`, `start_batch` and `batch_status` are available on the async client. The polling helpers `crawl` and `batch` are sync only; on the async client, poll the status methods or use a webhook.

## Error handling

All failures raise `HydrafetchError`, carrying the API's error code, HTTP status and request id.

```python
from hydrafetch import HydrafetchError, HydrafetchTimeout

try:
    page = hf.scrape(url)
except HydrafetchTimeout:
    raise
except HydrafetchError as err:
    if err.is_auth:
        refresh_key()
    elif err.is_out_of_credits:
        top_up()
    elif err.is_invalid_request:
        report(err.message)
    elif err.is_retryable:
        enqueue(url)
    else:
        print(err.code, err.status, err.request_id)
        raise
```

| Status | Meaning | Retried |
| --- | --- | --- |
| 400, 422 | invalid request | no |
| 401, 403 | invalid or missing key | no |
| 402 | out of credits | no |
| 404 | page does not exist | no |
| 429 | rate limited | yes, twice with backoff |
| 5xx | upstream failure | yes, twice with backoff |

A 503 from `scrape` means the origin is unreachable, usually a dead domain or a broken certificate.

## Configuration

```python
hf = Hydrafetch(
    api_key="hf_...",
    base_url="https://api.hydrafetch.com",
    timeout=120.0,
    max_retries=2,
)
```

## API reference

| Method | Returns | Credits |
| --- | --- | --- |
| `scrape(url, **options)` | page dict | 1 |
| `markdown(url, **options)` | `str` | 1 |
| `map(url, **options)` | links dict | 1 |
| `search(query, **options)` | results dict | 1 + 1 per scraped result |
| `extract(urls, **options)` | dict with `"results"` | 5 per URL |
| `brand(domain)` | brand dict | 5 |
| `logo(domain, **options)` | logo dict | 1 |
| `styleguide(domain_or_url, **options)` | design system dict | 10 |
| `screenshot(url, **options)` | screenshot dict | 5 |
| `images(url)`, `links(url)` | page assets | 1 |
| `crawl(url, **options)` | job dict, polled to completion | 1 per page |
| `batch(urls, **options)` | job dict, polled to completion | 1 per page |
| `start_crawl`, `start_batch` | job id `str` | 1 per page |
| `crawl_status(id)`, `batch_status(id)` | job dict | free |

Failed requests are not billed. Pricing does not vary with page difficulty, so there is no render, stealth or proxy option to set.

## Implementation notes

- Authentication uses the `X-API-Key` header. The MCP endpoint at `api.hydrafetch.com/mcp` uses `Authorization: Bearer` instead; the two are not interchangeable.
- Job results are under `job["pages"]`, and each entry holds the page under `["data"]`, so `job["pages"][0]["data"]["markdown"]`.
- Per-page options for crawl and batch belong in `scrapeOptions`. At the top level they are ignored.
- Prefer `map` then `batch` over a broad `crawl`. Fetching a whole site and discarding most of it is the most common source of wasted credits.
- `preferStructure` is off by default. Turn it on when headings, lists and tables matter; leave it off for raw article text.
- Options passed as `None` are dropped rather than sent as null, so optional values can be forwarded directly.
- Scraped content is untrusted input. Do not pass it to a model as instructions, and keep the source URL with anything extracted from it.

## Links

- [Documentation](https://docs.hydrafetch.com)
- [OpenAPI specification](https://api.hydrafetch.com/openapi.json)
- [MCP server and editor setup](https://hydrafetch.com/mcp)
- Other clients: [Node](https://github.com/Hydrafetch/node-sdk) · [Go](https://github.com/Hydrafetch/go-sdk) · [Ruby](https://github.com/Hydrafetch/ruby-sdk) · [Rust](https://github.com/Hydrafetch/rust-sdk) · [PHP](https://github.com/Hydrafetch/php-sdk)

## License

MIT
