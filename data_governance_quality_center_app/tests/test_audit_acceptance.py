from datetime import date

from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase


class TestAuditAcceptance(TransactionCase):
    """Acceptance tests of T-004 (append-only audit trail).

    Requirements: FR-DGQ-0072, FR-DGQ-0073, FR-DGQ-0074.
    Left to the build and to later tasks: the entries a rule change or a
    correction writes (SC-DGQ-0072-1/2, they need the rule and correction
    models), the audit export file (SC-DGQ-0074-1) and its visibility to
    managers (SC-DGQ-0074-2); the spec names neither the export method, its
    answer nor the access group identifiers.
    The purge dialog is driven with the Form helper: the date is set in the
    form, the dialog is saved, then the purge method is called on it.
    """

    def _event(self, word):
        selection = self.env['data.quality.audit']._fields['event'].selection
        keys = [key for key, _label in selection]
        matching = [key for key in keys if word in key]
        self.assertTrue(matching, "no audit event containing %r in %s" % (word, keys))
        return matching[0]

    def _entry(self, when, word='merge', name='Acme'):
        return self.env['data.quality.audit'].sudo().create({
            'date': when,
            'event': self._event(word),
            'user_id': self.env.user.id,
            'company_id': self.env.company.id,
            'res_model': 'res.partner',
            'res_id': 1,
            'record_name': name,
        })

    def _purge(self, limit):
        with Form(self.env['data.quality.audit.purge']) as form:
            form.date_before = limit
        wizard = form.save()
        return wizard

    def test_entry_is_stored_with_its_columns(self):
        """FR-DGQ-0072: an entry keeps event, user, time, company and record."""
        entry = self._entry('2026-07-15 10:00:00')
        self.env.flush_all()
        self.assertEqual(entry.event, self._event('merge'))
        self.assertEqual(entry.user_id, self.env.user)
        self.assertEqual(entry.company_id, self.env.company)
        self.assertEqual(entry.res_model, 'res.partner')
        self.assertEqual(entry.record_name, 'Acme')

    def test_entry_cannot_be_edited(self):
        """SC-DGQ-0073-1: editing an audit entry is refused, for any user."""
        entry = self._entry('2026-07-15 10:00:00')
        admin = self.env.ref('base.user_admin')
        for env_entry in (entry, entry.with_user(admin), entry.sudo()):
            with self.assertRaises(UserError):
                env_entry.write({'record_name': 'Changed'})
        self.assertEqual(entry.record_name, 'Acme')

    def test_entry_cannot_be_deleted(self):
        """SC-DGQ-0073-1: deleting an audit entry is refused, for any user."""
        entry = self._entry('2026-07-15 10:00:00')
        admin = self.env.ref('base.user_admin')
        for env_entry in (entry.with_user(admin), entry.sudo()):
            with self.assertRaises(UserError):
                env_entry.unlink()
        self.assertTrue(entry.exists())

    def test_purge_dialog_counts_older_entries(self):
        """SC-DGQ-0073-2: the dialog shows how many entries will go."""
        self._entry('2023-06-01 10:00:00')
        self._entry('2026-06-01 10:00:00')
        self.assertEqual(self._purge(date(2024, 1, 1)).entry_count, 1)

    def test_purge_removes_old_entries_and_records_itself(self):
        """SC-DGQ-0073-2: 2023 entries go, 2026 stays, the purge leaves its own entry."""
        old = self._entry('2023-06-01 10:00:00', name='Old')
        recent = self._entry('2026-06-01 10:00:00', name='Recent')
        self._purge(date(2024, 1, 1)).action_purge()
        self.env.flush_all()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        purges = self.env['data.quality.audit'].sudo().search(
            [('event', '=', self._event('purge'))])
        self.assertEqual(len(purges), 1)
        self.assertEqual(purges.user_id, self.env.user)
        self.assertIn('2024-01-01', '%s %s' % (purges.description or '', purges.changes or ''))

    def test_purge_boundary_around_the_date_limit(self):
        """SC-DGQ-0073-2 boundary: the day before the limit goes, the limit day stays."""
        before = self._entry('2023-12-31 12:00:00', name='Before')
        on_limit = self._entry('2024-01-01 12:00:00', name='OnLimit')
        self._purge(date(2024, 1, 1)).action_purge()
        self.assertFalse(before.exists())
        self.assertTrue(on_limit.exists())

    def test_purge_needs_a_date(self):
        """FR-DGQ-0073: the purge date is required."""
        field = self.env['data.quality.audit.purge']._fields['date_before']
        self.assertTrue(field.required)
