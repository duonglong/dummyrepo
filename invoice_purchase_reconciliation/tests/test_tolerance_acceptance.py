from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestToleranceAcceptance(TransactionCase):
    """Acceptance tests of T-002 (company tolerances).

    Requirements: FR-IPR-0007, FR-IPR-0008, NFR-IPR-0002.
    The tolerance list is an editable list without a dialog, so the tests
    drive the model; the screen itself is left to the build.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env['res.company']
        cls.company_a = Company.create({'name': 'IPR Company A'})
        cls.company_b = Company.create({'name': 'IPR Company B'})
        cls.admin_a = cls._user('ipr_admin_a', 'account.group_account_manager', cls.company_a)
        cls.biller_a = cls._user('ipr_biller_a', 'account.group_account_invoice', cls.company_a)
        cls.pmanager_a = cls._user('ipr_pm_a', 'purchase.group_purchase_manager', cls.company_a)

    @classmethod
    def _user(cls, login, group_xmlid, company):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(group_xmlid).id])],
        })

    def _tol(self, user=None, company=None, **vals):
        model = self.env['ipr.tolerance']
        if user:
            model = model.with_user(user).with_context(allowed_company_ids=user.company_ids.ids)
        vals.setdefault('company_id', (company or self.company_a).id)
        return model.create(vals)

    def test_negative_refused(self):
        """FR-IPR-0008 / SC-IPR-0008-1: a negative tolerance is refused."""
        for field, value in (
            ('price_tolerance_percent', -1.0),
            ('price_tolerance_amount', -0.01),
            ('qty_tolerance_value', -1.0),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValidationError, 'cannot be negative'):
                    with self.env.cr.savepoint():
                        self._tol(user=self.admin_a, **{field: value})

    def test_negative_refused_on_write(self):
        """FR-IPR-0008: the refusal also applies when an existing record is changed."""
        rec = self._tol(user=self.admin_a)
        with self.assertRaisesRegex(ValidationError, 'cannot be negative'):
            rec.write({'price_tolerance_percent': -1.0})

    def test_zero_and_large_percent_accepted(self):
        """FR-IPR-0008 boundary: 0 is allowed, a percentage above 100 too."""
        rec = self._tol(user=self.admin_a, price_tolerance_percent=0.0, qty_tolerance_value=0.0)
        self.assertEqual(rec.price_tolerance_percent, 0.0)
        rec.write({'price_tolerance_percent': 150.0})
        self.assertEqual(rec.price_tolerance_percent, 150.0)

    def test_admin_sets_tolerances(self):
        """FR-IPR-0007: an accounting administrator sets percent or fixed values."""
        rec = self._tol(
            user=self.admin_a, price_tolerance_type='percent', price_tolerance_percent=3.0,
            qty_tolerance_type='fixed', qty_tolerance_value=1.0)
        data = self.env['ipr.tolerance']._ipr_for_company(self.company_a)
        self.assertEqual(rec.price_tolerance_percent, 3.0)
        self.assertEqual(data['price_type'], 'percent')
        self.assertEqual(data['price_percent'], 3.0)
        self.assertEqual(data['qty_type'], 'fixed')
        self.assertEqual(data['qty_value'], 1.0)

    def test_billing_user_cannot_write(self):
        """SC-IPR-0007-2: a billing user who is not accounting administrator cannot change tolerances but can read."""
        rec = self._tol(user=self.admin_a, price_tolerance_percent=2.0)
        as_biller = rec.with_user(self.biller_a)
        self.assertEqual(as_biller.price_tolerance_percent, 2.0)
        with self.assertRaises(AccessError):
            as_biller.write({'price_tolerance_percent': 9.0})
        with self.assertRaises(AccessError):
            self._tol(user=self.biller_a, company=self.company_a)

    def test_default_zero(self):
        """FR-IPR-0007: a company without a record has both tolerances at zero, as percent."""
        data = self.env['ipr.tolerance']._ipr_for_company(self.company_b)
        self.assertEqual(data['price_percent'], 0.0)
        self.assertEqual(data['qty_value'], 0.0)
        self.assertEqual(data['price_type'], 'percent')
        self.assertEqual(data['qty_type'], 'percent')

    @mute_logger('odoo.sql_db')
    def test_unique_per_company(self):
        """FR-IPR-0007: one record per company."""
        self._tol(user=self.admin_a)
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self._tol(user=self.admin_a)

    def test_company_rule_and_purchase_manager_read_only(self):
        """NFR-IPR-0002 / FR-IPR-0007: other companies' records are invisible; a purchase manager reads only."""
        rec_a = self._tol(company=self.company_a)
        rec_b = self._tol(company=self.company_b)
        visible = self.env['ipr.tolerance'].with_user(self.admin_a).search([])
        self.assertIn(rec_a, visible)
        self.assertNotIn(rec_b, visible)
        with self.assertRaises(AccessError):
            rec_b.with_user(self.admin_a).read(['price_tolerance_percent'])
        as_pm = rec_a.with_user(self.pmanager_a)
        self.assertEqual(as_pm.price_tolerance_percent, rec_a.price_tolerance_percent)
        with self.assertRaises(AccessError):
            as_pm.write({'price_tolerance_percent': 1.0})

    def test_menus(self):
        """FR-IPR-0007: a Bill Reconciliation menu under Accounting and one under Purchase open the tolerances."""
        menus = self.env['ir.ui.menu'].sudo().search([('name', '=', 'Bill Reconciliation')])
        self.assertEqual(len(menus), 2)
        for menu in menus:
            self.assertEqual(menu.action.res_model, 'ipr.tolerance')
