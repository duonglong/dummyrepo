import { Component, onWillStart, useState } from "@odoo/owl";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { DgqTrend } from "./trend";

export class DgqDashboard extends Component {
    static template = "data_governance_quality_center_app.Dashboard";
    static props = ["*"];
    static components = { DgqTrend };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ figures: null, layout: [], editing: false, companyIds: [], date: "" });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.layout = await this.orm.call("data.quality.dashboard", "get_layout", []);
        this.state.figures = await this.orm.call("data.quality.dashboard", "get_figures", [], {
            company_ids: this.state.companyIds,
            date: this.state.date || false,
        });
    }

    async toggleCompany(companyId) {
        const ids = this.state.companyIds;
        const index = ids.indexOf(companyId);
        if (index < 0) {
            ids.push(companyId);
        } else {
            ids.splice(index, 1);
        }
        await this.load();
    }

    async onDateChange(ev) {
        this.state.date = ev.target.value;
        await this.load();
    }

    async drill(figure, extra = {}) {
        const action = await this.orm.call("data.quality.dashboard", "drill_down", [], {
            figure,
            company_ids: this.state.companyIds,
            date: this.state.date || false,
            ...extra,
        });
        await this.env.services.action.doAction(action);
    }

    async saveLayout(layout) {
        this.state.layout = await this.orm.call("data.quality.dashboard", "save_layout", [
            layout.map(({ widget, visible }) => ({ widget, visible })),
        ]);
    }

    async toggleWidget(widget) {
        await this.saveLayout(
            this.state.layout.map((item) =>
                item.widget === widget ? { ...item, visible: !item.visible } : item
            )
        );
    }

    async moveWidget(widget, step) {
        const layout = [...this.state.layout];
        const index = layout.findIndex((item) => item.widget === widget);
        const target = index + step;
        if (target < 0 || target >= layout.length) {
            return;
        }
        [layout[index], layout[target]] = [layout[target], layout[index]];
        await this.saveLayout(layout);
    }

    async resetLayout() {
        this.state.layout = await this.orm.call("data.quality.dashboard", "reset_layout", []);
    }

    severityLabel(severity) {
        return {
            info: _t("Information"),
            warning: _t("Warning"),
            critical: _t("Critical"),
        }[severity];
    }

    formatDate(value) {
        return formatDateTime(deserializeDateTime(value));
    }

    formatScore(score) {
        return score === null || score === undefined ? "–" : score.toFixed(1);
    }
}

registry.category("actions").add("data_quality_dashboard", DgqDashboard);
