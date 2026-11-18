from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import job_runner

ADMIN_GROUP = 'supplier_procurement_management_hub.group_spm_admin'


class ProcurementJob(models.Model):
    """A scheduled job of the app: its on/off switch, its scheduled action and its runs.

    Requirements: FR-SPM-0096, FR-SPM-0097, FR-SPM-0012
    A job is one data record per code. Later tasks add codes with ``selection_add``
    and the two methods ``_spm_collect_<code>`` and ``_spm_process_<code>``.
    """
    _name = 'procurement.job'
    _inherit = ['procurement.audit.mixin']
    _description = 'Scheduled Job'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Selection([
        ('requalification', 'Supplier re-qualification'),
    ], required=True, readonly=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, help="Off: the scheduled action does not run; Run now still works.")
    cron_id = fields.Many2one('ir.cron', string="Scheduled action", ondelete='restrict', readonly=True)
    run_ids = fields.One2many('procurement.job.run', 'job_id', readonly=True)
    last_run_id = fields.Many2one('procurement.job.run', compute='_compute_last_run')

    _code_unique = models.Constraint('UNIQUE(code)', 'A job exists once.')

    _spm_audited_fields = ('active',)

    @api.depends('run_ids')
    def _compute_last_run(self):
        for job in self:
            job.last_run_id = job.run_ids[:1]

    def write(self, vals):
        """Switch the scheduled action with the job; nothing but the switch changes by hand.

        Requirements: FR-SPM-0096
        """
        if not self.env.su and set(vals) - {'active'}:
            raise AccessError(self.env._("Only the on/off switch of a job can be changed."))
        result = super().write(vals)
        if 'active' in vals:
            # elevated: an administrator may switch a job but has no right on scheduled actions
            self.cron_id.sudo().write({'active': vals['active']})
        return result

    @api.model
    def _spm_run(self, code, trigger='scheduled'):
        """Run the job of this code (the scheduled action calls it) and return its run.

        Requirements: FR-SPM-0096, FR-SPM-0097
        """
        # elevated: the scheduler runs as the superuser and Run now is checked for the administrator role
        job = self.sudo().with_context(active_test=False).search([('code', '=', code)], limit=1)
        if not job:
            raise UserError(self.env._("No job exists for the code %s.", code))
        job = job.with_context(spm_change_origin='job')
        return job_runner.execute(job.env, job, trigger)

    def action_spm_run_now(self):
        """Run this job now, whatever its switch says, and open its run.

        Requirements: FR-SPM-0096
        """
        self.ensure_one()
        if not self.env.user.has_group(ADMIN_GROUP):
            raise AccessError(self.env._("Only a Procurement hub administrator can run a job."))
        self.check_access('read')
        run = self._spm_run(self.code, trigger='manual')
        return {
            'type': 'ir.actions.act_window', 'res_model': run._name, 'res_id': run.id,
            'view_mode': 'form', 'name': self.env._("Run"),
        }

    # --- re-qualification ---------------------------------------------------

    def _spm_collect_requalification(self):
        """Return the suppliers Approved or Conditional whose re-qualification date has come.

        Requirements: FR-SPM-0012
        A supplier that already has an open re-qualification application is left out.
        """
        today = fields.Date.context_today(self)
        open_partners = self.env['procurement.supplier.application'].search([
            ('is_requalification', '=', True), ('state', 'not in', ('approved', 'rejected')),
        ]).partner_id
        status = self.env['procurement.supplier.status'].search(
            [('kind', '=', 'requalification_due'), ('default_for_kind', '=', True)], limit=1)
        # the status is looked up once per run and travels with the records
        return self.env['res.partner'].with_context(spm_requalification_status_id=status.id).search([
            ('spm_status_kind', 'in', ('approved', 'conditional')),
            ('spm_requalification_date', '<=', today),
            ('id', 'not in', open_partners.ids),
        ], order='id')

    def _spm_process_requalification(self, partner):
        """Move one supplier to the re-qualification due status, prefill its application, notify its buyer.

        Requirements: FR-SPM-0012
        Returns the number of notifications sent. Everything is checked before the first write, so a
        record that cannot be notified changes nothing and is tried again on the next run.
        """
        buyer = partner.spm_responsible_user_id
        if not buyer:
            raise UserError(self.env._("%s has no responsible buyer.", partner.display_name))
        if not buyer.email:
            raise UserError(self.env._("The buyer %s has no e-mail address.", buyer.name))
        if not partner.spm_supplier_type_id:
            raise UserError(self.env._("%s has no supplier type.", partner.display_name))
        status = self.env['procurement.supplier.status'].browse(
            partner.env.context.get('spm_requalification_status_id'))
        if not status:
            raise UserError(self.env._("No default status of the Re-qualification due kind is configured."))
        application = self.env['procurement.supplier.application'].create(
            self._spm_requalification_values(partner, buyer))
        partner.write({'spm_status_id': status.id})
        application.activity_schedule(
            'mail.mail_activity_data_todo', user_id=buyer.id,
            summary=self.env._("Re-qualify %s", partner.display_name))
        application.message_notify(
            partner_ids=buyer.partner_id.ids,
            subject=self.env._("Re-qualification due: %s", partner.display_name),
            body=self.env._("The re-qualification of %s is due.", partner.display_name))
        return 1

    def _spm_requalification_values(self, partner, buyer):
        """Return the values of the re-qualification application: the current data of the supplier."""
        bank = partner.bank_ids[:1]
        return {
            'is_requalification': True,
            'partner_id': partner.id,
            'user_id': buyer.id,
            'company_id': (partner.company_id or buyer.company_id).id,
            'email': partner.email or buyer.email,  # the application needs an address
            'supplier_type_id': partner.spm_supplier_type_id.id,
            'legal_name': partner.name,
            'street': partner.street,
            'city': partner.city,
            'zip': partner.zip,
            'country_id': partner.country_id.id,
            'phone': partner.phone,
            'website': partner.website,
            'vat': partner.vat,
            'operating_locations': partner.spm_operating_locations,
            'product_category_ids': [(6, 0, partner.spm_product_category_ids.ids)],
            'bank_account_number': bank.account_number,
            'bank_name': bank.bank_name,
            'bank_bic': bank.bank_bic,
            'bank_holder': bank.holder_name,
            'contact_ids': [(0, 0, {
                'name': contact.name, 'email': contact.email, 'phone': contact.phone, 'function': contact.function,
            }) for contact in partner.child_ids.filtered(lambda child: child.type == 'contact')],
        }
