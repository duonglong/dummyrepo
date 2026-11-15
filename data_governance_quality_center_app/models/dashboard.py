from datetime import datetime, time, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..const import (
    DASHBOARD_LOWEST_MODELS, DASHBOARD_RECENT_ISSUES, DASHBOARD_WIDGET_STEP,
    DRILL_DOWN_RECORD_LIMIT, ISSUE_OPEN_STATES, SEVERITY_WEIGHT_DEFAULTS, TREND_PERIODS)
from ..services import health, scoring, trend


class DataQualityDashboard(models.AbstractModel):
    """The service that answers the dashboard screen: figures from the latest snapshots.

    Requirements: FR-DGQ-0032, FR-DGQ-0033, FR-DGQ-0034, FR-DGQ-0035, FR-DGQ-0036, FR-DGQ-0037, NFR-DGQ-0004
    """
    _name = 'data.quality.dashboard'
    _description = 'Data Quality Dashboard'

    @api.model
    def get_figures(self, company_ids=None, date=None):
        """Every figure of the dashboard for the chosen companies, as of the end of a date.

        Only the companies the user works with (the active ones of the company switcher, the set
        the record rules use) are offered and used. For each company and model the latest
        snapshot taken on or before the date counts; the overall and per-company scores are
        weighted sums of those figures, not averages of scores.

        Requirements: FR-DGQ-0032, FR-DGQ-0033, BR-DGQ-0001
        """
        if not self.env.user.has_group('data_governance_quality_center_app.group_dgq_viewer'):
            raise AccessError(self.env._("You are not allowed to open the quality dashboard."))
        allowed = self.env.companies
        companies = self._chosen_companies(company_ids)
        end = self._end_of_day(date)
        snapshots = self._latest_snapshots(companies, end)
        by_model, by_company = {}, {}
        open_fields = ['issues_open_%s' % severity for severity in SEVERITY_WEIGHT_DEFAULTS]
        totals = dict.fromkeys(
            ['applicable_weight', 'failed_weight', 'records_scanned', 'records_error', 'records_warning', *open_fields], 0)
        for snap in snapshots:
            model = snap.model_id.sudo()  # sudo: a viewer may not read ir.model; the name of a scored model is no secret
            for name in totals:
                totals[name] += snap[name]
            for key, bucket, label in (
                    (model.id, by_model, model.name),
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
            'open_issues': sum(totals[name] for name in open_fields),
            'critical_issues': totals['issues_open_critical'],
            'issues_by_severity': {severity: totals['issues_open_%s' % severity] for severity in SEVERITY_WEIGHT_DEFAULTS},
            'lowest_models': sorted(scored, key=lambda row: (row['score'], row['name']))[:DASHBOARD_LOWEST_MODELS],
            'recent_issues': self._recent_issues(companies, end),
        }

    @api.model
    def _chosen_companies(self, company_ids):
        """The chosen companies among the ones the user works with; all of them when none is chosen.

        Requirements: FR-DGQ-0033
        """
        allowed = self.env.companies
        return allowed.filtered(lambda company: company.id in company_ids) if company_ids else allowed

    @api.model
    def _end_of_day(self, date):
        """The first instant after the chosen day in the user's time zone, in UTC, or None without a date.

        Requirements: FR-DGQ-0033
        """
        if not date:
            return None
        try:
            day = fields.Date.to_date(date)
        except ValueError:
            raise UserError(self.env._("The date %s is not valid.", date)) from None
        local_start = datetime.combine(day + timedelta(days=1), time.min, tzinfo=self.env.tz)
        return local_start.astimezone(timezone.utc).replace(tzinfo=None)

    @api.model
    def _latest_snapshots(self, companies, end):
        """The latest model-level snapshot of each company and model before the end instant.

        Snapshots are written in date order, so the highest id of a group is its latest.

        Requirements: FR-DGQ-0033
        """
        Snapshot = self.env['data.quality.score.snapshot']
        domain = [('company_id', 'in', companies.ids), ('rule_id', '=', False), ('severity', '=', False)]
        if end:
            domain.append(('date', '<', end))
        groups = Snapshot._read_group(domain, ['company_id', 'model_id'], ['id:max'])
        return Snapshot.browse([latest for _company, _model, latest in groups])

    @staticmethod
    def _with_score(row):
        value = scoring.score(row['applicable_weight'], row['failed_weight'])
        return dict(row, score=value, has_score=value is not None)

    @api.model
    def _recent_issues(self, companies, end):
        domain = [('company_id', 'in', [False, *companies.ids])]
        if end:
            domain.append(('last_found', '<', end))
        issues = self.env['data.quality.issue'].search(
            domain, order='last_found desc, id desc',
            limit=DASHBOARD_RECENT_ISSUES)
        return [{
            'id': issue.id, 'name': issue.name, 'severity': issue.severity, 'state': issue.state,
            'last_found': fields.Datetime.to_string(issue.last_found)} for issue in issues]

    @api.model
    def _check_viewer(self):
        if not self.env.user.has_group('data_governance_quality_center_app.group_dgq_viewer'):
            raise AccessError(self.env._("You are not allowed to open the quality dashboard."))

    @api.model
    def _chosen_companies(self, company_ids):
        allowed = self.env.companies
        return allowed.filtered(lambda company: company.id in company_ids) if company_ids else allowed

    @api.model
    def get_trend(self, company_ids=None, model_id=None, period='week', date_from=None, date_to=None):
        """The score per day, week or month for the chosen companies and model, from the last snapshot of each period.

        Requirements: FR-DGQ-0034
        """
        self._check_viewer()
        if period not in TREND_PERIODS:
            raise UserError(self.env._("The period must be day, week or month."))
        domain = [('company_id', 'in', self._chosen_companies(company_ids).ids), ('rule_id', '=', False),
                  ('severity', '=', False)]
        if model_id:
            domain.append(('model_id', '=', model_id))
        if date_from:
            domain.append(('date', '>=', self._end_of_day(fields.Date.subtract(fields.Date.to_date(date_from), days=1))))
        if date_to:
            domain.append(('date', '<', self._end_of_day(date_to)))
        snapshots = self.env['data.quality.score.snapshot'].search(domain)
        return {'period': period, 'points': trend.trend_points(self.env, snapshots, period)}

    @api.model
    def explain_change(self, snapshot_id, previous_id=None):
        """The causes of the score change from the previous snapshot of the same model and company to this one.

        Without ``previous_id`` the snapshot before it is used. The causes come biggest effect first, each with its points.

        Requirements: FR-DGQ-0035
        """
        self._check_viewer()
        Snapshot = self.env['data.quality.score.snapshot']
        after = Snapshot.browse(snapshot_id).exists()
        after.check_access('read')
        if previous_id:
            before = Snapshot.browse(previous_id).exists()
        else:
            before = Snapshot.search([
                ('company_id', '=', after.company_id.id), ('model_id', '=', after.model_id.id), ('rule_id', '=', False),
                ('severity', '=', False), '|', ('date', '<', after.date), '&', ('date', '=', after.date), ('id', '<', after.id),
            ], limit=1)
        if not before:
            raise UserError(self.env._("There is no earlier snapshot to compare with."))
        before.check_access('read')
        if (before.company_id, before.model_id) != (after.company_id, after.model_id):
            raise UserError(self.env._("Both snapshots must be of the same model and company."))
        return dict(trend.explain_change(self.env, before, after), model=after.model_id.name, company=after.company_id.name)

    @api.model
    def drill_down(self, figure, company_ids=None, model_id=None, date=None, severity=None):
        """The window action of the issues, or of the records, behind a figure, filtered as the figure is.

        Figures are ``open_issues``, ``critical_issues``, ``severity`` (with ``severity``), ``model`` (with
        ``model_id``) and ``records_error`` or ``records_warning``, which open the records of the model when
        one is given. The lists respect the user's own access.

        Requirements: FR-DGQ-0036
        """
        self._check_viewer()
        companies = self._chosen_companies(company_ids)
        end = self._end_of_day(date)
        domain = [('state', 'in', ISSUE_OPEN_STATES), ('company_id', 'in', [False, *companies.ids])]
        if end:
            domain.append(('first_found', '<', end))
        name = self.env._("Open issues")
        severity = {'critical_issues': 'critical', 'records_error': 'critical', 'records_warning': 'warning'}.get(
            figure, severity)
        if figure not in ('open_issues', 'severity', 'model', 'critical_issues', 'records_error', 'records_warning'):
            raise UserError(self.env._("Unknown figure."))
        if severity:
            domain.append(('severity', '=', severity))
        if model_id:
            domain.append(('res_model', '=', self.env['ir.model'].browse(model_id).model))
        if figure in ('records_error', 'records_warning') and model_id:
            groups = self.env['data.quality.issue']._read_group(
                domain, ['res_id'], order='res_id', limit=DRILL_DOWN_RECORD_LIMIT + 1)
            model = self.env['ir.model'].browse(model_id)
            cut = len(groups) > DRILL_DOWN_RECORD_LIMIT
            return {
                'type': 'ir.actions.act_window',
                'name': self.env._("%(model)s (first %(limit)s)", model=model.name, limit=DRILL_DOWN_RECORD_LIMIT)
                if cut else model.name, 'res_model': model.model,
                'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')],
                'domain': [('id', 'in', [res_id for (res_id,) in groups[:DRILL_DOWN_RECORD_LIMIT]])],
            }
        return {
            'type': 'ir.actions.act_window', 'name': name, 'res_model': 'data.quality.issue',
            'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')], 'domain': domain,
        }

    @api.model
    def _widgets(self):
        """The widgets of the dashboard in their default order, as (key, translated label)."""
        return self.env['data.quality.dashboard.layout']._fields['widget']._description_selection(self.env)

    @api.model
    def get_layout(self):
        """The widgets of the user's dashboard in order, each with its visibility.

        A widget the user never touched keeps its default place and is shown.

        Requirements: FR-DGQ-0037
        """
        self._check_viewer()
        lines = {line.widget: line for line in self.env['data.quality.dashboard.layout'].search(
            [('user_id', '=', self.env.uid)])}
        layout = []
        for index, (widget, label) in enumerate(self._widgets()):
            line = lines.get(widget)
            layout.append({
                'widget': widget, 'label': label,
                'sequence': line.sequence if line else index * DASHBOARD_WIDGET_STEP,
                'visible': line.visible if line else True, '_default': index})
        layout.sort(key=lambda item: (item['sequence'], item['_default']))
        return [{key: value for key, value in item.items() if key != '_default'} for item in layout]

    @api.model
    def save_layout(self, widgets):
        """Keep the user's order and visibility; ``widgets`` lists the widget keys top to bottom with their visibility.

        Requirements: FR-DGQ-0037
        """
        self._check_viewer()
        Layout = self.env['data.quality.dashboard.layout']
        known = {widget for widget, _label in self._widgets()}
        lines = {line.widget: line for line in Layout.search([('user_id', '=', self.env.uid)])}
        for index, item in enumerate(widgets):
            if item['widget'] not in known:
                raise UserError(self.env._("Unknown widget."))
            values = {'sequence': index * DASHBOARD_WIDGET_STEP, 'visible': bool(item['visible'])}
            if item['widget'] in lines:
                lines[item['widget']].write(values)
            else:
                Layout.create(dict(values, widget=item['widget']))
        return self.get_layout()

    @api.model
    def reset_layout(self):
        """Delete the user's lines, so the default layout shows again.

        Requirements: FR-DGQ-0037
        """
        self._check_viewer()
        self.env['data.quality.dashboard.layout'].search([('user_id', '=', self.env.uid)]).unlink()
        return self.get_layout()

    @api.model
    def get_health(self):
        """The figures of the health page (administrators only).

        Requirements: FR-DGQ-0079
        """
        if not self.env.user.has_group('data_governance_quality_center_app.group_dgq_admin'):
            raise AccessError(self.env._("The health page is for administrators."))
        return health.health(self.env)
