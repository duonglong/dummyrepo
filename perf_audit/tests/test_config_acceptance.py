from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged

CHECK_CODES = {
    'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8',
    'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7',
}


class ConfigAcceptanceBase(TransactionCase):
    """Users and lookups shared by the T-002 acceptance tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = cls.env['res.users'].create({
            'name': 'Audit Admin',
            'login': 'audit_acc_admin',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])],
        })
        cls.plain_user = cls.env['res.users'].create({
            'name': 'Audit Plain',
            'login': 'audit_acc_plain',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _thresholds(self, code=None):
        """Threshold records of one check (by code, any case) or the global ones."""
        Threshold = self.env['perf.audit.threshold'].sudo()
        if code is None:
            return Threshold.search([('check_id', '=', False)])
        check = self.env['perf.audit.check'].sudo().search([]).filtered(
            lambda c: (c.code or '').upper() == code)
        self.assertEqual(len(check), 1, 'check %s must exist once' % code)
        return Threshold.search([('check_id', '=', check.id)])

    def _values(self, code=None):
        return {(t.value, t.unit) for t in self._thresholds(code)}

    def _numbers(self, code=None):
        return {t.value for t in self._thresholds(code)}


@tagged('post_install', '-at_install')
class TestStartingThresholdsAcceptance(ConfigAcceptanceBase):
    """Requirements: BR-OPA-0006 (starting threshold values, T-002)."""

    def test_all_fifteen_checks_are_shipped(self):
        """BR-OPA-0006: every check A1-A8, B1-B7 ships as a record."""
        codes = {c.code.upper() for c in self.env['perf.audit.check'].sudo().search([])}
        self.assertEqual(codes, CHECK_CODES)

    def test_global_thresholds(self):
        """BR-OPA-0006: query limit 30 s, big table 100,000 rows, weak evidence 7 days."""
        values = self._values()
        self.assertIn((30.0, 'seconds'), values)
        self.assertIn((100000.0, 'rows'), values)
        self.assertIn((7.0, 'days'), values)

    def test_a3_minimum_index_size(self):
        """BR-OPA-0006: A3 minimum index size 1 MB."""
        self.assertIn((1.0, 'megabytes'), self._values('A3'))

    def test_a4_dead_rows_and_percent(self):
        """BR-OPA-0006: A4 10,000 dead rows and 20 percent dead."""
        self.assertIn((20.0, 'percent'), self._values('A4'))
        self.assertIn(10000.0, self._numbers('A4'))

    def test_a5_days_percent_and_min_rows(self):
        """BR-OPA-0006: A5 30 days, 20 percent modified, tables of at least 10,000 rows."""
        self.assertIn((30.0, 'days'), self._values('A5'))
        self.assertIn((20.0, 'percent'), self._values('A5'))
        self.assertIn(10000.0, self._numbers('A5'))

    def test_a6_top_queries(self):
        """BR-OPA-0006: A6 top 20 queries."""
        self.assertIn(20.0, self._numbers('A6'))

    def test_a7_size_and_growth(self):
        """BR-OPA-0006: A7 1 GB (as megabytes) or 20 percent growth."""
        values = self._values('A7')
        self.assertIn((20.0, 'percent'), values)
        self.assertTrue(
            values & {(1024.0, 'megabytes'), (1000.0, 'megabytes')},
            'A7 needs a 1 GB size threshold expressed in megabytes')

    def test_a8_five_minutes(self):
        """BR-OPA-0006: A8 5 minutes."""
        self.assertIn((5.0, 'minutes'), self._values('A8'))

    def test_b2_fan_out(self):
        """BR-OPA-0006: B2 fan-out 1,000."""
        self.assertIn(1000.0, self._numbers('B2'))

    def test_b6_tracked_fields_and_updates(self):
        """BR-OPA-0006: B6 10 tracked fields and 100,000 updates."""
        self.assertTrue({10.0, 100000.0} <= self._numbers('B6'))

    def test_b7_failures_and_lateness(self):
        """BR-OPA-0006: B7 3 consecutive failures and 1 hour late."""
        values = self._values('B7')
        self.assertIn(3.0, {v for v, _u in values})
        self.assertTrue(values & {(1.0, 'hours'), (60.0, 'minutes')})

    def test_default_value_equals_starting_value(self):
        """BR-OPA-0006: a shipped threshold starts at its default value."""
        thresholds = self.env['perf.audit.threshold'].sudo().search([])
        self.assertTrue(thresholds)
        for t in thresholds:
            self.assertEqual(t.value, t.default_value, t.key)


@tagged('post_install', '-at_install')
class TestConfigureChecksAcceptance(ConfigAcceptanceBase):
    """Requirements: FR-OPA-0031 (configure checks and thresholds, T-002)."""

    def _big_table(self):
        thresholds = self._thresholds().filtered(lambda t: t.unit == 'rows' and t.value == 100000.0)
        self.assertEqual(len(thresholds), 1)
        return thresholds.with_user(self.admin_user)

    def test_sc_0031_1_change_big_table_threshold(self):
        """SC-OPA-0031-1: the administrator changes 100,000 to 50,000; it is kept, the default stays."""
        threshold = self._big_table()
        threshold.write({'value': 50000})
        self.env.flush_all()
        threshold.invalidate_recordset()
        self.assertEqual(threshold.value, 50000.0)
        self.assertEqual(threshold.default_value, 100000.0)

    def test_sc_0031_2_negative_threshold_refused(self):
        """SC-OPA-0031-2: -5 is refused with a message that a threshold must be positive."""
        threshold = self._big_table()
        with self.assertRaises(ValidationError) as cm:
            threshold.write({'value': -5})
            self.env.flush_all()
        self.assertIn('positive', str(cm.exception))

    def test_zero_threshold_refused(self):
        """FR-OPA-0031 boundary: 0 is not positive and is refused."""
        threshold = self._big_table()
        with self.assertRaises(ValidationError):
            threshold.write({'value': 0})
            self.env.flush_all()

    def test_just_above_zero_accepted(self):
        """FR-OPA-0031 boundary: a small positive number is accepted."""
        threshold = self._big_table()
        threshold.write({'value': 0.5})
        self.assertEqual(threshold.value, 0.5)

    def test_new_negative_threshold_refused(self):
        """FR-OPA-0031: a new threshold created with a negative value is refused too."""
        check = self.env['perf.audit.check'].sudo().search([], limit=1)
        with self.assertRaises(ValidationError):
            self.env['perf.audit.threshold'].with_user(self.admin_user).create({
                'check_id': check.id,
                'key': 'acc_negative_probe',
                'name': 'Probe',
                'unit': 'count',
                'value': -5,
                'default_value': 1,
            })
            self.env.flush_all()

    def test_switch_check_off_and_on(self):
        """FR-OPA-0031: a check is switched off and stays listed, then back on."""
        check = self.env['perf.audit.check'].sudo().search([], limit=1).with_user(self.admin_user)
        self.assertTrue(check.active_check)
        check.write({'active_check': False})
        self.env.flush_all()
        check.invalidate_recordset()
        self.assertFalse(check.active_check)
        self.assertIn(check.id, self.env['perf.audit.check'].with_user(self.admin_user).search([]).ids)
        check.write({'active_check': True})
        self.assertTrue(check.active_check)

    def test_checks_carry_their_thresholds(self):
        """FR-OPA-0031: a check lists its own thresholds through threshold_ids."""
        check = self.env['perf.audit.check'].sudo().search([]).filtered(lambda c: c.code.upper() == 'A8')
        self.assertTrue(check.threshold_ids)


@tagged('post_install', '-at_install')
class TestAdminOnlyAcceptance(ConfigAcceptanceBase):
    """Requirements: FR-OPA-0033 (restrict the tool to system administrators, T-002)."""

    def test_sc_0033_1_plain_user_cannot_read_checks(self):
        """SC-OPA-0033-1: a non-administrator internal user is refused the checks."""
        with self.assertRaises(AccessError):
            self.env['perf.audit.check'].with_user(self.plain_user).search([])

    def test_sc_0033_1_plain_user_cannot_read_thresholds(self):
        """SC-OPA-0033-1: a non-administrator internal user is refused the thresholds."""
        with self.assertRaises(AccessError):
            self.env['perf.audit.threshold'].with_user(self.plain_user).search([])

    def test_plain_user_cannot_write_threshold(self):
        """FR-OPA-0033: a non-administrator cannot change a threshold."""
        threshold = self.env['perf.audit.threshold'].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            threshold.with_user(self.plain_user).write({'value': 7})

    def test_plain_user_cannot_create_check(self):
        """FR-OPA-0033: a non-administrator cannot create a check."""
        with self.assertRaises(AccessError):
            self.env['perf.audit.check'].with_user(self.plain_user).create({
                'code': 'ZZ', 'name': 'Probe', 'category': 'odoo'})

    def test_sc_0033_2_admin_sees_checks_and_thresholds(self):
        """SC-OPA-0033-2: a system administrator reads the checks and thresholds."""
        checks = self.env['perf.audit.check'].with_user(self.admin_user).search([])
        self.assertEqual(len(checks), 15)
        self.assertTrue(self.env['perf.audit.threshold'].with_user(self.admin_user).search([]))

    def test_menu_only_for_administrators(self):
        """FR-OPA-0033: the audit's top-level menu shows for an administrator, not for a plain user."""
        Menu = self.env['ir.ui.menu']
        domain = [('parent_id', '=', False), ('name', 'ilike', 'audit')]
        self.assertTrue(Menu.with_user(self.admin_user).search(domain))
        self.assertFalse(Menu.with_user(self.plain_user).search(domain))
