import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class DgqDashboard extends Component {
    static template = "data_governance_quality_center_app.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({ figures: null, companyIds: [], date: "" });
        onWillStart(() => this.load());
    }

    async load() {
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

    formatScore(score) {
        return score === null || score === undefined ? "–" : score.toFixed(1);
    }
}

registry.category("actions").add("data_quality_dashboard", DgqDashboard);
