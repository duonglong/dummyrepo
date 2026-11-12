{
    'name': 'Form View Field Organizer',
    'summary': 'Hide, group, reorder and conditionally show fields on any form, per group and company',
    'version': '20.0.1.0.0',
    'category': 'Technical',
    'author': 'Empty Sandbox',
    'depends': ['web'],
    'data': [
        'security/ir.access.csv',
        'views/fvf_layout_views.xml',
        'views/fvf_layout_preview_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'form_view_field_organizer/static/src/fvf_layout_preview/*',
        ],
        'web.assets_unit_tests': [
            'form_view_field_organizer/static/tests/fvf_layout_preview.test.js',
        ],
    },
    'license': 'LGPL-3',
}
