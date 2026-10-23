#!/usr/bin/env python3
"""Refresh the local storefront catalog from Bare Artisan's public Shopify store."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://bareartisanproducts.com"
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "catalog.json"
USER_AGENT = "BareArtisanCatalogSync/1.0 (+https://bareartisanproducts.com)"
REQUEST_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 4

CATEGORY_SPECS = (
    ("Body Butters", "body-butters"),
    ("Beard Oils", "beard-oils"),
    ("Soaps", "soaps"),
    ("Facial Serum", "facial-serum"),
    ("Skin Tonic", "skin-tonic"),
    ("Hair Products", "hair-products"),
    ("Insect Repellent", "insect-repellent"),
    ("Infused Coconut Oil", "infused-in-coconut-oil"),
    ("Mists", "mist"),
    ("Deodorant", "deodorant"),
    ("Under Eye Serum", "eye-serum"),
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})
_last_request_at = 0.0


def _get(url: str) -> requests.Response:
    global _last_request_at

    for attempt in range(MAX_RETRIES + 1):
        delay = REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if delay > 0:
            time.sleep(delay)

        response = _session.get(url, timeout=(5, 30))
        _last_request_at = time.monotonic()
        if response.status_code != 429 or attempt == MAX_RETRIES:
            response.raise_for_status()
            return response

        retry_after = response.headers.get("Retry-After")
        try:
            wait_seconds = float(retry_after) if retry_after else 2**attempt
        except ValueError:
            wait_seconds = 2**attempt
        time.sleep(max(1.0, wait_seconds))

    raise RuntimeError(f"Could not fetch {url} after {MAX_RETRIES + 1} attempts.")


def _shopify_json(path: str) -> dict[str, Any]:
    response = _get(urljoin(f"{BASE_URL}/", path.lstrip("/")))
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a Shopify JSON object from {response.url}.")
    return payload


def _text(markup: str | bytes | None) -> str:
    if not markup:
        return ""
    text = " ".join(BeautifulSoup(markup, "html.parser").stripped_strings)
    return " ".join(text.replace("\ufffd", " ").split())


def _ingredients(description_html: str | None, description: str) -> str:
    soup = BeautifulSoup(description_html or "", "html.parser")
    for label in soup.find_all(string=re.compile(r"^\s*ingredients?\s*:?\s*$", re.I)):
        parent = label.parent
        following = parent.find_next_sibling() if parent else None
        if following:
            value = following.get_text(" ", strip=True)
            if value:
                return value

    match = re.search(r"\bingredients?\s*:\s*(.+)", description, re.I)
    if not match:
        return ""
    value = match.group(1)
    value = re.split(
        r"\b(?:directions|how to use|usage|benefits|storage)\s*:",
        value,
        maxsplit=1,
        flags=re.I,
    )[0]
    return value.strip(" \t\r\n.;")


def _story_and_logo(homepage_html: str | bytes) -> tuple[str, str]:
    soup = BeautifulSoup(homepage_html, "html.parser")
    story = ""
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if "about us" not in heading.get_text(" ", strip=True).casefold():
            continue
        section = heading.find_parent("section") or heading.parent
        if section:
            paragraphs = [
                _text(str(paragraph))
                for paragraph in section.find_all("p")
                if len(_text(str(paragraph))) > 80
            ]
            if paragraphs:
                story = max(paragraphs, key=len)
                break

    if not story:
        description = soup.find("meta", attrs={"name": "description"})
        story = description.get("content", "").strip() if description else ""

    logo = ""
    for image in soup.select("header img, .header img, [class*='logo'] img"):
        alt = image.get("alt", "").casefold()
        if "logo" in alt or "bare artisan" in alt:
            logo = image.get("src") or image.get("data-src") or ""
            if logo:
                break
    if not logo:
        logo_meta = soup.find("meta", property="og:image:secure_url")
        if logo_meta:
            logo = logo_meta.get("content", "")
    return story, urljoin(BASE_URL, logo) if logo else ""


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _product_record(product: dict[str, Any]) -> dict[str, Any] | None:
    title = str(product.get("title") or "").strip()
    handle = str(product.get("handle") or "").strip()
    if not title or not handle:
        return None

    variants = product.get("variants") or []
    priced_variants = [
        (variant, _money(variant.get("price")))
        for variant in variants
        if isinstance(variant, dict)
    ]
    priced_variants = [(variant, price) for variant, price in priced_variants if price is not None]
    if not priced_variants:
        return None
    available_variants = [pair for pair in priced_variants if pair[0].get("available")]
    purchasable_variants = available_variants or priced_variants
    prices = [price for _, price in priced_variants]
    cart_price = min(price for _, price in purchasable_variants)

    description_html = str(product.get("body_html") or "")
    description = _text(description_html)
    images = product.get("images") or []
    image = images[0] if images and isinstance(images[0], dict) else {}
    image_url = str(image.get("src") or product.get("featured_image") or "")
    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    elif not isinstance(tags, list):
        tags = []

    return {
        "id": str(product.get("id") or handle),
        "handle": handle,
        "name": title,
        "description": description,
        "ingredients": _ingredients(description_html, description),
        "price": float(cart_price),
        "price_label": f"${min(prices):.2f} USD"
        if min(prices) == max(prices)
        else f"${min(prices):.2f}–${max(prices):.2f} USD",
        "available": bool(available_variants),
        "image_url": urljoin(BASE_URL, image_url) if image_url else "",
        "image_alt": str(image.get("alt") or title),
        "product_url": f"{BASE_URL}/products/{handle}",
        "tags": tags,
        "category_handles": [],
        "categories": [],
    }


def scrape_catalog() -> dict[str, Any]:
    homepage = _get(f"{BASE_URL}/")
    story, logo = _story_and_logo(homepage.content)

    collection_payload = _shopify_json("/collections.json?limit=250")
    collections = collection_payload.get("collections")
    if not isinstance(collections, list):
        raise ValueError("Shopify collections response did not contain a collections list.")
    collection_by_handle = {
        str(collection.get("handle")): collection
        for collection in collections
        if isinstance(collection, dict) and collection.get("handle")
    }

    all_products_payload = _shopify_json("/collections/all/products.json?limit=250")
    all_products = all_products_payload.get("products")
    if not isinstance(all_products, list):
        raise ValueError("Shopify all-products response did not contain a products list.")

    products_by_id: dict[str, dict[str, Any]] = {}
    for raw_product in all_products:
        if not isinstance(raw_product, dict):
            continue
        product = _product_record(raw_product)
        if product is not None and product["available"]:
            products_by_id[product["id"]] = product

    categories: list[dict[str, Any]] = []
    for display_name, handle in CATEGORY_SPECS:
        collection = collection_by_handle.get(handle, {})
        category = {
            "name": display_name,
            "handle": handle,
            "description": _text(str(collection.get("description") or "")),
            "ingredients": "",
            "url": f"{BASE_URL}/collections/{handle}",
            "count": 0,
        }
        category["ingredients"] = _ingredients(
            str(collection.get("description") or ""),
            category["description"],
        )
        categories.append(category)

        product_payload = _shopify_json(
            f"/collections/{handle}/products.json?limit=250"
        )
        collection_products = product_payload.get("products")
        if not isinstance(collection_products, list):
            raise ValueError(
                f"Shopify product response for '{handle}' did not contain a products list."
            )

        for raw_product in collection_products:
            if not isinstance(raw_product, dict):
                continue
            product = _product_record(raw_product)
            if product is None:
                continue
            product_id = product["id"]
            saved_product = products_by_id.get(product_id)
            if saved_product is None:
                continue
            if handle not in saved_product["category_handles"]:
                saved_product["category_handles"].append(handle)
                saved_product["categories"].append(display_name)

    category_order = {
        handle: index for index, (_, handle) in enumerate(CATEGORY_SPECS)
    }
    products = sorted(
        products_by_id.values(),
        key=lambda product: (
            min(
                (
                    category_order[handle]
                    for handle in product["category_handles"]
                ),
                default=len(CATEGORY_SPECS),
            ),
            product["name"].casefold(),
        ),
    )
    if not products:
        raise ValueError("The store returned no products; the existing catalog was not changed.")

    category_counts = {
        category["handle"]: 0 for category in categories
    }
    for product in products:
        for handle in product["category_handles"]:
            category_counts[handle] += 1
    for category in categories:
        category["count"] = category_counts[category["handle"]]

    return {
        "source_url": BASE_URL,
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "brand": {
            "name": "Bare Artisan Products",
            "logo_url": logo,
            "story": story,
        },
        "categories": categories,
        "products": products,
    }


def save_catalog(catalog: dict[str, Any], path: Path = DATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(catalog, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    catalog = scrape_catalog()
    save_catalog(catalog)
    print(
        f"Saved {len(catalog['products'])} products in "
        f"{sum(category['count'] > 0 for category in catalog['categories'])} "
        f"categories to {DATA_PATH}"
    )


if __name__ == "__main__":
    main()
