# -*- coding: utf-8 -*-
"""PDSO Parameter Definition - Core parameter blueprint and domain model.

This module contains the core parameter definition class that serves as a blueprint
for PDSO parameters.
"""

# System Modules
from System import Guid

# 3rd Party Modules
from rpw import DB
from pyrevit import script

# Valid parameter types for PDSO shared parameters
VALID_PARAMETER_TYPES = ["string", "multilineText", "number", "area", "ElementId"]


class ExtendedSharedParameter(object):
    """Core parameter definition class - blueprint for a PDSO parameter.

    An instance of this class is not a parameter itself, it is more like the blueprint of a parameter.
    Parameter definitions can be used to:
    - Validate parameters exist on specific categories
    - Define parameters on families (instance or type parameters)
    - Bind parameters to Area Schemes for area value synchronization
    - Specify parameters for integrity hash calculation

    Attributes:
        parameter_name (str): Display name of the parameter
        parameter_type (str): Type of parameter (must be one of VALID_PARAMETER_TYPES)
        is_required (bool): Whether this parameter is required for PDSO operations
        parameter_guid (Guid): System.Guid for the shared parameter

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

    def __init__(self, parameter_name, parameter_type, is_required, guid_string):
        """Initialize a parameter definition.

        Args:
            parameter_name (str): Display name of the parameter
            parameter_type (str): Type of parameter (must be one of VALID_PARAMETER_TYPES)
            is_required (bool): Whether this parameter is required
            guid_string (str): GUID string for the shared parameter

        Raises:
            ValueError: If parameter_type is not in VALID_PARAMETER_TYPES
        """
        if parameter_type not in VALID_PARAMETER_TYPES:
            raise ValueError(
                "Invalid parameter_type '{}'. Must be one of: {}".format(
                    parameter_type, ", ".join(VALID_PARAMETER_TYPES)
                )
            )
        self.parameter_name = parameter_name
        self.parameter_type = parameter_type
        self.is_required = is_required
        self.parameter_guid = Guid(guid_string)

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

    def default_value(self):
        """Get the default value for this parameter type.

        Returns:
            Default value appropriate for parameter type:
            - string/multilineText: empty string
            - number/area: 0.0
            - ElementId: InvalidElementId

        Raises:
            ValueError: If parameter_type is not recognized
        """
        # Dictionary mapping parameter types to default values
        defaults = {
            "string": "",
            "multilineText": "",
            "number": 0.0,
            "area": 0.0,
            "ElementId": DB.ElementId.InvalidElementId,
        }

        if self.parameter_type not in defaults:
            raise ValueError(
                "Unrecognized parameter_type: '{}'. This should not happen if __init__ validation passed.".format(
                    self.parameter_type
                )
            )

        return defaults[self.parameter_type]
