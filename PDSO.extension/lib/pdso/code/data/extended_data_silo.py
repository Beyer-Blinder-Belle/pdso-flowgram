# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import db
from pyrevit import script

# Custom Modules
from pdso.code.data.shared_parameter_constants import LAST_SYNCED
from pdso.code.data.extended_shared_parameter import ROLE_AREA
from pdso.code.business.parameter_registry_service import get_registry


class ExtendedDataSilo(object):
    """Wrapper for rpw.Element (Data Silo) with PDSO-specific properties.

    Manages parameter updates, relationships to areas and program volumes,
    and level mapping for stacking operations. Provides automatic property
    delegation to rpw_element.

    Attributes:
        logger: PyRevit logger for debug output
        output: PyRevit output for user-facing messages
        rpw_element: Wrapped rpw.Element (FamilyInstance) instance
        params_to_update (dict): Queued parameter updates {param_name: new_value}
        related_area_elements (dict): Dictionary mapping area scheme name to ExtendedArea
        related_program_volume_family: ExtendedProgramVolumeFamily object
        related_program_volume_instances (list): ExtendedProgramVolumeInstance objects
        required_levels (list): ExtendedLevel objects where program volumes should exist
        missing_level_mappings (list): Level mapping keys that couldn't be resolved
        parsing_errors (list): Errors from parsing stacking parameter
    """

    def __init__(self, rpw_element):
        """Initialize extended data silo wrapper.

        Args:
            rpw_element: rpw.Element (FamilyInstance) instance to wrap
        """

        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        # Not needed

        # Identity/ Lookup Properties
        # None

        # Wrapped Element or Collection
        self.rpw_element = rpw_element  # Store the rpw-wrapped element

        # Hashing for integrity checks
        # self._hash = None

        # PDSO-specific properties and correlations
        self.params_to_update = {}  # Dict[str (Parameter Name), Any (New Value)]
        # self.entity_fields_to_update = {}  # Dict[str (Field Name), Any (New Value)]
        self.related_area_elements = {}  # Dict[str (Area Scheme Name), ExtendedArea]
        self.related_program_volume_family = None
        self.related_program_volume_instances = []  # List[ExtendedProgramVolumeInstance]

        # Level mapping properties for stacking
        self.required_levels = []  # List[ExtendedLevel] - levels where program volumes should exist

        # TO DO - look at naming convention here, make less ambiguous
        self.missing_level_mappings = []  # List[str] - keys that couldn't be mapped
        self.parsing_errors = []  # List[str] - errors from parsing stacking parameter

        # Init Logic
        self.queue_resetting_params()

    def queue_resetting_params(self):
        """Queue parameter resets for sync operation.

        Adds default values for PDSO parameters to the params_to_update queue.
        Parameters queued: tracked Area parameters and Last Synced.
        """

        registry = get_registry(self.rpw_element.Document)
        pdso_params_to_clear = registry.by_role(ROLE_AREA)
        last_synced = registry.get(LAST_SYNCED)
        if last_synced is not None:
            pdso_params_to_clear.append(last_synced)

        for parameter in pdso_params_to_clear:
            if not parameter.has_default_value():
                continue
            default_val = parameter.default_value()
            self.params_to_update[parameter.parameter_name] = default_val

    def commit_pending_changes(self):
        """Apply all pending parameter updates to the data silo.

        Must be called within an active Revit transaction. Updates all
        parameters from the params_to_update queue and clears the queue.

        Notes:
            Continues on error for individual parameter updates.
        """

        for param_name, new_value in self.params_to_update.items():
            try:
                self.rpw_element.parameters[param_name].value = new_value
            except Exception as e:
                self.logger.error(
                    "Failed to update parameter '{}' on data silo {}: {}".format(
                        param_name, self.Id, str(e)
                    )
                )

        self.params_to_update.clear()

        self.logger.debug("Updated parameters on Data Silo {}".format(self.Id))

    # Convenience properties
    def get_area_for_program_volume_family_geometry(self):
        """Retrieve the ExtendedArea used for the Program Volume Family geometry.

        Returns:
            ExtendedArea: First related area element from related_area_elements dictionary, or None

        Notes:
            TODO: Improve fallback logic for multi-area scenarios to select appropriate area.

        Returns:
            ExtendedArea or None if not found.
        """

        # To do - improve with fallback logic and/or multi-area logic
        # related_area_elements is keyed by Area Scheme name.
        # A model that uses the default scheme names: the GSF Area gives the shape
        gsf_area = self.related_area_elements.get("PDSO - GSF", None)
        if gsf_area is not None:
            return gsf_area

        # A model with its own scheme names: the smallest related Area gives
        # the shape (the unit, not the floor plate that contains it)
        smallest = None
        smallest_size = None
        for ext_area in self.related_area_elements.values():
            if ext_area is None:
                continue
            try:
                size = ext_area.Area
            except Exception:
                continue
            if size <= 0:
                continue
            if smallest is None or size < smallest_size:
                smallest = ext_area
                smallest_size = size
        return smallest

    @property
    def Name(self):
        """Get the element Id from the wrapped rpw_element."""
        try:
            return "Name doesn't exist.. this is the ID " + str(
                self.rpw_element.Id.Value
            )
        except AttributeError as e:
            raise AttributeError(
                "Cannot access Id: rpw_element is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

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

    @property
    def LevelId(self):
        """Get the LevelId property from the wrapped rpw_element."""
        try:
            return self.rpw_element.LevelId
        except AttributeError as e:
            raise AttributeError(
                "Cannot access LevelId: rpw_element is not set or invalid. Error: {}".format(
                    str(e)
                )
            )

    @property
    def level_id_as_int(self):
        """Get the Id of the Level element associated with this Data Silo."""
        level_id = self.rpw_element.LevelId.Value
        # level_element = db.Element.from_id(level_id, doc=self.rpw_element.doc, wrapped=True)
        return level_id

    # Pass-through properties to rpw_element
    @property
    def parameters(self):
        """Get the parameters collection from the wrapped rpw_element."""
        try:
            return self.rpw_element.parameters
        except AttributeError as e:
            raise AttributeError(
                "Cannot access parameters: rpw_element is not set or invalid. Error: {}".format(
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
