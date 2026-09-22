"""The Mati demo dataset.

Every number the demo script quotes is defined here, once. Keeping the data
separate from the seeding logic means the demo can be re-pointed at a
different catalogue (or at output from the spreadsheet normalizer) without
touching any of the code that talks to Odoo.

Mati's confirmed operating model, encoded below:

    ONE legal company, ONE TIN, TWO operational shops
    Factory + Model  -> product.template
    Color   + Size   -> product.product
    Retail / Wholesale -> two pricelists, same product identity
    Quantity         -> stock at a shop, not product master data
    Supplier         -> delivers directly to a shop (no central warehouse)
"""

# ---------------------------------------------------------------------------
# Legal entity - one company, one TIN, two operational shops.
# ---------------------------------------------------------------------------
COMPANY_NAME = "Mati's Shoes"
COMPANY_TIN = "0012345678"

SUPPLIER_NAME = "ABC Footwear Factory"
WHOLESALE_CUSTOMER = "Bole Shoe Traders"

# ---------------------------------------------------------------------------
# Shops. There is NO central warehouse: stock arrives from the supplier
# straight into the shop that ordered it.
# ---------------------------------------------------------------------------
SHOPS = [
    {"code": "SHOP1", "name": "Shop 1", "default_pricelist": "retail"},
    {"code": "SHOP2", "name": "Shop 2", "default_pricelist": "wholesale"},
]

# ---------------------------------------------------------------------------
# Catalogue. Factory + Model is the template; Color + Size are the variants.
#
# All variants of one template share the same retail and wholesale price -
# Mati prices by model, not by size.
# ---------------------------------------------------------------------------
PRODUCTS = {
    "zala2147": {
        "factory": "Zala",
        "model": "2147",
        "sku_prefix": "ZALA-2147",
        "colors": ["Black", "White"],
        "sizes": ["39", "40"],
        "retail_price": 6000.0,
        "wholesale_price": 5200.0,
        "cost": 3400.0,
        "barcode_index": 0,
    },
    "rasdashen218": {
        "factory": "Rasdashen",
        "model": "218",
        "sku_prefix": "RASD-218",
        "colors": ["Brown"],
        "sizes": ["41", "42"],
        "retail_price": 4800.0,
        "wholesale_price": 4100.0,
        "cost": 2700.0,
        "barcode_index": 1,
    },
    "kangarooc1": {
        "factory": "Kangaroo",
        "model": "C1",
        "sku_prefix": "KANG-C1",
        "colors": ["Black"],
        "sizes": ["40", "41"],
        "retail_price": 5500.0,
        "wholesale_price": 4700.0,
        "cost": 3100.0,
        "barcode_index": 2,
    },
}

# Colour -> (SKU fragment, barcode digit). Barcode digits must stay stable:
# changing one changes a printed label.
COLOR_CODES = {
    "Black": ("BLK", 0),
    "White": ("WHT", 1),
    "Brown": ("BRN", 2),
}

ALL_COLORS = ["Black", "White", "Brown"]
ALL_SIZES = ["39", "40", "41", "42"]

# ---------------------------------------------------------------------------
# Pricing. Two contexts, one product identity.
# ---------------------------------------------------------------------------
PRICELISTS = {
    "retail": {"name": "Retail Pricelist", "sequence": 10, "field": "retail_price"},
    "wholesale": {"name": "Wholesale Pricelist", "sequence": 20, "field": "wholesale_price"},
}

# ---------------------------------------------------------------------------
# Opening stock, per shop. Quantity is stock at a location - never product
# master data. These are the numbers the demo script reads out.
#
# ZALA-2147-BLK-39 opens at 12 in Shop 1 on purpose: the purchase demo
# receives 20 more and the expected result is exactly 32.
# ---------------------------------------------------------------------------
OPENING_STOCK = {
    "ZALA-2147-BLK-39": {"SHOP1": 12, "SHOP2": 5},
    "ZALA-2147-BLK-40": {"SHOP1": 8, "SHOP2": 6},
    "ZALA-2147-WHT-39": {"SHOP1": 4, "SHOP2": 7},
    "ZALA-2147-WHT-40": {"SHOP1": 3, "SHOP2": 4},
    "RASD-218-BRN-41": {"SHOP1": 5, "SHOP2": 2},
    "RASD-218-BRN-42": {"SHOP1": 6, "SHOP2": 3},
    "KANG-C1-BLK-40": {"SHOP1": 9, "SHOP2": 2},
    "KANG-C1-BLK-41": {"SHOP1": 7, "SHOP2": 4},
}

# ---------------------------------------------------------------------------
# The deterministic purchase scenario (demo part D).
# ---------------------------------------------------------------------------
DEMO_PURCHASE = {
    "sku": "ZALA-2147-BLK-39",
    "shop": "SHOP1",
    "quantity": 20,
    "opening": 12,
    "after_receipt": 32,
}

# POS: one till per shop. Both tills can reach both pricelists so a wholesale
# sale can be rung up at Shop 1 without duplicating any product; each simply
# defaults to the pricelist that shop normally uses.
POS_CONFIGS = [
    {"name": "Shop 1 POS", "shop": "SHOP1", "default_pricelist": "retail"},
    {"name": "Shop 2 POS", "shop": "SHOP2", "default_pricelist": "wholesale"},
]


def ean13_check_digit(base12: str) -> str:
    """Standard EAN-13 check digit.

    Mati generates real barcodes in external label software; Odoo only has to
    store the same value against the right variant. The demo values are still
    valid EAN-13 so that a real scanner reads them during the walkthrough.
    """
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(base12))
    return str((10 - total % 10) % 10)


def build_barcode(product_index: int, color_index: int, size: str) -> str:
    """Deterministic, valid EAN-13 for one variant.

    Layout: 200 | product(2) | colour(1) | size(2) | filler(4) | check(1)
    The 200 prefix is the range reserved for in-store use.
    """
    base12 = f"200{product_index:02d}{color_index:01d}{int(size):02d}0000"
    return base12 + ean13_check_digit(base12)


def build_sku(spec: dict, color: str, size: str) -> str:
    """e.g. ZALA-2147-BLK-39."""
    color_code = COLOR_CODES[color][0]
    return f"{spec['sku_prefix']}-{color_code}-{size}"


def iter_variants():
    """(product key, spec, colour, size, sku, barcode) for every demo variant."""
    for key, spec in PRODUCTS.items():
        for color in spec["colors"]:
            for size in spec["sizes"]:
                yield (
                    key,
                    spec,
                    color,
                    size,
                    build_sku(spec, color, size),
                    build_barcode(spec["barcode_index"], COLOR_CODES[color][1], size),
                )
