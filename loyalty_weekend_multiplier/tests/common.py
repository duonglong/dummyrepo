from odoo.tests.common import TransactionCase


class LoyaltyWeekendCommon(TransactionCase):
    """Shared fixtures of the loyalty_weekend_multiplier tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Weekend Customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Weekend Product',
            'list_price': 100.0,
        })

    @classmethod
    def _create_loyalty_program(cls, **vals):
        """Create a Loyalty Cards program; ``vals`` override the defaults."""
        return cls.env['loyalty.program'].create({
            'name': 'Weekend Loyalty',
            'program_type': 'loyalty',
            **vals,
        })
