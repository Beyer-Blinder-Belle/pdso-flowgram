# -*- coding: utf-8 -*-
# Python standard libraries
from collections import defaultdict

# System Modules

# 3rd Party Modules
from rpw import db, DB
from rpw.utils.dotnet import List
from pyrevit import script

# Custom Modules
from pdso.code.data.extended_data_silo import ExtendedDataSilo


class ExtendedDataSiloCollection(object):
    """Collection wrapper for Data Silo elements.

    Manages a collection of ExtendedDataSilo objects, providing efficient
    querying, validation, and batch operations. Checks for family existence
    before attempting to load instances.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        doc: Revit Document instance
        data_silo_family_name (str): Family name to filter data silo instances
        extended_data_silos (list): Collection of ExtendedDataSilo objects
        family_exists_in_model (bool): True if data silo family is loaded in document
    """

    def __init__(self, doc, data_silo_family_name):
        """Initialize the collection and load all data silo instances if family exists.

        Args:
            doc: Revit Document instance
            data_silo_family_name (str): Family name to filter data silo instances
        """
        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        self.doc = doc

        # Identity/ Lookup Properties
        self.data_silo_family_name = data_silo_family_name

        # Wrapped Element or Collection
        self.extended_data_silos = []

        # Hashing for integrity checks
        self._hash = None

        # PDSO-specific properties and correlations
        self.family_exists_in_model = False

        # Init Logic
        self.logger.info(
            "Initializing ExtendedDataSiloCollection for silos named '{}'".format(
                data_silo_family_name
            )
        )
        self.family_exists_in_model = self.is_family_loaded_in_model()

        if self.family_exists_in_model:
            self.extended_data_silos = self.get_extended_data_silos()

    @property
    def rpw_elements(self):
        """Get the underlying rpw.Element objects for all ExtendedDataSilo instances.

        Returns:
            list: Collection of rpw.Element objects wrapped by ExtendedDataSilo instances
        """
        return self.extended_data_silos

    def is_family_loaded_in_model(self):
        """Check if the Data Silo family is loaded in the current Revit model.

        Returns:
            bool: True if family with matching name exists in document
        """

        built_in_parameter = DB.ElementId(DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        filter_rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(
            built_in_parameter, str(self.data_silo_family_name)
        )
        element_filter = DB.ElementParameterFilter(filter_rule)
        collector = (
            DB.FilteredElementCollector(self.doc)
            .OfClass(DB.FamilySymbol)
            .WherePasses(element_filter)
        )
        symbols = list(collector)

        if symbols is None or len(symbols) == 0:
            return False

        else:
            return True

    def get_extended_data_silos(self):
        """Retrieve all Data Silo instances in the model as ExtendedDataSilo objects.

        Returns:
            list: Collection of ExtendedDataSilo objects for all instances of the specified family

        Notes:
            Continues on error if individual instance cannot be wrapped.
        """
        extended_data_silos = []

        built_in_parameter = DB.ElementId(DB.BuiltInParameter.ELEM_FAMILY_PARAM)
        filter_rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(
            built_in_parameter, str(self.data_silo_family_name)
        )
        element_filter = DB.ElementParameterFilter(filter_rule)
        collector = (
            DB.FilteredElementCollector(self.doc)
            .OfClass(DB.FamilyInstance)
            .WherePasses(element_filter)
        )
        rpw_data_silos = [db.Element(elem) for elem in collector]

        for rpw_data_silo in rpw_data_silos:
            try:
                extended_data_silo = ExtendedDataSilo(rpw_data_silo)
                extended_data_silos.append(extended_data_silo)

            except Exception as e:
                self.logger.warning(
                    "Failed to wrap data silo {}: {}".format(rpw_data_silo.Id, str(e))
                )

        return extended_data_silos

    def are_data_silo_uids_unique(self):
        """Check if all PDSO UIDs in the collection are unique.

        Returns:
            bool: True if all UIDs are unique, False if duplicates exist
        """

        self.logger.debug("Checking if all PDSO UIDs in the collection are unique")

        duplicates = self.get_elements_with_duplicate_values("PDSO - Program UID")

        if duplicates is None:
            return False
        uids_are_unique = len(duplicates) == 0

        return uids_are_unique

    def find_id_int(self, element_id_int):
        """Find a data silo by its element ID."""
        return next(
            (
                data_silo
                for data_silo in self.extended_data_silos
                if data_silo.Id == element_id_int
            ),
            None,
        )

    def filter_by_level_int(self, level_id_int):
        """Filter ExtendedDataSilo objects by LevelIdInt property."""
        return [
            ext_data_silo
            for ext_data_silo in self.extended_data_silos
            if ext_data_silo.level_id_int == level_id_int
        ]

    def get_elements_with_duplicate_values(self, parameter_name):
        """
        Find elements that have duplicate values for a given parameter.

        Args:
            elements: List of elements to check
            parameter_name: Name of parameter to check for duplicates

        Returns:
            Dict mapping duplicate values to lists of elements
        """

        try:
            self.logger.debug(
                "Checking for duplicate values in parameter '{}' across ExtendedDataSilo collection".format(
                    parameter_name
                )
            )
            value_to_elements = defaultdict(list)

            for ext_data_silo in self.extended_data_silos:
                try:
                    # Safely access parameter.. .Id returns a ElementID object, need .Value for int
                    """
                    if parameter_name not in ext_data_silo.rpw_element.parameters:
                        self.logger.debug("Parameter '{}' not found in element {}".format(parameter_name, str(ext_data_silo.rpw_element)))
                        continue
                    """
                    param = ext_data_silo.rpw_element.parameters[parameter_name]
                    if param is None:
                        # self.logger.debug("Parameter '{}' is None for element {}".format(parameter_name, str(ext_data_silo.rpw_element.Id)))
                        continue

                    value = param.value
                    if value is None:
                        # self.logger.debug("Parameter '{}' has None value for element {}".format(parameter_name, str(ext_data_silo.rpw_element.Id)))
                        continue

                    value_to_elements[value].append(ext_data_silo)

                    """
                    except (AttributeError, KeyError) as e:
                        self.logger.warning("Error accessing parameter '{}' for element : {}".format(
                            parameter_name, str(e)))
                        continue
                    """

                except Exception as e:
                    self.logger.error(
                        "Unexpected error processing element: {}".format(str(e))
                    )
                    continue

            # Build duplicate results
            duplicates = {
                value: elements
                for value, elements in value_to_elements.items()
                if len(elements) > 1
            }
            """
            self.logger.debug("Found {} duplicate values for parameter '{}' in ExtendedDataSilo collection".format(
                len(duplicates), parameter_name))
            """
            # Log duplicate details separately to avoid crashes from large dict comprehensions
            for value, elements in duplicates.items():
                self.logger.debug(
                    "  Value '{}': {} elements with IDs {}".format(
                        value,
                        len(elements),
                        [ext_data_silo.Id for ext_data_silo in elements],
                    )
                )
            return duplicates

        except Exception as e:
            self.logger.error(
                "Critical error in get_elements_with_duplicate_values for parameter '{}': {}".format(
                    parameter_name, str(e)
                )
            )
            return {}

    def get_data_silos_without_program_volume_family(self):
        """Get a list of ExtendedDataSilo objects that are not linked to any program volume family."""

        # Use list comprehension to filter silos without a linked program volume family
        silos_without_family = [
            ext_data_silo
            for ext_data_silo in self.extended_data_silos
            if ext_data_silo.related_program_volume_family is None
        ]
        return silos_without_family

    def check_worksharing_availability(self):
        """Check that all required elements are available for editing in workshared models

        Returns:
            list: List of unavailable element IDs
        """

        # Try to check out all data silos
        element_ids_to_checkout = [
            ext_data_silo.Id for ext_data_silo in self.extended_data_silos
        ]
        typed_list_of_elements_to_check_out = List[DB.ElementId](
            element_ids_to_checkout
        )
        checked_out_elements = DB.WorksharingUtils.CheckoutElements(
            self.doc, typed_list_of_elements_to_check_out
        )

        # Find elements that couldn't be checked out
        unavailable_element_ids = []

        for element_id in element_ids_to_checkout:
            if element_id not in checked_out_elements:
                unavailable_element_ids.append(element_id)

        return unavailable_element_ids

    def get_data_silos_with_partial_coverage(self, configured_schemes):
        """Get data silos that are missing correlations for some configured area schemes.

        Args:
            configured_schemes (list): List of configured area scheme names (e.g., ["PDSO - GSF", "PDSO - NSF"])

        Returns:
            list: List of dicts with {'id': ElementId, 'uid': str, 'level': str, 'missing_schemes': list}
        """
        partial_coverage_silos = []
        for ext_data_silo in self.extended_data_silos:
            missing_schemes = []
            for scheme_name in configured_schemes:
                if (
                    scheme_name not in ext_data_silo.related_area_elements
                    or ext_data_silo.related_area_elements[scheme_name] is None
                ):
                    missing_schemes.append(scheme_name)

            if missing_schemes and len(missing_schemes) < len(configured_schemes):
                # Has some but not all schemes
                uid = "Unknown"
                try:
                    uid_param = ext_data_silo.rpw_element.parameters.get(
                        "PDSO - Program UID"
                    )
                    if uid_param and uid_param.value:
                        uid = uid_param.value
                except:
                    pass

                partial_coverage_silos.append(
                    {
                        "id": ext_data_silo.Id,
                        "uid": uid,
                        "level": ext_data_silo.Level.Name
                        if ext_data_silo.Level
                        else "N/A",
                        "missing_schemes": missing_schemes,
                    }
                )
        return partial_coverage_silos

    def get_data_silos_without_coverage(self):
        """Get data silos that have no area correlations at all.

        Returns:
            list: List of dicts with {'id': ElementId, 'uid': str, 'level': str}
        """
        no_coverage_silos = []
        for ext_data_silo in self.extended_data_silos:
            if (
                not ext_data_silo.related_area_elements
                or len(ext_data_silo.related_area_elements) == 0
            ):
                uid = "Unknown"
                try:
                    uid_param = ext_data_silo.rpw_element.parameters.get(
                        "PDSO - Program UID"
                    )
                    if uid_param and uid_param.value:
                        uid = uid_param.value
                except:
                    pass

                no_coverage_silos.append(
                    {
                        "id": ext_data_silo.Id,
                        "uid": uid,
                        "level": ext_data_silo.Level.Name
                        if ext_data_silo.Level
                        else "N/A",
                    }
                )
        return no_coverage_silos
