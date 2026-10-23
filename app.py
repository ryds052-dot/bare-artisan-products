import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
import stripe
from flask import Flask, jsonify, render_template, request
from stripe import StripeError

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "data" / "catalog.json"
FREE_SHIPPING_CENTS = 3000
MAX_QUANTITY_PER_PRODUCT = 10
PHONE_NUMBER = "18762874304"
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
PAYPAL_ENVIRONMENT = os.environ.get("PAYPAL_ENVIRONMENT", "sandbox").strip().lower()

if PAYPAL_ENVIRONMENT not in {"sandbox", "live"}:
    raise RuntimeError("PAYPAL_ENVIRONMENT must be either 'sandbox' or 'live'.")

PAYPAL_API_BASE = (
    "https://api-m.paypal.com"
    if PAYPAL_ENVIRONMENT == "live"
    else "https://api-m.sandbox.paypal.com"
)

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

# These are the nine storefront SKUs and prices supplied for this build. Payment
# providers always receive these server-side values, never prices from the browser.
PRODUCT_SPEC = (
    ("bare-dream-cream", "Bare Dream Cream", 3000, "butters"),
    ("bare-rosemary-body-butter", "Bare Rosemary Body Butter", 3000, "butters"),
    ("bare-facial-serum", "7-Essential Oil Facial Serum", 2800, "serums"),
    ("bare-under-eye-serum", "Bare Under-Eye Renewal Serum", 2400, "serums"),
    ("bare-citrus-blast-soap", "Bare Citrus Blast Soap", 1200, "soaps"),
    ("bare-natural-deodorant", "Bare Aluminum-Free Deodorant", 1600, "soaps"),
    ("bare-neem-infused-in-coconut-oil", "Organic Neem Infused Coconut Oil", 2200, "oils"),
    ("bare-aloe-mist", "Bare Aloe Soothing Mist", 1800, "oils"),
    ("bare-insect-repellent", "Neem & Lemongrass Repellent", 2000, "oils"),
)

CATEGORY_SPEC = (
    ("Body Butters", "butters"),
    ("Serums & Facial", "serums"),
    ("Soaps & Body", "soaps"),
    ("Oils & Mists", "oils"),
)

INGREDIENT_OVERRIDES = {
    "bare-citrus-blast-soap": (
        "Citrus fruit, zinc, vitamins C and E, and alpha hydroxy acids"
    ),
    "bare-natural-deodorant": (
        "Baking soda, kaolin clay, basil, and ylang ylang essential oils"
    ),
    "bare-neem-infused-in-coconut-oil": "Neem infused in coconut oil",
    "bare-aloe-mist": "Infused aloe vera, witch hazel, and glycerin",
    "bare-insect-repellent": (
        "Neem, eucalyptus, peppermint, lemongrass, and clove oils"
    ),
}

_paypal_session = requests.Session()
_paypal_session.headers.update({"Accept": "application/json"})


class InvalidCart(ValueError):
    pass


def _load_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        source_catalog = json.load(catalog_file)

    source_products = {
        product["handle"]: product
        for product in source_catalog.get("products", [])
        if isinstance(product, dict) and product.get("handle")
    }
    source_categories = {
        category["handle"]: category
        for category in source_catalog.get("categories", [])
        if isinstance(category, dict) and category.get("handle")
    }

    catalog_products = []
    for handle, name, price_cents, category_key in PRODUCT_SPEC:
        source = source_products.get(handle)
        if source is None:
            raise RuntimeError(
                f"Product '{handle}' is missing from data/catalog.json. "
                "Run scripts/scrape_catalog.py."
            )

        source_category_handles = {
            "butters": ("body-butters",),
            "serums": ("facial-serum", "eye-serum"),
            "soaps": ("soaps", "deodorant"),
            "oils": ("infused-in-coconut-oil", "mist", "insect-repellent"),
        }[category_key]
        source_ingredients = source.get("ingredients", "")
        if not source_ingredients and category_key == "butters":
            source_ingredients = source_categories.get("body-butters", {}).get(
                "ingredients", ""
            )
        if not source_ingredients and handle == "bare-facial-serum":
            source_ingredients = source_categories.get("facial-serum", {}).get(
                "ingredients", ""
            )
        if not source_ingredients and handle == "bare-under-eye-serum":
            source_ingredients = source_categories.get("eye-serum", {}).get(
                "ingredients", ""
            )
        ingredients = INGREDIENT_OVERRIDES.get(handle, source_ingredients)

        catalog_products.append(
            {
                **source,
                "sku": handle,
                "handle": handle,
                "name": name,
                "price_cents": price_cents,
                "price": price_cents / 100,
                "price_label": f"${price_cents / 100:.2f} USD",
                "ingredients": ingredients,
                "category_key": category_key,
                "category_label": next(
                    name for name, key in CATEGORY_SPEC if key == category_key
                ),
                "category_handles": list(source_category_handles),
                "available": bool(source.get("available", True)),
            }
        )

    categories = [
        {
            "key": key,
            "name": name,
            "count": sum(
                product["category_key"] == key for product in catalog_products
            ),
        }
        for name, key in CATEGORY_SPEC
    ]
    return {
        "source_url": source_catalog.get("source_url", "https://bareartisanproducts.com"),
        "scraped_at": source_catalog.get("scraped_at", ""),
        "brand": source_catalog.get("brand", {}),
        "categories": categories,
        "products": catalog_products,
    }


