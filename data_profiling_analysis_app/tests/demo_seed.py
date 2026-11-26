"""Generated demo data for the Data Profiling & Analysis walkthrough (XML-RPC only).

Usage:  demo_seed.py --url URL [--db DB] [--count [--store]]
Without --db the only database of the server is used. --count prints one "<model> <n>" line per seeded model and,
against the stored counts, exits 1 on any difference. Rerunning the seed changes nothing: everything is looked up
by name first.
"""
import argparse
import json
import sys
import urllib.request
import xmlrpc.client
from pathlib import Path

PASSWORD = 'demo-dpa-pass'
PREFIX = 'DPA-'
STORE = Path('/workspace/out/demo_seed_counts.json')
CITIES = ['Brussels'] * 20 + ['Paris'] * 12 + ['Ghent'] * 8 + ['Lyon'] * 5 + [False] * 5
PHONES = [f'555-01{i:02d}' for i in range(41)] + ['5550101', '+32 470 123', 'n/a', False, '  ', False, False, False, False]
TYPES = ['contact'] * 30 + ['invoice'] * 12 + ['delivery'] * 8


class Server:
    def __init__(self, url, db):
        self.url = url.rstrip('/')
        if not db:
            request = urllib.request.Request(
                self.url + '/web/database/list', data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {}}).encode(),
                headers={'Content-Type': 'application/json'})
            db = json.load(urllib.request.urlopen(request))['result'][0]
        self.db = db
        self.object = xmlrpc.client.ServerProxy(self.url + '/xmlrpc/2/object')
        self.common = xmlrpc.client.ServerProxy(self.url + '/xmlrpc/2/common')
        self.uids = {}

    def call(self, login, model, method, *args, **kwargs):
        password = 'admin' if login == 'admin' else PASSWORD
        if login not in self.uids:
            self.uids[login] = self.common.authenticate(self.db, login, password, {})
        try:
            return self.object.execute_kw(self.db, self.uids[login], password, model, method, list(args), kwargs)
        except xmlrpc.client.Fault as fault:
            # a method that returns None is done on the server but cannot be marshalled back
            if 'cannot marshal None' in fault.faultString:
                return None
            raise

    def admin(self, model, method, *args, **kwargs):
        return self.call('admin', model, method, *args, **kwargs)

    def find_or_create(self, login, model, domain, values):
        found = self.call(login, model, 'search', domain, limit=1, context={'active_test': False})
        return found[0] if found else self.call(login, model, 'create', values)


def seed_users(srv):
    group = srv.admin('ir.model.data', 'check_object_reference', 'data_profiling_analysis_app', 'group_data_profiling')[1]
    internal = srv.admin('ir.model.data', 'check_object_reference', 'base', 'group_user')[1]
    ids = {}
    for login, name, member in (('dpa_anna', 'Anna Analyst', True), ('dpa_ben', 'Ben Brown', True),
                                ('dpa_paul', 'Paul Outsider', False)):
        groups = [internal] + ([group] if member else [])
        ids[login] = srv.find_or_create('admin', 'res.users', [('login', '=', login)], {
            'name': name, 'login': login, 'email': login + '@example.com', 'password': PASSWORD,
            'group_ids': [(6, 0, groups)]})
    admin_id = srv.admin('res.users', 'search', [('login', '=', 'admin')])[0]
    srv.admin('res.users', 'write', [admin_id], {'group_ids': [(4, group)]})
    return ids


def seed_partners(srv, users):
    tags = {}
    for tag in ('VIP', 'Export', 'Wholesale', 'Newsletter'):
        tags[tag] = srv.find_or_create('admin', 'res.partner.category', [('name', '=', 'DPA ' + tag)], {'name': 'DPA ' + tag})
    sales = [users['dpa_anna']] * 6 + [users['dpa_ben']] * 3 + [False]
    for index in range(50):
        ref = PREFIX + (f'{index:03d}' if index % 5 else f'x{index}')
        if srv.admin('res.partner', 'search_count', [('ref', '=', ref)]):
            continue
        phone = PHONES[index % len(PHONES)]
        srv.admin('res.partner', 'create', {
            'name': f'Demo Contact {index:02d}', 'ref': ref, 'type': TYPES[index],
            'email': f'contact{index:02d}@example.com' if index % 4 else False,
            'phone': phone, 'city': CITIES[index], 'user_id': sales[index % len(sales)],
            'color': 500 if index == 7 else 2 + index % 6,
            'category_id': [(6, 0, [tags['VIP'], tags['Export']][:1 + index % 3 % 2] if index % 3 else [])],
        })
    return tags


