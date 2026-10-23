import json
from pathlib import Path

from flask import Flask, render_template

app = Flask(__name__)
CATALOG_PATH = Path(__file__).resolve().parent / "data" / "catalog.json"


@app.get("/")
def home():
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        catalog = json.load(catalog_file)

    products = catalog["products"]
    if not products:
        raise RuntimeError("The catalog snapshot is empty. Run scripts/scrape_catalog.py.")
    featured_product = next(
        (
            product
            for product in products
            if product["handle"] == "bare-dream-cream"
        ),
        products[0],
    )
    return render_template(
        "index.html",
        catalog=catalog,
        featured_product=featured_product,
    )
