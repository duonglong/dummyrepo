import pytz

from odoo import api, fields, models
from odoo.fields import Datetime


class LoyaltyProgram(models.Model):
    _inherit = 'loyalty.program'

    weekend_double_points = fields.Boolean(
        string='Double points at the weekend',
        help='Purchases made on Saturday or Sunday (company timezone) earn twice the points.',
        compute='_compute_weekend_double_points',
        store=True,
        readonly=False,
        copy=True,
        default=False,
    )

    @api.depends('program_type')
    def _compute_weekend_double_points(self):
        """Requirements: FR-DLP-001, BR-DLP-003.

        Only Loyalty Cards programs can double points: any other type resets the option.
        """
        for program in self:
            if program.program_type != 'loyalty':
                program.weekend_double_points = False

    def _get_weekend_factor(self, order_date, company):
        """Requirements: BR-DLP-001, BR-DLP-002, FR-DLP-004.

        :param order_date: datetime in UTC (naive, as stored) of the purchase
        :param company: res.company whose timezone decides the day
        :return: 2 for a weekend purchase on an opted-in loyalty card program, else 1
        """
        self.ensure_one()
        if self.program_type != 'loyalty' or not self.weekend_double_points:
            return 1
        order_date = Datetime.to_datetime(order_date) or fields.Datetime.now()
        tz = pytz.timezone(company.tz or 'UTC')
        local = pytz.utc.localize(order_date).astimezone(tz)
        return 2 if local.weekday() >= 5 else 1

    def _get_pos_order_points(self, order, lines=None):
        """Requirements: FR-DLP-003, FR-DLP-006, BR-DLP-001, NFR-REL-001.

        Double the points earned by a point of sale order placed on a weekend. A refund
        reverses the points recorded in the history, so only earning is doubled here.
        """
        points = super()._get_pos_order_points(order, lines)
        return points * self._get_weekend_factor(order.date_order, order.company_id)

    @api.model
    def _load_pos_data_fields(self, config):
        """Requirements: FR-DLP-007: send the weekend option to the point of sale."""
        return super()._load_pos_data_fields(config) + ['weekend_double_points']
