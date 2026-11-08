"""Every module of this package is imported here, so a new check is one new file."""
import importlib
import pkgutil

from . import base

for _module in pkgutil.iter_modules(__path__):
    if _module.name != 'base':
        importlib.import_module(f'{__name__}.{_module.name}')
