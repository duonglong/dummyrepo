{
    'name': 'Loyalty weekend double points',
    'version': '20.0.1.0.0',
    'category': 'Sales/Loyalty',
    'summary': 'Double loyalty points on purchases made on Saturday and Sunday',
    'depends': ['sale_loyalty', 'pos_loyalty'],
    'data': ['views/loyalty_program_views.xml'],
    'assets': {
        'point_of_sale._assets_pos': [
            'loyalty_weekend_double_points/static/src/app/**/*',
        ],
        'web.assets_tests': [
            'loyalty_weekend_double_points/static/tests/tours/**/*',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
