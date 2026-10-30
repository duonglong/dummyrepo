/* global posmodel */
import * as PosLoyalty from "@pos_loyalty/../tests/tours/utils/pos_loyalty_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import { registry } from "@web/core/registry";

// FR-LAP-008 / SC-LAP-008-1: a 100.00 order dated on a Saturday shows 200.00
// points to earn with Weekend Multiplier 2.00.
registry.category("web_tour.tours").add("pos_weekend_points_tour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.clickPartnerButton(),
            ProductScreen.clickCustomer("Weekend POS Customer"),
            ProductScreen.addOrderline("Weekend POS Product", "1"),
            {
                content: "Date the order on a Saturday",
                trigger: ".loyalty-points-won",
                run: () => {
                    // 2026-10-24 is a Saturday.
                    posmodel.getOrder().date_order = posmodel.getOrder().date_order.set({
                        year: 2026,
                        month: 10,
                        day: 24,
                        hour: 12,
                    });
                },
            },
            PosLoyalty.pointsAwardedAre("+200"),
        ].flat(),
});
