# -*- coding: utf-8 -*-
# Python standard libraries

# System Modules

# 3rd Party Modules
from pyrevit import script

# Custom Modules


class ExtendedArea(object):
    """Wrapper for rpw.Area with PDSO-specific properties.

    Manages relationships to data silos and area-specific PDSO data.
    Provides automatic property delegation to rpw_area.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        rpw_area: Wrapped rpw.Element (Area) instance
        _hash: Cached geometry hash for change detection
        related_data_silos (list): ExtendedDataSilo objects spatially correlated to this area
        bound_silo_parameter_name (str): Parameter name for binding area values to silos

    Example:
        area = ExtendedArea(rpw_area_element, "PDSO - GSF")
        if area.related_data_silos:
            print("Area contains {} data silos".format(len(area.related_data_silos)))
    """

    def __init__(self, rpw_area, bound_silo_parameter_name):
        """Initialize extended area wrapper.

        Args:
            rpw_area: rpw.Element (Area) instance to wrap
            bound_silo_parameter_name (str): Parameter name for binding area values to data silos
        """

        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        # Not needed

        # Identity/ Lookup Properties
        # Not needed

        # Wrapped Element or Collection
        self.rpw_area = rpw_area  # Store the rpw-wrapped element

        # Hashing for integrity checks
        self._hash = None

        # PDSO-specific properties and correlations
        self.related_data_silos = []  # Dict[str (Area Scheme Name), ExtendedArea]
        self.bound_silo_parameter_name = bound_silo_parameter_name

        # Init Logic
        # Not needed

    @property
    def Id(self):
        """Get the element Id from the wrapped rpw_area.

        Returns:
            ElementId: Revit ElementId of the wrapped area element

        Raises:
            AttributeError: If rpw_area is not set or invalid
        """
        try:
            return self.rpw_area.Id
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Id: rpw_area is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def parameters(self):
        """Get the parameters collection from the wrapped rpw_area.

        Returns:
            ParameterSet: Collection of parameters on the area element

        Raises:
            AttributeError: If rpw_area is not set or invalid
        """
        try:
            return self.rpw_area.parameters
        except AttributeError as e:
            raise AttributeError(
                "Cannot access parameters: rpw_area is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def Level(self):
        """Get the Level property from the wrapped rpw_area.

        Returns:
            Level: Revit Level element associated with this area

        Raises:
            AttributeError: If rpw_area is not set or invalid
        """
        try:
            return self.rpw_area.Level
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Level: rpw_area is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def LevelId(self):
        """Get the LevelId property from the wrapped rpw_area.

        Returns:
            ElementId: Revit ElementId of the associated Level

        Raises:
            AttributeError: If rpw_area is not set or invalid
        """
        try:
            return self.rpw_area.LevelId
        except AttributeError as e:
            raise AttributeError(
                "Cannot access LevelId: rpw_area is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def Area(self):
        """Get the Area property from the wrapped rpw_area.

        Returns:
            float: Area value in square feet (Revit internal units)

        Raises:
            AttributeError: If rpw_area is not set or invalid
        """
        try:
            return self.rpw_area.Area
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Area: rpw_area is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def area_scheme_name(self):
        """Get the Area Scheme Name this area belongs to.

        Returns:
            str: Name of the area scheme

        Raises:
            AttributeError: If rpw_area is not set or invalid
        """
        try:
            return self.rpw_area.AreaScheme.Name
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Area Scheme Name: rpw_area is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    def __getattr__(self, name):
        """
        Fallback for any other attributes not explicitly defined.
        Try to get the attribute from rpw_area with better error handling.
        """
        try:
            return getattr(self.rpw_area, name)
        except AttributeError:
            raise AttributeError(
                "'ExtendedArea' object has no attribute '{}' and neither does its rpw_area".format(
                    name
                )
            )

    @property
    def rpw_element(self):
        """Alias for rpw_area to maintain consistency with other extended classes.

        Returns:
            rpw.Element: The wrapped area element (same as rpw_area)
        """
        return self.rpw_area
