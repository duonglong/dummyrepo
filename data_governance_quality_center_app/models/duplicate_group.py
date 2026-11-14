from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import matching, merge


class DataQualityDuplicateGroup(models.Model):
    """A set of likely duplicate records found by a rule, with its highest pair score.

    Requirements: FR-DGQ-0039, FR-DGQ-0040, FR-DGQ-0041
    """
    _name = 'data.quality.duplicate.group'
    _inherit = ['data.quality.result.mixin']
    _description = 'Data Quality Duplicate Group'
    _order = 'score desc, id'

    name = fields.Char(help="The name of the first record of the group.")
    rule_id = fields.Many2one(
        'data.quality.duplicate.rule', string='Rule', required=True, ondelete='cascade', index=True)
    check_id = fields.Many2one(
        'data.quality.duplicate.check', string='Check', ondelete='set null')
    state = fields.Selection([
        ('open', 'Open'),
        ('dismissed', 'Not Duplicates'),
        ('merged', 'Merged'),
    ], required=True, default='open', index=True)
    score = fields.Float(string='Highest Score', digits=(5, 1))
    line_ids = fields.One2many('data.quality.duplicate.line', 'group_id', string='Records')
    merge_id = fields.Many2one(
        'data.quality.merge', string='Merge', ondelete='set null', readonly=True,
        help="The merge that closed this group; empty again when it is rolled back.")
    comparison = fields.Html(
        compute='_compute_comparison', sanitize=False,
        help="The records side by side: the matching fields first, then the other filled fields.")

    @api.depends('line_ids', 'rule_id.field_line_ids')
    def _compute_comparison(self):
        """Build the side-by-side table: scores, differing values marked, linked documents.

        Requirements: FR-DGQ-0040
        """
        for group in self:
            group.comparison = group._render_comparison()

    def _render_comparison(self):
        """Return the comparison table of the group as HTML."""
        self.ensure_one()
        lines = self.line_ids.sorted(lambda line: (line.sequence, line.id))
        ids = [line.res_id for line in lines]
        rows = merge.field_rows(self.env, self.res_model, ids, self.rule_id._get_match_lines())
        head = Markup('').join(
            Markup('<th>%s<div class="fw-normal text-muted">%s</div><div class="fw-normal">%s</div></th>') % (
                line.record_name or '', self.env._("Score %s", int(round(line.score))),
                self.env._("%s linked documents", line.link_count))
            for line in lines)
        body = Markup('')
        for row in rows:
            cells = Markup('')
            for line in lines:
                score = row['scores'].get(line.res_id)
                cells += Markup('<td class="%s">%s%s</td>') % (
                    'table-warning fw-bold' if row['differs'] else '', row['values'].get(line.res_id, ''),
                    Markup(' <span class="text-muted">(%s)</span>') % int(round(score)) if score is not None else '')
            body += Markup('<tr><th class="%s">%s</th>%s</tr>') % (
                'text-primary' if row['is_match'] else '', row['label'], cells)
        return Markup('<table class="table table-sm table-bordered"><thead><tr><th/>%s</tr></thead>'
                      '<tbody>%s</tbody></table>') % (head, body)

    def _check_manager(self):
        """Refuse the action to anyone below manager."""
        if not self.env.is_superuser() and not self.env.user.has_group(
                'data_governance_quality_center_app.group_dgq_manager'):
            raise AccessError(self.env._("Only a data quality manager can do this."))

    def _check_open(self):
        """Refuse the action on a group that is closed: its decision stays as it was taken."""
        self.ensure_one()
        if self.state != 'open':
            state_label = dict(self._fields['state']._description_selection(self.env))[self.state]
            raise UserError(self.env._(
                "This group is %s: only an open group can be changed.", state_label))

    def _rescore(self):
        """Score the records of the group again against its new first record and refresh its score.

        The score of the group is the highest score of a record against the first one.
        """
        self.ensure_one()
        lines = self.line_ids.sorted(lambda line: (line.sequence, line.id))
        match_lines = self.rule_id._get_match_lines()
        names = [name for name, _method, _weight in match_lines]
        # sudo: the app rescores its own groups, the manager only decided who leaves
        records = self.env[self.res_model].sudo().browse(lines.mapped('res_id'))
        values = {data['id']: data for data in records.read(names, load='')}
        first = values.get(lines[0].res_id, {})
        scores = {}
        for line in lines:
            scores[line.id] = 100.0 if line == lines[0] else matching.pair_score(
                match_lines, first, values.get(line.res_id, {})) or 0.0
            line.sudo().score = scores[line.id]  # sudo: same reason
        self.sudo().score = max((score for line_id, score in scores.items() if line_id != lines[0].id), default=0.0)

    def _dismiss_records(self, lines):
        """Set the records of ``lines`` aside from the others of the group and close a group left too small."""
        self.ensure_one()
        self._check_manager()
        self._check_open()
        # sudo: managers decide on groups but the results are written by the app
        Dismissal = self.env['data.quality.duplicate.dismissal'].sudo()
        remaining = self.line_ids - lines
        group_ids = self.line_ids.mapped('res_id')
        known = {
            (d.res_id_a, d.res_id_b) for d in Dismissal.search([
                ('rule_id', '=', self.rule_id.id), ('res_id_a', 'in', group_ids), ('res_id_b', 'in', group_ids)])}
        vals = []
        for line in lines:
            for other in self.line_ids - line:
                pair = tuple(sorted((line.res_id, other.res_id)))
                if pair not in known:
                    known.add(pair)
                    vals.append({
                        'rule_id': self.rule_id.id, 'res_model': self.res_model,
                        'res_id_a': pair[0], 'res_id_b': pair[1], 'user_id': self.env.user.id})
        Dismissal.create(vals)
        group = self.sudo()  # sudo: the app writes the group, the manager only decides
        if len(remaining) < 2:
            group.state = 'dismissed'
        else:
            lines.sudo().unlink()  # sudo: same reason
            self._rescore()

    def action_dismiss(self):
        """Close the group as not duplicates: its records are not grouped again under the rule.

        Requirements: FR-DGQ-0041
        """
        for group in self:
            group._dismiss_records(group.line_ids)
        return True

    def action_merge(self):
        """Open the merge preview of the records of the group.

        Requirements: FR-DGQ-0042
        """
        self.ensure_one()
        self._check_manager()
        self._check_open()
        ids = self.line_ids.sorted(lambda line: (line.sequence, line.id)).mapped('res_id')
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Merge Preview"),
            'res_model': 'data.quality.merge.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_group_id': self.id, 'default_res_model': self.res_model,
                'default_res_ids': ids,
            },
        }
