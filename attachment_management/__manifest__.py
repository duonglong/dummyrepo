{
    'name': 'Attachment Management',
    'summary': 'Find, preview and download the attachments of every kind of record from one place',
    'version': '20.0.1.0.0',
    'category': 'Productivity/Documents',
    'depends': ['mail'],
    'data': [
        'views/ir_attachment_views.xml',
        'views/menus.xml',
        'data/ir_cron_data.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
}
