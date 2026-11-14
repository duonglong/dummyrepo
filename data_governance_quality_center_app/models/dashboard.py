from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..const import DASHBOARD_LOWEST_MODELS, DASHBOARD_RECENT_ISSUES
from ..services import scoring


class DataQualityDashboard(models.AbstractModel):
    """The service that answers the dashboard screen: figures from the latest snapshots.

    Requirements: FR-DGQ-0032, FR-DGQ-0033, NFR-DGQ-0004
    """
    _name = 'data.quality.dashboard'
    _description = 'Data Quality Dashboard'

    @api.model
    def get_figures(self, company_ids=None, date=None):
        """Every figure of the dashboard for the chosen companies, as of the end of a date.

        Companies the user may not access are never used. For each company and model the latest
        snapshot taken on or before the date counts; the overall and per-company scores are
        weighted sums of those figures, not averages of scores.

        Requirements: FR-DGQ-0032, FR-DGQ-0033, BR-DGQ-0001
        """
        if not self.env.user.has_group('data_governance_quality_center_app.group_dgq_viewer'):
            raise AccessError(self.env._("You are not allowed to open the quality dashboard."))
        allowed = self.env.user.company_ids
        companies = allowed.filtered(lambda company: company.id in company_ids) if company_ids else allowed
        snapshots = self._latest_snapshots(companies, date)
        by_model, by_company = {}, {}
        totals = dict.fromkeys(
            ('applicable_weight', 'failed_weight', 'records_scanned', 'records_error', 'records_warning',
             'issues_open_info', 'issues_open_warning', 'issues_open_critical'), 0)
        for snap in snapshots:
            for name in totals:
                totals[name] += snap[name]
            for key, bucket, label in (
                    (snap.model_id.id, by_model, snap.model_id.name),
                    (snap.company_id.id, by_company, snap.company_id.name)):
                row = bucket.setdefault(key, {'id': key, 'name': label, 'applicable_weight': 0, 'failed_weight': 0})
                row['applicable_weight'] += snap.applicable_weight
                row['failed_weight'] += snap.failed_weight
        models_scored = [self._with_score(row) for row in by_model.values()]
        scored = [row for row in models_scored if row['score'] is not None]
        return {
            'has_scan': bool(snapshots) or bool(self.env['data.quality.score.snapshot'].search_count([], limit=1)),
            'can_start_scan': self.env.user.has_group('data_governance_quality_center_app.group_dgq_operator'),
            'companies': [{'id': company.id, 'name': company.name} for company in allowed],
            'score': scoring.score(totals['applicable_weight'], totals['failed_weight']),
            'by_model': sorted(models_scored, key=lambda row: row['name']),
            'by_company': sorted((self._with_score(row) for row in by_company.values()), key=lambda row: row['name']),
            'records_scanned': totals['records_scanned'],
            'records_error': totals['records_error'],
            'records_warning': totals['records_warning'],
            'open_issues': sum(totals['issues_open_%s' % s] for s in ('info', 'warning', 'critical')),
            'critical_issues': totals['issues_open_critical'],
            'issues_by_severity': {s: totals['issues_open_%s' % s] for s in ('info', 'warning', 'critical')},
            'lowest_models': sorted(scored, key=lambda row: (row['score'], row['name']))[:DASHBOARD_LOWEST_MODELS],
            'recent_issues': self._recent_issues(companies),
        }

    @api.model
    def _latest_snapshots(self, companies, date):
        """The latest model-level snapshot of each company and model on or before the date.

        Snapshots are written in date order, so the highest id of a group is its latest.

        Requirements: FR-DGQ-0033
        """
        Snapshot = self.env['data.quality.score.snapshot']
        domain = [('company_id', 'in', companies.ids), ('rule_id', '=', False)]
        if date:
            end = fields.Datetime.add(fields.Datetime.to_datetime(fields.Date.to_date(date)), days=1)
            domain.append(('date', '<', end))
        groups = Snapshot._read_group(domain, ['company_id', 'model_id'], ['id:max'])
        return Snapshot.browse([latest for _company, _model, latest in groups])

    @staticmethod
    def _with_score(row):
        value = scoring.score(row['applicable_weight'], row['failed_weight'])
        return dict(row, score=value, has_score=value is not None)

    @api.model
    def _recent_issues(self, companies):
        issues = self.env['data.quality.issue'].search(
            [('company_id', 'in', [False, *companies.ids])], order='last_found desc, id desc',
            limit=DASHBOARD_RECENT_ISSUES)
        return [{
            'id': issue.id, 'name': issue.name, 'severity': issue.severity, 'state': issue.state,
            'last_found': fields.Datetime.to_string(issue.last_found)} for issue in issues]
