import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

/**
 * Requirements: FR-AM-0014, FR-AM-0016
 * Show the duplicate warning with one Open button per copy the person may open.
 */
export const amDuplicateService = {
    dependencies: ["bus_service", "notification", "action"],

    start(env, { bus_service, notification, action }) {
        bus_service.subscribe("attachment_management/duplicate", ({ message, copies }) => {
            const buttons = copies.map((copy) => ({
                name: _t("Open %s", copy.name),
                primary: false,
                onClick: () =>
                    action.doAction({
                        type: "ir.actions.act_window",
                        res_model: copy.res_model,
                        res_id: copy.res_id,
                        views: [[false, "form"]],
                        target: "current",
                    }),
            }));
            notification.add(message, { type: "warning", sticky: false, buttons });
        });
    },
};

registry.category("services").add("amDuplicate", amDuplicateService);