def _provider_configuration() -> dict[str, Any]:
    paypal_enabled = bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)
    stripe_enabled = bool(STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY)
    return {
        "paypal_enabled": paypal_enabled,
        "paypal_client_id": PAYPAL_CLIENT_ID if paypal_enabled else "",
        "paypal_environment": PAYPAL_ENVIRONMENT,
        "stripe_enabled": stripe_enabled,
        "stripe_publishable_key": (
            STRIPE_PUBLISHABLE_KEY if stripe_enabled else ""
        ),
    }


def _parse_cart(payload: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise InvalidCart("Provide cart items as a JSON array.")
    if not payload["items"] or len(payload["items"]) > len(PRODUCT_SPEC):
        raise InvalidCart("The cart must contain between 1 and 9 distinct products.")

    product_by_sku = {product["sku"]: product for product in catalog["products"]}
    normalized_items = []
    seen_skus = set()
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise InvalidCart("Each cart item must include a product SKU and quantity.")
        sku = item.get("sku")
        quantity = item.get("quantity")
        if not isinstance(sku, str) or sku not in product_by_sku:
            raise InvalidCart("The cart contains an unknown product.")
        if sku in seen_skus:
            raise InvalidCart("Each product may appear only once in the cart.")
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or not 1 <= quantity <= MAX_QUANTITY_PER_PRODUCT
        ):
            raise InvalidCart(
                f"Quantity for {product_by_sku[sku]['name']} must be between 1 and "
                f"{MAX_QUANTITY_PER_PRODUCT}."
            )

        product = product_by_sku[sku]
        if not product["available"]:
            raise InvalidCart(f"{product['name']} is currently unavailable.")
        normalized_items.append({**product, "quantity": quantity})
        seen_skus.add(sku)
    return normalized_items


def _cart_total_cents(items: list[dict[str, Any]]) -> int:
    return sum(item["price_cents"] * item["quantity"] for item in items)


def _log_order_event(provider: str, event: str, order_id: str, amount: int) -> None:
    app.logger.info(
        "order_event %s",
        json.dumps(
            {
                "provider": provider,
                "event": event,
                "order_id": order_id,
                "amount_cents": amount,
                "currency": "USD",
            },
            separators=(",", ":"),
        ),
    )


def _require_same_origin_json() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    if not request.is_json:
        return None, (jsonify(error="Send checkout data as application/json."), 415)

    origin = request.headers.get("Origin")
    if origin:
        if urlsplit(origin).netloc.casefold() != request.host.casefold():
            return None, (jsonify(error="Cross-origin checkout requests are not allowed."), 403)

    payload = request.get_json()
    if not isinstance(payload, dict):
        return None, (jsonify(error="Checkout data must be a JSON object."), 400)
    return payload, None