def make_profile(srv, login, name, model, field_names, **values):
    model_id = srv.admin('ir.model', 'search', [('model', '=', model)])[0]
    found = srv.call(login, 'dpa.profile', 'search', [('name', '=', name)], context={'active_test': False})
    if found:
        return found[0]
    field_ids = srv.admin('ir.model.fields', 'search', [('model', '=', model), ('name', 'in', field_names)])
    return srv.call(login, 'dpa.profile', 'create', dict(
        name=name, model_id=model_id, field_ids=[(6, 0, field_ids)], **values))


def seed_profiles(srv):
    profile_fields = ['name', 'email', 'phone', 'city', 'ref', 'type', 'user_id', 'category_id', 'color']
    quality = make_profile(srv, 'dpa_anna', 'Customer quality', 'res.partner', profile_fields,
                           empty_threshold=20.0, outdated_days=90)
    saved = srv.call('dpa_anna', 'dpa.snapshot', 'search_count', [('profile_id', '=', quality), ('is_saved', '=', True)])
    while saved < 2:
        if saved == 1:
            # improve the data between the two snapshots so the comparison shows a change
            for partner in srv.admin('res.partner', 'search', [('ref', 'like', PREFIX), ('email', '=', False)], limit=6):
                srv.admin('res.partner', 'write', [partner], {'email': f'fixed{partner}@example.com'})
        report = srv.call('dpa_anna', 'dpa.profile', 'action_run', [quality])['res_id']
        srv.call('dpa_anna', 'dpa.snapshot', 'action_save_snapshot', [report])
        saved += 1
    if not srv.call('dpa_anna', 'dpa.snapshot', 'search_count', [('profile_id', '=', quality), ('is_saved', '=', False)]):
        srv.call('dpa_anna', 'dpa.profile', 'action_run', [quality])  # leaves one unsaved report
    make_profile(srv, 'dpa_ben', 'Contact numbers', 'res.partner', ['color', 'name', 'type'], empty_threshold=5.0)
    empty = make_profile(srv, 'dpa_ben', 'Bank accounts (no records)', 'res.partner.bank', [])
    if not srv.admin('dpa.snapshot', 'search_count', [('profile_id', '=', empty)]):
        srv.call('dpa_ben', 'dpa.profile', 'action_run', [empty])
    scheduled = make_profile(srv, 'dpa_anna', 'Weekly contact check', 'res.partner', ['name', 'email', 'phone'],
                             schedule_active=True, schedule_interval_number=1, schedule_interval_type='weeks',
                             schedule_next_run='2026-12-01 02:00:00')
    return quality, scheduled


MODELS = {
    'res.users': [('login', 'like', 'dpa_')],
    'res.partner': [('ref', 'like', PREFIX)],
    'dpa.profile': [],
    'dpa.snapshot': [('is_saved', '=', True)],
}


def counts(srv):
    return {model: srv.admin(model, 'search_count', domain, context={'active_test': False}) for model, domain in MODELS.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--db')
    parser.add_argument('--count', action='store_true')
    parser.add_argument('--store', action='store_true')
    args = parser.parse_args()
    srv = Server(args.url, args.db)
    if not args.count:
        users = seed_users(srv)
        seed_partners(srv, users)
        seed_profiles(srv)
    current = counts(srv)
    for model, number in current.items():
        print(model, number)
    if args.count and args.store:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(current))
    elif args.count and STORE.exists() and json.loads(STORE.read_text()) != current:
        print('COUNTS DIFFER from', STORE.name, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
