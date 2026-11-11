{
    'name': 'Attachment Management',
    'summary': 'Find, preview and download the attachments of every kind of record from one place',
    'version': '20.0.1.0.0',
    'category': 'Productivity/Documents',
    'depends': [
        'mail',
    ],
    'data': [
        'security/ir.access.csv',
        'views/ir_attachment_views.xml',
        'views/ir_attachment_actions_views.xml',
        'views/menus.xml',
        'views/attachment_naming_rule_views.xml',
        'data/ir_cron_data.xml',
        'views/attachment_zip_download_views.xml',
        'data/ir_cron_zip_data.xml',
        'data/ir_actions_server.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'attachment_management/static/src/am_service.js',
            'attachment_management/static/src/am_duplicate.js',
        ],
    },
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
}
