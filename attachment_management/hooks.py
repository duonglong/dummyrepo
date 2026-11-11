from odoo.tools import SQL

from .models.ir_attachment import OTHER_GROUP, TYPE_GROUPS


def _type_group_case():
    """SQL CASE giving the file type group of a mimetype, built from the Python table."""
    branches = [
        SQL("WHEN mimetype LIKE ANY (%s) THEN %s",
            [pattern.replace('*', '%') for pattern in patterns], group)
        for group, patterns in TYPE_GROUPS
    ]
    return SQL("CASE %s ELSE %s END", SQL(" ").join(branches), OTHER_GROUP)


def pre_init_hook(env):
    """Requirements: FR-AM-0005, NFR-AM-0001.

    Add and fill the two stored search columns by SQL so a large attachment
    table is not recomputed through the ORM.
    """
    cr = env.cr
    cr.execute("ALTER TABLE ir_attachment ADD COLUMN IF NOT EXISTS file_type_group varchar")
    cr.execute("ALTER TABLE ir_attachment ADD COLUMN IF NOT EXISTS res_model_id integer")
    cr.execute(SQL("UPDATE ir_attachment SET file_type_group = %s", _type_group_case()))
    cr.execute("""
        UPDATE ir_attachment a SET res_model_id = m.id
          FROM ir_model m WHERE m.model = a.res_model
    """)


def post_init_hook(env):
    """Requirements: FR-AM-0004. Fill the record labels of the attachments already stored."""
    env['ir.attachment']._cron_refresh_record_labels()
