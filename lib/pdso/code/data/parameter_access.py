# -*- coding: utf-8 -*-
"""Read and write PDSO parameters on an element by GUID, not by name.

A model can hold a project or family parameter with the same name as a PDSO
shared parameter. Looking a parameter up by name can then read or write the
wrong one. The GUID comes from the parameters the model tracks (the registry),
then from the built-in catalog. Only a name with no known GUID is looked up by
name.
"""

# 3rd Party Modules
from rpw import DB, db

# Custom Modules
from pdso.code.data.shared_parameter_constants import (
    PROGRAM_STACKING,
    PROGRAM_UID,
    standard_pdso_parameters,
)

__all__ = [
    "PROGRAM_UID",
    "PROGRAM_STACKING",
    "get_parameter",
    "get_value",
    "has_value",
    "set_if_changed",
    "set_value",
]


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


# Two lengths or areas closer than this are the same value (decimal feet / SF)
DOUBLE_TOLERANCE = 1e-9


def has_value(parameter, value):
    """True if a Revit Parameter already holds value, so writing it changes nothing.

    A text parameter with no value equals "" or None. A number parameter that
    was never set does not equal any number (writing 0 makes it show 0 instead
    of blank). Anything that can't be compared counts as different.
    """
    try:
        storage = parameter.StorageType
        if storage == DB.StorageType.String:
            return (parameter.AsString() or "") == (value or "")
        if value is None or not parameter.HasValue:
            return False
        if storage == DB.StorageType.Double:
            return abs(parameter.AsDouble() - float(value)) < DOUBLE_TOLERANCE
        if storage == DB.StorageType.Integer:
            return parameter.AsInteger() == int(value)
        if storage == DB.StorageType.ElementId:
            return parameter.AsElementId() == value
    except Exception:
        pass
    return False


def set_if_changed(element, name, value):
    """Set a PDSO parameter only if it holds a different value.

    An element that isn't written isn't edited: in a workshared model it isn't
    borrowed and isn't sent at Sync with Central.

    Returns:
        bool: True if the value was written.

    Raises:
        KeyError: The element doesn't have the parameter.
    """
    parameter = get_parameter(element, name)
    if parameter is None:
        raise KeyError("The element has no parameter '{}'".format(name))
    if has_value(parameter, value):
        return False
    db.Parameter(parameter).value = value
    return True
