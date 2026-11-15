# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import db
from pyrevit import script

# Custom Modules
from pdso.code.data.extensible_storage_schemas import PdsoProgramVolumeFamilySchema


class ExtendedProgramVolumeFamily(object):
    """Wrapper for rpw.Family (Program Volume Family) with PDSO-specific properties.

    Manages family-level data including geometry hashes, renaming operations,
    and extensible storage for program volume families. Provides automatic
    property delegation to rpw_element.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        rpw_element: Wrapped rpw.Element (Family) instance
        params_to_update (dict): Queued parameter updates {param_name: new_value}
        entity_fields_to_update (dict): Queued extensible storage updates {field_name: new_value}
        parent_data_silo: ExtendedDataSilo object this family is associated with
        desired_family_name (str): New family name if rename is queued
    """

    def __init__(self, rpw_element):
        """Initialize extended program volume family wrapper.

        Args:
            rpw_element: rpw.Element (Family) instance to wrap
        """

        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        # Not needed

        # Identity/ Lookup Properties
        # None

        # Wrapped Element or Collection
        self.rpw_element = rpw_element  # Store the rpw-wrapped family

        # Hashing for integrity checks
        # self._hash = None

        # PDSO-specific properties and correlations
        self.params_to_update = {}  # Dict[str (Parameter Name), Any (New Value)]
        self.entity_fields_to_update = {}  # Dict[str (Field Name), Any (New Value)]
        self.parent_data_silo = None
        self.desired_family_name = None
        # self.family_needs_creation = False
        # self.family_needs_rename = False
        # self.family_needs_update = False

        # Init Logic
        self.queue_resetting_params()

    def queue_resetting_params(self):
        # None for now
        pass

    @property
    def entity(self):
        """Retrieve the extensible storage entity associated with this family.

        Returns:
            PdsoProgramVolumeFamilySchema: Entity with area geometry hashes and basepoints, or None if not found
        """
        if self.rpw_element is None:
            self.logger.debug(
                "No rpw_element associated with this ExtendedProgramVolumeFamily - this is expected for new families."
            )
            return None

        try:
            with PdsoProgramVolumeFamilySchema(
                self.rpw_element.unwrap(), update=False
            ) as entity:
                return entity
        except Exception as e:
            self.logger.error(
                "Failed to retrieve PdsoProgramVolumeFamilySchema for program volume family: {}".format(
                    str(e)
                )
            )
            return None

    def retrieve_from_entity(self, field_name, key=None):
        """
        Retrieve a specific field value from the extensible storage schema entity attached to this family.

        Args:
            field_name (str): Name of the field to retrieve.
            key (str, optional): If the field is a dictionary, the specific key to retrieve.
        """

        if self.entity is not None and hasattr(self.entity, field_name):
            self.logger.debug("Entity has {} field".format(field_name))

            field = getattr(self.entity, field_name, None)

            if field is None:
                self.logger.debug("Field: {} is None".format(field_name))
                return None

            elif key is None:
                self.logger.debug("No key provided, returning field.")
                return field

            elif field is not None and key in field:
                self.logger.debug("Field: {} has key {}".format(field_name, key))
                return field[key]

            else:
                self.logger.warning(
                    "Failed to find key {} in field {}. Returning None".format(
                        key, field_name
                    )
                )
                return None
        else:
            self.logger.debug(
                "Entity does not have {} field. Returning None".format(field_name)
            )
            return None

    def commit_pending_changes(self):
        """
        Must be called within an open Revit transaction. Apply any pending updates to:
            Parameter values for the wrapped rpw_element.
            The associated extensible storage schema data entity.
        """

        if self.rpw_element is None:
            # Placeholder wrapper for silos that could not produce a family this run.
            self.entity_fields_to_update.clear()
            self.logger.debug(
                "Skipping Program Volume Family commit because no family element is attached"
            )
            return

        # Rename Family if desired name does not match actual
        if (
            self.desired_family_name
            and self.rpw_element.Name != self.desired_family_name
        ):
            try:
                self.rpw_element.Name = self.desired_family_name
                self.logger.debug(
                    "Renamed Program Volume Family {} to '{}'".format(
                        self.Id, self.desired_family_name
                    )
                )
            except Exception as e:
                self.logger.error(
                    "Failed to rename Program Volume Family {}: {}".format(
                        self.Id, str(e)
                    )
                )

        # Rename Family Symbol if needed
        if self.desired_family_name:
            try:
                symbols = self.rpw_element.get_symbols()
                if symbols and len(symbols) > 0:
                    first_symbol = symbols[0]
                    if (
                        first_symbol is not None
                        and first_symbol.name != self.desired_family_name
                    ):
                        first_symbol.name = self.desired_family_name
                        self.logger.debug(
                            "Renamed Program Volume Family Symbol for Family {} to '{}'".format(
                                self.Id, self.desired_family_name
                            )
                        )
                else:
                    self.logger.warning(
                        "No symbols found for Program Volume Family {}".format(self.Id)
                    )
            except Exception as e:
                self.logger.error(
                    "Failed to rename Program Volume Family Symbol for Family {}: {}".format(
                        self.Id, str(e)
                    )
                )

        # Update the attached extensible storage schema entity
        with PdsoProgramVolumeFamilySchema(self.rpw_element.unwrap()) as entity:
            for field_name, new_value in self.entity_fields_to_update.items():
                try:
                    setattr(entity, field_name, new_value)
                except Exception as e:
                    self.logger.error(
                        "Failed to update field '{}' in extensible storage schema for program volume family {}: {}".format(
                            field_name, self.Id, str(e)
                        )
                    )

        # Clear the pending updates after committing
        self.entity_fields_to_update.clear()

        self.logger.debug(
            "Updated name and area_curve_hashes on Program Volume Family {}".format(
                self.Id
            )
        )

    # Convenience properties
    @property
    def Id(self):
        """Get the element Id from the wrapped rpw_element."""
        try:
            return self.rpw_element.Id
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Id: rpw_element is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def Level(self):
        """Get the Level property from the wrapped rpw_element."""
        try:
            element_id = self.rpw_element.LevelId
            rpw_level = db.Element.from_id(element_id)
            return rpw_level
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Level: rpw_element is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    def __getattr__(self, name):
        """
        Fallback for any other attributes not explicitly defined.
        Try to get the attribute from rpw_element with better error handling.
        """
        try:
            return getattr(self.rpw_element, name)
        except AttributeError:
            raise AttributeError(
                "'ExtendedProgramVolumeFamily' object has no attribute '{}' and neither does its rpw_element".format(
                    name
                )
            )
