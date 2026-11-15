from odoo import fields, models

OPERATOR_GROUP = 'data_governance_quality_center_app.group_dgq_operator'


class DataQualityTeam(models.Model):
    """A group of people responsible for an area of data, with a leader and a chosen member per model.

    Requirements: FR-DGQ-0065
    """
    _name = 'data.quality.team'
    _description = 'Data Quality Team'
    _order = 'name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', ondelete='set null')
    leader_id = fields.Many2one('res.users', string='Leader', ondelete='set null')
    member_ids = fields.Many2many('res.users', string='Members')
    assignment_ids = fields.One2many('data.quality.team.assignment', 'team_id', string='Who Gets What')

    def _dgq_assignee(self, model_name):
        """The user who gets the new issues of a model: the member chosen for it, else the leader.

        Requirements: FR-DGQ-0065
        """
        self.ensure_one()
        chosen = self.assignment_ids.filtered(lambda line: line.model_id.model == model_name).user_id[:1]
        return chosen or self.leader_id

    def _dgq_can_take(self, user, issue):
        """Whether the user may be assigned the issue: operator access and a view of its company."""
        return bool(user) and user.has_group(OPERATOR_GROUP) and (
            not issue.company_id or issue.company_id in user.company_ids)


class DataQualityTeamAssignment(models.Model):
    """The member of a team who gets the new issues of one model.

    Requirements: FR-DGQ-0065
    """
    _name = 'data.quality.team.assignment'
    _description = 'Data Quality Team Assignment'

    team_id = fields.Many2one('data.quality.team', required=True, ondelete='cascade', index=True)
    model_id = fields.Many2one('ir.model', string='Model', required=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', string='Assigned To', required=True, ondelete='cascade')

    _team_model_unique = models.Constraint(
        'UNIQUE (team_id, model_id)', 'A team names one person for each model.')
