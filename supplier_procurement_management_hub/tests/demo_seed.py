"""Generated demo data for the procurement hub: users, suppliers, requests, sourcing,
contracts, approvals, deliveries, bills and scorecards.

Run it against a running demo server (address in .runs/demo-<db>.url):

    python3 demo_seed.py            # seed (safe to rerun: records are found by name first)
    python3 demo_seed.py --count    # print "<model> <n>" per seeded model; with --store save them,
                                    # otherwise exit 1 when a count differs from the stored one
"""
import glob
import json
import sys
import xmlrpc.client as xc
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
COUNT_FILE = HERE.parent.parent / '.runs' / 'demo-seed-counts.json'
RUNS = min(glob.glob(str(HERE.parent.parent / '.runs' / 'demo-*.url')))
URL = Path(RUNS).read_text().strip()
DB = Path(RUNS).name[len('demo-'):-len('.url')]
PWD = 'admin'
MODULE = 'supplier_procurement_management_hub'
NOW = datetime.now(tz=timezone.utc).replace(microsecond=0, tzinfo=None)
TODAY = NOW.date()
SEEDED = {}
COUNTED = ('res.users', 'product.product', 'res.partner', 'procurement.supplier.document',
           'procurement.request', 'procurement.sourcing.event', 'procurement.sourcing.response.version',
           'procurement.approval.policy', 'procurement.approval.request', 'purchase.order',
           'procurement.contract', 'procurement.supplier.application', 'account.move', 'procurement.reconciliation',
           'procurement.scorecard.template', 'procurement.supplier.scorecard',
           'procurement.questionnaire', 'procurement.supplier.assessment')

uid = xc.ServerProxy(URL + '/xmlrpc/2/common').authenticate(DB, 'admin', PWD, {})
_obj = xc.ServerProxy(URL + '/xmlrpc/2/object', allow_none=True)


def call(model, method, *args, **kw):
    return _obj.execute_kw(DB, uid, PWD, model, method, list(args), kw)


_uids = {}


def call_as(login, model, method, *args, **kw):
    """Call as a demo user (password ``demo``), so the module sees the role."""
    if login not in _uids:
        _uids[login] = xc.ServerProxy(URL + '/xmlrpc/2/common').authenticate(DB, login, 'demo', {})
    return _obj.execute_kw(DB, _uids[login], 'demo', model, method, list(args), kw)


def section(fn, *args):
    """Run one seeding section; a refused step is reported and the next section still runs."""
    try:
        return fn(*args)
    except xc.Fault as err:
        text = str(err.faultString).strip().splitlines()
        print(f'WARNING {fn.__name__}: {text[-1][:300] if text else err.faultCode}')
        if "--debug" in sys.argv:
            raise


def d(days):
    return (TODAY + timedelta(days=days)).isoformat()


def dt(days, hour=17):
    return (datetime.combine(TODAY, datetime.min.time()) + timedelta(days=days, hours=hour)).strftime('%Y-%m-%d %H:%M:%S')


def ref(xmlid):
    return call('ir.model.data', 'check_object_reference', *xmlid.split('.', 1))[1]


def find(model, domain, **kw):
    return call(model, 'search', domain, limit=1, **kw)


def make(model, key, vals, context=None):
    """Create the record unless one with the same key fields exists; returns its id."""
    domain = [(k, '=', vals[k]) for k in key]
    found = call(model, 'search', domain, limit=1, context={'active_test': False})
    SEEDED.setdefault(model, 0)
    if found:
        SEEDED[model] += 1
        return found[0]
    new = call(model, 'create', vals, context=context or {'tracking_disable': True})
    SEEDED[model] += 1
    return new


def seed_users():
    groups = {
        'requester': 'group_spm_requester', 'manager_dept': 'group_spm_department_manager',
        'buyer': 'group_spm_buyer', 'finance': 'group_spm_finance',
        'auditor': 'group_spm_auditor', 'manager': 'group_spm_manager', 'admin': 'group_spm_admin',
    }
    extra = {
        'buyer': ['purchase.group_purchase_user'],
        'finance': ['account.group_account_invoice', 'purchase.group_purchase_user'],
        'manager': ['purchase.group_purchase_manager'],
    }
    users = {}
    for role, group in groups.items():
        gids = [ref(f'{MODULE}.{group}'), ref('base.group_partner_manager')]
        gids += [ref(x) for x in extra.get(role, [])]
        users[role] = make('res.users', ['login'], {
            'name': f'Demo {role.replace("_", " ").title()}', 'login': f'demo_{role}',
            'email': f'demo_{role}@example.com', 'password': 'demo',
            'group_ids': [(6, 0, gids)],
        }, context={'no_reset_password': True, 'tracking_disable': True})
    # the administrator is a procurement manager as well
    call('res.users', 'write', [uid], {'group_ids': [(4, ref(f'{MODULE}.group_spm_admin')),
                                                     (4, ref('purchase.group_purchase_manager')),
                                                     (4, ref('account.group_account_invoice'))]})
    users['admin_user'] = uid
    return users


