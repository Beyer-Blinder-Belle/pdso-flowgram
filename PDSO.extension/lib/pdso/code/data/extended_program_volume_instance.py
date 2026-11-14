# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import revit, db, DB
from pyrevit import script

# Custom Modules
from pdso.code.data.shared_parameter_constants import LAST_SYNCED
from pdso.code.data.extended_shared_parameter import ROLE_AREA
from pdso.code.business.parameter_registry_service import get_registry
from pdso.code.data.extensible_storage_schemas import PdsoProgramVolumeInstanceSchema


class ExtendedProgramVolumeInstance(object):
    """Wrapper for rpw.Element (Program Volume Instance) with PDSO-specific properties.

    Manages parameter updates, extensible storage, location changes, and deletion
    for program volume instances. Tracks stacking relationships and orphan status.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        rpw_element: Wrapped rpw.Element instance
        params_to_update (dict): Queued parameter updates {param_name: new_value}
        entity_fields_to_update (dict): Queued extensible storage updates {field_name: new_value}
        parent_data_silo: ExtendedDataSilo object this instance is associated with
        desired_instance_location: XYZ point for relocating instance
        is_primary_instance (bool): True if on canonical level (co-located with data silo)
        target_level: ExtendedLevel object where instance should be placed
        should_be_deleted (bool): True if instance is orphaned
    """

    def __init__(self, rpw_element):
        """Initialize extended program volume instance wrapper.

        Args:
            rpw_element: rpw.Element instance to wrap (Program Volume family instance)
        """
        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        # Not needed

        # Identity/ Lookup Properties
        # Not needed

        # Wrapped Element or Collection
        self.rpw_element = rpw_element

        # Hashing for integrity checks
        # self._hash = None

        # PDSO-specific properties and correlations
        self.params_to_update = {}  # Dict[str (Parameter Name), Any (New Value)]
        self.entity_fields_to_update = {}  # Dict[str (Field Name), Any (New Value)]
        self.params_to_update = {}  # Dict[str (Parameter Name), Any (New Value)]
        self.parent_data_silo = None
        self.desired_instance_location = None

        # TO DO - is "target_level" right? Instances cannot change their levels after being created so it sort of doesn't matter?
        # Level stacking properties
        self.is_primary_instance = (
            False  # True if on canonical level (co-located with data silo)
        )
        self.target_level = None  # ExtendedLevel - the level this instance should be on
        self.should_be_deleted = (
            False  # True if instance is orphaned (on level not in required_levels)
        )

        # Init Logic
        self.queue_resetting_params()

    def queue_resetting_params(self):
        """Queue parameter resets at the start of every sync.

        Adds default values for PDSO parameters to the params_to_update queue.
        Parameters cleared: every tracked parameter.
        """

        # Every tracked parameter is reset, then refilled from the Data Silo
        pdso_params_to_clear = get_registry(revit.doc).parameters

        for parameter in pdso_params_to_clear:
            if not parameter.has_default_value():
                continue
            default_val = parameter.default_value()
            self.params_to_update[parameter.parameter_name] = default_val

    def commit_pending_changes(self):
        """Apply all pending parameter, entity, and location updates.

        Must be called within an active Revit transaction. Updates:
        - Creates new instance if rpw_element is None
        - Parameter values from params_to_update queue
        - Extensible storage fields from entity_fields_to_update queue
        - Instance location from desired_instance_location

        Clears all queues after applying changes.

        Notes:
            Continues on error for individual parameter updates.
            Location updates are skipped if desired_instance_location is None.
        """
        # Handle new instance creation
        if self.rpw_element is None:
            if self.desired_instance_location is None:
                self.logger.error(
                    "Cannot create new Program Volume instance - no desired location set"
                )
                return

            if self.parent_data_silo is None:
                self.logger.error(
                    "Cannot create new Program Volume instance - no parent data silo"
                )
                return

            self.logger.debug(
                ".  Creating new Program Volume instance at ({:.2f}, {:.2f}, {:.2f})".format(
                    self.desired_instance_location.X,
                    self.desired_instance_location.Y,
                    self.desired_instance_location.Z,
                )
            )

            # Get the Family from the parent data silo
            program_volume_family = (
                self.parent_data_silo.related_program_volume_family.rpw_element
            )

            if program_volume_family is None:
                self.logger.error(
                    ".  Cannot create instance - Program Volume family does not exist for Data Silo {}".format(
                        self.parent_data_silo.Id.Value
                    )
                )
                return

            # Get the first FamilySymbol from the Family
            family_symbol_ids = program_volume_family.unwrap().GetFamilySymbolIds()
            if not family_symbol_ids or family_symbol_ids.Count == 0:
                self.logger.error(
                    ".  No FamilySymbols found in family '{}'".format(
                        program_volume_family.Name
                    )
                )
                return

            family_symbol = revit.doc.GetElement(list(family_symbol_ids)[0])

            # Ensure the FamilySymbol is activated
            if not family_symbol.IsActive:
                self.logger.debug(".  Activating FamilySymbol for placement")
                family_symbol.Activate()

            # Use target_level if specified, otherwise fall back to data silo level
            target_level_wrapped = (
                self.target_level.rpw_level
                if self.target_level
                else self.parent_data_silo.rpw_element.Level
            )

            # Unwrap to get the actual Revit API Level object
            if hasattr(target_level_wrapped, "unwrap"):
                target_level = target_level_wrapped.unwrap()
            else:
                target_level = target_level_wrapped

            # For NewFamilyInstance, use Z=0 to ensure 0' 0" Elevation from Level
            # Revit interprets the Z coordinate differently when creating vs moving instances
            # When creating: Z should be 0 (or relative to absolute origin)
            # When moving: Z should use ProjectElevation
            target_point = DB.XYZ(
                self.desired_instance_location.X, self.desired_instance_location.Y, 0.0
            )

            self.logger.debug(
                ".  Placing new instance at point: ({:.2f}, {:.2f}, {:.2f})".format(
                    target_point.X, target_point.Y, target_point.Z
                )
            )

            try:
                instance = revit.doc.Create.NewFamilyInstance(
                    target_point,
                    family_symbol,
                    target_level,
                    DB.Structure.StructuralType.NonStructural,
                )

                self.rpw_element = db.Element(instance)
                self.logger.info(
                    ".  Successfully created Program Volume instance (ID: {}) on level {} (primary: {})".format(
                        instance.Id, target_level.Name, self.is_primary_instance
                    )
                )

                # Debug: Confirm rpw_element is set and can be unwrapped
                self.logger.debug(
                    ".  [DEBUG] New instance rpw_element set: {} (unwrap type: {})".format(
                        self.rpw_element is not None,
                        type(self.rpw_element.unwrap()).__name__
                        if self.rpw_element
                        else "N/A",
                    )
                )

                # Clear desired location after successful creation
                self.desired_instance_location = None

            except Exception as create_error:
                self.logger.error(
                    ".  Failed to create Program Volume instance: {}".format(
                        str(create_error)
                    )
                )
                raise

        # Update parameters on existing or newly created instance
        for param_name, new_value in self.params_to_update.items():
            try:
                self.rpw_element.parameters[param_name].value = new_value
            except Exception as e:
                self.logger.error(
                    "Failed to update parameter '{}' on program volume instance {}: {}".format(
                        param_name, self.Id, str(e)
                    )
                )

        self.params_to_update.clear()

        # Update the attached extensible storage schema entity
        with PdsoProgramVolumeInstanceSchema(self.rpw_element.unwrap()) as entity:
            for field_name, new_value in self.entity_fields_to_update.items():
                try:
                    setattr(entity, field_name, new_value)
                except Exception as e:
                    self.logger.error(
                        "Failed to update field '{}' in extensible storage schema for program volume instance {}: {}".format(
                            field_name, self.Id, str(e)
                        )
                    )

        # Clear the pending updates after committing
        self.entity_fields_to_update.clear()

        # Update instance location if desired_location is set
        if self.desired_instance_location is not None:
            if self.rpw_element is None:
                self.logger.debug(
                    "Program Volume Instance does not exist in the model, cannot update location."
                )
            else:
                try:
                    # Get the target level for this instance
                    target_level_wrapped = (
                        self.target_level.rpw_level
                        if self.target_level
                        else self.parent_data_silo.rpw_element.Level
                    )

                    # Unwrap to get the actual Revit API Level object
                    if hasattr(target_level_wrapped, "unwrap"):
                        instance_level = target_level_wrapped.unwrap()
                    else:
                        instance_level = target_level_wrapped

                    # Create target point with level's ProjectElevation for Z to ensure 0' 0" Elevation from Level
                    # ProjectElevation is what Revit uses to calculate the "Elevation from Level" parameter
                    target_location = DB.XYZ(
                        self.desired_instance_location.X,
                        self.desired_instance_location.Y,
                        instance_level.ProjectElevation,
                    )

                    self.rpw_element.Location.Point = target_location
                    self.logger.debug(
                        "Updated location of Program Volume Instance {}".format(self.Id)
                    )
                except Exception as e:
                    self.logger.error(
                        "Failed to update location of Program Volume Instance {}: {}".format(
                            self.Id, str(e)
                        )
                    )
                finally:
                    self.desired_instance_location = None

        self.logger.debug(
            "Committed all changes on Program Volume Instance {}".format(
                self.Id if self.rpw_element else "NEW"
            )
        )

    @property
    def Id(self):
        """Get the element Id from the wrapped rpw_element.

        Returns:
            ElementId: Revit ElementId of the wrapped element

        Raises:
            AttributeError: If rpw_element is not set or invalid
        """
        try:
            return self.rpw_element.Id
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Id: rpw_element is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def parameters(self):
        """Get the parameters collection from the wrapped rpw_element.

        Returns:
            ParameterSet: Collection of parameters on the element

        Raises:
            AttributeError: If rpw_element is not set or invalid
        """
        try:
            return self.rpw_element.parameters
        except AttributeError as e:
            raise AttributeError(
                "Cannot access parameters: rpw_element is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def Level(self):
        """Get the Level property from the wrapped rpw_element.

        Returns:
            rpw.Element: Wrapped Level element

        Raises:
            AttributeError: If rpw_element is not set or invalid
        """
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

    @property
    def LevelId(self):
        """Get the LevelId property from the wrapped rpw_element.

        Returns:
            ElementId: Revit ElementId of the associated Level

        Raises:
            AttributeError: If rpw_element is not set or invalid
        """
        try:
            return self.rpw_element.LevelId
        except AttributeError as e:
            raise AttributeError(
                "Cannot access LevelId: rpw_element is not set or invalid. Error: {}".format(
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
                "'ExtendedDataSilo' object has no attribute '{}' and neither does its rpw_element".format(
                    name
                )
            )

    @property
    def level_id_as_int(self):
        """Get the integer value of the Level ElementId.

        Returns:
            int: Integer representation of the Level's ElementId
        """
        level_id = self.rpw_element.LevelId.Value
        # level_element = db.Element.from_id(level_id, doc=self.rpw_element.doc, wrapped=True)
        return level_id

    def reset_stale_data(self):
        """Clear PDSO parameters by setting them to default values.

        Must be called within an active transaction. Immediately updates
        parameter values rather than queuing changes.

        Notes:
            Parameters cleared: tracked Area parameters and Last Synced.
            Continues on error if individual parameter update fails.
        """
        registry = get_registry(revit.doc)
        pdso_params_to_clear = registry.by_role(ROLE_AREA)
        last_synced = registry.get(LAST_SYNCED)
        if last_synced is not None:
            pdso_params_to_clear.append(last_synced)

        for parameter in pdso_params_to_clear:
            if not parameter.has_default_value():
                continue
            self.logger.debug(
                "Clearing parameter '{}' on data silo id: {}".format(
                    parameter.parameter_name, self.Id
                )
            )

            try:
                default_val = parameter.default_value()
                self.rpw_element.parameters[
                    parameter.parameter_name
                ].value = default_val
            except Exception as e:
                self.logger.error(
                    "Failed to clear parameter '{}' on area {}: {}".format(
                        parameter.parameter_name, self.Id, str(e)
                    )
                )
