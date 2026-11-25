"""Mark the lead time, payment days and warranty that existing quotations state as given.

A stored 0 could not tell a stated 0 from a blank field, so only a number above 0 is taken as stated.
"""


def migrate(cr, version):
    cr.execute("UPDATE procurement_sourcing_response_line SET lead_time_given = TRUE WHERE lead_time_days > 0")
    cr.execute("""
        UPDATE procurement_sourcing_response_version
           SET payment_days_given = payment_days > 0, warranty_given = warranty_months > 0""")
