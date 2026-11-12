{
    'name': 'Smart Field Dependency Viewer',
    'summary': 'See which computed and related fields depend on a field, as a list and a diagram',
    'version': '20.0.1.0.0',
    'category': 'Technical',
    'depends': ['web'],
    'data': [
        'security/ir.access.csv',
        'views/sfd_dependency_viewer_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'smart_field_dependency_viewer/static/src/sfd_dependency_map/*',
        ],
        'web.assets_unit_tests': [
            'smart_field_dependency_viewer/static/tests/*.test.js',
        ],
    },
    'license': 'LGPL-3',
}
