import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

export class DgqTrend extends Component {
    static template = "data_governance_quality_center_app.Trend";
    static props = {
        companyIds: { type: Array },
        companies: { type: Array },
        models: { type: Array },
        date: { type: String },
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            period: "week",
            companyId: 0,
            modelId: 0,
            points: [],
            explanation: null,
        });
        onWillStart(() => this.load(this.props));
        onWillUpdateProps((next) => this.load(next));
    }

    async load(props) {
        const result = await this.orm.call("data.quality.dashboard", "get_trend", [], {
            company_ids: this.state.companyId ? [this.state.companyId] : props.companyIds,
            model_id: this.state.modelId || false,
            period: this.state.period,
            date_to: props.date || false,
        });
        this.state.points = result.points;
        this.state.explanation = null;
    }

    async setPeriod(period) {
        this.state.period = period;
        await this.load(this.props);
    }

    async setScope(field, ev) {
        this.state[field] = parseInt(ev.target.value, 10);
        await this.load(this.props);
    }

    periodLabel(period) {
        return { day: _t("Day"), week: _t("Week"), month: _t("Month") }[period];
    }

    async explain(point) {
        this.state.explanation = await this.orm.call("data.quality.dashboard", "explain_change", [
            point.snapshot_id,
            point.previous_snapshot_id,
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
