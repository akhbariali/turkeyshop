# ROUTE / روت — Turkey to Iran shoe shop

Deployment is **offline at the owner’s request**. The shop service and both importer timers are stopped and disabled. Application files, cached products and the database are retained on the server.

Configured address when running: http://94.184.25.127:8091 · English: http://94.184.25.127:8091/?lang=en

Persian is the default with RTL layout, Persian numerals and locally hosted Vazirmatn. The EN/فارسی switch preserves the original English design. Both versions use the same catalog, local `/products/{id}` pages, size selector, gallery, cart and preorder intake. Product names and source descriptions retain their original language; storefront labels, explanations, pricing and checkout are bilingual.

## Sources

Six brands: Nike, adidas, ECCO, Camper, Puma and New Balance. All connections were verified **from the deployment server**. This is not proof of access from every Iranian ISP. Skechers returned an empty challenge response and was excluded.

- **Nike:** follows all 65 pages from its public Turkish catalog API, including colorways (3,115 at initial discovery). Product HTML provides descriptions, images and EU size labels. `ACTIVE` size status is **not** treated as confirmed stock. The inventory endpoint was unavailable during verification, so these sizes cannot be ordered until their stock is verified.
- **adidas:** paginates its public Turkish search API (59 pages, 2,791 shoes at initial discovery). This provides price, images, color and style-level orderability. Product/stock endpoints currently return HTTP 403; size lists are unavailable and ordering is disabled until size stock is verified. The scheduled worker retries; nothing is fabricated.
- **Camper:** follows men's, women's and children's category pagination (70 pages, 2,720 colorways at initial discovery). Its page data includes size-level availability and size-specific prices.
- **New Balance:** paginates all three shoe categories (40 pages, 347 unique shoes at initial discovery); barcode-level stocks are mapped to EU sizes.
- **Puma:** follows published load-more links for men's, women's, boys' and girls' shoes (21 pages, 457 styles at discovery). Product stock is restricted to the selected colorway, rather than combining sizes from all colors.
- **ECCO:** discovers the published English product sitemaps and shoe categories. ProductGroup variants supply size stock. Additional sitemap products are progressively added by the background queue; obvious accessories are excluded.

Counts vary with source updates. A failed catalog page preserves existing records and reports the error instead of marking missing products sold out. Only a fully successful catalog crawl retires missing records. ECCO's sitemap discovery is separately queued for product enrichment.

## Unattended importer

The shop serves only its database; customer requests **never** scrape a supplier.

- `turkeyshop-sync.timer`: catalog discovery, current prices, and Parisan feeds every six hours.
- `turkeyshop-refresh.timer`: runs a batch of the detail/availability queue five minutes after the preceding batch finishes. Each batch handles up to 240 product records, plus ECCO discovery and primary-image downloads. A complete pass is staggered across batches; this does not mean every shoe is refreshed every five minutes.
- Descriptions, features and source HTML are captured on first successful enrichment. Subsequent checks update prices, sizes and availability while keeping cached source content. Source snapshots are compressed under `/var/lib/turkeyshop/source`.
- Primary images are progressively cached under `/var/lib/turkeyshop/media`; additional gallery images continue to use their source URLs. All fonts are local. Initial enrichment continues unattended after catalog discovery.
- Failed detail endpoints retry after six hours. Unknown or stale stock cannot be ordered. Quotes and order submissions require an explicit available size and a stock check less than 12 hours old. Missing sizes, unknown stock, sold-out products and invalid size labels are rejected. Free-text size entry is removed from product pages and the cart. Known sold-out products are hidden from the collection; their direct pages remain visible with ordering disabled. A catalog listing alone cannot reactivate a previously sold-out product; verified size-level restocking is required.
- A file lock prevents overlapping importer jobs. Per-host throttling, timeouts and bounded retries keep network work controlled.

```sh
ssh root@94.184.25.127 'systemctl status turkeyshop turkeyshop-sync.timer turkeyshop-refresh.timer --no-pager'
ssh root@94.184.25.127 'journalctl -u turkeyshop-sync -u turkeyshop-refresh -n 60 --no-pager'
ssh root@94.184.25.127 'systemctl start turkeyshop-sync.service'
ssh root@94.184.25.127 'systemctl start turkeyshop-refresh.service'
```

For a specific brand: run `/usr/bin/python3 /opt/turkeyshop/importer.py --catalog --brand Nike` as the dedicated service user with the environment file and `PYTHONPATH=/opt/turkeyshop/vendor`. Prefer the supplied systemd services for routine operation. `--all` performs catalog discovery plus a detail batch; `--refresh --limit N` changes the batch size.

## Pricing and orders

Parisan `/rate.json` and `/shipping-rates.json` are fetched automatically. All amounts are **toman**, not rial:

`converted product cost + 10% commission on product cost + estimated shipping`

Cards and product pages include estimated **air shipping for a 1 kg package per pair**. The bag allows ground freight and a different estimated weight. Shipping weight is explicitly an estimate, not a measured supplier weight. The final total is rounded up to 1,000 toman with decimal arithmetic. Size-specific source prices are used when available. Price feeds, exchange-rate timestamps and catalog data must be less than 24 hours old for quotes.

Preorders are stored in SQLite with contact information, requested sizes, reference code and a server-calculated quote snapshot. There is no supplier purchase, payment gateway, email/SMS delivery or automatic fulfillment. Final price, package weight and availability require confirmation before payment.

## Deployment and private order viewer

- Application: `/opt/turkeyshop`
- SQLite: `/var/lib/turkeyshop/shop.db`
- Root-only configuration: `/etc/turkeyshop.env`
- Dedicated unprivileged user: `turkeyshop`
- HTTP port: **8091**
- Runtime dependencies are vendored in `/opt/turkeyshop/vendor`; no global Python packages were changed.
- Existing nginx configuration and other services were left untouched.

Use an SSH tunnel for the admin token:

```sh
ssh -L 18091:127.0.0.1:8091 root@94.184.25.127
```

Open http://localhost:18091/admin. Obtain `ADMIN_TOKEN` from `/etc/turkeyshop.env` in your own SSH session and paste it into that local page. The token stays in page memory and is not stored in the browser. The API rejects unauthenticated access and lists the newest 200 requests.

The public site currently uses HTTP. Configure a domain and TLS before collecting real customer contact/address data. Existing TLS services were not modified. No external backup destination is configured.

## Local development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python importer.py --all
.venv/bin/python app.py
```

Set `SHOP_DB`, `PORT`, and `ADMIN_TOKEN` through the environment. Default local database: `data/shop.db`. Admin access is disabled without a token. Fixture tests cover real brand data shapes, color-specific stock, unknown Nike inventory, cache preservation, stale pricing, invalid sizes, size-specific prices and shipping-inclusive totals. JS rendering tests check Persian default, English switching, local product links and cart calculations. Browser control was unavailable, so visual QA could not be performed.
