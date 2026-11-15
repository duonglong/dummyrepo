"""Quality scores: the weighted share of failed checks, and the snapshot rows a finished scan stores."""
from ..const import ISSUE_OPEN_STATES, SEVERITY_WEIGHT_DEFAULTS, SEVERITY_WEIGHT_PARAMETER


def severity_weights(env):
    """The weight of a failed check by severity, from the settings or the defaults.

    Requirements: BR-DGQ-0001
    """
    params = env['ir.config_parameter'].sudo()  # sudo: settings are read for any user
    return {
        severity: params.get_int(SEVERITY_WEIGHT_PARAMETER % severity, default)
        for severity, default in SEVERITY_WEIGHT_DEFAULTS.items()}


def score(applicable_weight, failed_weight):
    """100 x (1 - failed / applicable), or None when nothing applies (never 100).

    Requirements: BR-DGQ-0001
    """
    if not applicable_weight:
        return None
    return 100.0 * (1 - failed_weight / applicable_weight)


def snapshot_values(env, scan):
    """Values of the snapshots of a finished scan: per model and company, and per rule inside them.

    Each rule tally counts a record once per rule, whatever the number of fields it checks, and
    records the rule did not apply to are not in it (BR-DGQ-0002). A record of no company is
    counted for the first company of the scan.

    Requirements: FR-DGQ-0031, BR-DGQ-0001, BR-DGQ-0002
    """
    weights = severity_weights(env)
    default_company = scan.company_ids[:1].id
    scan = scan.sudo()  # sudo: the scan's own results are read whatever the requester may see
    models = scan.checkpoint_ids.model_id
    rows = {}  # (model id, company id) -> model figures

    def model_row(model, company_id):
        return rows.setdefault((model.id, company_id), {
            'applicable_weight': 0, 'failed_weight': 0, 'records_scanned': 0, 'records_failing': 0,
            'records_error': 0, 'records_warning': 0, 'rules': {}})

    for model in models:
        if 'company_id' in env[model.model]._fields:
            for company in scan.company_ids:
                model_row(model, company.id)
    for tally in scan.tally_ids:
        company_id = tally.company_id.id or default_company
        if not company_id:
            continue
        row = model_row(tally.model_id, company_id)
        if not tally.rule_id:
            for name in ('records_scanned', 'records_failing', 'records_error', 'records_warning'):
                row[name] += tally[name]
            continue
        weight = weights[tally.severity]
        row['applicable_weight'] += tally.records_applicable * weight
        row['failed_weight'] += tally.records_failed * weight
        figures = row['rules'].setdefault(tally.rule_id.id, {
            'rule_version': tally.rule_version, 'severity': tally.severity, 'applicable_weight': 0,
            'failed_weight': 0, 'records_scanned': 0, 'records_failing': 0, 'records_error': 0,
            'records_warning': 0})
        figures['applicable_weight'] += tally.records_applicable * weight
        figures['failed_weight'] += tally.records_failed * weight
        figures['records_scanned'] += tally.records_applicable
        figures['records_failing'] += tally.records_failed
        figures['records_error'] += tally.records_failed if tally.severity == 'critical' else 0
        figures['records_warning'] += tally.records_failed if tally.severity == 'warning' else 0
    open_issues = open_issue_counts(env, models, default_company)
    values = []
    for (model_id, company_id), row in rows.items():
        common = {
            'scan_id': scan.id, 'model_id': model_id, 'company_id': company_id, 'weights_used': weights,
            'is_illustrative': scan.is_illustrative}
        model_name = env['ir.model'].browse(model_id).model
        counts = open_issues.get((model_name, company_id), {})
        values.append(dict(
            common, **_figures(row), **{
                'issues_open_%s' % severity: counts.get(severity, 0) for severity in weights}))
        for rule_id, figures in row['rules'].items():
            rule_counts = {severity: counts.get((rule_id, severity), 0) for severity in weights}
            values.append(dict(
                common, rule_id=rule_id, rule_version=figures['rule_version'], severity=figures['severity'],
                **_figures(figures), **{'issues_open_%s' % s: n for s, n in rule_counts.items()}))
    return values


def _figures(row):
    """The figures every snapshot row carries, the score being absent when no weight applied."""
    value = score(row['applicable_weight'], row['failed_weight'])
    return {
        'applicable_weight': row['applicable_weight'], 'failed_weight': row['failed_weight'],
        'has_score': value is not None, 'score': value or 0.0,
        'records_scanned': row['records_scanned'], 'records_failing': row['records_failing'],
        'records_error': row['records_error'], 'records_warning': row['records_warning'],
    }


def open_issue_counts(env, models, default_company):
    """Open issues now: {(model name, company id): {severity: n}} and {(rule id, severity): n} per company.

    An issue of a record with no company is counted for the same company as the record's scores
    (the scan's first company, `default_company`), so figures and scores agree. Sample issues of the
    demo data are never counted: a live figure counts live results only.

    Requirements: FR-DGQ-0031, FR-DGQ-0080
    """
    groups = env['data.quality.issue'].sudo()._read_group(  # sudo: counts of the stored issues
        [('res_model', 'in', models.mapped('model')), ('state', 'in', ISSUE_OPEN_STATES),
         ('is_illustrative', '=', False)],
        ['res_model', 'company_id', 'rule_id', 'severity'], ['__count'])
    counts = {}
    for res_model, company, rule, severity, count in groups:
        by_model = counts.setdefault((res_model, company.id or default_company), {})
        by_model[severity] = by_model.get(severity, 0) + count
        by_model[(rule.id, severity)] = by_model.get((rule.id, severity), 0) + count
    return counts