def seed_catalog():
    cats = {n: make('product.category', ['name'], {'name': n})
            for n in ('Office furniture', 'IT hardware', 'Cleaning supplies')}
    prods = {}
    for name, cat, price in [
        ('Office chair', 'Office furniture', 120.0), ('Standing desk', 'Office furniture', 340.0),
        ('Meeting table', 'Office furniture', 520.0), ('Laptop 14"', 'IT hardware', 980.0),
        ('Docking station', 'IT hardware', 150.0), ('Monitor 27"', 'IT hardware', 260.0),
        ('USB-C cable', 'IT hardware', 9.5), ('Floor cleaner 5L', 'Cleaning supplies', 14.0),
        ('Paper towels', 'Cleaning supplies', 3.2),
    ]:
        prods[name] = make('product.product', ['name'], {
            'name': name, 'type': 'consu', 'categ_id': cats[cat], 'standard_price': price * 0.8,
            'list_price': price, 'is_storable': True})
    return cats, prods


def set_status(partner, status, reason):
    current = call('res.partner', 'read', [partner], ['spm_status_id'])[0]['spm_status_id']
    if not current or current[0] != status:
        call('res.partner', 'action_spm_set_status', [partner], status, reason)


def seed_suppliers(users, cats):
    status = {k: find('procurement.supplier.status', [('kind', '=', k)]) for k in
              ('approved', 'in_review', 'conditional', 'suspended', 'prospect')}
    names = [
        ('Acme Office Supplies', 'approved', 'low', 'Europe/Brussels'),
        ('Beta Furniture Works', 'approved', 'medium', 'Europe/Paris'),
        ('Gamma IT Distribution', 'approved', 'low', 'America/New_York'),
        ('Delta Cleaning Co', 'conditional', 'medium', 'Europe/Berlin'),
        ('Epsilon Logistics', 'in_review', 'high', 'Asia/Singapore'),
        ('Zeta Components', 'suspended', 'high', 'Europe/Rome'),
    ]
    extra = ['Orion Metals', 'Pluto Packaging', 'Sirius Print', 'Vega Tools', 'Lyra Textiles',
             'Atlas Hardware', 'Nova Paper', 'Helix Plastics', 'Cobalt Chemicals', 'Juno Safety',
             'Kappa Cables', 'Titan Pallets']
    sup = {}
    for name, st, risk, tz in names:
        vals = {'name': name, 'is_company': True, 'supplier_rank': 1, 'email': f'sales@{name.split()[0].lower()}.example.com',
                'city': 'Brussels', 'tz': tz, 'spm_risk_level': risk,
                'spm_responsible_user_id': users['buyer'],
                'spm_requalification_date': d(120)}
        sup[name] = make('res.partner', ['name'], vals)
        if status.get(st):
            set_status(sup[name], status[st][0], 'Demo data: initial status')
    for name in extra:  # many suppliers: a many2one picker must search, not list
        pid = make('res.partner', ['name'], {'name': name, 'is_company': True, 'supplier_rank': 1})
        set_status(pid, status['approved'][0], 'Demo data: initial status')
    return sup


def seed_documents(sup):
    types = {n: find('procurement.document.type', [('name', '=', n)]) for n in
             ('Tax certificate', 'Insurance certificate', 'Quality certificate')}
    rows = [
        ('Acme Office Supplies', 'Tax certificate', 'verified', d(-100), d(250)),
        ('Acme Office Supplies', 'Insurance certificate', 'verified', d(-300), d(20)),
        ('Beta Furniture Works', 'Tax certificate', 'to_verify', d(-10), d(300)),
        ('Beta Furniture Works', 'Insurance certificate', 'rejected', d(-50), d(100)),
        ('Gamma IT Distribution', 'Tax certificate', 'verified', d(-400), d(-5)),
        ('Delta Cleaning Co', 'Quality certificate', 'verified', d(-20), d(15)),
    ]
    for partner, typ, state, issue, expiry in rows:
        if not types[typ]:
            continue
        make('procurement.supplier.document', ['partner_id', 'document_type_id'], {
            'partner_id': sup[partner], 'document_type_id': types[typ][0],
            'verification_state': state, 'issue_date': issue, 'expiry_date': expiry,
            'is_current': True,
            'review_comment': 'Certificate illegible, please upload a clear copy' if state == 'rejected' else False})


