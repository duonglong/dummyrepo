{
    'name': 'Performance Audit',
    'version': '20.0.1.0.0',
    'depends': ['base'],
    'data': [
        'security/ir.access.csv',
        'data/perf_audit_check_data.xml',
        'data/perf_audit_threshold_data.xml',
        'data/perf_audit_action_data.xml',
        'views/perf_audit_check_views.xml',
        'views/perf_audit_finding_views.xml',
        'views/perf_audit_run_views.xml',
        'views/perf_audit_menus.xml',
    ],
    'license': 'LGPL-3',
}