def _paypal_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    token_response = _paypal_session.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        headers={"Accept": "application/json"},
        data={"grant_type": "client_credentials"},
        timeout=(5, 20),
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise ValueError("PayPal did not return an access token.")

    response = _paypal_session.request(
        method,
        f"{PAYPAL_API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=(5, 20),
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("PayPal returned an invalid response.")
    return result


@app.get("/")
def home():
    catalog = _load_catalog()
    featured_product = next(
        product
        for product in catalog["products"]
        if product["sku"] == "bare-dream-cream"
    )
    return render_template(
        "index.html",
        catalog=catalog,
        featured_product=featured_product,
        payments=_provider_configuration(),
    )


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/api/paypal/orders")
def create_paypal_order():
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        return jsonify(error="PayPal checkout is not configured."), 503
    payload, error_response = _require_same_origin_json()
    if error_response:
        return error_response
    try:
        items = _parse_cart(payload, _load_catalog())
    except InvalidCart as error:
        return jsonify(error=str(error)), 400

    total_cents = _cart_total_cents(items)
    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": "USD",
                    "value": f"{Decimal(total_cents) / 100:.2f}",
                },
                "items": [
                    {
                        "name": item["name"][:127],
                        "quantity": str(item["quantity"]),
                        "unit_amount": {
                            "currency_code": "USD",
                            "value": f"{item['price_cents'] / 100:.2f}",
                        },
                    }
                    for item in items
                ],
            }
        ],
    }
    try:
        result = _paypal_request("POST", "/v2/checkout/orders", order_payload)
    except (requests.RequestException, ValueError):
        app.logger.exception("PayPal order creation failed.")
        return jsonify(error="PayPal could not create the order. Please try again."), 502

    order_id = result.get("id")
    if not isinstance(order_id, str) or result.get("status") != "CREATED":
        app.logger.error("PayPal returned an unexpected order-creation response.")
        return jsonify(error="PayPal returned an invalid order response."), 502
    _log_order_event("paypal", "created", order_id, total_cents)
    return jsonify(id=order_id)


@app.post("/api/paypal/orders/<order_id>/capture")
def capture_paypal_order(order_id: str):
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        return jsonify(error="PayPal checkout is not configured."), 503
    _, error_response = _require_same_origin_json()
    if error_response:
        return error_response
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", order_id):
        return jsonify(error="Invalid PayPal order ID."), 400
    try:
        result = _paypal_request(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            {},
        )
    except (requests.RequestException, ValueError):
        app.logger.exception("PayPal order capture failed.")
        return jsonify(error="PayPal could not capture the order. Please try again."), 502

    if result.get("status") != "COMPLETED":
        return jsonify(error="PayPal has not completed this payment."), 409
    units = result.get("purchase_units") or []
    captures = (units[0].get("payments") or {}).get("captures") if units else []
    capture = captures[0] if captures else {}
    if not capture or capture.get("status") != "COMPLETED":
        return jsonify(error="PayPal has not completed this payment."), 409
    try:
        amount_cents = int(
            Decimal(capture["amount"]["value"]) * 100
        )
    except (KeyError, InvalidOperation, TypeError, ValueError):
        app.logger.error("PayPal returned a completed capture without a valid amount.")
        return jsonify(error="PayPal returned an invalid capture response."), 502

    capture_id = str(capture.get("id") or order_id)
    _log_order_event("paypal", "captured", capture_id, amount_cents)
    return jsonify(status="COMPLETED", capture_id=capture_id)


@app.post("/api/stripe/payment-intents")
def create_stripe_payment_intent():
    payload, error_response = _require_same_origin_json()
    if error_response:
        return error_response
    if not STRIPE_SECRET_KEY or not STRIPE_PUBLISHABLE_KEY:
        return jsonify(error="Stripe checkout is not configured."), 503
    try:
        items = _parse_cart(payload, _load_catalog())
    except InvalidCart as error:
        return jsonify(error=str(error)), 400

    total_cents = _cart_total_cents(items)
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        intent = stripe.PaymentIntent.create(
            amount=total_cents,
            currency="usd",
            automatic_payment_methods={"enabled": True},
            metadata={
                f"item_{index}": f"{item['sku']}:{item['quantity']}"
                for index, item in enumerate(items, start=1)
            },
        )
    except StripeError:
        app.logger.exception("Stripe payment intent creation failed.")
        return jsonify(error="Stripe could not start checkout. Please try again."), 502

    client_secret = getattr(intent, "client_secret", None)
    intent_id = getattr(intent, "id", None)
    if not client_secret or not intent_id:
        app.logger.error("Stripe returned an incomplete payment intent.")
        return jsonify(error="Stripe returned an invalid payment response."), 502
    _log_order_event("stripe", "payment_intent_created", intent_id, total_cents)
    return jsonify(client_secret=client_secret, payment_intent_id=intent_id)


@app.post("/api/stripe/webhook")
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify(error="Stripe webhooks are not configured."), 503
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            request.get_data(),
            signature,
            STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, StripeError):
        return jsonify(error="Invalid Stripe webhook signature or payload."), 400

    if event["type"] == "payment_intent.succeeded":
        payment_intent = event["data"]["object"]
        _log_order_event(
            "stripe",
            "payment_succeeded",
            str(payment_intent["id"]),
            int(payment_intent["amount_received"]),
        )
    return jsonify(received=True)
