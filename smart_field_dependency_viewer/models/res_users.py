import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

DEFAULT_DEPTH_LIMIT = 5
_WHOLE_NUMBER = re.compile(r"[0-9]+")


class ResUsers(models.Model):
    _inherit = "res.users"

    sfd_depth_limit = fields.Integer(
        string="Dependency depth limit",
        default=0,
        groups="base.group_system",
        help="Levels a dependency chain may go before it is flagged as too deep. "
        "0 means not set: the default applies.",
    )

    @api.constrains("sfd_depth_limit")
    def _check_sfd_depth_limit(self) -> None:
        """Refuse a stored limit below 1 other than 0 (not set).

        Requirements: FR-SFD-0013
        """
        if any(user.sfd_depth_limit < 0 for user in self):
            raise ValidationError(self._sfd_invalid_limit_message())

    def sfd_set_depth_limit(self, value: int | str) -> int:
        """Store the current user's own depth limit.

        Accepts a whole number of at least 1 (an int or a string of digits);
        anything else is refused and the previous limit is kept.

        Requirements: FR-SFD-0010, FR-SFD-0013
        """
        self.ensure_one()
        if not self.env.is_system() or self != self.env.user:
            raise AccessError(_("You can only set your own depth limit."))
        limit = self._sfd_parse_depth_limit(value)
        self.sfd_depth_limit = limit
        return limit

    def _sfd_effective_depth_limit(self) -> int:
        """Return the person's limit, or the default when none is set.

        Requirements: BR-SFD-0004, FR-SFD-0010
        """
        self.ensure_one()
        return self.sfd_depth_limit if self.sfd_depth_limit >= 1 else DEFAULT_DEPTH_LIMIT

    @api.model
    def _sfd_invalid_limit_message(self) -> str:
        return _("The depth limit must be a whole number of at least 1.")

    @api.model
    def _sfd_parse_depth_limit(self, value: int | str) -> int:
        """Return value as a whole number of at least 1, or raise ValidationError."""
        if isinstance(value, str) and _WHOLE_NUMBER.fullmatch(value.strip()):
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValidationError(self._sfd_invalid_limit_message())
        return value
