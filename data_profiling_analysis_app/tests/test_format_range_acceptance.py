from datetime import datetime

from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import freeze_time


@tagged('post_install', '-at_install')
class TestFormatRangeAcceptance(TransactionCase):
    """Acceptance tests of T-005 (most common format, inconsistent values, unusual numbers).

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
            'name': 'Anna Analyst', 'login': 'anna_fmt', 'email': 'anna_fmt@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.marc = Users.create({
            'name': 'Marc', 'login': 'marc_fmt', 'email': 'marc_fmt@example.com',
            'group_ids': [(6, 0, internal.ids)],
        })
        cls.lisa = Users.create({
            'name': 'Lisa', 'login': 'lisa_fmt', 'email': 'lisa_fmt@example.com',
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

    # -- FR-DPA-0008 -------------------------------------------------------
    def test_most_common_pattern(self):
        """Requirements: FR-DPA-0008, BR-DPA-0003, SC-DPA-0008-1 (999-9999 held by 80.0%)."""
        self._contacts(
            [{'phone': '555-010%d' % i} for i in range(8)] + [{'phone': '5550101'}, {'phone': '+32 470 123'}])
        line = self._line(self._run(self._profile(['phone'])), 'phone')
        self.assertEqual(line.main_pattern, '999-9999')
        self.assertEqual(line.main_pattern_share, 80.0)

    # -- FR-DPA-0009 -------------------------------------------------------
    def test_two_values_flagged(self):
        """Requirements: FR-DPA-0009, SC-DPA-0009-1 (2 inconsistent phones, listed with their record)."""
        self._contacts(
            [{'phone': '555-010%d' % i} for i in range(8)]
            + [{'phone': '5550101', 'name': 'Deco Addict'}, {'phone': '+32 470 123', 'name': 'Azure Interior'}])
        report = self._run(self._profile(['phone']))
        line = self._line(report, 'phone')
        self.assertEqual(line.inconsistent_count, 2)
        found = self._findings(report, 'inconsistent_format', line)
        self.assertEqual(
            sorted((f.value_label, f.record_name) for f in found),
            [('+32 470 123', 'Azure Interior'), ('5550101', 'Deco Addict')])
        for finding in found:
            self.assertEqual(finding.res_model, 'res.partner')
            self.assertEqual(self.env['res.partner'].browse(finding.res_id).display_name, finding.record_name)

    def test_exactly_60_percent_still_flags(self):
        """Requirements: FR-DPA-0009, SC-DPA-0009-2 (6 of 10 in AA-999: the 4 others are flagged)."""
        refs = ['AB-%03d' % i for i in range(6)] + ['A-1234', '12', 'ABC', 'AB_12']
        self._contacts([{'ref': ref} for ref in refs])
        line = self._line(self._run(self._profile(['ref'])), 'ref')
        self.assertEqual(line.main_pattern, 'AA-999')
        self.assertEqual(line.inconsistent_count, 4)

    def test_no_common_format(self):
        """Requirements: FR-DPA-0009, SC-DPA-0009-3 (10 different name patterns: nothing flagged)."""
        names = ['Ab', 'Abc', 'Abcd', 'Abcde', 'Abcdef', 'Ab Cd', 'A1', 'A12', 'A-B', 'A.B.C']
        self._contacts([{'name': name} for name in names])
        report = self._run(self._profile(['name']))
        line = self._line(report, 'name')
        self.assertFalse(line.has_common_format)
        self.assertEqual(line.inconsistent_count, 0)
        self.assertFalse(self._findings(report, 'inconsistent_format'))

    # -- FR-DPA-0010 -------------------------------------------------------
    def test_one_unusual_number(self):
        """Requirements: FR-DPA-0010, BR-DPA-0004, SC-DPA-0010-1.

        The color index of contacts stands in for the price of products; the
        500 is on the contact "Desk".
        """
        prices = [10, 10, 11, 11, 12, 12, 12, 13, 13, 14]
        self._contacts([{'color': price} for price in prices] + [{'color': 500, 'name': 'Desk'}])
        report = self._run(self._profile(['color']))
        line = self._line(report, 'color')
        self.assertTrue(line.range_computed)
        self.assertEqual((line.range_low, line.range_high), (8.0, 16.0))
        found = self._findings(report, 'unusual_number', line)
        self.assertEqual(len(found), 1)
        self.assertEqual(found.record_name, 'Desk')
        self.assertIn('500', found.value_label)
        self.assertEqual(line.unusual_count, 1)

    def test_too_few_values(self):
        """Requirements: FR-DPA-0010, SC-DPA-0010-2 (9 values: no range, nothing flagged)."""
        self._contacts([{'color': value} for value in (10, 10, 11, 11, 12, 12, 13, 14, 500)])
        report = self._run(self._profile(['color']))
        line = self._line(report, 'color')
        self.assertFalse(line.range_computed)
        self.assertEqual(line.unusual_count, 0)
        self.assertFalse(self._findings(report, 'unusual_number'))
