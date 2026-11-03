import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PosLoyalty from "@pos_loyalty/../tests/tours/utils/pos_loyalty_util";
import { registry } from "@web/core/registry";

function setBrowserDate(isoDate) {
    return {
        content: `Pretend it is ${isoDate}`,
        trigger: "body",
        run: () => {
            const { DateTime, Settings } = luxon;
            const fixed = DateTime.fromISO(isoDate, { zone: "utc" }).toMillis();
            Settings.now = () => fixed;
            posmodel.getOrder().date_order = DateTime.now();
        },
    };
}

// the order keeps its opening moment, only the clock moves on (the order is paid later)
function moveClockTo(isoDate) {
    return {
        content: `Clock moves on to ${isoDate}`,
        trigger: "body",
        run: () => {
            const { DateTime, Settings } = luxon;
            const fixed = DateTime.fromISO(isoDate, { zone: "utc" }).toMillis();
            Settings.now = () => fixed;
        },
    };
}

function buildOrder(isoDate, expectedPoints) {
    return [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        setBrowserDate(isoDate),
        ProductScreen.addOrderline("Whiteboard Pen", "1", "40"),
        ProductScreen.clickPartnerButton(),
        ProductScreen.clickCustomer("Weekend screen customer"),
        PosLoyalty.pointsAwardedAre(`+${expectedPoints}`),
    ];
}

registry.category("web_tour.tours").add("WeekendPointsSaturdayTour", {
    steps: () =>
        [
            ...buildOrder("2026-11-07T10:00:00", 80),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Bank"),
            PaymentScreen.clickValidate(),
        ].flat(),
});

registry.category("web_tour.tours").add("WeekendPointsWeekdayTour", {
    steps: () => buildOrder("2026-11-03T10:00:00", 40).flat(),
});

// opened Friday 23:50 Brussels (22:50 UTC), points read Saturday 00:10 Brussels (23:10 UTC)
registry.category("web_tour.tours").add("WeekendPointsMidnightTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            setBrowserDate("2026-11-06T22:50:00"),
            ProductScreen.addOrderline("Whiteboard Pen", "1", "40"),
            moveClockTo("2026-11-06T23:10:00"),
            ProductScreen.clickPartnerButton(),
            ProductScreen.clickCustomer("Weekend screen customer"),
            PosLoyalty.pointsAwardedAre("+80"),
        ].flat(),
});
