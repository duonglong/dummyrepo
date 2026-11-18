"""Acceptance tests of T-008 (job log and no overlapping runs), written from the specification.

Requirements: FR-SPM-0096, FR-SPM-0097, NFR-SPM-0007

SC-SPM-0096-1 and SC-SPM-0097-1 name the document reminder job and the
scorecard job, built by later tasks. The same rules are checked on the daily
re-qualification job, the only job of M1:
- SC-0096-1: ten due suppliers, one whose responsible buyer has no e-mail
  address (no notification can be sent for it): the run logs 10 processed,
  9 sent and 1 error naming that supplier, and the other nine are done.
- SC-0097-1: a run of the job that is still going makes a new run skip and
  log a skipped run. The 'one snapshot per supplier and period' half is built
  with the scorecards.
"""
from odoo import fields
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestJobLogAcceptance(TransactionCase):
    """Requirements: FR-SPM-0096, FR-SPM-0097"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.today = fields.Date.context_today(self.env['res.partner'])
        buyer_group = self.env.ref(f'{MODULE}.group_spm_buyer').id
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t008l', 'email': 'spm_buyer_t008l@example.com',
            'group_ids': [(6, 0, [buyer_group])]})
        self.mute_buyer = self.env['res.users'].create({
            'name': 'Mute Buyer', 'login': 'spm_mute_t008l', 'email': False,
            'group_ids': [(6, 0, [buyer_group])]})
        self.mute_buyer.partner_id.write({'email': False})
        self.supplier_type = self.env['procurement.supplier.type'].create({'name': 'Goods T008L'})
        self.approved = self.env['procurement.supplier.status'].search([('kind', '=', 'approved')], limit=1)
        selection = dict(self.env['procurement.job']._fields['code'].selection)
        self.code = [k for k in selection if 'qualif' in k][0]
        self.job = self.env['procurement.job'].search([('code', '=', self.code)])

    def _supplier(self, name, buyer):
        return self.env['res.partner'].create({
            'name': name, 'is_company': True, 'spm_supplier_type_id': self.supplier_type.id,
            'spm_status_id': self.approved.id, 'spm_requalification_date': self.today,
            'spm_responsible_user_id': buyer.id})

    def test_sc_0096_1_one_failing_record_does_not_stop_the_others(self):
        """SC-SPM-0096-1: 10 due records, one that cannot be notified; the run
        logs 10 processed, 9 sent and 1 error naming that supplier."""
        suppliers = [self._supplier(f'Supplier {i}', self.buyer) for i in range(9)]
        broken = self._supplier('Supplier Without Mail', self.mute_buyer)
        self.env['procurement.job']._spm_run(self.code)
        self.env.flush_all()
        run = self.job.last_run_id
        self.assertEqual(run.state, 'done')
        self.assertEqual(run.processed_count, 10)
        self.assertEqual(run.sent_count, 9)
        self.assertEqual(run.error_count, 1)
        self.assertEqual(len(run.errors), 1)
        self.assertEqual(run.errors[0].get('id'), broken.id)
        for supplier in suppliers:
            supplier.invalidate_recordset()
            self.assertEqual(supplier.spm_status_kind, 'requalification_due')

    def test_sc_0096_1_a_failed_record_is_retried_on_the_next_run(self):
        """FR-SPM-0096: the failed record was not marked as sent, so the next
        run tries it again and logs the error again."""
        self._supplier('Supplier Without Mail', self.mute_buyer)
        self.env['procurement.job']._spm_run(self.code)
        self.env['procurement.job']._spm_run(self.code)
        self.env.flush_all()
        runs = self.job.run_ids.filtered(lambda r: r.state != 'skipped')
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs.mapped('error_count'), [1, 1])

    def test_sc_0097_1_a_run_while_another_is_going_is_skipped_and_logged(self):
        """SC-SPM-0097-1: a run of the job is still going; a second run
        starts, is skipped and is logged as skipped, doing none of the work."""
        acme = self._supplier('Acme', self.buyer)
        self.env['procurement.job.run'].create({
            'job_id': self.job.id, 'state': 'running',
            'date_start': fields.Datetime.now(), 'trigger': 'scheduled'})
        self.env['procurement.job']._spm_run(self.code)
        self.env.flush_all()
        skipped = self.job.run_ids.filtered(lambda r: r.state == 'skipped')
        self.assertEqual(len(skipped), 1)
        acme.invalidate_recordset()
        self.assertEqual(acme.spm_status_kind, 'approved')

    def test_nfr_0007_run_twice_in_a_row_gives_the_same_result_as_once(self):
        """NFR-SPM-0007 (twice in a row): two runs leave one application and
        one activity per due supplier."""
        acme = self._supplier('Acme', self.buyer)
        for _ in range(2):
            self.env['procurement.job']._spm_run(self.code)
        self.env.flush_all()
        applications = self.env['procurement.supplier.application'].search([
            ('partner_id', '=', acme.id), ('is_requalification', '=', True)])
        self.assertEqual(len(applications), 1)
        activities = self.env['mail.activity'].search([
            ('user_id', '=', self.buyer.id),
            ('res_model', 'in', ['res.partner', 'procurement.supplier.application']),
            ('res_id', 'in', [acme.id] + applications.ids)])
        self.assertEqual(len(activities), 1)
