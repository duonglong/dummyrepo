import { LoyaltyProgram } from "@pos_loyalty/app/models/loyalty_program";
import { patch } from "@web/core/utils/patch";

// Saturday and Sunday in luxon's ISO numbering
const WEEKEND_FIRST_WEEKDAY = 6;
const WEEKEND_MULTIPLIER = 2;

patch(LoyaltyProgram.prototype, {
    /**
     * Requirements: FR-DLP-007, NFR-REL-001
     * mirror of *models/loyalty_program.py* LoyaltyProgram._get_weekend_factor
     * @param {object} order
     * @returns {number} 2 on a weekend (company timezone) for an opted-in loyalty card program, else 1
     */
    getWeekendFactor(order) {
        if (this.program_type !== "loyalty" || !this.weekend_double_points) {
            return 1;
        }
        const tz = order.company?.tz;
        const date = tz ? order.date_order.setZone(tz) : order.date_order.toUTC();
        return date.weekday >= WEEKEND_FIRST_WEEKDAY ? WEEKEND_MULTIPLIER : 1;
    },
    // mirror of *models/loyalty_program_pos.py* LoyaltyProgram._get_pos_order_points
    getEarnedPoints(order) {
        const points = super.getEarnedPoints(...arguments);
        if (order.isRefund && !this.is_payment_program) {
            // a refund reverses the points recorded for the original order, already doubled
            return points;
        }
        return points * this.getWeekendFactor(order);
    },
});
