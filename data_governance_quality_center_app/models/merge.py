import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import merge

_logger = logging.getLogger(__name__)


class DataQualityMerge(models.Model):
    """The history of a merge: who, when, the values before and the links moved, to undo it.

    Requirements: FR-DGQ-0043, FR-DGQ-0045, BR-DGQ-0008
    """
    _name = 'data.quality.merge'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Merge'
    _order = 'date desc, id desc'

    name = fields.Char(help="The records merged and the record kept.")
    group_id = fields.Many2one(
        'data.quality.duplicate.group', string='Group', ondelete='set null', index=True)
    kept_res_id = fields.Many2oneReference(string='Record Kept', model_field='res_model', readonly=True)
    merged_res_ids = fields.Json(string='Records Merged Away', readonly=True)
    merged_names = fields.Char(string='Merged Away', readonly=True)
    values_before = fields.Json(string='Values Before', readonly=True)
    values_written = fields.Json(string='Values Written', readonly=True)
    moved_links = fields.Json(string='Links Moved', readonly=True)
    user_id = fields.Many2one('res.users', string='Merged By', ondelete='set null', readonly=True)
    date = fields.Datetime(string='Merged On', readonly=True)
    state = fields.Selection(
        [('done', 'Merged'), ('rolled_back', 'Rolled Back')], required=True, default='done', index=True)
    reversible = fields.Boolean(string='Can Be Rolled Back', default=True, readonly=True)
    irreversible_reason = fields.Char(string='Why Not', readonly=True)
    rollback_user_id = fields.Many2one('res.users', string='Rolled Back By', ondelete='set null', readonly=True)
    rollback_date = fields.Datetime(string='Rolled Back On', readonly=True)
    rollback_report = fields.Json(
        string='Rollback Report', readonly=True,
        help="The fields that were changed after the merge and so were left as they are.")
    rollback_blocked = fields.Char(
        string='Rollback Not Offered', compute='_compute_rollback_blocked',
        help="Why this merge cannot be rolled back; empty when it can.")

    def _blocked_reason(self):
        """Return why the merge cannot be rolled back, or False when it can (BR-DGQ-0008)."""
        self.ensure_one()
        if self.state != 'done':
            return self.env._("This merge was already rolled back.")
        if not self.reversible:
            return self.irreversible_reason or self.env._("This merge cannot be undone.")
        ids = [*(self.merged_res_ids or []), self.kept_res_id]
        # sudo: a merged-away record is archived, and the manager may not see archived ones
        records = self.env[self.res_model].sudo().with_context(active_test=False).browse(ids).exists()
        if len(records) != len(ids):
            return self.env._("A merged record no longer exists, so the merge cannot be undone.")
        return False

    @api.depends('state', 'reversible', 'irreversible_reason', 'merged_res_ids', 'kept_res_id')
    def _compute_rollback_blocked(self):
        """Tell why a merge offers no rollback.

        Requirements: BR-DGQ-0008
        """
        for record in self:
            record.rollback_blocked = record._blocked_reason()

    def action_rollback(self):
        """Roll the merge back: records active again, moved links pointed back, values restored.

        Only what the merge moved is moved back: a link created on the kept record since stays;
        a field changed since keeps its value and is listed in the report.

        Requirements: FR-DGQ-0045, BR-DGQ-0008
        """
        self.ensure_one()
        self.env['data.quality.duplicate.group']._check_manager()
        reason = self._blocked_reason()
        if reason:
            raise UserError(reason)
        ids = [*self.merged_res_ids, self.kept_res_id]
        if not (merge.lock_records(self.env, self._name, self.ids)
                and merge.lock_records(self.env, self.res_model, ids)):
            raise UserError(self.env._("These records changed. Open the merge again."))
        self.invalidate_recordset()
        if self.state != 'done':
            raise UserError(self.env._("This merge was already rolled back."))
        # sudo: the app restores the records and its own history; the manager only decides
        Model = self.env[self.res_model].sudo().with_context(active_test=False)
        kept = Model.browse(self.kept_res_id)
        with self.env.cr.savepoint():
            Model.browse(self.merged_res_ids).write({'active': True})
            for entries in (self.moved_links or {}).values():
                merge.move_links_back(self.env, entries)
            report = self._restore_values(kept)
            group = self.group_id.sudo()  # sudo: the app reopens its own group
            if group and group.state == 'merged':
                group.write({'state': 'open', 'merge_id': False})
            self.sudo().write({  # sudo: the app writes its own history
                'state': 'rolled_back', 'rollback_user_id': self.env.user.id,
                'rollback_date': fields.Datetime.now(), 'rollback_report': report})
        _logger.info("dgq merge rollback %s: %s by uid %s", self.res_model, self.id, self.env.uid)
        return True

    def _restore_values(self, kept):
        """Give back the values the merge replaced where the field still holds the merged value.

        Returns the report: the fields left as they are because they changed since the merge.
        """
        left = []
        restore = {}
        for name, written in (self.values_written or {}).items():
            if merge.field_values(kept, [name])[name] == written:
                restore[name] = (self.values_before or {}).get(name)
            else:
                left.append({'field': name, 'label': kept._fields[name]._description_string(self.env)})
        if restore:
            kept.write(restore)
        return {'fields_left_as_changed': left}
