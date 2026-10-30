import { patch } from "@web/core/utils/patch";
import { LoyaltyProgram } from "@pos_loyalty/app/models/loyalty_program";

// Saturday and Sunday in luxon's ISO numbering (BR-LAP-001).
const WEEKEND_ISO_WEEKDAYS = [6, 7];

patch(LoyaltyProgram.prototype, {
    /**
     * Multiply the points earned by a weekend order (FR-LAP-008).
     * mirror of *models/pos_order.py* LoyaltyProgram._get_pos_order_points
     */
    getEarnedPoints(order) {
        const points = super.getEarnedPoints(...arguments);
        if (
            this.program_type !== "loyalty" ||
            order.isRefund ||
            !points ||
            !WEEKEND_ISO_WEEKDAYS.includes(order.date_order?.weekday)
        ) {
            return points;
        }
        // Round down to 2 decimals like the server (FR-LAP-006).
        return Math.floor(points * this.weekend_multiplier * 100 + 1e-9) / 100;
    },
});
