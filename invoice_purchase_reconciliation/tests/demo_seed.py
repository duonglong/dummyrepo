"""Seed realistic demo records for the reconciliation screens through XML-RPC.

Usage:
    python demo_seed.py [--url URL] [--db DB]          create the records (does nothing when they exist)
    python demo_seed.py --count [--store]               print "<model> <n>" lines; --store saves them,
                                                       otherwise compare with the stored counts

The URL and database default to the running demo (``.runs/demo-<db>.url`` in the workspace).
Generated data only.
"""

import argparse
import json
import sys
import xmlrpc.client
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
COUNT_FILE = WORKSPACE / 'out' / 'demo-counts.json'
PASSWORD = 'demo'
MARKER = 'IPR Demo Supplier 01'
COUNTED_MODELS = [
    'res.partner', 'product.product', 'purchase.order', 'stock.picking', 'account.move',
    'ipr.check.line', 'ipr.exception', 'ipr.history', 'ipr.tolerance', 'res.users',
]


def defaults():
    for url_file in sorted((WORKSPACE / '.runs').glob('demo-*.url')):
        return url_file.read_text().strip(), url_file.stem[len('demo-'):]
    return 'http://127.0.0.1:8069', 'demo'


class Rpc:
    def __init__(self, url, db, login, password):
        self.db, self.password = db, password
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        self.uid = common.authenticate(db, login, password, {})
        if not self.uid:
            raise SystemExit(f'login {login} failed')
        self.models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

    def call(self, model, method, *args, **kwargs):
        return self.models.execute_kw(self.db, self.uid, self.password, model, method, list(args), kwargs)

    def create(self, model, vals, **ctx):
        return self.call(model, 'create', vals, context=ctx)

    def search(self, model, domain, **kwargs):
        return self.call(model, 'search', domain, **kwargs)

    def read(self, model, ids, fields):
        return self.call(model, 'read', ids, fields)

    def xmlid(self, xmlid):
        module, name = xmlid.split('.')
        return self.call('ir.model.data', 'search_read', [('module', '=', module), ('name', '=', name)],
                         fields=['res_id'], limit=1)[0]['res_id']


def counts(admin):
    result = {}
    for model in COUNTED_MODELS:
        domain = []
        if model == 'res.partner':
            domain = [('name', 'like', 'IPR Demo')]
        elif model == 'res.users':
            domain = [('login', 'like', 'ipr_demo_')]
        elif model == 'account.move':
            domain = [('move_type', '=', 'in_invoice'), ('ref', 'like', 'DEMO-')]
        elif model == 'purchase.order':
            domain = [('partner_id.name', 'like', 'IPR Demo')]
        elif model == 'product.product':
            domain = [('name', 'like', 'IPR Demo')]
        result[model] = admin.call(model, 'search_count', domain, context={'active_test': False})
    return result


def make_users(admin, company_id):
    groups = {
        'ipr_demo_accountant': ['account.group_account_invoice'],
        'ipr_demo_purchase_user': ['purchase.group_purchase_user'],
        'ipr_demo_purchase_manager': ['purchase.group_purchase_manager'],
        'ipr_demo_account_admin': ['account.group_account_manager'],
        'ipr_demo_account_admin2': ['account.group_account_manager'],
    }
    for login, xmlids in groups.items():
        admin.create('res.users', {
            'name': login.replace('ipr_demo_', 'IPR Demo ').replace('_', ' ').title(),
            'login': login, 'password': PASSWORD, 'company_id': company_id,
            'company_ids': [(6, 0, [company_id])],
            'group_ids': [(6, 0, [admin.xmlid(x) for x in xmlids] + [admin.xmlid('base.group_user')])],
        }, no_reset_password=True)


def make_catalog(admin, company_id):
    suppliers = []
    for n in range(1, 31):
        suppliers.append(admin.create('res.partner', {
            'name': f'IPR Demo Supplier {n:02d}', 'supplier_rank': 1, 'company_id': False}))
    contact = admin.create('res.partner', {
        'name': 'IPR Demo Supplier 01 Accounts', 'parent_id': suppliers[0], 'type': 'contact'})
    products = []
    for n, (name, cost) in enumerate([
            ('Office chair', 10.0), ('Standing desk', 250.0), ('Monitor arm', 35.0), ('Desk lamp', 18.5),
            ('Filing cabinet', 120.0), ('Whiteboard', 60.0), ('Paper box', 4.2), ('Toner cartridge', 48.0)]):
        products.append(admin.create('product.product', {
            'name': f'IPR Demo {name}', 'is_storable': True, 'type': 'consu',
            'purchase_method': 'purchase', 'standard_price': cost, 'list_price': cost * 1.4}))
    for n in range(9, 91):
        admin.create('product.product', {
            'name': f'IPR Demo Spare part {n:03d}', 'type': 'consu', 'standard_price': 1.0 + n})
    return suppliers, contact, products