def seed_requests(users, prods):
    unit = ref('uom.product_uom_unit')
    rows = [
        ('Chairs for the new open space', 'draft', [('Office chair', 40.0), ('Standing desk', 10.0)]),
        ('Laptop refresh for sales team', 'submitted', [('Laptop 14"', 12.0), ('Docking station', 12.0), ('Monitor 27"', 12.0)]),
        ('Monthly cleaning stock', 'submitted', [('Floor cleaner 5L', 30.0), ('Paper towels', 200.0)]),
    ]
    made = {}
    for reason, state, lines in rows:
        rid = make('procurement.request', ['reason'], {
            'requester_id': users['requester'], 'needed_date': d(30), 'reason': reason,
            'buyer_id': users['buyer'],
            'line_ids': [(0, 0, {'product_id': prods[p], 'name': p, 'quantity': q, 'uom_id': unit})
                         for p, q in lines]})
        cur = call('procurement.request', 'read', [rid], ['state'])[0]['state']
        if state == 'submitted' and cur == 'draft':
            call_as('demo_requester', 'procurement.request', 'action_spm_submit', [rid])
        made[reason] = rid
    return made


def seed_sourcing(users, prods, sup):
    unit = ref('uom.product_uom_unit')
    title = 'Office furniture tender'
    eid = find('procurement.sourcing.event', [('title', '=', title)])
    if eid:
        eid = eid[0]
    else:
        eid = create_event(users, prods, sup, title, unit)
    SEEDED['procurement.sourcing.event'] = SEEDED.get('procurement.sourcing.event', 0) + 1
    return quote_event(eid, sup, unit)


def create_event(users, prods, sup, title, unit):
    partners = [sup['Acme Office Supplies'], sup['Beta Furniture Works'], sup['Gamma IT Distribution']]
    eid = call_as('demo_buyer', 'procurement.sourcing.event', 'create', {
        'title': title, 'buyer_id': users['buyer'], 'deadline': dt(10),
        'invited_partner_ids': [(6, 0, partners)],
        'line_ids': [(0, 0, {'name': 'Office chair', 'product_id': prods['Office chair'], 'quantity': 60.0, 'uom_id': unit}),
                     (0, 0, {'name': 'Standing desk', 'product_id': prods['Standing desk'], 'quantity': 25.0, 'uom_id': unit}),
                     (0, 0, {'name': 'Meeting table', 'product_id': prods['Meeting table'], 'quantity': 4.0, 'uom_id': unit})],
        'criterion_ids': [(0, 0, {'name': 'Price', 'kind': 'price', 'direction': 'lower', 'weight': 60.0}),
                          (0, 0, {'name': 'Lead time', 'kind': 'lead_time', 'direction': 'lower', 'weight': 40.0})],
    })
    call_as('demo_buyer', 'procurement.sourcing.event', 'action_spm_send', [eid])
    return eid


def quote_event(eid, sup, unit):
    event = call('procurement.sourcing.event', 'read', [eid], ['line_ids', 'response_ids', 'currency_id'])[0]
    prices = {  # partner: (chair, desk, table, lead days); Gamma leaves the table out
        sup['Acme Office Supplies']: (118.0, 335.0, 540.0, 14),
        sup['Beta Furniture Works']: (125.0, 320.0, 515.0, 9),
        sup['Gamma IT Distribution']: (121.0, 349.0, None, 21),
    }
    for resp in call('procurement.sourcing.response', 'read', event['response_ids'], ['partner_id', 'state']):
        if resp['state'] != 'invited':
            continue
        offer = prices[resp['partner_id'][0]]
        lines = [{'event_line_id': line_id, 'price_unit': price, 'discount': 0.0,
                  'lead_time_days': offer[3]}
                 for line_id, price in zip(event['line_ids'], offer[:3]) if price is not None]
        call_as('demo_buyer', 'procurement.sourcing.response', 'spm_enter_quotation', [resp['id']], lines)
    call('procurement.sourcing.event', 'write', [eid], {'rate_date': d(0), 'tax_basis': 'excluded'})
    call('procurement.sourcing.event.line', 'write', event['line_ids'], {'comparison_uom_id': unit})
    call_as('demo_buyer', 'procurement.sourcing.event', 'action_spm_compare', [eid])
    call_as('demo_buyer', 'procurement.sourcing.event', 'action_spm_evaluate', [eid])
    return eid


