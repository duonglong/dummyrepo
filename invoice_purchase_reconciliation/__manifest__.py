# Requirements: NFR-IPR-0003, NFR-IPR-0004
{
    'name': 'Invoice & Purchase Reconciliation',
    'version': '20.0.1.0.0',
    'summary': 'Check vendor bills against purchase orders and receipts',
    'author': 'Empty Sandbox',
    'category': 'Accounting/Purchase',
    'license': 'LGPL-3',
    'depends': ['purchase_stock'],
    'data': [
        'security/ir.access.csv',
        'views/ipr_tolerance_views.xml',
        'views/ipr_history_views.xml',
        'views/account_move_views.xml',
        'views/ipr_approval_views.xml',
    ],
    'application': True,
}
