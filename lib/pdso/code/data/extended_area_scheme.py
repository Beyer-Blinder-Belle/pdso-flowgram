# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import db, DB
from pyrevit import script

# Custom Modules
from pdso.code.data.extended_area import (
    ExtendedArea,
)  # Import here to avoid circular dependency


class ExtendedAreaScheme(object):
    """
    Wrapper for rpw.Area objects with additional PDSO-specific properties.

    This makes all properties explicit and provides type hints/documentation.
    """

    def __init__(self, doc, area_scheme_name, bound_silo_parameter_name):

        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        self.doc = doc

        # Identity/ Lookup Properties
        self.area_scheme_name = area_scheme_name
        self.area_scheme_exists_in_model = False

        # Wrapped Element or Collection
        self.rpw_area_scheme = None
        self.extended_areas = []

        # Hashing for integrity checks
        # Not needed

        # PDSO-specific properties and correlations
        self.bound_silo_parameter_name = bound_silo_parameter_name

        # Init Logic
        self.logger.debug(
            "Initializing ExtendedAreaScheme for scheme '{}'".format(area_scheme_name)
        )

        # Try to get the Area Scheme from the model
        self.logger.debug(
            "Attempting to retrieve Area Scheme '{}' from model".format(
                area_scheme_name
            )
        )
        self.rpw_area_scheme = self.get_area_scheme_by_name()

        # Confirm area scheme exists in the model
        self.area_scheme_exists_in_model = self.is_area_scheme_loaded_in_model()

        # assign the rpw area scheme object
        if self.area_scheme_exists_in_model:
            self.extended_areas = self.get_extended_areas()

    def get_area_scheme_by_name(self):
        """Retrieve the Area Scheme by name from the Revit document."""
        self.logger.debug(
            "Searching for Area Scheme '{}' in model".format(self.area_scheme_name)
        )

        rpw_area_scheme = db.Collector(
            doc=self.doc,
            of_class=DB.AreaScheme,
            where=lambda x: x.parameters["Name"] == self.area_scheme_name,
        ).get_first(wrapped=True)

        return rpw_area_scheme

    def is_area_scheme_loaded_in_model(self):
        """Check if the Area Scheme exists in the current Revit model."""
        self.logger.debug(
            "Checking if Area Scheme '{}' exists in model".format(self.area_scheme_name)
        )

        if self.rpw_area_scheme is None:
            self.logger.debug(
                "Area scheme '{}' not found in model (expected - not all schemes required)".format(
                    self.area_scheme_name
                )
            )
            return False
        else:
            self.logger.debug(
                "Area scheme '{}' found in model".format(self.area_scheme_name)
            )
            return True

    def get_extended_areas(self):
        """Retrieve all Area instances in the model as ExtendedArea objects."""
        extended_areas = []

        built_in_parameter = db.BipEnum.get_id("AREA_SCHEME_ID")
        filter = db.ParameterFilter(built_in_parameter, equals=self.rpw_area_scheme.Id)
        collector = db.Collector(parameter_filter=filter)
        rpw_areas = collector.get_elements(wrapped=True)

        for rpw_area in rpw_areas:
            try:
                extended_area = ExtendedArea(rpw_area, self.bound_silo_parameter_name)
                extended_areas.append(extended_area)

            except Exception as e:
                self.logger.warning(
                    "Failed to wrap area {}: {}".format(rpw_area.Id, str(e))
                )

        return extended_areas

    def get_grouped_areas(self):
        """Get all areas that are part of a group (will be skipped during sync).

        Returns:
            list: List of dicts with {'id': ElementId, 'scheme': str, 'level': str}
        """
        grouped_areas = []
        for extended_area in self.extended_areas:
            if extended_area.rpw_area.GroupId != DB.ElementId.InvalidElementId:
                grouped_areas.append(
                    {
                        "id": extended_area.Id,
                        "scheme": self.area_scheme_name,
                        "level": extended_area.Level.Name
                        if extended_area.Level
                        else "N/A",
                    }
                )
        return grouped_areas

    def get_areas_without_data_silos(self):
        """Get all areas that don't have any correlated data silos.

        Returns:
            list: List of dicts with {'id': ElementId, 'scheme': str, 'level': str, 'name': str}
        """
        areas_without_silos = []
        for extended_area in self.extended_areas:
            if not extended_area.related_data_silos:
                area_name = "Unnamed"
                try:
                    name_param = extended_area.rpw_area.parameters.get("Name")
                    if name_param and name_param.value:
                        area_name = name_param.value
                except:
                    pass

                areas_without_silos.append(
                    {
                        "id": extended_area.Id,
                        "scheme": self.area_scheme_name,
                        "level": extended_area.Level.Name
                        if extended_area.Level
                        else "N/A",
                        "name": area_name,
                    }
                )
        return areas_without_silos
