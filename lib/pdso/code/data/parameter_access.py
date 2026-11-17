# -*- coding: utf-8 -*-
"""Read and write PDSO parameters on an element by GUID, not by name.

A model can hold a project or family parameter with the same name as a PDSO
shared parameter. Looking a parameter up by name can then read or write the
wrong one. The GUID comes from the parameters the model tracks (the registry),
then from the built-in catalog. Only a name with no known GUID is looked up by
name.
"""

# 3rd Party Modules
from rpw import db

# Custom Modules
from pdso.code.data.shared_parameter_constants import (
    PROGRAM_STACKING,
    PROGRAM_UID,
    standard_pdso_parameters,
)

__all__ = ["PROGRAM_UID", "PROGRAM_STACKING", "get_parameter", "get_value", "set_value"]


def _unwrap(element):
    return element.unwrap() if hasattr(element, "unwrap") else element


def _guid(name, doc):
    try:
        # Imported here: the registry service imports the data layer
        from pdso.code.business.parameter_registry_service import get_registry

        tracked = get_registry(doc).get(name)
    except Exception:
        tracked = None
    if tracked is not None and tracked.parameter_guid is not None:
        return tracked.parameter_guid
    builtin = standard_pdso_parameters.get(name)
    return builtin.parameter_guid if builtin is not None else None


def get_parameter(element, name):
    """The element's Revit Parameter for a PDSO parameter name, or None."""
    element = _unwrap(element)
    guid = _guid(name, element.Document)
    if guid is not None:
        return element.get_Parameter(guid)
    return element.LookupParameter(name)


def get_value(element, name, default=None):
    """The value of a PDSO parameter (as rpw reads it), or default if it's missing."""
    parameter = get_parameter(element, name)
    if parameter is None:
        return default
    return db.Parameter(parameter).value


def set_value(element, name, value):
    """Set a PDSO parameter. Raises KeyError if the element doesn't have it."""
    parameter = get_parameter(element, name)
    if parameter is None:
        raise KeyError("The element has no parameter '{}'".format(name))
    db.Parameter(parameter).value = value
