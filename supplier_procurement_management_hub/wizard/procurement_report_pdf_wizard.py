import ast

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain

# Models besides the report views that the dialog prints.
PRINTABLE_MODELS = ('procurement.contract',)
REPORT_PREFIX = 'procurement.report.'
PRINT_TIME_FORMAT = '%Y-%m-%d %H:%M'


class ProcurementReportPdfWizard(models.TransientModel):
    """Prints a report as PDF with the filters, the user and the time of printing.

    Requirements: FR-SPM-0104, FR-SPM-0105
    The figures are read again with the filters and the rights of the user who prints, so the
    PDF holds what the screen shows to that user and never a figure of another company.
    """
    _name = 'procurement.report.pdf.wizard'
    _description = 'Print a Report'

    report_model = fields.Char(required=True, readonly=True)
    domain = fields.Char(default='[]', readonly=True)
    group_by = fields.Char(help="A field of the report to group the figures by, such as partner_id or date:month.")
    filter_text = fields.Text(
        string="Filters", compute='_compute_filter_text',
        help="The filters of the figures, read from the filter itself so the printout cannot be mistaken "
             "for another scope.")
    filter_description = fields.Text(string="Note", help="A note printed beside the filters.")

    @api.model
    def default_get(self, fields_list):
        """Take the report and the filter from the list the dialog was opened on.

        Requirements: FR-SPM-0104
        The selected rows narrow the filter, so the printout holds what the export of the selection holds.
        """
        values = super().default_get(fields_list)
        model_name = self.env.context.get('active_model')
        if model_name and 'report_model' in fields_list:
            values['report_model'] = model_name
            domain = Domain(self.env.context.get('active_domain') or [])
            if self.env.context.get('active_ids'):
                domain &= Domain('id', 'in', self.env.context['active_ids'])
            values['domain'] = repr(list(domain))
        return values

    @api.depends('report_model', 'domain')
    def _compute_filter_text(self):
        for wizard in self:
            try:
                wizard.filter_text = wizard._describe_domain(wizard._spm_domain())
            except UserError:
                wizard.filter_text = False

    def _describe_domain(self, domain):
        """The conditions of a domain in words: field labels, operators and the names of the records.

        Requirements: FR-SPM-0104
        """
        if self.report_model not in self.env or not domain:
            return self.env._("No filter")
        model = self.env[self.report_model]
        tokens = iter(domain)
        parts = []
        try:
            while (part := self._describe_term(model, tokens)) is not None:
                parts.append(part)
        except (StopIteration, TypeError, ValueError):
            return str(domain)
        return ' and '.join(parts)

    def _describe_term(self, model, tokens):
        token = next(tokens, None)
        if token is None:
            return None
        if token == '!':
            return self.env._("not (%s)", self._describe_term(model, tokens))
        if token in ('&', '|'):
            left, right = self._describe_term(model, tokens), self._describe_term(model, tokens)
            return f"({left} {self.env._('and') if token == '&' else self.env._('or')} {right})"
        name, operator, value = token
        field = model._fields.get(name)
        if field is None:
            return f"{name} {operator} {value}"
        if name == 'id':
            return self.env._("%s selected records", len(value) if isinstance(value, (list, tuple, set)) else 1)
        return f"{field._description_string(self.env)} {operator} {self._describe_value(field, value)}"

    def _describe_value(self, field, value):
        """A value as a person reads it: the names of records and the labels of selections."""
        values = list(value) if isinstance(value, (list, tuple, set)) else [value]
        if field.relational and all(isinstance(item, int) for item in values):
            try:
                values = self.env[field.comodel_name].browse(values).exists().mapped('display_name')
            except AccessError:
                return self.env._("%s records", len(values))
        elif field.type == 'selection':
            labels = dict(field._description_selection(self.env))
            values = [labels.get(item, item) for item in values]
        return ', '.join(str(item) for item in values)

    def spm_filter_text(self):
        """The filters of the printout, with the note beside them."""
        self.ensure_one()
        return '; '.join(part for part in (self.filter_text, self.filter_description) if part)

    def _spm_model(self):
        """The report model, only a printable one the user may read."""
        self.ensure_one()
        name = self.report_model
        if name not in self.env or not (name.startswith(REPORT_PREFIX) or name in PRINTABLE_MODELS):
            raise UserError(self.env._("This is not a report that can be printed."))
        model = self.env[name]
        model.check_access('read')
        return model

    def _spm_domain(self):
        """The filter as a domain, refused when it is not a plain list of conditions."""
        try:
            return list(Domain(ast.literal_eval(self.domain or '[]')))
        except (ValueError, SyntaxError, TypeError):
            raise UserError(self.env._("The filter of the report is not valid.")) from None

    def _spm_groups(self, model):
        """The grouping of the figures, refused when the report cannot be grouped that way.

        The savings of a report that has baselines are always split by baseline kind, so no
        printed savings figure stands without its kind.
        """
        group = (self.group_by or '').strip()
        groups = [group] if group else []
        if group:
            try:
                model._read_group([], [group], ['__count'], limit=1)
            except (ValueError, AccessError):
                raise UserError(self.env._("The report cannot be grouped by %s.", group)) from None
        if 'baseline_kind' in model._fields and group != 'baseline_kind':
            groups.append('baseline_kind')
        return groups

    def action_print(self):
        """Return the action that prints the report.

        Requirements: FR-SPM-0104
        """
        model = self._spm_model()
        self._spm_domain()
        self._spm_groups(model)
        return self.env.ref('supplier_procurement_management_hub.action_report_spm_report').report_action(self)

    def spm_print_time(self):
        """The time of printing in the user's time zone."""
        return fields.Datetime.context_timestamp(self, fields.Datetime.now()).strftime(PRINT_TIME_FORMAT)

    def _spm_measures(self, model):
        """The summed or averaged figures of the report the user may read."""
        readable = model.fields_get()
        return [
            (name, field) for name, field in model._fields.items()
            if name in readable and field.store and field.aggregator in ('sum', 'avg')
            and field.type in ('integer', 'float', 'monetary') and name != 'id']

    @staticmethod
    def _spm_format(field, value):
        if value is None or value is False:
            return '-'
        if field.type == 'integer':
            return f"{value:,.0f}"
        return f"{value:,.2f}"

    def _spm_key_label(self, model, spec, key):
        """The words of one group key: a record's name, a selection's label or the date."""
        field = model._fields[spec.split(':')[0]]
        if key in (None, False):
            return self.env._("Undefined")
        if hasattr(key, 'display_name'):
            return key.display_name
        if field.type == 'selection':
            return dict(field._description_selection(self.env)).get(key, str(key))
        return str(key)

    def _spm_group_title(self, model, groups):
        """The heading of the first column: the labels of the fields the figures are grouped by."""
        titles = []
        for spec in groups:
            name, _sep, granularity = spec.partition(':')
            label = model._fields[name]._description_string(self.env)
            titles.append(f"{label} ({granularity})" if granularity else label)
        return ' / '.join(titles) or self.env._("Group")

    def spm_figures(self):
        """Return the column labels and the rows (the groups, then the totals) of the printout.

        Requirements: FR-SPM-0104, FR-SPM-0105
        Read with the rights of the user who prints: the record rules of their companies apply.
        A report with baselines prints one total per baseline kind, never a mixed one.
        """
        self.ensure_one()
        model = self._spm_model()
        domain = self._spm_domain()
        measures = self._spm_measures(model)
        groups = self._spm_groups(model)
        aggregates = [f"{name}:{field.aggregator}" for name, field in measures] + ['__count']
        labels = [self._spm_group_title(model, groups), self.env._("Records")] + [
            field._description_string(self.env) for _name, field in measures]

        def row(label, values):
            cells = [f"{values[-1]:,.0f}"] + [
                self._spm_format(field, value) for (_name, field), value in zip(measures, values)]
            return {'label': label, 'cells': cells}

        def key_label(key_values):
            return ' / '.join(self._spm_key_label(model, spec, key) for spec, key in zip(groups, key_values))

        rows = [
            row(key_label(key_row[:len(groups)]), key_row[len(groups):])
            for key_row in (model._read_group(domain, groups, aggregates) if groups else [])]
        total_by = ['baseline_kind'] if 'baseline_kind' in model._fields else []
        for key_row in model._read_group(domain, total_by, aggregates):
            suffix = (self._spm_key_label(model, 'baseline_kind', key_row[0]),) if total_by else ()
            rows.append(row(' - '.join((self.env._("Total"),) + suffix), key_row[len(total_by):]))
        return labels, rows
