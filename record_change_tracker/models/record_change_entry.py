from collections import defaultdict

from odoo import _, api, fields, models
from odoo.fields import Domain


class RecordChangeEntry(models.Model):
    """Requirements: FR-RCT-001, FR-RCT-003.

    One readable change of one followed field of one record: field, old and new value as text,
    author and date.
    """
    _name = 'record.change.entry'
    _description = 'Recorded change of a followed field'
    _order = 'change_date desc, id desc'

    res_model = fields.Char(string='Record type', required=True, index=True, readonly=True)
    res_id = fields.Many2oneReference(
        string='Record', model_field='res_model', required=True, index=True, readonly=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', ondelete='set null', readonly=True)
    field_name = fields.Char(string='Technical name', required=True, readonly=True)
    field_label = fields.Char(string='Field label', required=True, readonly=True)
    old_value = fields.Text(string='Old value', readonly=True)
    new_value = fields.Text(string='New value', readonly=True)
    author_id = fields.Many2one(
        'res.users', string='Changed by', ondelete='set null', readonly=True)
    change_date = fields.Datetime(
        string='Changed on', required=True, default=fields.Datetime.now, index=True, readonly=True)

    res_access_read = fields.Boolean(
        string='Record readable', groups=fields.NO_ACCESS, compute='_compute_res_access_read',
        search='_search_res_access_read', compute_sudo=True, depends_context=('uid',))

    is_new = fields.Boolean(
        string='Is new', compute='_compute_is_new', depends_context=('uid',))
    new_label = fields.Char(string='New', compute='_compute_is_new', depends_context=('uid',))

    @api.depends('res_model', 'res_id', 'change_date', 'author_id')
    def _compute_is_new(self):
        """Requirements: FR-RCT-005, FR-RCT-006, NFR-USE-001.

        New: the user has a previous visit, the change is later and was made by someone else.
        The word is also given as text, so the mark does not rely on colour.
        """
        visits = self.env['record.change.visit'].sudo().search([
            ('user_id', '=', self.env.uid), ('res_model', 'in', self.mapped('res_model')),
            ('res_id', 'in', self.mapped('res_id'))])
        previous = {(v.res_model, v.res_id): v.previous_visit for v in visits}
        for entry in self:
            seen = previous.get((entry.res_model, entry.res_id))
            entry.is_new = bool(
                seen and entry.change_date > seen and entry.author_id.id != self.env.uid)
            entry.new_label = _('New') if entry.is_new else ''

    @api.depends('res_model', 'res_id')
    def _compute_res_access_read(self):
        """Requirements: NFR-SEC-001. True when the current user can read the linked record."""
        by_model = defaultdict(list)
        for entry in self:
            by_model[entry.res_model].append(entry.res_id)
        user_env = self.env(su=False)
        readable = {}
        for model, ids in by_model.items():
            if model in user_env and user_env[model].has_access('read'):
                readable[model] = set(user_env[model].browse(ids)._filtered_access('read').ids)
        for entry in self:
            entry.res_access_read = entry.res_id in readable.get(entry.res_model, ())

    @api.model
    def _search_res_access_read(self, operator, value):
        """Requirements: NFR-SEC-001. Entries whose record the current user can read."""
        if operator != 'in':
            return NotImplemented
        user_env = self.env(su=False)
        sec_domain = Domain.FALSE
        for model in ('res.partner', 'sale.order', 'purchase.order', 'product.template'):
            if model not in user_env or not user_env[model].has_access('read'):
                continue
            query = user_env[model].with_context(active_test=False)._search(Domain.TRUE)
            sec_domain |= Domain('res_model', '=', model) & Domain('res_id', 'any!', query)
        return sec_domain

    def init(self):
        """Index serving the history of one record, newest first (NFR-PERF-001)."""
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS record_change_entry_record_date_idx "
            "ON record_change_entry (res_model, res_id, change_date DESC)")
