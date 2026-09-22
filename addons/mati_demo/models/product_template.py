"""Factory and Model as product master data.

Mati's confirmed catalogue rule:

    Factory + Model  ->  product.template   ("Zala 2147")
    Color   + Size   ->  product.product    ("Zala 2147 / Black / 39")

Factory is deliberately NOT a product attribute. Making it one would generate
a variant per factory, which is wrong: a shoe is made by exactly one factory,
so the factory identifies the model rather than varying it. It is master data
on the template, the same way a brand is.

These two fields are the only catalogue extension in this module. They sit
here rather than in a reusable addon because "factory" and "model" are
footwear-retail vocabulary, not integration infrastructure. If a second shoe
retailer arrives they lift out into a small shared module unchanged - the
field names carry no Mati-specific prefix for exactly that reason.
"""

from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    shoe_factory = fields.Char(
        string="Factory",
        index=True,
        help="Manufacturer of this shoe model, e.g. Zala. Master data on the "
        "product, not a variant dimension.",
    )
    shoe_model = fields.Char(
        string="Model",
        index=True,
        help="Factory's model reference, e.g. 2147. Combined with the factory "
        "this identifies the product template.",
    )
    shoe_reference = fields.Char(
        string="Factory / Model",
        compute="_compute_shoe_reference",
        store=True,
        help="Factory and model together, as the business says it out loud.",
    )

    @api.depends("shoe_factory", "shoe_model")
    def _compute_shoe_reference(self):
        for template in self:
            parts = [template.shoe_factory or "", template.shoe_model or ""]
            template.shoe_reference = " ".join(part for part in parts if part) or False