def purchase_order(partner, lines, buyer='demo_buyer', confirm=False, planned=None, requester=None):
    """A draft order made as the buyer; ``lines`` are (product id, qty, price) tuples."""
    key = f'{partner}:' + ','.join(f'{p}x{q}@{pr}' for p, q, pr in lines) + f':{planned}'
    found = find('purchase.order', [('partner_id', '=', partner), ('origin', '=', key)])
    SEEDED['purchase.order'] = SEEDED.get('purchase.order', 0) + 1
    if found:
        return found[0]
    vals = {'partner_id': partner, 'origin': key,
            'order_line': [(0, 0, {'product_id': p, 'name': 'Demo line', 'product_qty': q, 'price_unit': pr,
                                   'tax_ids': [(6, 0, [])],
                                   **({'date_planned': planned} if planned else {})})
                           for p, q, pr in lines]}
    if requester:
        vals['spm_requester_id'] = requester
    oid = call_as(buyer, 'purchase.order', 'create', vals, context={'tracking_disable': True})
    if confirm:
        call_as(buyer, 'purchase.order', 'button_confirm', [oid])
    return oid


def seed_approvals(users, prods, sup):
    company = call('res.users', 'read', [uid], ['company_id'])[0]['company_id'][0]
    policy = make('procurement.approval.policy', ['name'], {
        'name': 'Orders above 5,000', 'sequence': 10, 'company_id': company, 'amount_min': 5000.0,
        'escalation_hours': 48, 'escalation_user_id': users['manager'], 'cumulative_period': 'month',
        'step_ids': [(0, 0, {'name': 'Department manager', 'sequence': 10, 'approver_type': 'users',
                             'mode': 'any', 'user_ids': [(6, 0, [users['manager_dept']])]}),
                     (0, 0, {'name': 'Procurement manager', 'sequence': 20, 'approver_type': 'users',
                             'mode': 'any', 'user_ids': [(6, 0, [users['manager']])]})]})
    del policy
    acme, beta = sup['Acme Office Supplies'], sup['Beta Furniture Works']
    pending = purchase_order(acme, [(prods['Office chair'], 60.0, 118.0), (prods['Standing desk'], 25.0, 335.0)])
    if not call('purchase.order', 'read', [pending], ['spm_approval_request_ids'])[0]['spm_approval_request_ids']:
        call_as('demo_buyer', 'purchase.order', 'action_spm_submit_approval', [pending])
    small = purchase_order(beta, [(prods['Paper towels'], 100.0, 3.2)])
    return small


def seed_contract(users, prods, sup):
    company = call('res.users', 'read', [uid], ['company_id'])[0]['company_id'][0]
    cur = call('res.company', 'read', [company], ['currency_id'])[0]['currency_id'][0]
    unit = ref('uom.product_uom_unit')
    name = 'Framework agreement 2026'
    if find('procurement.contract', [('service_description', '=', name)]):
        SEEDED['procurement.contract'] = SEEDED.get('procurement.contract', 0) + 1
        return
    cid = call_as('demo_manager', 'procurement.contract', 'create', {
        'partner_id': sup['Acme Office Supplies'], 'owner_id': users['manager'], 'company_id': company,
        'currency_id': cur, 'date_start': d(-60), 'date_end': d(300), 'notice_days': 60,
        'scope_kind': 'products', 'service_description': name, 'ceiling_value': 50000.0,
        'line_ids': [(0, 0, {'product_id': prods['Office chair'], 'price_unit': 115.0, 'uom_id': unit,
                             'min_qty': 20.0, 'lead_time_days': 10, 'ceiling_qty': 400.0}),
                     (0, 0, {'product_id': prods['Standing desk'], 'price_unit': 330.0, 'uom_id': unit,
                             'min_qty': 5.0, 'lead_time_days': 14, 'ceiling_qty': 100.0})]})
    SEEDED['procurement.contract'] = SEEDED.get('procurement.contract', 0) + 1
    call_as('demo_manager', 'procurement.contract', 'action_spm_submit', [cid])
    call_as('demo_admin', 'procurement.contract', 'action_spm_approve', [cid])


