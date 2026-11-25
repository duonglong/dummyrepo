# Supplier & Procurement Management Hub

Supplier onboarding, sourcing, approvals and control on top of Odoo Purchase.
Seven internal role groups (Requester, Department manager, Buyer, Procurement
manager, Finance reviewer, Auditor, Administrator) plus Odoo's portal group.

## Extension points

Business logic sits in `services/` and is called by models, controllers and jobs alike; a
portal path and a back-office path therefore apply the same rule. A module that depends on this
one adds, without changing it:

- **A supplier field.** Add the field to `res.partner` with `_inherit`, then create a
  `procurement.portal.field` record for it to show or let suppliers edit it in the portal
  (bank, status and risk fields are refused). To make it part of an application form, extend
  `procurement.supplier.type.field.field_key` with `selection_add`.
- **An evaluation criterion.** Create a `procurement.evaluation.criterion` record for a
  qualitative review, or a `procurement.sourcing.criterion` record (kind manual) for a weighted
  comparison; no code is needed.
- **A scorecard metric.** Extend `procurement.scorecard.template.metric.metric` with
  `selection_add` and override `_spm_metric_computers` to add the function
  `(env, partner, company, start, end)` that returns a result built like those of
  `services/performance.py`.
- **An approver type.** Extend `procurement.approval.policy.step.approver_type` with
  `selection_add` and override `_spm_approver_resolvers` to map the new type to a method that
  returns the users of the step for an order.

Scheduled jobs add a code with `selection_add` on `procurement.job.code` and the two methods
`_spm_collect_<code>` and `_spm_process_<code>`.

## Hardening tests

`tests/test_nfr_*.py` hold the release-gate tests: portal isolation at page, form, download,
chatter and data-call level; least privilege and CSRF; multi-company rules and reports;
pagination and indexes; repeated jobs; the extension points above.

## Edition and phases

The app needs Odoo 20.0 Community modules only and no external service. It is delivered in
seven phases: onboarding and security; portal and quotes; comparison and approvals; contracts
and delivery; matching; scorecards, risk and analytics; automation and hardening.
