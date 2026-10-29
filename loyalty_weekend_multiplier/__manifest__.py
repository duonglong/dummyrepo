{
    'name': 'Loyalty Weekend Multiplier',
    'version': '20.0.1.0.0',
    'summary': 'Multiply loyalty points earned on Saturday and Sunday orders',
    'category': 'Sales',
    'author': 'Empty Sandbox',
    'depends': ['loyalty', 'sale_loyalty', 'pos_loyalty'],
    'data': [
        'views/loyalty_program_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'loyalty_weekend_multiplier/static/src/app/pos_weekend_points.js',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
}