def make_order(admin, partner, lines, received):
    """lines: list of (product, qty, price); received: dict index -> quantity received."""
    order_id = admin.create('purchase.order', {
        'partner_id': partner,
        'order_line': [(0, 0, {'product_id': p, 'product_qty': q, 'price_unit': price,
                               'name': 'IPR demo line'}) for p, q, price in lines],
    })
    admin.call('purchase.order', 'button_confirm', [order_id])
    if received:
        order = admin.read('purchase.order', [order_id], ['picking_ids', 'order_line'])[0]
        picking = order['picking_ids'][0]
        moves = admin.search('stock.move', [('picking_id', '=', picking)], order='id')
        for index, move in enumerate(moves):
            qty = received.get(index, 0)
            admin.call('stock.move', 'write', [move], {'quantity': qty, 'picked': bool(qty)})
        admin.call('stock.picking', 'button_validate', [picking],
                   context={'skip_backorder': True, 'skip_sms': True})
    return admin.read('purchase.order', [order_id], ['order_line'])[0]['order_line']


def bill(accountant, partner, ref, lines, **extra):
    """lines: list of (purchase_line_id or False, product, qty, price)."""
    vals = {
        'move_type': 'in_invoice', 'partner_id': partner, 'ref': ref, 'invoice_date': '2026-11-20',
        'invoice_line_ids': [(0, 0, {
            'product_id': product, 'quantity': qty, 'price_unit': price,
            **({'purchase_line_id': pl} if pl else {'name': 'Freight'})}) for pl, product, qty, price in lines],
    }
    vals.update(extra)
    return accountant.create('account.move', vals)


