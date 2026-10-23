import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

import app as flask_app
from scripts import scrape_catalog


class CatalogScraperTests(unittest.TestCase):
    def setUp(self):
        self.product = {
            "id": 123,
            "handle": "island-butter",
            "title": "Island Butter",
            "body_html": (
                "<p>A rich botanical body butter.</p>"
                "<p><strong>Ingredients:</strong> Shea butter and coconut oil.</p>"
            ),
            "variants": [
                {"price": "20.00", "available": True},
                {"price": "30.00", "available": False},
            ],
            "images": [
                {"src": "//cdn.shopify.com/island-butter.jpg", "alt": "Island Butter"}
            ],
        }

    def test_product_record_extracts_store_details_and_available_price(self):
        product = scrape_catalog._product_record(self.product)

        self.assertIsNotNone(product)
        self.assertEqual(product["name"], "Island Butter")
        self.assertEqual(product["description"], "A rich botanical body butter. Ingredients: Shea butter and coconut oil.")
        self.assertEqual(product["ingredients"], "Shea butter and coconut oil")
        self.assertEqual(product["price"], 20.0)
        self.assertEqual(product["price_label"], "$20.00–$30.00 USD")
        self.assertTrue(product["available"])
        self.assertEqual(product["image_url"], "https://cdn.shopify.com/island-butter.jpg")

    def test_scraper_merges_products_across_requested_collections(self):
        homepage = """
            <html><head>
                <meta property="og:image:secure_url" content="//cdn.shopify.com/bare-logo.jpg">
            </head><body><section><h2>ABOUT US</h2>
                <p>Bare Artisan Products creates thoughtful botanical skincare,
                hand\ufffdcrafted with care in Jamaica for everyday rituals and family.</p>
            </section></body></html>
        """

        def collection_response(path):
            if path == "/collections.json?limit=250":
                return {
                    "collections": [
                        {
                            "handle": "body-butters",
                                "description": (
                                    "<p>Made with care.</p><p><strong>Ingredients:</strong> "
                                    "Shea butter and coconut oil.</p>"
                                ),
                        },
                        {"handle": "beard-oils", "description": ""},
                    ]
                }
            if path == "/collections/body-butters/products.json?limit=250":
                return {"products": [self.product]}
            if path == "/collections/beard-oils/products.json?limit=250":
                return {"products": [self.product]}
            return {"products": []}

        with (
            patch.object(
                scrape_catalog,
                "_get",
                return_value=type(
                    "Response",
                    (),
                    {"content": homepage.encode("utf-8")},
                )(),
            ),
            patch.object(
                scrape_catalog,
                "_shopify_json",
                side_effect=collection_response,
            ),
        ):
            catalog = scrape_catalog.scrape_catalog()

        self.assertEqual(len(catalog["products"]), 1)
        self.assertEqual(
            catalog["products"][0]["category_handles"],
            ["body-butters", "beard-oils"],
        )
        self.assertEqual(catalog["categories"][0]["count"], 1)
        self.assertEqual(
            catalog["categories"][0]["ingredients"],
            "Shea butter and coconut oil",
        )
        self.assertEqual(catalog["categories"][1]["count"], 1)
        self.assertEqual(
            catalog["brand"]["logo_url"],
            "https://cdn.shopify.com/bare-logo.jpg",
        )
        self.assertIn("Jamaica", catalog["brand"]["story"])
        self.assertNotIn("\ufffd", catalog["brand"]["story"])

    def test_catalog_save_is_readable_json(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalog.json"
            scrape_catalog.save_catalog({"products": []}, target)

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"products": []})

    def test_flask_template_renders_the_catalog_snapshot(self):
        response = flask_app.app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        self.assertEqual(len(page.select(".product-card")), 9)
        self.assertEqual(
            page.select_one(".product-card h3").get_text(strip=True),
            "Bare Dream Cream",
        )
        self.assertEqual(len(page.select("[data-filter]")), 5)
        self.assertIn("$30.00 USD", page.get_text())
        self.assertIn("Jamaica", page.get_text())

    def test_storefront_catalog_has_the_requested_nine_products_and_prices(self):
        catalog = flask_app._load_catalog()

        self.assertEqual(len(catalog["products"]), 9)
        self.assertEqual(
            [product["name"] for product in catalog["products"]],
            [spec[1] for spec in flask_app.PRODUCT_SPEC],
        )
        self.assertEqual(
            [product["price_cents"] for product in catalog["products"]],
            [spec[2] for spec in flask_app.PRODUCT_SPEC],
        )
        self.assertEqual(
            [category["name"] for category in catalog["categories"]],
            ["Body Butters", "Serums & Facial", "Soaps & Body", "Oils & Mists"],
        )

    def test_cart_uses_server_prices_and_rejects_unknown_products(self):
        catalog = flask_app._load_catalog()
        items = flask_app._parse_cart(
            {
                "items": [
                    {
                        "sku": "bare-dream-cream",
                        "quantity": 2,
                        "price": 0.01,
                        "name": "Forged price",
                    }
                ]
            },
            catalog,
        )

        self.assertEqual(flask_app._cart_total_cents(items), 6000)
        with self.assertRaises(flask_app.InvalidCart):
            flask_app._parse_cart(
                {"items": [{"sku": "not-a-product", "quantity": 1}]},
                catalog,
            )

    def test_payment_endpoints_reject_cross_origin_and_missing_configuration(self):
        client = flask_app.app.test_client()
        payload = {"items": [{"sku": "bare-dream-cream", "quantity": 1}]}
        with patch.object(flask_app, "PAYPAL_CLIENT_ID", "configured"), patch.object(
            flask_app, "PAYPAL_CLIENT_SECRET", "configured"
        ):
            response = client.post(
                "/api/paypal/orders",
                json=payload,
                headers={"Origin": "https://attacker.invalid"},
            )
        self.assertEqual(response.status_code, 403)

        with patch.object(flask_app, "STRIPE_SECRET_KEY", ""), patch.object(
            flask_app, "STRIPE_PUBLISHABLE_KEY", ""
        ):
            response = client.post("/api/stripe/payment-intents", json=payload)
        self.assertEqual(response.status_code, 503)

    def test_stripe_intent_uses_server_calculated_amount(self):
        intent = SimpleNamespace(
            client_secret="pi_test_secret",
            id="pi_test",
        )
        payload = {
            "items": [
                {
                    "sku": "bare-dream-cream",
                    "quantity": 2,
                    "price": 0.01,
                }
            ]
        }
        with (
            patch.object(flask_app, "STRIPE_SECRET_KEY", "sk_test_configured"),
            patch.object(flask_app, "STRIPE_PUBLISHABLE_KEY", "pk_test_configured"),
            patch.object(
                flask_app.stripe.PaymentIntent,
                "create",
                return_value=intent,
            ) as create_intent,
        ):
            response = flask_app.app.test_client().post(
                "/api/stripe/payment-intents",
                json=payload,
                headers={"Origin": "http://localhost"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["client_secret"], "pi_test_secret")
        self.assertEqual(create_intent.call_args.kwargs["amount"], 6000)

    def test_paypal_order_uses_server_calculated_amount(self):
        payload = {
            "items": [
                {
                    "sku": "bare-dream-cream",
                    "quantity": 2,
                    "price": 0.01,
                }
            ]
        }
        paypal_result = {"id": "PAYPAL-ORDER-1", "status": "CREATED"}
        with (
            patch.object(flask_app, "PAYPAL_CLIENT_ID", "configured"),
            patch.object(flask_app, "PAYPAL_CLIENT_SECRET", "configured"),
            patch.object(
                flask_app,
                "_paypal_request",
                return_value=paypal_result,
            ) as create_order,
        ):
            response = flask_app.app.test_client().post(
                "/api/paypal/orders",
                json=payload,
                headers={"Origin": "http://localhost"},
            )

        self.assertEqual(response.status_code, 200)
        order = create_order.call_args.args[2]
        self.assertEqual(order["purchase_units"][0]["amount"]["value"], "60.00")

    def test_paypal_capture_requires_same_origin_and_configured_credentials(self):
        client = flask_app.app.test_client()
        with patch.object(flask_app, "PAYPAL_CLIENT_ID", ""), patch.object(
            flask_app, "PAYPAL_CLIENT_SECRET", ""
        ):
            unavailable = client.post(
                "/api/paypal/orders/PAYPAL-ORDER-1/capture",
                json={},
            )
        self.assertEqual(unavailable.status_code, 503)

        with patch.object(flask_app, "PAYPAL_CLIENT_ID", "configured"), patch.object(
            flask_app, "PAYPAL_CLIENT_SECRET", "configured"
        ):
            cross_origin = client.post(
                "/api/paypal/orders/PAYPAL-ORDER-1/capture",
                json={},
                headers={"Origin": "https://attacker.invalid"},
            )
        self.assertEqual(cross_origin.status_code, 403)

    def test_payment_sdk_clients_render_only_with_provider_configuration(self):
        with (
            patch.object(flask_app, "PAYPAL_CLIENT_ID", "paypal-client-id"),
            patch.object(flask_app, "PAYPAL_CLIENT_SECRET", "paypal-server-secret"),
            patch.object(flask_app, "STRIPE_SECRET_KEY", "stripe-server-secret"),
            patch.object(
                flask_app,
                "STRIPE_PUBLISHABLE_KEY",
                "pk_test_publishable",
            ),
        ):
            response = flask_app.app.test_client().get("/")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("www.paypal.com/sdk/js", html)
        self.assertIn("js.stripe.com/v3/", html)
        self.assertIn("paypal-client-id", html)
        self.assertIn("pk_test_publishable", html)
        self.assertNotIn("paypal-server-secret", html)
        self.assertNotIn("stripe-server-secret", html)


if __name__ == "__main__":
    unittest.main()
