from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, tagged

THRESHOLD_MSG = "The empty-value threshold must be between 0 and 100."
PERIOD_MSG = "The outdated period must be at least 1 day."


@tagged('post_install', '-at_install')
class TestProfileAcceptance(TransactionCase):
    """Acceptance tests of T-002 (profile model, thresholds and refusals).

    The profile form is driven with Odoo's Form helper on the fields name,
    model_id, empty_threshold and outdated_days; the form view is built by
    the task. The model list is checked through the allowed_model_ids helper
    of the profile, as the form's domain is built from it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        cls.dpa_group = group
        cls.anna = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Anna Analyst',
            'login': 'anna_dpa',
            'email': 'anna@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id] + group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.cron_model = cls.env['ir.model']._get('ir.cron')

    def _create_profile(self, **vals):
        """Create a profile as Anna through the form."""
        form = Form(self.env['dpa.profile'].with_user(self.anna))
        form.name = vals.get('name', 'Customer quality')
        form.model_id = vals.get('model_id', self.partner_model)
        if 'empty_threshold' in vals:
            form.empty_threshold = vals['empty_threshold']
        if 'outdated_days' in vals:
            form.outdated_days = vals['outdated_days']
        return form.save()

    def test_only_readable_models_offered(self):
        """Requirements: FR-DPA-0001, SC-DPA-0001-1.

        Anna may read contacts but not scheduled actions (the stand-in for
        employees, which this module cannot depend on).
        """
        self.assertTrue(self.dpa_group, "the Data Profiling group must exist")
        profile = self.env['dpa.profile'].with_user(self.anna).new({})
        allowed = profile.allowed_model_ids
        self.assertIn(self.partner_model, allowed)
        self.assertNotIn(self.cron_model, allowed)

    def test_unreadable_model_refused_on_create(self):
        """Requirements: FR-DPA-0001, SC-DPA-0001-1 (model Anna cannot read is refused)."""
        with self.assertRaises((ValidationError, AccessError)):
            self.env['dpa.profile'].with_user(self.anna).create({
                'name': 'Crons', 'model_id': self.cron_model.id,
            })

    def test_new_profile_starting_settings(self):
        """Requirements: FR-DPA-0001, SC-DPA-0001-2."""
        profile = self._create_profile()
        self.assertEqual(profile.name, 'Customer quality')
        self.assertEqual(profile.model_id, self.partner_model)
        self.assertEqual(profile.empty_threshold, 20.0)
        self.assertEqual(profile.outdated_days, 365)
        self.assertTrue(profile.active)

    def test_threshold_above_100_refused(self):
        """Requirements: FR-DPA-0006, SC-DPA-0006-1 (out of range refused, keeps 20)."""
        profile = self._create_profile()
        for bad in (120.0, 100.01, -0.01, -5.0):
            with self.assertRaises(ValidationError) as cm:
                with Form(profile) as form:
                    form.empty_threshold = bad
            self.assertIn(THRESHOLD_MSG, str(cm.exception))
            profile.invalidate_recordset()
            self.assertEqual(profile.empty_threshold, 20.0)

    def test_threshold_bounds_accepted(self):
        """Requirements: FR-DPA-0006, SC-DPA-0006-2 (0 and 100 saved)."""
        profile = self._create_profile()
        with Form(profile) as form:
            form.empty_threshold = 0.0
        profile.invalidate_recordset()
        self.assertEqual(profile.empty_threshold, 0.0)
        with Form(profile) as form:
            form.empty_threshold = 100.0
        profile.invalidate_recordset()
        self.assertEqual(profile.empty_threshold, 100.0)

    def test_outdated_period_below_one_refused(self):
        """Requirements: FR-DPA-0012, SC-DPA-0012-1 (0 and negative refused, keeps 90)."""
        profile = self._create_profile(outdated_days=90)
        self.assertEqual(profile.outdated_days, 90)
        for bad in (0, -1):
            with self.assertRaises(ValidationError) as cm:
                with Form(profile) as form:
                    form.outdated_days = bad
            self.assertIn(PERIOD_MSG, str(cm.exception))
            profile.invalidate_recordset()
            self.assertEqual(profile.outdated_days, 90)

    def test_outdated_period_of_one_accepted(self):
        """Requirements: FR-DPA-0012, SC-DPA-0012-1 (boundary: 1 day is saved)."""
        profile = self._create_profile(outdated_days=90)
        with Form(profile) as form:
            form.outdated_days = 1
        profile.invalidate_recordset()
        self.assertEqual(profile.outdated_days, 1)
