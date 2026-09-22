"""Normalize Mati's inventory spreadsheet into the platform's domain model.

Mati keeps one flat sheet: a row per shoe, with a quantity column. That sheet
is a *report*, not a domain model, and importing it literally would produce
one product per row and a quantity field on the product - both wrong.

This script does the translation:

    flat spreadsheet row
            |
            v
    factory + model   ->  product template   (one per Factory + Model)
    colour  + size    ->  product variant    (one per combination)
    retail_price      ->  Retail Pricelist   (per template, not per variant)
    wholesale_price   ->  Wholesale Pricelist
    shop + quantity   ->  opening stock at that shop's location

Deliberately small and dependency-free: CSV in, JSON out. The JSON is then
fed to Odoo by ``mati.demo.setup``, so the mapping rules live in exactly one
place and can be reviewed without running anything.

Usage
-----
    python tools/normalize_mati_inventory.py inventory.csv -o normalized.json
    python tools/normalize_mati_inventory.py inventory.csv --report

Expected columns (case-insensitive, extra columns ignored):

    factory, model, color, size, shop, quantity
    category, sku, barcode, cost, retail_price, wholesale_price   [optional]

**Shop is mandatory.** Mati's quantity means "stock at a shop"; a row without
a shop is ambiguous and the script refuses to guess which one it meant.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import OrderedDict
from pathlib import Path

REQUIRED_COLUMNS = ("factory", "model", "color", "size", "shop", "quantity")
OPTIONAL_COLUMNS = (
    "category",
    "sku",
    "barcode",
    "cost",
    "retail_price",
    "wholesale_price",
)

COLOR_CODES = {"black": "BLK", "white": "WHT", "brown": "BRN", "blue": "BLU", "red": "RED"}


class NormalizationError(Exception):
    """The source data cannot be normalized without guessing."""


def _slug(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def color_code(color: str) -> str:
    return COLOR_CODES.get((color or "").strip().lower(), _slug(color)[:3] or "XXX")


def build_sku(factory: str, model: str, color: str, size: str) -> str:
    return f"{_slug(factory)[:4]}-{_slug(model)}-{color_code(color)}-{(size or '').strip()}"


def ean13_check_digit(base12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return str((10 - total % 10) % 10)


def _decimal(value, field, row_number):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise NormalizationError(f"row {row_number}: {field} is not a number: {value!r}") from exc


def normalize_rows(rows) -> dict:
    """Turn flat rows into templates, variants, prices and opening stock."""
    templates: "OrderedDict[str, dict]" = OrderedDict()
    problems: list[str] = []
    seen_barcodes: dict[str, str] = {}

    for row_number, raw in enumerate(rows, start=2):  # row 1 is the header
        row = { (k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
                for k, v in raw.items() }

        missing = [c for c in REQUIRED_COLUMNS if not row.get(c)]
        if missing:
            problems.append(f"row {row_number}: missing {', '.join(missing)}")
            continue

        factory, model = row["factory"], row["model"]
        color, size, shop = row["color"], row["size"], row["shop"]
        key = f"{factory}|{model}"

        try:
            quantity = _decimal(row["quantity"], "quantity", row_number)
            retail = _decimal(row.get("retail_price"), "retail_price", row_number)
            wholesale = _decimal(row.get("wholesale_price"), "wholesale_price", row_number)
            cost = _decimal(row.get("cost"), "cost", row_number)
        except NormalizationError as exc:
            problems.append(str(exc))
            continue

        template = templates.setdefault(
            key,
            {
                "factory": factory,
                "model": model,
                "name": f"{factory} {model}",
                "category": row.get("category") or "All",
                "retail_price": None,
                "wholesale_price": None,
                "cost": None,
                "colors": [],
                "sizes": [],
                "variants": OrderedDict(),
            },
        )

        # Price belongs to the model, not the variant. Disagreements inside one
        # model are reported rather than silently resolved - Mati's rule is one
        # price per Factory + Model.
        for field, value in (
            ("retail_price", retail),
            ("wholesale_price", wholesale),
            ("cost", cost),
        ):
            if value is None:
                continue
            if template[field] is None:
                template[field] = value
            elif abs(template[field] - value) > 0.009:
                problems.append(
                    f"row {row_number}: {factory} {model} has conflicting {field} "
                    f"({template[field]} vs {value}); price is per model, not per variant"
                )

        if color not in template["colors"]:
            template["colors"].append(color)
        if size not in template["sizes"]:
            template["sizes"].append(size)

        sku = row.get("sku") or build_sku(factory, model, color, size)
        barcode = row.get("barcode") or ""
        if barcode:
            owner = seen_barcodes.setdefault(barcode, sku)
            if owner != sku:
                problems.append(
                    f"row {row_number}: barcode {barcode} is already used by {owner}"
                )

        variant = template["variants"].setdefault(
            sku,
            {"sku": sku, "color": color, "size": size, "barcode": barcode, "opening_stock": {}},
        )
        if barcode and not variant["barcode"]:
            variant["barcode"] = barcode

        shop_key = shop.strip().upper().replace(" ", "")
        if shop_key in variant["opening_stock"]:
            problems.append(
                f"row {row_number}: {sku} appears twice for shop {shop}; "
                "quantities must be stated once per shop"
            )
            continue
        variant["opening_stock"][shop_key] = quantity

    result = {
        "templates": [
            {**template, "variants": list(template["variants"].values())}
            for template in templates.values()
        ],
        "problems": problems,
    }
    result["summary"] = {
        "templates": len(result["templates"]),
        "variants": sum(len(t["variants"]) for t in result["templates"]),
        "shops": sorted(
            {
                shop
                for t in result["templates"]
                for v in t["variants"]
                for shop in v["opening_stock"]
            }
        ),
        "problems": len(problems),
    }
    return result


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def print_report(result: dict) -> None:
    summary = result["summary"]
    print(f"templates : {summary['templates']}")
    print(f"variants  : {summary['variants']}")
    print(f"shops     : {', '.join(summary['shops']) or '-'}")
    print("")
    for template in result["templates"]:
        prices = f"retail {template['retail_price']}, wholesale {template['wholesale_price']}"
        print(f"  {template['name']}  ({prices})")
        for variant in template["variants"]:
            stock = "  ".join(f"{s}={int(q)}" for s, q in sorted(variant["opening_stock"].items()))
            print(f"    {variant['sku']:<24} {variant['barcode'] or '(no barcode)':<15} {stock}")
    if result["problems"]:
        print("")
        print(f"PROBLEMS ({len(result['problems'])}) - nothing is guessed, fix the source:")
        for problem in result["problems"]:
            print(f"  - {problem}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path, help="flat inventory CSV")
    parser.add_argument("-o", "--output", type=Path, help="write normalized JSON here")
    parser.add_argument("--report", action="store_true", help="print a human-readable summary")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any row could not be normalized",
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"no such file: {args.source}", file=sys.stderr)
        return 2

    result = normalize_rows(load_csv(args.source))

    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"wrote {args.output}")
    if args.report or not args.output:
        print_report(result)

    if result["problems"] and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
