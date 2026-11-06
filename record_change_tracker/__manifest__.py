{
    'name': 'Record Change Tracker',
    'version': '20.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Change history of contacts, sales orders, purchase orders and products',
    'depends': ['mail', 'sale', 'purchase'],
    'data': [
        'security/record_change_security.xml',
        'views/record_change_settings_views.xml',
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/product_template_views.xml',
        'data/record_change_data.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
}
