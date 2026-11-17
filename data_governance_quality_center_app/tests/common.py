from odoo.tests.common import TransactionCase


class DGQTestCase(TransactionCase):
    """Base class of the data quality center tests.

    Requirements: NFR-DGQ-0008
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
