# -*- coding: utf-8 -*-
# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import db, DB
from rpw.utils.dotnet import List
from pyrevit import script

# Custom Modules
from pdso.code.data.extended_program_volume_family import ExtendedProgramVolumeFamily


class ExtendedProgramVolumeFamilyCollection(object):
    """Collection wrapper for Program Volume Family elements.

    Manages a collection of ExtendedProgramVolumeFamily objects, providing
    efficient querying and batch operations for program volume families.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        doc: Revit Document instance
        program_volume_family_name_prefix (str): Family name prefix for filtering
        extended_program_volume_families (list): Collection of ExtendedProgramVolumeFamily objects
    """

    def __init__(self, doc, program_volume_family_name_prefix):
        """Initialize the collection and load all program volume families.

        Args:
            doc: Revit Document instance
            program_volume_family_name_prefix (str): Prefix to filter program volume families
        """
        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        self.doc = doc

        # Identity/ Lookup Properties
        self.program_volume_family_name_prefix = program_volume_family_name_prefix

        # Wrapped Element or Collection
        self.extended_program_volume_families = []

        # Hashing for integrity checks
        self._hash = None

        # PDSO-specific properties and correlations
        # Not needed

        # Init Logic
        self.logger.debug(
            "Initializing ExtendedProgramVolumeFamilyCollection for volumes with family name prefix '{}'".format(
                program_volume_family_name_prefix
            )
        )

        # to do - review pattern for conisistency - mutate vs return
        self.extended_program_volume_families = (
            self.get_extended_program_volume_families()
        )

    def get_extended_program_volume_families(self):
        """Retrieve all Program Volume Families in the model as ExtendedProgramVolumeFamily objects.

        Returns:
            list: Collection of ExtendedProgramVolumeFamily objects matching the family name prefix
        """

        wrapped_generic_model_families = db.Collector(
            doc=self.doc, of_class="Family"
        ).get_elements(wrapped=True)

        # Use list comprehension to retain only the families that start with the correct prefix
        extended_program_volume_families = [
            ExtendedProgramVolumeFamily(family)
            for family in wrapped_generic_model_families
            if family.Name.startswith(self.program_volume_family_name_prefix)
        ]

        return extended_program_volume_families

    def get_program_volume_families_without_data_silos(self):
        """Get a list of ExtendedProgramVolumeFamily objects that are not linked to any data silo.

        Returns:
            list: ExtendedProgramVolumeFamily objects with parent_data_silo set to None
        """

        # Use list comprehension to filter families without a linked data silo
        families_without_silo = [
            ext_program_volume_family
            for ext_program_volume_family in self.extended_program_volume_families
            if ext_program_volume_family.parent_data_silo is None
        ]
        return families_without_silo

    def check_worksharing_availability(self):
        """Check that all required elements are available for editing in workshared models

        Returns:
            list: List of unavailable element IDs
        """

        # Try to check out all data silos
        element_ids_to_checkout = [
            ext_program_volume_family.Id
            for ext_program_volume_family in self.extended_program_volume_families
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

    def get_orphaned_families(self):
        """Get program volume families without a parent data silo.

        Returns:
            list: List of dicts with {'name': str, 'id': ElementId (if available)}
        """
        orphaned = []
        for ext_family in self.extended_program_volume_families:
            if ext_family.parent_data_silo is None:
                family_dict = {
                    "name": ext_family.rpw_element.Name
                    if ext_family.rpw_element
                    else "Unknown"
                }
                # Family elements may not have a direct ID in the same way as instances
                try:
                    if hasattr(ext_family, "Id"):
                        family_dict["id"] = ext_family.Id
                except:
                    pass

                orphaned.append(family_dict)
        return orphaned