def seed(url, db):
    admin = Rpc(url, db, 'admin', 'admin')
    if admin.search('res.partner', [('name', '=', MARKER)]):
        print('demo records exist; nothing created')
        return
    company_id = admin.read('res.users', [admin.uid], ['company_id'])[0]['company_id'][0]
    make_users(admin, company_id)
    tolerance = admin.search('ipr.tolerance', [('company_id', '=', company_id)])
    vals = {'price_tolerance_type': 'percent', 'price_tolerance_percent': 5.0,
            'qty_tolerance_type': 'fixed', 'qty_tolerance_value': 2.0}
    if tolerance:
        admin.call('ipr.tolerance', 'write', tolerance, vals)
    else:
        admin.create('ipr.tolerance', {'company_id': company_id, **vals})
    suppliers, contact, products = make_catalog(admin, company_id)
    accountant = Rpc(url, db, 'ipr_demo_accountant', PASSWORD)
    manager = Rpc(url, db, 'ipr_demo_purchase_manager', PASSWORD)
    account_admin = Rpc(url, db, 'ipr_demo_account_admin', PASSWORD)
    chair, desk, arm, lamp, cabinet, board, paper, toner = products

    # 1. matched order with 15 lines across products, fully received
    items = [(products[i % 8], 10 + i, round(5 + i * 3.5, 2)) for i in range(15)]
    pl = make_order(admin, suppliers[0], items, {i: 10 + i for i in range(15)})
    b = bill(accountant, suppliers[0], 'DEMO-MATCH-001',
             [(pl[i], items[i][0], items[i][1], items[i][2]) for i in range(15)] + [(False, paper, 1, 25.0)])
    accountant.call('account.move', 'action_ipr_check', [b])

    # 2. price exception waiting (billed 12 against 10), then a second one over the quantity
    pl = make_order(admin, suppliers[1], [(chair, 100, 10.0), (desk, 20, 250.0)], {0: 100, 1: 20})
    b = bill(accountant, suppliers[1], 'DEMO-PRICE-001', [(pl[0], chair, 100, 12.0), (pl[1], desk, 20, 250.0)])
    accountant.call('account.move', 'action_ipr_check', [b])

    pl = make_order(admin, suppliers[2], [(arm, 50, 35.0)], {0: 40})
    b = bill(accountant, suppliers[2], 'DEMO-QTY-001', [(pl[0], arm, 50, 35.0)])
    accountant.call('account.move', 'action_ipr_check', [b])

    # 3. possible duplicate: an earlier bill and a second one typed with other case and spaces, on a contact
    pl = make_order(admin, suppliers[0], [(lamp, 30, 18.5)], {0: 30})
    first = bill(accountant, suppliers[0], 'DEMO-DUP-778', [(pl[0], lamp, 30, 18.5)])
    accountant.call('account.move', 'action_ipr_check', [first])
    second = bill(accountant, contact, ' demo-dup-778', [(False, lamp, 30, 18.5)])
    accountant.call('account.move', 'action_ipr_check', [second])

    # 4. approved (with comment) and posted; rejected; approved and then changed (reset)
    pl = make_order(admin, suppliers[3], [(cabinet, 10, 120.0)], {0: 10})
    approved = bill(accountant, suppliers[3], 'DEMO-APPROVED-001', [(pl[0], cabinet, 10, 135.0)])
    accountant.call('account.move', 'action_ipr_check', [approved])
    manager.call('account.move', 'action_ipr_approve', [approved], 'Price rise agreed by phone')
    accountant.call('account.move', 'action_post', [approved])

    pl = make_order(admin, suppliers[4], [(board, 8, 60.0)], {0: 8})
    rejected = bill(accountant, suppliers[4], 'DEMO-REJECTED-001', [(pl[0], board, 8, 90.0)])
    accountant.call('account.move', 'action_ipr_check', [rejected])
    account_admin.call('account.move', 'action_ipr_reject', [rejected], 'Ask the supplier for a credit note')

    pl = make_order(admin, suppliers[5], [(toner, 12, 48.0)], {0: 12})
    reset = bill(accountant, suppliers[5], 'DEMO-RESET-001', [(pl[0], toner, 12, 55.0)])
    accountant.call('account.move', 'action_ipr_check', [reset])
    manager.call('account.move', 'action_ipr_approve', [reset], None)
    accountant.call('account.move', 'write', [reset], {'invoice_line_ids': [
        (1, accountant.search('account.move.line', [('move_id', '=', reset), ('display_type', '=', 'product')])[0],
         {'price_unit': 58.0})]})

    # 5. approved and posted bill, payment-ready; waiting posted is not reachable (posting is refused)
    pl = make_order(admin, suppliers[6], [(paper, 500, 4.2)], {0: 500})
    ok = bill(accountant, suppliers[6], 'DEMO-POSTED-001', [(pl[0], paper, 500, 4.2)])
    accountant.call('account.move', 'action_post', [ok])

    # 6. a draft bill never checked, and a bill with no order
    bill(accountant, suppliers[7], 'DEMO-UNCHECKED-001', [(False, paper, 3, 4.2)])
    nr = bill(accountant, suppliers[8], 'DEMO-NOORDER-001', [(False, toner, 2, 50.0)])
    accountant.call('account.move', 'action_ipr_check', [nr])

    # 7. a cancelled bill with history, deleted by the accountant so the kept entries show deletion
    gone = bill(accountant, suppliers[9], 'DEMO-DELETED-001', [(False, paper, 5, 4.2)])
    accountant.call('account.move', 'action_ipr_check', [gone])
    accountant.call('account.move', 'unlink', [gone])

    # 8. many waiting bills so the Bills to approve list has more than a handful
    for n in range(10, 22):
        pl = make_order(admin, suppliers[n], [(chair, 20, 10.0)], {0: 20})
        b = bill(accountant, suppliers[n], f'DEMO-BULK-{n:03d}', [(pl[0], chair, 20, 10.0 + n * 0.3)])
        accountant.call('account.move', 'action_ipr_check', [b])
    print('demo records created')


def main():
    default_url, default_db = defaults()
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=default_url)
    parser.add_argument('--db', default=default_db)
    parser.add_argument('--count', action='store_true')
    parser.add_argument('--store', action='store_true')
    args = parser.parse_args()
    if not args.count:
        seed(args.url, args.db)
        return 0
    current = counts(Rpc(args.url, args.db, 'admin', 'admin'))
    for model, number in current.items():
        print(model, number)
    if args.store:
        COUNT_FILE.write_text(json.dumps(current, indent=1))
        return 0
    if not COUNT_FILE.exists():
        print('no stored counts')
        return 1
    stored = json.loads(COUNT_FILE.read_text())
    different = {m: (stored.get(m), n) for m, n in current.items() if stored.get(m) != n}
    for model, (before, now) in different.items():
        print(f'DIFFERENT {model}: stored {before}, now {now}')
    return 1 if different else 0


if __name__ == '__main__':
    sys.exit(main())
