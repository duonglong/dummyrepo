{
    'name': 'Duplicate Record Detector',
    'version': '20.0.1.0.0',
    'summary': 'Find possible duplicate contacts and products with your own rules',
    'category': 'Productivity',
    'depends': ['mail', 'product'],
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'data/ir_cron.xml',
        'views/duplicate_group_views.xml',
        'wizard/duplicate_merge_wizard_views.xml',
        'views/duplicate_rule_views.xml',
        'views/menus.xml',
    ],
    'author': 'Empty Sandbox',
    'license': 'LGPL-3',
}
