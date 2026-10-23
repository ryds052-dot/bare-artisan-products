import json
import tempfile
import unittest
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
        product = scrape_catalog._product_record(self.product)
        product["categories"] = ["Body Butters"]
        product["category_handles"] = ["body-butters"]
        product["product_url"] = "https://bareartisanproducts.com/products/island-butter"
        catalog = {
            "brand": {
                "logo_url": "https://cdn.shopify.com/bare-logo.jpg",
                "story": "Made with care in Jamaica.",
            },
            "categories": [
                {
                    "name": "Body Butters",
                    "handle": "body-butters",
                    "count": 1,
                }
            ],
            "products": [product],
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with patch.object(flask_app, "CATALOG_PATH", catalog_path):
                response = flask_app.app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        self.assertEqual(len(page.select(".catalog-item")), 1)
        self.assertEqual(page.select_one(".catalog-item h3").get_text(strip=True), "Island Butter")
        self.assertIn("Body Butters", page.select(".cat-btn")[1].get_text())
        self.assertIn("Shea butter and coconut oil", page.get_text())
        self.assertIn("Made with care in Jamaica.", page.get_text())


if __name__ == "__main__":
    unittest.main()
