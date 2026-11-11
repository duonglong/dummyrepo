import { registry } from "@web/core/registry";

const COPIES = [1, 2, 3, 4, 5].map((index) => ({
    name: `contract_copy_${index}.pdf`,
    res_model: "res.partner",
    res_id: index,
}));
const MESSAGE = `This file is already stored: ${COPIES.map(
    (copy) => `${copy.name} (Contact ${copy.res_id})`
).join(", ")}. Your upload was kept.`;

/**
 * Requirements: FR-AM-0016, UX-AM-003
 * Shows the warning as the bus delivers it and reads what the browser drew.
 */
registry.category("web_tour.tours").add("am_duplicate_warning_tour", {
    steps: () => [
        {
            trigger: ".o_action_manager",
            run() {
                const { bus_service } = odoo.__WOWL_DEBUG__.root.env.services;
                bus_service.notificationBus.trigger("attachment_management/duplicate", {
                    id: 1,
                    payload: { message: MESSAGE, copies: COPIES },
                });
            },
        },
        {
            trigger: ".o_notification .o_notification_buttons button",
            run() {
                const content = document.querySelector(".o_notification_content");
                const buttons = document.querySelector(".o_notification_buttons");
                const fails = [];
                if (content.scrollHeight > content.clientHeight + 1) {
                    fails.push(`message cut: ${content.scrollHeight} > ${content.clientHeight}`);
                }
                if (buttons.getBoundingClientRect().top < content.getBoundingClientRect().bottom - 1) {
                    fails.push("buttons sit beside the message");
                }
                if (fails.length) {
                    throw new Error(fails.join("; "));
                }
                document.body.classList.add("am_duplicate_tour_checked");
            },
        },
        { trigger: "body.am_duplicate_tour_checked" },
    ],
});
