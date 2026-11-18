"""Writing the protected audit trail.

Requirements: BR-SPM-0018, FR-SPM-0109, NFR-SPM-0002
"""


def log(env, event, record, old_values=None, new_values=None, comment=''):
    """Write one audit entry about ``record`` and return it.

    Requirements: BR-SPM-0018

    The entry holds the acting user, the time, the company of the record (empty
    for a shared record), the record, the values before and after and a comment.
    Elevated rights (NFR-SPM-0002 named place: audit write): the trail has no
    create access for any group, so the entry is created with ``sudo()`` after
    the caller has already done its own role check.
    """
    record = record.ensure_one()
    if record._name == 'res.company':
        company = record
    elif 'company_id' in record._fields:
        company = record.company_id
    else:
        company = env['res.company']
    return env['procurement.audit.log'].sudo().create({
        'user_id': env.user.id,
        'company_id': company.id,
        'event': event,
        'res_model': record._name,
        'res_id': record.id,
        'record_name': record.display_name,
        'old_values': old_values or {},
        'new_values': new_values or {},
        'comment': comment,
    })
