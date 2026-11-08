from odoo import api, fields, models


class DuplicateGroup(models.Model):
    _name = 'duplicate.group'
    _description = 'Set of Possible Duplicates'
    _order = 'id desc'

    rule_id = fields.Many2one(
        'duplicate.rule', string='Rule', required=True, index=True, ondelete='cascade')
    model_name = fields.Char(
        string='Record type', required=True, index=True,
        help="Contacts or product templates: the kind of records in this set.")
    state = fields.Selection(
        [('open', 'Open'), ('merged', 'Merged'), ('not_duplicate', 'Not a duplicate')],
        required=True, default='open', index=True)
    company_id = fields.Many2one(
        'res.company', index=True, ondelete='cascade',
        help="The company of the records of this set. Empty when every record is shared by all companies.")
    line_ids = fields.One2many('duplicate.group.line', 'group_id', string='Records')
    record_count = fields.Integer(compute='_compute_records', store=True)
    record_names = fields.Char(compute='_compute_records', store=True)
    kept_res_id = fields.Many2oneReference(model_field='model_name', string='Kept record')
    kept_name = fields.Char()
    merge_date = fields.Datetime()
    merge_user_id = fields.Many2one('res.users', string='Merged by', ondelete='set null')

    @api.depends('line_ids.name')
    def _compute_records(self) -> None:
        """Requirements: FR-DRD-0004 (a set shows how many records it holds and their names)."""
        for group in self:
            group.record_count = len(group.line_ids)
            group.record_names = ', '.join(group.line_ids.mapped('name'))

    @api.model
    def _record_matches(self, rule: models.BaseModel, record: models.BaseModel,
                        matches: models.BaseModel) -> models.BaseModel:
        """Requirements: FR-DRD-0004, BR-DRD-0004, BR-DRD-0005.

        Put ``record`` and its matches in open sets of ``rule`` and return the matches that were
        recorded. Records already marked as not duplicates of ``record`` are left out. A shared
        record that matches records of several companies joins one set per company.
        """
        matches -= self._known_apart(rule, record)
        for company, members in self._company_clusters(record, matches):
            self._add_cluster(rule, company, record | members)
        return matches

    @api.model
    def _known_apart(self, rule: models.BaseModel, record: models.BaseModel) -> models.BaseModel:
        """Return the records marked as not duplicates of ``record`` under ``rule``."""
        lines = self.env['duplicate.group.line'].search([
            ('group_id.state', '=', 'not_duplicate'), ('group_id.rule_id', '=', rule.id),
            ('res_model', '=', record._name), ('res_id', '=', record.id)])
        return record.browse(lines.group_id.line_ids.mapped('res_id')) - record

    @api.model
    def _company_clusters(self, record: models.BaseModel, matches: models.BaseModel) -> list:
        """Split the matches so that two companies never share a set (shared records join each)."""
        if not matches:
            return []
        companies = matches.company_id
        if record.company_id or not companies:
            return [(record.company_id, matches)]
        return [(company, matches.filtered(lambda m: m.company_id in (company, companies.browse())))
                for company in companies]

    @api.model
    def _add_cluster(self, rule: models.BaseModel, company: models.BaseModel,
                     records: models.BaseModel) -> None:
        """Add the records to the open set of the rule that holds any of them, else a new set."""
        lines = self.env['duplicate.group.line'].search([
            ('group_id.state', '=', 'open'), ('group_id.rule_id', '=', rule.id),
            ('res_model', '=', records._name), ('res_id', 'in', records.ids)])
        groups = lines.group_id.filtered(lambda g: not (g.company_id and company) or g.company_id == company)
        target = groups[:1] or self.create({
            'rule_id': rule.id, 'model_name': rule.model_name, 'company_id': company.id})
        for other in groups[1:].filtered(lambda g: not (g.company_id and target.company_id)
                                         or g.company_id == target.company_id):
            target._absorb(other)
        if company and not target.company_id:
            target.company_id = company
        present = target.line_ids.mapped('res_id')
        self.env['duplicate.group.line'].create([{
            'group_id': target.id, 'res_model': record._name, 'res_id': record.id,
            'name': record.display_name, 'company_id': record.company_id.id,
        } for record in records.filtered(lambda r: r.id not in present)])

    def _absorb(self, other: models.BaseModel) -> None:
        """Move the records of another set into this one and delete that set."""
        present = self.line_ids.mapped('res_id')
        other.line_ids.filtered(lambda l: l.res_id in present).unlink()
        other.line_ids.write({'group_id': self.id})
        self.company_id = self.company_id or other.company_id
        other.unlink()
