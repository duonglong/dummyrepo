import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

export class DgqHealth extends Component {
    static template = "data_governance_quality_center_app.Health";
    props = useProps({ ...standardActionServiceProps });

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ health: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.health = await this.orm.call("data.quality.dashboard", "get_health", []);
    }

    openRecord(model, id) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
        });
    }

    formatDate(value) {
        return value ? formatDateTime(deserializeDateTime(value)) : "–";
    }

    formatDuration(seconds) {
        return _t("%s s", seconds);
    }

    get heldRows() {
        const held = this.state.health.held;
        return [
            [_t("Scan lines"), held.scan_lines],
            [_t("Issues"), held.issues],
            [_t("Snapshots"), held.snapshots],
            [_t("Audit entries"), held.audit_entries],
        ];
    }

    get cleanupRows() {
        const cleanup = this.state.health.last_cleanup;
        const labels = {
            scan_lines: _t("Scan lines"),
            import_files: _t("Import files"),
            issues: _t("Closed issues"),
        };
        return cleanup ? Object.entries(cleanup.deleted).map(([key, count]) => [labels[key] || key, count]) : [];
    }
}

registry.category("actions").add("data_quality_health", DgqHealth);
