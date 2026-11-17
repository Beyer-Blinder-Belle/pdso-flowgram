# -*- coding: utf-8 -*-
# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import db, DB
from rpw.utils.dotnet import List
from pyrevit import script

# Custom Modules
from pdso.code.data.extended_program_volume_instance import (
    ExtendedProgramVolumeInstance,
)


class ExtendedProgramVolumeInstanceCollection(object):
    """Collection wrapper for Program Volume Instance elements.

    Manages a collection of ExtendedProgramVolumeInstance objects, providing
    efficient querying, validation, and batch operations. Handles orphan detection
    and deletion of instances no longer associated with data silos.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        doc: Revit Document instance
        program_volume_family_name_prefix (str): Family name prefix for filtering
        validation_passed (bool): True if level mapping validation passed
        validation_result: ValidationResult object with error details if validation failed
        instances_to_delete (list): ExtendedProgramVolumeInstance objects marked for deletion
        extended_program_volume_instances (list): Collection of ExtendedProgramVolumeInstance objects
        wrapped_program_volume_families (dict): Dictionary mapping family names to Family objects
    """

    def __init__(self, doc, program_volume_family_name_prefix):
        """Initialize the collection and load all program volume instances.

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
        # Not needed

        # Hashing for integrity checks
        self._hash = None

        # PDSO-specific properties and correlations
        # Not needed

        # Level mapping validation
        # TO DO - look at naming here, make less ambiguous
        self.validation_passed = False  # True if level mapping validation passed
        self.validation_result = None  # ValidationResult object with error details
        self.instances_to_delete = []  # List[ExtendedProgramVolumeInstance] - orphaned instances

        # Init Logic
        self.logger.debug(
            "Initializing ExtendedProgramVolumeInstanceCollection for volumes with family name prefix '{}'".format(
                program_volume_family_name_prefix
            )
        )

        self.extended_program_volume_instances = (
            self.get_extended_program_volume_instances()
        )
        self.wrapped_program_volume_families = (
            self.get_wrapped_program_volume_families()
        )

    def get_extended_program_volume_instances(self):
        """Retrieve all Program Volume instances in the model as ExtendedProgramVolumeInstance objects.

        Returns:
            list: Collection of ExtendedProgramVolumeInstance objects matching the family name prefix
        """

        extended_program_volume_instances = []

        # Single efficient call to get ALL family instances that might be program volumes
        # Use the prefix to filter at the API level for maximum efficiency

        all_family_instances = db.Collector(
            doc=self.doc, of_class="FamilyInstance"
        ).get_elements(wrapped=True)

        for generic_model_instance in all_family_instances:
            try:
                # Check if this instance belongs to a program volume family
                family_name = generic_model_instance.Symbol.Family.Name

                # Filter by our prefix
                if family_name.startswith(self.program_volume_family_name_prefix):
                    # discovered_family_names.add(family_name)

                    # Wrap the instance
                    extended_volume = ExtendedProgramVolumeInstance(
                        generic_model_instance, doc=self.doc
                    )
                    extended_program_volume_instances.append(extended_volume)

                    if (
                        self.logger
                        and len(extended_program_volume_instances) % 100 == 0
                    ):  # Log progress every 100 items
                        self.logger.debug(
                            "Processed {} program volume instances".format(
                                len(extended_program_volume_instances)
                            )
                        )
            except Exception as e:
                if self.logger:
                    self.logger.debug(
                        "Failed to process instance {}: {}".format(
                            generic_model_instance.Id, str(e)
                        )
                    )
                continue

        return extended_program_volume_instances

    @property
    def ext_program_volumes_without_data_silo(self):
        """Get a list of ExtendedProgramVolume objects that are not linked to any data silo.

        Returns:
            list: ExtendedProgramVolumeInstance objects with parent_data_silo set to None
        """

        # Use list comprehension to filter volumes without a parent data silo
        volumes_without_silo = [
            ext_program_volume
            for ext_program_volume in self.extended_program_volume_instances
            if ext_program_volume.parent_data_silo is None
        ]
        return volumes_without_silo

    @property
    def unique_family_names(self):
        """Get a set of unique family names for the program volumes in the collection.

        Returns:
            set: Set of unique family names (str)
        """
        family_names = set()
        for ext_program_volume in self.extended_program_volume_instances:
            try:
                family_name = ext_program_volume.rpw_element.Symbol.Family.Name
                family_names.add(family_name)
            except Exception as e:
                if self.logger:
                    self.logger.debug(
                        "Failed to get family name for program volume {}: {}".format(
                            ext_program_volume.Id, str(e)
                        )
                    )
                continue
        return family_names

    def get_wrapped_program_volume_families(self):
        """Get a dictionary mapping family names to their corresponding Family objects.

        Returns:
            dict: Dictionary with family names as keys and rpw.Element (Family) as values
        """

        wrapped_generic_model_families = db.Collector(
            doc=self.doc, of_class="Family"
        ).get_elements(wrapped=True)

        # Use list comprehension to retain only the families that start with the correct prefix
        wrapped_program_volume_families = [
            family
            for family in wrapped_generic_model_families
            if family.Name.startswith(self.program_volume_family_name_prefix)
        ]

        return wrapped_program_volume_families

    def add_wrapped_program_volume_family(self, wrapped_family):
        """Add a wrapped Family object to the collection."""
        self.wrapped_program_volume_families.append(wrapped_family)

    def family_name_from_uid(self, uid):
        """Get the Family name from a uid."""
        family_name = self.program_volume_family_name_prefix + uid

        return family_name

    def wrapped_program_volume_family_by_name(self, family_name):
        """Get the Family object for a given family name."""

        for family in self.wrapped_program_volume_families:
            if family.Name == family_name:
                return family
        return None

    def check_worksharing_availability(self):
        """Check that all required elements are available for editing in workshared models

        Args:
            model (PDSO_Core_Data_Model): The data model to check

        Returns:
            list: List of unavailable element IDs
        """

        # Try to check out all data silos
        element_ids_to_checkout = [
            ext_program_volume.Id
            for ext_program_volume in self.extended_program_volume_instances
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

    def commit_pending_changes(self):
        """Commit all pending changes and delete orphaned instances.

        Must be called within an open Revit transaction. Performs:
        1. Validation check (logs warnings if validation failed)
        2. Commits pending parameter updates on valid instances
        3. Deletes orphaned instances marked for deletion

        Notes:
            Continues processing even if some data silos have validation errors.
            A data silo with stacking errors gets instances on the levels that
            did resolve. Its instances on other levels are kept (correlation
            does not mark them for deletion) and are not moved; they still get
            the data silo's data.
        """
        # Check validation status and log warnings
        if not self.validation_passed:
            self.logger.warning(
                "Level mapping validation failed - continuing with the levels that resolved"
            )
            if self.validation_result:
                self.logger.warning(self.validation_result.get_summary_message())

        # Commit parameter updates on valid instances
        skipped_count = 0
        failed_count = 0
        newly_created_count = 0
        existing_updated_count = 0

        for ext_program_volume_instance in self.extended_program_volume_instances:
            # Skip instances that should be deleted (they'll be handled later)
            if ext_program_volume_instance.should_be_deleted:
                continue

            # Skip instances without parent data silo (orphaned from start)
            if ext_program_volume_instance.parent_data_silo is None:
                self.logger.warning(
                    "Skipping commit for instance without parent data silo (ID: {})".format(
                        ext_program_volume_instance.Id
                        if ext_program_volume_instance.rpw_element
                        else "NEW"
                    )
                )
                skipped_count += 1
                continue

            try:
                # Track if this is a new instance (rpw_element was None before commit)
                was_new = ext_program_volume_instance.rpw_element is None

                ext_program_volume_instance.commit_pending_changes()

                # Count newly created vs updated
                if was_new:
                    newly_created_count += 1
                else:
                    existing_updated_count += 1

            except Exception as e:
                # One instance that fails must not roll back the whole
                # transaction (the other instances and the deletions)
                self.logger.error(
                    "Failed to commit changes for instance {}: {}".format(
                        ext_program_volume_instance.Id
                        if ext_program_volume_instance.rpw_element
                        else "NEW",
                        str(e),
                    )
                )
                failed_count += 1

        # Log summary of commit operation
        self.logger.debug(
            "[DEBUG] Commit summary: {} newly created, {} existing updated, {} skipped".format(
                newly_created_count, existing_updated_count, skipped_count
            )
        )

        if skipped_count > 0:
            self.logger.warning(
                "Skipped {} instances with no parent Data Silo".format(
                    skipped_count
                )
            )
        if failed_count > 0:
            self.logger.error(
                "{} Program Volume instances could not be updated (see the errors above)".format(
                    failed_count
                )
            )

        self.logger.debug(
            "Committed changes to {} program volume instances ({} skipped)".format(
                len(self.extended_program_volume_instances) - skipped_count,
                skipped_count,
            )
        )

        # Delete orphaned instances
        if self.instances_to_delete:
            self.logger.info(
                "Deleting {} orphaned program volume instances...".format(
                    len(self.instances_to_delete)
                )
            )

            for ext_instance in self.instances_to_delete:
                try:
                    # Log details before deletion
                    instance_uid = "UNKNOWN"
                    level_name = "UNKNOWN"
                    element_id = ext_instance.Id

                    if ext_instance.parent_data_silo:
                        parent_uid = ext_instance.parent_data_silo.parameters[
                            "PDSO - Program UID"
                        ].value
                        instance_uid = parent_uid or "UNKNOWN"

                    # Safely get level name using the Level property (which handles wrapping)
                    try:
                        level = ext_instance.Level
                        if level:
                            level_name = level.Name
                    except:
                        pass

                    self.logger.debug(
                        "Deleting orphaned instance - UID: {}, Level: {}, ElementId: {}".format(
                            instance_uid, level_name, element_id
                        )
                    )

                    # Delete the element
                    self.doc.Delete(ext_instance.rpw_element.Id)

                except Exception as e:
                    self.logger.error(
                        "Failed to delete orphaned instance {}: {}".format(
                            ext_instance.Id, str(e)
                        )
                    )

            # Clear the deletion list
            self.instances_to_delete = []
            self.logger.info("Orphaned instances deleted successfully")

    def get_orphaned_instances(self):
        """Get program volume instances without a parent data silo.

        Returns:
            list: List of dicts with {'id': ElementId, 'level': str, 'family': str}
        """
        orphaned = []
        for ext_instance in self.extended_program_volume_instances:
            if ext_instance.parent_data_silo is None:
                family_name = "Unknown"
                try:
                    family_name = ext_instance.rpw_element.Symbol.Family.Name
                except:
                    pass

                orphaned.append(
                    {
                        "id": ext_instance.Id,
                        "level": ext_instance.Level.Name
                        if ext_instance.Level
                        else "N/A",
                        "family": family_name,
                    }
                )
        return orphaned
