from datetime import datetime

from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import freeze_time


@tagged('post_install', '-at_install')
class TestStatsAcceptance(TransactionCase):
    """Acceptance tests of T-006 (outdated records, number and date statistics, empty model report).

    The profile is made through Odoo's Form helper (name, model, chosen fields,
    thresholds); the Run button is the method ``action_run`` of the profile,
    called on the saved record as the analyst, because the button itself is
    built with the view by the task. The report is read back as the unsaved
    ``dpa.snapshot`` of the profile. Contacts (and contact tags or currency
    rates where a scenario needs a date) stand in for the products of the
    scenarios: this module depends on base only.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.anna = Users.create({
            'name': 'Anna Analyst', 'login': 'anna_sta', 'email': 'anna_sta@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.marc = Users.create({
            'name': 'Marc', 'login': 'marc_sta', 'email': 'marc_sta@example.com',
            'group_ids': [(6, 0, internal.ids)],
        })
        cls.lisa = Users.create({
            'name': 'Lisa', 'login': 'lisa_sta', 'email': 'lisa_sta@example.com',
            'group_ids': [(6, 0, internal.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        # Only the contacts a test creates are profiled: archive every other one.
        cls.env.cr.execute("UPDATE res_partner SET active = false")
        cls.env.invalidate_all()

    def _contacts(self, vals_list):
        return self.env['res.partner'].create(
            [dict(vals, name=vals.get('name', 'Contact %d' % i)) for i, vals in enumerate(vals_list)])

    def _profile(self, field_names=(), name='Customer quality', model='res.partner', **settings):
        """Create a profile through the form, as the analyst."""
        form = Form(self.env['dpa.profile'].with_user(self.anna))
        form.name = name
        form.model_id = self.env['ir.model']._get(model)
        for fname in field_names:
            form.field_ids.add(self.env['ir.model.fields']._get(model, fname))
        for key, value in settings.items():
            setattr(form, key, value)
        return form.save()

    def _run(self, profile):
        profile.with_user(self.anna).action_run()
        self.env.flush_all()
        self.env.invalidate_all()
        report = self.env['dpa.snapshot'].search(
            [('profile_id', '=', profile.id), ('is_saved', '=', False)])
        self.assertEqual(len(report), 1, "one unsaved report must result from a run")
        return report

    def _line(self, report, field_name):
        line = report.field_line_ids.filtered(lambda l: l.field_name == field_name)
        self.assertEqual(len(line), 1, "one line expected for %s" % field_name)
        return line

    def _values(self, line):
        return line.value_ids.sorted(lambda v: (v.sequence, v.id))

    def _findings(self, report, kind, line=None):
        found = report.finding_ids.filtered(lambda f: f.kind == kind)
        return found.filtered(lambda f: f.field_line_id == line) if line else found

    def _age(self, partner, when):
        """Set the last update of a record, as the ORM would have saved it.

        Pending recomputations are flushed first, so that no later flush
        rewrites the write date set here.
        """
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET write_date = %s WHERE id = %s", (when, partner.id))
        self.env.invalidate_all()

    # -- FR-DPA-0011 -------------------------------------------------------
    def test_one_day_past_the_period(self):
        """Requirements: FR-DPA-0011, BR-DPA-0005, SC-DPA-0011-1 (91 days outdated, 90 days not)."""
        a, b = self._contacts([{'name': 'A'}, {'name': 'B'}])
        self._age(a, '2026-08-26 12:00:00')
        self._age(b, '2026-08-27 12:00:00')
        profile = self._profile(['name'], outdated_days=90)
        with freeze_time('2026-11-25 12:00:00'):
            report = self._run(profile)
        self.assertEqual(report.outdated_count, 1)
        outdated = report.outdated_ids
        self.assertEqual(len(outdated), 1)
        self.assertEqual(outdated.record_name, 'A')
        self.assertEqual(outdated.days_since_update, 91)
        self.assertEqual(outdated.last_update, datetime(2026, 8, 26, 12, 0, 0))

    def test_oldest_first(self):
        """Requirements: FR-DPA-0011, SC-DPA-0011-2 (C, 400 days, before A, 200 days)."""
        a, c = self._contacts([{'name': 'A'}, {'name': 'C'}])
        self._age(a, '2026-05-09 12:00:00')   # 200 days before the run
        self._age(c, '2025-10-21 12:00:00')   # 400 days before the run
        profile = self._profile(['name'], outdated_days=90)
        with freeze_time('2026-11-25 12:00:00'):
            report = self._run(profile)
        self.assertEqual(report.outdated_ids.mapped('record_name'), ['C', 'A'])

    # -- FR-DPA-0016 -------------------------------------------------------
    def test_number_statistics(self):
        """Requirements: FR-DPA-0016, SC-DPA-0016-1.

        The color index of contacts stands in for the quantity of products.
        """
        self._contacts([{'color': q} for q in (2, 4, 4, 4, 5, 5, 7, 9)])
        line = self._line(self._run(self._profile(['color'])), 'color')
        self.assertEqual(line.min_value, 2.0)
        self.assertEqual(line.max_value, 9.0)
        self.assertEqual(line.sum_value, 40.0)
        self.assertEqual(line.mean_value, 5.0)
        self.assertEqual(line.median_value, 4.5)
        self.assertAlmostEqual(line.stddev_value, 2.0, places=6)

    # -- FR-DPA-0017 -------------------------------------------------------
    def test_date_span(self):
        """Requirements: FR-DPA-0017, SC-DPA-0017-1.

        Contacts have no date field; the date of a currency rate (readable by
        everyone) is the profiled date, on three rates of one currency.
        """
        Rate = self.env['res.currency.rate']
        Rate.search([]).unlink()
        currency = self.env.ref('base.EUR')
        for day in ('2026-01-03', '2026-03-15', '2026-11-01'):
            Rate.create({'name': day, 'currency_id': currency.id, 'rate': 1.0, 'company_id': False})
        profile = self._profile(['name'], name='Rates', model='res.currency.rate')
        line = self._line(self._run(profile), 'name')
        self.assertEqual(str(line.earliest_date), '2026-01-03')
        self.assertEqual(str(line.latest_date), '2026-11-01')
        self.assertEqual(line.span_days, 302)

    # -- FR-DPA-0028 -------------------------------------------------------
    def test_empty_model(self):
        """Requirements: FR-DPA-0028, SC-DPA-0028-1 (no contact tag: 0 records, "-", notice, no flag)."""
        self.env.cr.execute("UPDATE res_partner_category SET active = false")
        self.env.invalidate_all()
        profile = self._profile(name='Tags', model='res.partner.category')
        report = self._run(profile)
        self.assertEqual(report.record_count, 0)
        self.assertEqual(report.notice, 'No records to profile.')
        self.assertTrue(report.field_line_ids)
        for line in report.field_line_ids:
            self.assertFalse(line.is_rated, "%s must show '-' instead of a rate" % line.field_name)
            self.assertFalse(line.high_empty)
