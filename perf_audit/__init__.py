"""Performance audit: joins PostgreSQL statistics with the Odoo registry.

Requirements: NFR-OPA-0002 (the module uses only the rights Odoo already has).
"""
from . import checks
from . import models
from . import wizard
