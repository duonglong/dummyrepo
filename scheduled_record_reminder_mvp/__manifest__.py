{
    'name': 'Scheduled Record Reminder',
    'version': '20.0.1.0.0',
    'author': 'Empty Sandbox',
    'category': 'Productivity',
    'summary': 'Remind people by email or activity before or after the date of any record',
    'depends': ['mail'],
    'data': [
        'security/reminder_security.xml',
        'security/ir.access.csv',
        'data/ir_cron_data.xml',
        'views/record_reminder_rule_views.xml',
        'views/menus.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'application': True,
}
