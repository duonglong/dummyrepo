from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestTolerance(TransactionCase):
    """Unit tests of the company tolerances.

    Requirements: FR-IPR-0007, FR-IPR-0008, NFR-IPR-0002
    The list is editable in place (no dialog), so the model is driven directly.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'IPR Tolerance Co'})
        cls.admin = cls._user('ipr_tol_admin', 'account.group_account_manager')
        cls.pmanager = cls._user('ipr_tol_pm', 'purchase.group_purchase_manager')

    @classmethod
    def _user(cls, login, group_xmlid):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id, cls.env.ref(group_xmlid).id])],
        })

    def _create(self, **vals):
        return self.env['ipr.tolerance'].with_user(self.admin).create(
            dict(vals, company_id=self.company.id))

    def test_negative_message(self):
        """FR-IPR-0008: the refusal reads that a tolerance cannot be negative."""
        with self.assertRaisesRegex(ValidationError, 'A tolerance cannot be negative.'):
            self._create(price_tolerance_percent=-1)

    def test_just_below_zero_refused_on_each_field(self):
        """FR-IPR-0008: each of the amount and quantity values refuses a value below zero."""
        for field, value in (
            ('price_tolerance_amount', -0.01), ('qty_tolerance_value', -0.01)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self._create(**{field: value})

    def test_default_zero_and_types(self):
        """FR-IPR-0007: no record means zero tolerances typed as percent."""
        data = self.env['ipr.tolerance']._ipr_for_company(self.company)
        self.assertEqual(data['price_percent'], 0.0)
        self.assertEqual(data['price_amount'], 0.0)
        self.assertEqual(data['qty_value'], 0.0)
        self.assertEqual((data['price_type'], data['qty_type']), ('percent', 'percent'))
        self.assertEqual(data['currency'], self.company.currency_id)

    @mute_logger('odoo.sql_db')
    def test_unique_per_company(self):
        """FR-IPR-0007: a second record for a company is refused."""
        self._create()
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self._create()

    def test_percent_above_100_accepted(self):
        """FR-IPR-0008: a percentage above 100 is allowed."""
        self.assertEqual(self._create(price_tolerance_percent=150).price_tolerance_percent, 150)

    def test_purchase_manager_reads_only(self):
        """FR-IPR-0007 / NFR-IPR-0002: a purchase manager reads but cannot write or create."""
        rec = self._create()
        as_pm = rec.with_user(self.pmanager)
        as_pm.read(['price_tolerance_percent'])
        with self.assertRaises(AccessError):
            as_pm.write({'price_tolerance_percent': 1})
        with self.assertRaises(AccessError):
            self.env['ipr.tolerance'].with_user(self.pmanager).create({'company_id': self.company.id})

    def test_company_rule(self):
        """NFR-IPR-0002: the administrator of one company does not see another company's record."""
        other = self.env['res.company'].create({'name': 'IPR Other Co'})
        rec = self.env['ipr.tolerance'].create({'company_id': other.id})
        self.assertNotIn(rec, self.env['ipr.tolerance'].with_user(self.admin).search([]))

    def test_menus_open_for_each_role(self):
        """FR-IPR-0007: the Bill Reconciliation menus of each role open the list without an access error.

        The menu clicks themselves are left to the screen review.
        """
        menus = self.env['ir.ui.menu'].sudo().search([('name', '=', 'Bill Reconciliation')])
        self.assertEqual(len(menus), 2)
        for user in (self.admin, self.pmanager):
            visible = menus.with_user(user).search([('name', '=', 'Bill Reconciliation')])
            self.assertTrue(visible)
            for menu in visible:
                self.env[menu.sudo().action.res_model].with_user(user).search([])
