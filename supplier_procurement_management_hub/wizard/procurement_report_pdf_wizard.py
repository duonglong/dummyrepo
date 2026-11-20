import ast

from odoo import api, fields, models
from odoo.exceptions import UserError
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
    filter_description = fields.Text(
        string="Filters", help="The filters the printout states, so it cannot be mistaken for another scope.")

    @api.model
    def default_get(self, fields_list):
        """Take the report and the filter from the list the dialog was opened on."""
        values = super().default_get(fields_list)
        model_name = self.env.context.get('active_model')
        if model_name and 'report_model' in fields_list:
            values['report_model'] = model_name
            domain = self.env.context.get('active_domain')
            if domain is None and self.env.context.get('active_ids'):
                domain = [('id', 'in', self.env.context['active_ids'])]
            values['domain'] = repr(domain or [])
            if 'filter_description' in fields_list:
                values['filter_description'] = self._describe_domain(model_name, domain or [])
        return values

    @api.model
    def _describe_domain(self, model_name, domain):
        """The conditions of a domain in words, with the field labels."""
        fields_info = self.env[model_name].fields_get() if model_name in self.env else {}
        parts = [
            f"{fields_info.get(cond[0], {}).get('string', cond[0])} {cond[1]} {cond[2]}"
            for cond in domain if isinstance(cond, (tuple, list)) and len(cond) == 3]
        return '; '.join(parts) or self.env._("No filter")

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

    def action_print(self):
        """Return the action that prints the report.

        Requirements: FR-SPM-0104
        """
        self._spm_model()
        self._spm_domain()
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
        if field.type == 'integer':
            return f"{value or 0:,.0f}"
        return f"{value or 0.0:,.2f}"

    def spm_figures(self):
        """Return the column labels and the rows (the groups, then the total) of the printout.

        Requirements: FR-SPM-0104, FR-SPM-0105
        Read with the rights of the user who prints: the record rules of their companies apply.
        """
        self.ensure_one()
        model = self._spm_model()
        domain = self._spm_domain()
        measures = self._spm_measures(model)
        rates = [rate for rate in getattr(model, '_spm_rates', ())
                 if all(name in dict(measures) for name in rate[1:3])]
        aggregates = [f"{name}:{field.aggregator}" for name, field in measures] + ['__count']
        group = (self.group_by or '').strip()
        if group and group.split(':')[0] not in model._fields:
            raise UserError(self.env._("The report cannot be grouped by %s.", group))
        labels = [self.env._("Records")] + [
            field._description_string(self.env) for _name, field in measures] + [str(rate[0]) for rate in rates]

        def row(label, values):
            figures = dict(zip([name for name, _field in measures], values))
            cells = [f"{values[-1]:,.0f}"] + [self._spm_format(field, figures[name]) for name, field in measures]
            for _label, numerator, denominator, factor in rates:
                total = figures[denominator]
                cells.append(f"{factor * figures[numerator] / total:,.2f}" if total else '-')
            return {'label': label, 'cells': cells}

        rows = []
        if group:
            for key_row in model._read_group(domain, [group], aggregates):
                key = key_row[0]
                label = key.display_name if hasattr(key, 'display_name') else (
                    str(key) if key not in (None, False) else self.env._("Undefined"))
                rows.append(row(label, key_row[1:]))
        (totals,) = model._read_group(domain, [], aggregates)
        rows.append(row(self.env._("Total"), totals))
        return labels, rows
