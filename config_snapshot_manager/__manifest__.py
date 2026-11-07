{
    'name': 'Config Snapshot Manager',
    'version': '20.0.1.0.0',
    'summary': 'Export, compare and apply system settings between environments',
    'author': 'Empty Sandbox',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [
        'security/ir.access.csv',
        'views/config_snapshot_item_views.xml',
        'views/config_snapshot_log_views.xml',
        'views/menus.xml',
    ],
}
