import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

export class DgqTrend extends Component {
    static template = "data_governance_quality_center_app.Trend";
    static props = { companyIds: { type: Array } };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ period: "week", points: [], explanation: null });
        onWillStart(() => this.load(this.props.companyIds));
        onWillUpdateProps((next) => this.load(next.companyIds));
    }

    async load(companyIds) {
        const result = await this.orm.call("data.quality.dashboard", "get_trend", [], {
            company_ids: companyIds,
            period: this.state.period,
        });
        this.state.points = result.points;
        this.state.explanation = null;
    }

    async setPeriod(period) {
        this.state.period = period;
        await this.load(this.props.companyIds);
    }

    async explain(point) {
        this.state.explanation = await this.orm.call("data.quality.dashboard", "explain_change", [
            point.snapshot_id,
        ]);
    }

    causeLabel(kind) {
        return {
            rule_added: _t("Rule added"),
            rule_removed: _t("Rule removed"),
            rule_switched_off: _t("Rule switched off"),
            rule_version_changed: _t("Rule changed"),
            applicable_weight_changed: _t("Records added or removed"),
            failed_weight_changed: _t("Failures changed"),
        }[kind];
    }

    formatScore(score) {
        return score === null ? "–" : score.toFixed(1);
    }

    formatPoints(points) {
        return (points > 0 ? "+" : "") + points.toFixed(1);
    }
}
