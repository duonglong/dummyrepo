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
        'views/fvf_layout_widget_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'form_view_field_organizer/static/src/fvf_section/fvf_section.js',
            'form_view_field_organizer/static/src/fvf_section/fvf_section.xml',
            'form_view_field_organizer/static/src/fvf_section/fvf_section.scss',
            'form_view_field_organizer/static/src/fvf_section/form_compiler.js',
            'form_view_field_organizer/static/src/fvf_layout_lines/fvf_layout_lines.js',
            'form_view_field_organizer/static/src/fvf_layout_lines/fvf_layout_lines.xml',
            'form_view_field_organizer/static/src/fvf_layout_lines/fvf_layout_lines.scss',
        ],
        'web.assets_unit_tests': [
            'form_view_field_organizer/static/tests/**/*.test.js',
        ],
    },
    'license': 'LGPL-3',
}
