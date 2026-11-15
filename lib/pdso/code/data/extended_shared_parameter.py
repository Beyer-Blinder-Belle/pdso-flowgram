# -*- coding: utf-8 -*-
"""PDSO Parameter Definition - Core parameter blueprint and domain model.

This module contains the core parameter definition class that serves as a blueprint
for PDSO parameters, plus the table of parameter data types the PDSO tools support.
"""

# System Modules
from System import Guid

# 3rd Party Modules
from rpw import DB
from pyrevit import script

# Supported PDSO parameter data types.
# Key: internal type name (stored in code, never in the model)
# Value: (label shown in the UI, dotted path under DB.SpecTypeId, default value)
PARAMETER_TYPES = {
    "string": ("Text", "String.Text", ""),
    "multilineText": ("Multiline Text", "String.MultilineText", ""),
    "number": ("Number", "Number", 0.0),
    "integer": ("Integer", "Int.Integer", 0),
    "area": ("Area", "Area", 0.0),
    "length": ("Length", "Length", 0.0),
    "yesno": ("Yes/No", "Boolean.YesNo", 0),
}

# Display order for type pickers in the UI
PARAMETER_TYPE_ORDER = [
    "string",
    "multilineText",
    "number",
    "integer",
    "area",
    "length",
    "yesno",
]

# Kept for backwards compatibility with older imports
VALID_PARAMETER_TYPES = list(PARAMETER_TYPE_ORDER)

# Parameter roles
# core:     the PDSO tools read or write it directly; it can never be untracked
# area:     receives an Area value during Area Harvest; required while an
#           Area Scheme mapping points at it
# standard: a built-in program data field; optional
# custom:   added by a user in the Parameter Manager; optional
ROLE_CORE = "core"
ROLE_AREA = "area"
ROLE_STANDARD = "standard"
ROLE_CUSTOM = "custom"


def spec_type_id_for(parameter_type):
    """Return the Revit ForgeTypeId (SpecTypeId) for a PDSO parameter type.

    Args:
        parameter_type (str): Key of PARAMETER_TYPES

    Returns:
        ForgeTypeId: The matching SpecTypeId
    """
    spec = DB.SpecTypeId
    for part in PARAMETER_TYPES[parameter_type][1].split("."):
        spec = getattr(spec, part)
    return spec


def parameter_type_from_spec(spec_type_id):
    """Map a Revit ForgeTypeId back to a PDSO parameter type.

    Args:
        spec_type_id (ForgeTypeId): Data type of a parameter definition

    Returns:
        str or None: Key of PARAMETER_TYPES, or None if the type isn't supported
    """
    if spec_type_id is None:
        return None
    for parameter_type in PARAMETER_TYPE_ORDER:
        if spec_type_id_for(parameter_type) == spec_type_id:
            return parameter_type
    return None


def parameter_type_label(parameter_type):
    """Return the UI label for a PDSO parameter type (or the raw value if unknown)."""
    if parameter_type in PARAMETER_TYPES:
        return PARAMETER_TYPES[parameter_type][0]
    return parameter_type or "Unsupported"


class ExtendedSharedParameter(object):
    """Core parameter definition class - blueprint for a PDSO parameter.

    An instance of this class is not a parameter itself, it is more like the blueprint of a parameter.
    Parameter definitions can be used to:
    - Validate parameters exist on specific categories
    - Create shared parameter definitions and bind them to the model
    - Bind parameters to Area Schemes for area value synchronization
    - Specify parameters for integrity hash calculation

    Attributes:
        parameter_name (str): Display name of the parameter
        parameter_type (str): Key of PARAMETER_TYPES (None if the model's type isn't supported)
        is_required (bool): True if the parameter can never be untracked
        parameter_guid (Guid): System.Guid for the shared parameter
        role (str): One of the ROLE_* constants
        description (str): Tooltip text for the shared parameter definition
        user_modifiable (bool): False if users shouldn't edit the value in Revit's UI

    Example:
        param_def = ExtendedSharedParameter(
            "PDSO - Program UID",
            "string",
            True,
            'd5695975-02f2-4f10-ac73-a803c8c3c6cd'
        )
        if param_def.is_available_on_element(element):
            print("Parameter exists on element")
    """

    def __init__(
        self,
        parameter_name,
        parameter_type,
        is_required,
        guid_string,
        role=ROLE_STANDARD,
        description="",
        user_modifiable=True,
    ):
        """Initialize a parameter definition.

        Args:
            parameter_name (str): Display name of the parameter
            parameter_type (str): Key of PARAMETER_TYPES, or None if unsupported
            is_required (bool): Whether this parameter can never be untracked
            guid_string (str or Guid or None): GUID for the shared parameter
                (None for a new custom parameter; Revit assigns one on creation)
            role (str): One of the ROLE_* constants
            description (str): Tooltip text for the shared parameter definition
            user_modifiable (bool): Whether users may edit the value in Revit's UI

        Raises:
            ValueError: If parameter_type is not None and not in PARAMETER_TYPES
        """
        if parameter_type is not None and parameter_type not in PARAMETER_TYPES:
            raise ValueError(
                "Invalid parameter_type '{}'. Must be one of: {}".format(
                    parameter_type, ", ".join(PARAMETER_TYPE_ORDER)
                )
            )
        self.parameter_name = parameter_name
        self.parameter_type = parameter_type
        self.is_required = is_required
        # None for a new custom parameter that hasn't been created yet
        if guid_string is None or isinstance(guid_string, Guid):
            self.parameter_guid = guid_string
        else:
            self.parameter_guid = Guid(guid_string)
        self.role = role
        self.description = description
        self.user_modifiable = user_modifiable

    def copy(self, **overrides):
        """Return a copy of this definition with some attributes replaced."""
        values = {
            "parameter_name": self.parameter_name,
            "parameter_type": self.parameter_type,
            "is_required": self.is_required,
            "guid_string": self.parameter_guid,
            "role": self.role,
            "description": self.description,
            "user_modifiable": self.user_modifiable,
        }
        values.update(overrides)
        return ExtendedSharedParameter(**values)

    def is_available_on_element(self, rpw_element):
        """Check if this parameter is available on the given element.

        Logs detailed information about the parameter if found.

        Args:
            rpw_element: rpw.Element wrapped Revit element

        Returns:
            bool: True if parameter exists on element, False otherwise
        """
        output_logger = script.get_logger()
        revit_parameter = rpw_element.get_Parameter(self.parameter_guid)

        if revit_parameter is None:
            output_logger.info(self.parameter_name + " exists: " + str(False))
            return False
        else:
            output_logger.info(self.parameter_name + " exists: " + str(True))
            output_logger.info("Parameter name is: " + revit_parameter.Definition.Name)
            output_logger.info("Is the param shared? " + str(revit_parameter.IsShared))
            output_logger.info(
                "Storage type of the param: " + str(revit_parameter.StorageType)
            )
            output_logger.info("--------")
            return True

    def has_default_value(self):
        """True if default_value() can be used to reset this parameter."""
        return self.parameter_type in PARAMETER_TYPES

    def default_value(self):
        """Get the default (reset) value for this parameter type.

        Returns:
            Default value appropriate for parameter type:
            - Text types: empty string
            - Number, Area, Length: 0.0
            - Integer, Yes/No: 0

        Raises:
            ValueError: If parameter_type is not supported
        """
        if self.parameter_type not in PARAMETER_TYPES:
            raise ValueError(
                "No default value for parameter '{}' of unsupported type '{}'".format(
                    self.parameter_name, self.parameter_type
                )
            )

        return PARAMETER_TYPES[self.parameter_type][2]
