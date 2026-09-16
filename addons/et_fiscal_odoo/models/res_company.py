"""Seller fiscal identity.

The TIN is a property of the legal entity, so it belongs on res.company and
res.partner - not in a fiscal module's own configuration table. This is a
plain Odoo field addition, which is the vanilla-first answer.
"""

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    et_fiscal_tin = fields.Char(
        string="Taxpayer Identification Number (TIN)",
        help="Ethiopian TIN of this legal entity. Sent as the seller identity "
        "on every fiscal document.",
    )
    et_fiscal_branch_code = fields.Char(
        string="Fiscal Branch Code",
        help="Optional branch/outlet identifier required by some fiscal providers.",
    )


class ResPartner(models.Model):
    _inherit = "res.partner"

    et_fiscal_tin = fields.Char(
        string="TIN",
        help="Ethiopian Taxpayer Identification Number. Required on B2B "
        "documents where the buyer must be identified.",
    )