def receive(order, qty):
    picking = call('purchase.order', 'read', [order], ['picking_ids'])[0]['picking_ids']
    open_picks = call('stock.picking', 'search', [('id', 'in', picking), ('state', 'not in', ('done', 'cancel'))])
    if not open_picks:
        return
    moves = call('stock.picking', 'read', open_picks[:1], ['move_ids'])[0]['move_ids']
    call('stock.move', 'write', moves, {'quantity': qty, 'picked': True})
    call('stock.picking', 'button_validate', open_picks[:1],
         context={'skip_backorder': True, 'picking_ids_not_to_backorder': open_picks[:1]})


def seed_deliveries(users, prods, sup):
    gamma, delta, acme = sup['Gamma IT Distribution'], sup['Delta Cleaning Co'], sup['Acme Office Supplies']
    late = purchase_order(gamma, [(prods['Laptop 14"'], 12.0, 975.0), (prods['Docking station'], 12.0, 149.0)],
                          confirm=True, planned=dt(-9, 8))
    partial = purchase_order(delta, [(prods['Floor cleaner 5L'], 100.0, 13.5)], confirm=True, planned=dt(-3, 8))
    ontime = purchase_order(acme, [(prods['Office chair'], 20.0, 118.0)], confirm=True, planned=dt(-12, 8))
    for order, qty in ((partial, 60.0), (ontime, 20.0)):
        if call('purchase.order', 'read', [order], ['state'])[0]['state'] == 'purchase':
            section(receive, order, qty)
    soon = purchase_order(sup['Beta Furniture Works'], [(prods['Meeting table'], 4.0, 515.0)],
                          confirm=True, planned=dt(6, 8))
    return late, partial, ontime, soon


def seed_bills(users, prods, sup):
    delta = sup['Delta Cleaning Co']
    order = find('purchase.order', [('partner_id', '=', delta), ('state', '=', 'purchase')])
    if not order:
        return
    line = call('purchase.order.line', 'search_read', [('order_id', '=', order[0])], ['product_id'], limit=1)[0]
    for ref_, qty, price in (('DEMO-BILL-001', 60.0, 13.5), ('DEMO-BILL-002', 80.0, 14.9)):
        bill = make('account.move', ['ref'], {
            'move_type': 'in_invoice', 'partner_id': delta, 'ref': ref_, 'invoice_date': d(-2),
            'invoice_line_ids': [(0, 0, {'product_id': line['product_id'][0], 'name': 'Floor cleaner 5L',
                                         'quantity': qty, 'price_unit': price, 'purchase_line_id': line['id'],
                                         'tax_ids': [(6, 0, [])]})]})
        del bill


def seed_scorecards(users, sup):
    company = call('res.users', 'read', [uid], ['company_id'])[0]['company_id'][0]
    make('procurement.scorecard.template', ['name'], {
        'name': 'Monthly delivery and price scorecard', 'period': 'month', 'company_id': company,
        'good_from': 85.0, 'acceptable_from': 60.0, 'review_threshold': 50.0,
        'partner_ids': [(6, 0, [sup['Acme Office Supplies'], sup['Gamma IT Distribution'],
                                sup['Delta Cleaning Co']])],
        'metric_ids': [(0, 0, {'metric': 'on_time', 'weight': 40.0}),
                       (0, 0, {'metric': 'fulfillment', 'weight': 30.0}),
                       (0, 0, {'metric': 'price_competitiveness', 'weight': 30.0})]})


def run_jobs():
    """Run the scorecard, document expiry and delivery escalation jobs once so their results show."""
    for code in ('scorecard', 'document_expiry', 'delivery_escalation', 'contract_reminder'):
        job = find('procurement.job', [('code', '=', code)])
        if job:
            call('procurement.job', 'action_spm_run_now', job)


