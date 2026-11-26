{
    'name': 'Data Profiling & Analysis',
    'version': '20.0.1.0.0',
    'author': 'Empty Sandbox',
    'category': 'Productivity',
    'summary': 'Profile the data quality of any model, save snapshots and compare them',
    'depends': ['base', 'web'],
    'external_dependencies': {'python': ['xlsxwriter']},
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'views/profile_views.xml',
        'views/snapshot_views.xml',
        'views/menus.xml',
    ],
    'application': True,
    'license': 'LGPL-3',
}
