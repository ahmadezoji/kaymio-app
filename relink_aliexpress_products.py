"""Replace the affiliate link of every AliExpress product on the WooCommerce site.

For each product whose external (Buy) URL points to AliExpress:
  1. find the AliExpress item id (from /item/<id>.html, or by following s.click short links)
  2. generate a new affiliate link with ALIEXPRESS_TRACKING_ID
  3. PUT it as the product's external_url (and set SKU AE-<id> when the SKU is empty)

Dry run by default; nothing is changed without --apply. Every run writes a CSV
(product id, old link, new link) to data/ so changes can be reverted.

    python relink_aliexpress_products.py            # preview
    python relink_aliexpress_products.py --apply    # update the site
    python relink_aliexpress_products.py --apply --limit 5
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from typing import Optional
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

load_dotenv()

from kaymio.integrations.aliexpress import AliExpressApiError, extract_item_ids, get_aliexpress_client  # noqa: E402
from kaymio.wordpress.wordpress_api_helper import (  # noqa: E402
    ALIEXPRESS_MATCH_TOKENS,
    _fetch_all_woocommerce_products,
    _product_external_link_matches,
)

WC_URL = os.getenv("WORDPRESS_API_URL", "").rstrip("/")
WC_AUTH = (os.getenv("WC_CONSUMER_KEY", ""), os.getenv("WC_CONSUMER_SECRET", ""))
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def resolve_item_id(url: str) -> Optional[str]:
    ids = extract_item_ids(unquote(url))
    if ids:
        return ids[0]
    # Short links (s.click.aliexpress.com/e/...) redirect to the item page.
    try:
        response = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20, allow_redirects=True)
    except requests.RequestException:
        return None
    chain = " ".join([r.headers.get("Location", "") for r in response.history] + [response.url, response.text[:20000]])
    ids = extract_item_ids(unquote(unquote(chain)))
    return ids[0] if ids else None


def generate_link(client, item_id: str, attempts: int = 4) -> str:
    """Retry briefly when the API's per-second rate limit kicks in."""
    for attempt in range(attempts):
        try:
            return client.generate_affiliate_link(f"https://www.aliexpress.com/item/{item_id}.html")
        except AliExpressApiError as exc:
            if exc.code != "ApiCallLimit" or attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def update_product(product_id: int, payload: dict) -> None:
    response = requests.put(f"{WC_URL}/wp-json/wc/v3/products/{product_id}", json=payload, auth=WC_AUTH, timeout=60)
    if response.status_code >= 400 and "sku" in payload:
        # SKU already used by another product: keep the link update, drop the SKU.
        payload = {k: v for k, v in payload.items() if k != "sku"}
        response = requests.put(f"{WC_URL}/wp-json/wc/v3/products/{product_id}", json=payload, auth=WC_AUTH, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually update the products")
    parser.add_argument("--limit", type=int, default=0, help="process at most N products")
    args = parser.parse_args()

    client = get_aliexpress_client()
    if not client.settings.tracking_id:
        print("ALIEXPRESS_TRACKING_ID is not set.")
        return 1

    result = _fetch_all_woocommerce_products(per_page=20)
    if result.get("error"):
        print(f"Cannot read WooCommerce products: {result['error']}")
        return 1
    products = [p for p in result["items"] if _product_external_link_matches(p, ALIEXPRESS_MATCH_TOKENS)]
    if args.limit:
        products = products[: args.limit]
    print(f"{len(products)} AliExpress product(s) found. Mode: {'APPLY' if args.apply else 'DRY RUN'}")

    os.makedirs("data", exist_ok=True)
    report_path = f"data/aliexpress_relink_{dt.datetime.now():%Y%m%d_%H%M%S}{'' if args.apply else '_dryrun'}.csv"
    counts = {"updated": 0, "would_update": 0, "failed": 0}
    with open(report_path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["product_id", "name", "item_id", "old_link", "new_link", "status"])
        for product in products:
            old_link = str(product.get("external_url") or "")
            item_id = resolve_item_id(old_link)
            new_link, status = "", ""
            if not item_id:
                status = "failed: item id not found"
            else:
                try:
                    new_link = generate_link(client, item_id)
                    if args.apply:
                        payload = {"external_url": new_link}
                        if not product.get("sku"):
                            payload["sku"] = f"AE-{item_id}"
                        update_product(product["id"], payload)
                        status = "updated"
                    else:
                        status = "would_update"
                except (AliExpressApiError, RuntimeError, requests.RequestException) as exc:
                    status = f"failed: {exc}"
            counts["failed" if status.startswith("failed") else status] += 1
            writer.writerow([product["id"], product.get("name", ""), item_id or "", old_link, new_link, status])
            print(f"#{product['id']:<6} {status:<14} {item_id or '-':<18} {str(product.get('name', ''))[:50]}")

    print(f"\nDone: {counts}. Report: {report_path}")
    return 0 if not counts["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