def seed_portal(users, sup):
    """Portal logins of two suppliers and one invited onboarding application."""
    for supplier, name, login in ((sup['Acme Office Supplies'], 'Jane Acme', 'demo_portal_acme'),
                                  (sup['Beta Furniture Works'], 'Bob Beta', 'demo_portal_beta')):
        contact = make('res.partner', ['email'], {
            'name': name, 'parent_id': supplier, 'type': 'contact', 'email': f'{login}@supplier.example'})
        make('res.users', ['login'], {
            'partner_id': contact, 'login': login, 'password': 'demo',
            'group_ids': [(6, 0, [ref('base.group_portal')])]},
            context={'no_reset_password': True, 'tracking_disable': True})
    docs = call('procurement.document.type', 'search', [])
    stype = make('procurement.supplier.type', ['name'], {
        'name': 'Goods supplier', 'required_document_type_ids': [(6, 0, docs[:2])]})
    company = call('res.users', 'read', [uid], ['company_id'])[0]['company_id'][0]
    app = make('procurement.supplier.application', ['email'], {
        'email': 'hello@newsupplier.example', 'supplier_type_id': stype, 'company_id': company,
        'legal_name': 'Newcomer Trading Ltd'})
    if call('procurement.supplier.application', 'read', [app], ['invitation_expiry'])[0]['invitation_expiry'] is False:
        call_as('demo_buyer', 'procurement.supplier.application', 'action_spm_send_invitation', [app])
    return app


def seed_qualification(users, sup):
    """One questionnaire with three questions and an approved assessment for two suppliers."""
    quest = make('procurement.questionnaire', ['name'], {
        'name': 'Supplier qualification', 'purpose': 'qualification', 'pass_score': 50.0})
    questions = []
    for seq, text in enumerate(('Is the quality system certified?', 'Is delivery tracked per shipment?',
                                'Does the supplier hold liability insurance?'), start=1):
        qid = make('procurement.questionnaire.question', ['questionnaire_id', 'name'], {
            'questionnaire_id': quest, 'name': text, 'sequence': seq * 10, 'kind': 'choice', 'max_points': 10.0})
        good = make('procurement.questionnaire.option', ['question_id', 'name'], {
            'question_id': qid, 'name': 'Yes', 'points': 10.0})
        weak = make('procurement.questionnaire.option', ['question_id', 'name'], {
            'question_id': qid, 'name': 'Partly', 'points': 5.0})
        questions.append((qid, good, weak))
    for name, pick in (('Acme Office Supplies', 1), ('Gamma IT Distribution', 2)):
        found = call('procurement.supplier.assessment', 'search',
                     [('partner_id', '=', sup[name]), ('questionnaire_id', '=', quest)], limit=1)
        SEEDED.setdefault('procurement.supplier.assessment', 0)
        SEEDED['procurement.supplier.assessment'] += 1
        if found:
            continue
        answers = [(0, 0, {'question_id': q, 'option_id': (opts[0] if i < pick + 1 else opts[1])})
                   for i, (q, *opts) in enumerate(questions)]
        assessment = call('procurement.supplier.assessment', 'create', {
            'partner_id': sup[name], 'questionnaire_id': quest, 'answer_ids': answers},
            context={'tracking_disable': True})
        call_as('demo_buyer', 'procurement.supplier.assessment', 'action_spm_submit', [assessment])
        call_as('demo_manager', 'procurement.supplier.assessment', 'action_spm_approve', [assessment])


def main_seed():
    users = seed_users()
    cats, prods = seed_catalog()
    sup = seed_suppliers(users, cats)
    seed_documents(sup)
    section(seed_requests, users, prods)
    section(seed_sourcing, users, prods, sup)
    section(seed_approvals, users, prods, sup)
    section(seed_contract, users, prods, sup)
    section(seed_deliveries, users, prods, sup)
    section(seed_bills, users, prods, sup)
    section(seed_scorecards, users, sup)
    section(seed_portal, users, sup)
    section(seed_qualification, users, sup)
    section(run_jobs)
    return users, cats, prods, sup


def count():
    out = {m: call(m, 'search_count', [], context={'active_test': False}) for m in sorted(SEEDED)}
    for model, n in out.items():
        print(model, n)
    return out


if __name__ == '__main__':
    if '--count' in sys.argv:
        SEEDED.update({m: 0 for m in COUNTED})
        counts = count()
        if '--store' in sys.argv:
            COUNT_FILE.write_text(json.dumps(counts))
        elif COUNT_FILE.exists():
            stored = json.loads(COUNT_FILE.read_text())
            diff = {m: (stored[m], counts[m]) for m in stored if stored[m] != counts.get(m)}
            if diff:
                print('DIFFERENCE', diff)
                sys.exit(1)
    else:
        main_seed()
        print(json.dumps(SEEDED))
