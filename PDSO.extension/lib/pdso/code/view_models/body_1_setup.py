# -*- coding: utf-8 -*-
"""ViewModel for Setup and Configuration UI.

This ViewModel manages the PDSO configuration interface with four tabs:
- Tab 1: Model and Setup Checks
- Tab 2: Data Silo Settings
- Tab 3: Program Volume Settings
- Tab 4: Level Ranges Mapping
"""

# Python standard libraries
import os
import traceback


# 3rd Party Modules
from pyrevit import revit, DB
from rpw import db

# System/Windows Modules
from System.Windows.Controls import (
    Grid,
    TextBlock,
    TextBox,
    ComboBox,
    Button,
    ColumnDefinition,
    ComboBoxItem,
    Image,
)
from System.Windows.Data import Binding
from System.Windows import (
    Thickness,
    HorizontalAlignment,
    VerticalAlignment,
    GridLength,
    GridUnitType,
)
import System

# Custom Modules
from base_view_model import PDSO_Base_View_Model
from config_mapping_item import ConfigMappingItem
from common.code.view_model_external_events import CustomizableEvent
from common.code import generic_utils
from pdso.code.business.extensible_storage_service import PDSOExtensibleStorageService
from pdso.code.data.shared_parameter_constants import standard_pdso_parameters


class PDSO_View_Model_1_Setup(PDSO_Base_View_Model):
    """ViewModel for Setup and Configuration UI.

    Manages PDSO document-level configuration including:
    - Model validation checks
    - Data Silo settings (family, UID pattern, area mappings, workset)
    - Program Volume settings (prefix, height, subcategories, workset)
    - Level range mappings

    Attributes:
        ext_storage_service: Service for extensible storage operations
        data_silo_family_name: Name of Data Silo family to use
        uid_pattern: UID validation regex pattern
        area_scheme_mappings: Collection of area scheme mapping items
        program_volume_family_name_prefix: Prefix for PV family naming
        program_volume_extrusion_height: Default extrusion height in feet
        subcategory_mappings: Collection of subcategory mapping items
        levels_mappings: Collection of level mapping items
        data_silo_workset: Workset name for data silos
        program_volume_workset: Workset name for program volumes
        area_workset: Workset name for areas
    """

    def __init__(self):
        """Initialize ViewModel and bind to model."""
        super(PDSO_View_Model_1_Setup, self).__init__()

        # Services
        self.ext_storage_service = PDSOExtensibleStorageService(revit.doc)

        # Backing fields for simple properties
        self._data_silo_family_name = ""
        self._uid_pattern = ""
        self._program_volume_family_name_prefix = ""
        self._program_volume_extrusion_height = 8.0

        # Backing fields for workset properties (split from dictionary)
        self._data_silo_workset = "Not enforced"
        self._program_volume_workset = "Not enforced"
        self._area_workset = "Not enforced"

        # Backing fields for check results (Tab 1)
        self._check_schema_status = "[ - ]"
        self._check_data_silo_family_status = "[ - ]"
        self._check_data_silo_instances_status = "[ - ]"
        self._check_shared_parameters_status = "[ - ]"
        self._check_area_schemes_status = "[ - ]"
        self._check_area_instances_status = "[ - ]"

        # Unsaved changes tracking
        self._has_unsaved_changes = False

        # Worksharing/ownership status
        self._config_ownership_message = ""
        self._config_ownership_color = "#4CAF50"  # Default green

        self._is_initializing = False

        # Collections for dynamic mappings
        self.area_scheme_mappings = []  # List of ConfigMappingItem

        # Cached ComboBox data
        self._cached_area_schemes = None
        self._cached_levels = None
        self._cached_generic_model_subcategories = None
        self._cached_worksets = None

        # UI container references (set during setup)
        self.area_scheme_mappings_container = None
        self.levels_mappings_container = None

        # External event handler for transactions from modeless window
        self.custom_event_handler = CustomizableEvent()

    def setup(self):
        """Complete setup of the view model (called after window creation)."""
        # Call base setup first (loads XAML, links resources, fixes images)
        super(PDSO_View_Model_1_Setup, self).setup("1_setup.xaml")

        # Show initializing message
        self.is_initializing = True

        try:
            # Ensure schemas exist and initialize with defaults if needed
            self.logger.info("Ensuring extensible storage schemas exist...")
            schema_status = self.ext_storage_service.ensure_schemas_exist()

            if not schema_status["success"]:
                self.logger.warning("Some schemas missing or failed to initialize")

            # Load configuration from storage
            self._load_config_from_storage()

            # Check worksharing status
            self._check_config_ownership()

            # Cache ComboBox data
            self._cache_combobox_data()

            # Wire up event handlers
            self._wire_event_handlers()

            # Initialize dynamic UI containers
            self._initialize_dynamic_containers()

        except Exception as e:
            self.logger.error("Error during setup: {}".format(e))
        finally:
            self.is_initializing = False

    # =============================================================================
    # Observable Properties - Simple Fields
    # =============================================================================

    @property
    def is_initializing(self):
        """Get initialization status."""
        return self._is_initializing

    @is_initializing.setter
    def is_initializing(self, value):
        """Set initialization status and notify UI."""
        if self._is_initializing != value:
            self._is_initializing = value
            self.on_property_changed("is_initializing")

    @property
    def data_silo_family_name(self):
        """Get Data Silo family name."""
        return self._data_silo_family_name

    @data_silo_family_name.setter
    def data_silo_family_name(self, value):
        """Set Data Silo family name and notify UI."""
        if self._data_silo_family_name != value:
            self._data_silo_family_name = value
            self.on_property_changed("data_silo_family_name")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    @property
    def uid_pattern(self):
        """Get UID validation pattern."""
        return self._uid_pattern

    @uid_pattern.setter
    def uid_pattern(self, value):
        """Set UID validation pattern and notify UI."""
        if self._uid_pattern != value:
            self._uid_pattern = value
            self.on_property_changed("uid_pattern")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    @property
    def program_volume_family_name_prefix(self):
        """Get Program Volume family name prefix."""
        return self._program_volume_family_name_prefix

    @program_volume_family_name_prefix.setter
    def program_volume_family_name_prefix(self, value):
        """Set Program Volume family name prefix and notify UI."""
        if self._program_volume_family_name_prefix != value:
            self._program_volume_family_name_prefix = value
            self.on_property_changed("program_volume_family_name_prefix")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    @property
    def program_volume_extrusion_height(self):
        """Get Program Volume extrusion height in feet."""
        return self._program_volume_extrusion_height

    @program_volume_extrusion_height.setter
    def program_volume_extrusion_height(self, value):
        """Set Program Volume extrusion height and notify UI."""
        if self._program_volume_extrusion_height != value:
            self._program_volume_extrusion_height = value
            self.on_property_changed("program_volume_extrusion_height")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    # Workset properties (discrete UI fields that map to dictionary)
    @property
    def data_silo_workset(self):
        """Get Data Silo workset name."""
        return self._data_silo_workset

    @data_silo_workset.setter
    def data_silo_workset(self, value):
        """Set Data Silo workset name and notify UI."""
        if self._data_silo_workset != value:
            self._data_silo_workset = value
            self.on_property_changed("data_silo_workset")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    @property
    def program_volume_workset(self):
        """Get Program Volume workset name."""
        return self._program_volume_workset

    @program_volume_workset.setter
    def program_volume_workset(self, value):
        """Set Program Volume workset name and notify UI."""
        if self._program_volume_workset != value:
            self._program_volume_workset = value
            self.on_property_changed("program_volume_workset")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    @property
    def area_workset(self):
        """Get Area workset name."""
        return self._area_workset

    @area_workset.setter
    def area_workset(self, value):
        """Set Area workset name and notify UI."""
        if self._area_workset != value:
            self._area_workset = value
            self.on_property_changed("area_workset")
            if not self._is_initializing:
                self.has_unsaved_changes = True

    # =============================================================================
    # Observable Properties - Check Results (Tab 1)
    # =============================================================================

    @property
    def check_schema_status(self):
        """Get schema check status."""
        return self._check_schema_status

    @check_schema_status.setter
    def check_schema_status(self, value):
        """Set schema check status and notify UI."""
        if self._check_schema_status != value:
            self._check_schema_status = value
            self.on_property_changed("check_schema_status")

    @property
    def check_data_silo_family_status(self):
        """Get Data Silo family check status."""
        return self._check_data_silo_family_status

    @check_data_silo_family_status.setter
    def check_data_silo_family_status(self, value):
        """Set Data Silo family check status and notify UI."""
        if self._check_data_silo_family_status != value:
            self._check_data_silo_family_status = value
            self.on_property_changed("check_data_silo_family_status")

    @property
    def check_data_silo_instances_status(self):
        """Get Data Silo instances check status."""
        return self._check_data_silo_instances_status

    @check_data_silo_instances_status.setter
    def check_data_silo_instances_status(self, value):
        """Set Data Silo instances check status and notify UI."""
        if self._check_data_silo_instances_status != value:
            self._check_data_silo_instances_status = value
            self.on_property_changed("check_data_silo_instances_status")

    @property
    def check_shared_parameters_status(self):
        """Get shared parameters check status."""
        return self._check_shared_parameters_status

    @check_shared_parameters_status.setter
    def check_shared_parameters_status(self, value):
        """Set shared parameters check status and notify UI."""
        if self._check_shared_parameters_status != value:
            self._check_shared_parameters_status = value
            self.on_property_changed("check_shared_parameters_status")

    @property
    def check_area_schemes_status(self):
        """Get area schemes check status."""
        return self._check_area_schemes_status

    @check_area_schemes_status.setter
    def check_area_schemes_status(self, value):
        """Set area schemes check status and notify UI."""
        if self._check_area_schemes_status != value:
            self._check_area_schemes_status = value
            self.on_property_changed("check_area_schemes_status")

    @property
    def check_area_instances_status(self):
        """Get area instances check status."""
        return self._check_area_instances_status

    @check_area_instances_status.setter
    def check_area_instances_status(self, value):
        """Set area instances check status and notify UI."""
        if self._check_area_instances_status != value:
            self._check_area_instances_status = value
            self.on_property_changed("check_area_instances_status")

    @property
    def has_unsaved_changes(self):
        """Get whether there are unsaved changes."""
        return self._has_unsaved_changes

    @has_unsaved_changes.setter
    def has_unsaved_changes(self, value):
        """Set unsaved changes flag and notify UI."""
        if self._has_unsaved_changes != value:
            self._has_unsaved_changes = value
            self.on_property_changed("has_unsaved_changes")
            self.on_property_changed("unsaved_changes_message")  # Update message
            self.on_property_changed("unsaved_changes_color")  # Update color

    @property
    def unsaved_changes_message(self):
        """Get message for unsaved changes indicator."""
        return (
            "There are unsaved changes; to discard these changes, close this window without saving."
            if self._has_unsaved_changes
            else "All settings saved"
        )

    @property
    def unsaved_changes_color(self):
        """Get color for unsaved changes indicator."""
        return "#FF9800" if self._has_unsaved_changes else "#4CAF50"

    @property
    def config_ownership_message(self):
        """Get configuration ownership status message."""
        return self._config_ownership_message

    @property
    def config_ownership_color(self):
        """Get configuration ownership status color."""
        return self._config_ownership_color

    # =============================================================================
    # Configuration Loading
    # =============================================================================

    def _load_config_from_storage(self):
        """Load configuration values from extensible storage."""
        self.logger.info("Loading configuration from extensible storage...")

        try:
            # Check if schema exists first
            if not self.ext_storage_service.check_document_pdso_config_schema_exists():
                self.logger.warning("Document schema doesn't exist - using defaults")
                return

            # Get config context manager
            config = self.ext_storage_service.get_document_config()
            if config:
                with config as c:
                    # Load simple fields
                    self.data_silo_family_name = c.data_silo_family_name or ""
                    self.uid_pattern = c.uid_pattern or ""
                    self.program_volume_family_name_prefix = (
                        c.program_volume_family_name_prefix or ""
                    )
                    self.program_volume_extrusion_height = (
                        c.program_volume_extrusion_height or 8.0
                    )

                    # Load worksets dictionary and split into discrete properties
                    worksets = c.worksets or {}
                    # Convert .NET Dictionary to Python dict
                    if worksets and hasattr(worksets, "Keys"):
                        worksets_dict = {k: worksets[k] for k in worksets.Keys}
                    else:
                        worksets_dict = worksets

                    self.data_silo_workset = worksets_dict.get(
                        "data-silos", "Not enforced"
                    )
                    self.program_volume_workset = worksets_dict.get(
                        "program-volumes", "Not enforced"
                    )
                    self.area_workset = worksets_dict.get("areas", "Not enforced")

                    # Load area scheme mappings
                    self.area_scheme_mappings = []
                    area_mappings = c.area_scheme_mappings or {}
                    # Convert .NET Dictionary to Python dict
                    if area_mappings and hasattr(area_mappings, "Keys"):
                        area_mappings = {
                            k: area_mappings[k] for k in area_mappings.Keys
                        }

                    if area_mappings:
                        for key, value in area_mappings.items():
                            item = ConfigMappingItem(key, value, self)
                            self.area_scheme_mappings.append(item)
                    else:
                        # Create default mappings if none exist
                        default_mappings = [
                            ("PDSO - GSF", "PDSO - GSF"),
                            ("PDSO - NSF", "PDSO - NSF"),
                            ("PDSO - ZFA", "PDSO - ZFA"),
                        ]
                        for key, value in default_mappings:
                            item = ConfigMappingItem(key, value, self)
                            self.area_scheme_mappings.append(item)

                    # Subcategory mappings removed - will be auto-generated from area schemes

                    # Load level mappings
                    self.levels_mappings = []
                    level_mappings = c.levels_mapping or {}
                    # Convert .NET Dictionary to Python dict
                    if level_mappings and hasattr(level_mappings, "Keys"):
                        level_mappings = {
                            k: level_mappings[k] for k in level_mappings.Keys
                        }

                    # Create mapping items
                    for key, element_id in level_mappings.items():
                        item = ConfigMappingItem(key, element_id, self)
                        self.levels_mappings.append(item)

                    # Sort by key using natural sort
                    self.levels_mappings.sort(
                        key=lambda item: ConfigMappingItem.natural_sort_key(item.key)
                    )

                    self.logger.info("Configuration loaded successfully")
                    self.logger.debug(
                        "Loaded {} area scheme mappings".format(
                            len(self.area_scheme_mappings)
                        )
                    )
                    self.logger.debug(
                        "Loaded {} level mappings".format(len(self.levels_mappings))
                    )

        except Exception as e:
            self.logger.error("Error loading configuration: {}".format(e))

    def _check_config_ownership(self):
        """Check worksharing ownership status of configuration element."""
        try:
            # Get the data storage element
            data_storage_element = self.ext_storage_service.data_storage_element

            if not data_storage_element:
                self._config_ownership_message = "Configuration is editable"
                self._config_ownership_color = "#4CAF50"
                self.on_property_changed("config_ownership_message")
                self.on_property_changed("config_ownership_color")
                return

            # Check if model is workshared
            if not revit.doc.IsWorkshared:
                self._config_ownership_message = (
                    "Configuration is editable (non-workshared model)"
                )
                self._config_ownership_color = "#4CAF50"
                self.on_property_changed("config_ownership_message")
                self.on_property_changed("config_ownership_color")
                return

            # Check ownership status
            status = DB.WorksharingUtils.GetCheckoutStatus(
                revit.doc, data_storage_element.Id
            )

            if status == DB.CheckoutStatus.OwnedByOtherUser:
                # Get owner info
                tooltip_info = DB.WorksharingUtils.GetWorksharingTooltipInfo(
                    revit.doc, data_storage_element.Id
                )
                owner = tooltip_info.Owner
                self._config_ownership_message = "Configuration is currently checked out by {}. Changes cannot be saved until they sync and you reload.".format(
                    owner
                )
                self._config_ownership_color = "#F44336"  # Red
                self.logger.warning("Config owned by: {}".format(owner))
            elif status == DB.CheckoutStatus.OwnedByCurrentUser:
                self._config_ownership_message = (
                    "Configuration is editable (checked out to you)"
                )
                self._config_ownership_color = "#4CAF50"  # Green
            else:
                # Available to checkout
                self._config_ownership_message = "Configuration is editable"
                self._config_ownership_color = "#4CAF50"  # Green

            self.on_property_changed("config_ownership_message")
            self.on_property_changed("config_ownership_color")

        except Exception as e:
            self.logger.error("Error checking config ownership: {}".format(e))
            self._config_ownership_message = "Could not determine ownership status"
            self._config_ownership_color = "#FF9800"  # Orange
            self.on_property_changed("config_ownership_message")
            self.on_property_changed("config_ownership_color")

    # =============================================================================
    # ComboBox Data Caching
    # =============================================================================

    def _cache_combobox_data(self):
        """Cache ComboBox data for dynamic UI elements."""
        self.logger.info("Caching ComboBox data...")

        try:
            doc = revit.doc

            # Cache area schemes
            self._cached_area_schemes = []
            area_scheme_collector = db.Collector(of_class="AreaScheme", doc=doc)
            area_schemes = area_scheme_collector.get_elements(wrapped=False)

            for scheme in area_schemes:
                self._cached_area_schemes.append(
                    {"name": scheme.Name, "element": scheme}
                )

            self.logger.debug(
                "Cached {} area schemes".format(len(self._cached_area_schemes))
            )

            # Cache levels
            self._cached_levels = []
            level_collector = db.Collector(of_class="Level", doc=doc)
            levels = level_collector.get_elements(wrapped=False)

            for level in levels:
                self._cached_levels.append({"name": level.Name, "id": level.Id})

            self.logger.debug("Cached {} levels".format(len(self._cached_levels)))

            # Cache Generic Model subcategories
            self._cached_generic_model_subcategories = []
            generic_model_category = doc.Settings.Categories.get_Item(
                DB.BuiltInCategory.OST_GenericModel
            )

            if generic_model_category and generic_model_category.SubCategories:
                for subcat in generic_model_category.SubCategories:
                    self._cached_generic_model_subcategories.append(
                        {"name": subcat.Name}
                    )

            self.logger.debug(
                "Cached {} Generic Model subcategories".format(
                    len(self._cached_generic_model_subcategories)
                )
            )

            # Cache worksets
            self._cached_worksets = ["Not enforced"]
            if doc.IsWorkshared:
                worksets_collector = DB.FilteredWorksetCollector(doc).OfKind(
                    DB.WorksetKind.UserWorkset
                )
                workset_names = [workset.Name for workset in worksets_collector]
                # Sort alphabetically
                workset_names.sort()
                self._cached_worksets.extend(workset_names)
                self.logger.debug(
                    "Cached {} worksets".format(len(self._cached_worksets) - 1)
                )
            else:
                self.logger.debug(
                    "Document is not workshared, workset dropdown will only show 'Not enforced'"
                )

        except Exception as e:
            self.logger.error("Error caching ComboBox data: {}".format(e))

    # =============================================================================
    # Model Validation Checks (Tab 1)
    # =============================================================================

    def run_model_checks(self):
        """Run all model validation checks and update status properties."""
        self.logger.info("Running model validation checks...")

        try:
            doc = revit.doc

            # Check 1: Ensure schemas are loaded and config is saved
            schema_exists = (
                self.ext_storage_service.check_document_pdso_config_schema_exists()
            )
            if schema_exists:
                self.check_schema_status = "✓ Schema loaded and config saved"
            else:
                self.check_schema_status = "✗ Schema not initialized"

            # Check 2: Is the Data Silo Family loaded?
            family_name = self.data_silo_family_name
            if family_name:
                family_collector = db.Collector(
                    of_class="Family", doc=doc
                ).get_elements(wrapped=False)
                family_found = any(f.Name == family_name for f in family_collector)

                if family_found:
                    self.check_data_silo_family_status = "[OK]"
                else:
                    self.check_data_silo_family_status = "[ X ]"
            else:
                self.check_data_silo_family_status = "[ ! ]"

            # Check 3: How many instances of the data silo family are placed?
            if family_name:
                instance_collector = db.Collector(
                    of_class="FamilyInstance", of_category="GenericModel", doc=doc
                ).get_elements(wrapped=True)

                ds_instances = [
                    inst
                    for inst in instance_collector
                    if inst.Symbol.Family.Name == family_name
                ]

                count = len(ds_instances)
                if count > 0:
                    self.check_data_silo_instances_status = "[OK] {}".format(count)
                else:
                    self.check_data_silo_instances_status = "[ ! ]"
            else:
                self.check_data_silo_instances_status = "[ ! ]"

            # Check 4: Are required shared parameters present?
            missing_params = []

            # Get all parameter bindings
            binding_map = doc.ParameterBindings
            param_names_in_doc = set()

            iterator = binding_map.ForwardIterator()
            iterator.Reset()
            while iterator.MoveNext():
                definition = iterator.Key
                param_names_in_doc.add(definition.Name)

            # Check which required parameters are missing
            for param_name, param_def in standard_pdso_parameters.items():
                if not param_def.required:
                    continue
                if param_name not in param_names_in_doc:
                    missing_params.append(param_name)

            total_required = sum(
                1 for p in standard_pdso_parameters.values() if p.required
            )

            if len(missing_params) == 0:
                self.check_shared_parameters_status = (
                    "✓ All {} required parameters present".format(total_required)
                )
            else:
                self.check_shared_parameters_status = (
                    "✗ Missing {} parameter(s): {}".format(
                        len(missing_params), ", ".join(missing_params[:3])
                    )
                )

            # Check 5: Do the Area Schemes exist?
            area_scheme_names = [item.key for item in self.area_scheme_mappings]
            existing_schemes = [s["name"] for s in self._cached_area_schemes]

            missing_schemes = [
                name for name in area_scheme_names if name not in existing_schemes
            ]

            if len(area_scheme_names) == 0:
                self.check_area_schemes_status = "⚠ No area schemes configured"
            elif len(missing_schemes) == 0:
                self.check_area_schemes_status = (
                    "✓ All {} configured area schemes exist".format(
                        len(area_scheme_names)
                    )
                )
            else:
                self.check_area_schemes_status = "✗ Missing {} scheme(s): {}".format(
                    len(missing_schemes), ", ".join(missing_schemes)
                )

            # Check 6: How many Area instances are in each Area Scheme?
            area_counts = {}
            area_collector = db.Collector(of_class="Area", doc=doc).get_elements(
                wrapped=True
            )

            for area in area_collector:
                try:
                    scheme_name = area.AreaScheme.Name
                    area_counts[scheme_name] = area_counts.get(scheme_name, 0) + 1
                except:
                    pass

            if len(area_counts) > 0:
                count_summary = ", ".join(
                    ["{}: {}".format(k, v) for k, v in area_counts.items()]
                )
                self.check_area_instances_status = "✓ Area counts: {}".format(
                    count_summary
                )
            else:
                self.check_area_instances_status = "⚠ No areas found in any scheme"

            self.logger.info("Model checks completed")

        except Exception as e:
            self.logger.error("Error running model checks: {}".format(e))
            self.check_schema_status = "✗ Error: {}".format(str(e))

    # =============================================================================
    # Event Handlers
    # =============================================================================

    def _wire_event_handlers(self):
        """Wire up button click event handlers."""
        self.logger.debug("Wiring event handlers...")

        try:
            presenter = self.FindName("BodyContent")
            if not presenter:
                self.logger.warning("BodyContent ContentPresenter not found")
                return

            if not presenter.Content:
                self.logger.warning(
                    "BodyContent.Content is None - XAML may not be loaded"
                )
                return

            self.logger.debug("BodyContent found, wiring buttons...")

            # Run Checks button (Tab 1)
            run_checks_button = self.find_element_by_name(
                presenter.Content, "RunChecksButton"
            )
            if run_checks_button:
                run_checks_button.Click += self._on_run_checks_clicked
                self.logger.debug("Wired Run Checks button")

            # Save button
            save_button = self.find_element_by_name(presenter.Content, "SaveButton")
            if save_button:
                save_button.Click += self._on_save_clicked
                self.logger.debug("Wired Save button")

            # Cancel button removed - using unsaved changes indicator instead

            # Add buttons for dynamic collections
            add_area_mapping_button = self.find_element_by_name(
                presenter.Content, "AddAreaMappingButton"
            )
            if add_area_mapping_button:
                add_area_mapping_button.Click += self._on_add_area_mapping_clicked
                self.logger.debug("Wired Add Area Mapping button")

            # Subcategory mappings removed - will be auto-generated from area schemes

            add_level_mapping_button = self.find_element_by_name(
                presenter.Content, "AddLevelMappingButton"
            )
            if add_level_mapping_button:
                add_level_mapping_button.Click += self._on_add_level_mapping_clicked
                self.logger.debug("Wired Add Level Mapping button")

            sort_level_mapping_button = self.find_element_by_name(
                presenter.Content, "SortLevelMappingsButton"
            )
            if sort_level_mapping_button:
                sort_level_mapping_button.Click += self._on_sort_level_mappings_clicked
                self.logger.debug("Wired Sort Level Mappings button")

        except Exception as e:
            self.logger.error("Error wiring event handlers: {}".format(e))

    def _on_run_checks_clicked(self, sender, args):
        """Handle Run Checks button click."""
        self.logger.info("Run Checks button clicked")
        try:
            self.run_model_checks()
        except Exception as e:
            self.logger.error("Error running checks: {}".format(e))

    def _on_save_clicked(self, sender, args):
        """Handle Save button click."""
        self.logger.info("Save button clicked")
        try:
            # Use external event to perform save in proper API context
            self.custom_event_handler.raise_event(self._do_save_configuration, self)
        except Exception as e:
            self.logger.error("Error initiating save: {}".format(e))

    # Cancel button removed - using unsaved changes indicator instead
    # def _on_cancel_clicked(self, sender, args):
    #     """Handle Cancel button click."""
    #     ...

    def _on_add_area_mapping_clicked(self, sender, args):
        """Handle Add Area Mapping button click."""
        self.logger.debug("Add Area Mapping button clicked")
        try:
            new_item = ConfigMappingItem("", "", self)
            self.area_scheme_mappings.append(new_item)
            self._refresh_area_scheme_mappings_ui()
            self.has_unsaved_changes = True
        except Exception as e:
            self.logger.error("Error adding area mapping: {}".format(e))

    # Subcategory mappings removed - will be auto-generated from area schemes
    # def _on_add_subcategory_mapping_clicked(self, sender, args):
    #     """Handle Add Subcategory Mapping button click."""
    #     ...

    def _on_add_level_mapping_clicked(self, sender, args):
        """Handle Add Level Mapping button click."""
        self.logger.debug("Add Level Mapping button clicked")
        try:
            new_item = ConfigMappingItem("", None, self)
            self.levels_mappings.append(new_item)
            self._refresh_levels_mappings_ui()
            self.has_unsaved_changes = True
        except Exception as e:
            self.logger.error("Error adding level mapping: {}".format(e))

    def _on_sort_level_mappings_clicked(self, sender, args):
        """Handle Sort Level Mappings button click."""
        self.logger.debug("Sort Level Mappings button clicked")
        try:
            # Sort mappings by key using natural sort
            self.levels_mappings.sort(
                key=lambda item: ConfigMappingItem.natural_sort_key(item.key)
            )
            self._refresh_levels_mappings_ui()
            self.logger.info("Level mappings sorted")
        except Exception as e:
            self.logger.error("Error sorting level mappings: {}".format(e))

    # =============================================================================
    # Dynamic UI Container Initialization
    # =============================================================================

    def _initialize_dynamic_containers(self):
        """Initialize dynamic UI containers with current data."""
        self.logger.debug("Initializing dynamic containers...")

        try:
            presenter = self.FindName("BodyContent")
            if not presenter or not presenter.Content:
                return

            # Find containers
            self.area_scheme_mappings_container = self.find_element_by_name(
                presenter.Content, "AreaSchemeMappingsContainer"
            )
            self.levels_mappings_container = self.find_element_by_name(
                presenter.Content, "LevelsMappingsContainer"
            )
            # Subcategory mappings removed - will be auto-generated from area schemes

            # Populate workset ComboBoxes
            data_silo_workset_combo = self.find_element_by_name(
                presenter.Content, "DataSiloWorksetComboBox"
            )
            if data_silo_workset_combo and self._cached_worksets:
                for workset_name in self._cached_worksets:
                    data_silo_workset_combo.Items.Add(workset_name)
                # Set selected item if value exists
                if self.data_silo_workset:
                    if self.data_silo_workset in self._cached_worksets:
                        data_silo_workset_combo.SelectedItem = self.data_silo_workset
                    else:
                        data_silo_workset_combo.SelectedIndex = (
                            0  # Default to "Not enforced"
                        )
                else:
                    data_silo_workset_combo.SelectedIndex = (
                        0  # Default to "Not enforced"
                    )
                self.logger.debug("Populated Data Silo Workset ComboBox")

            program_volume_workset_combo = self.find_element_by_name(
                presenter.Content, "ProgramVolumeWorksetComboBox"
            )
            if program_volume_workset_combo and self._cached_worksets:
                for workset_name in self._cached_worksets:
                    program_volume_workset_combo.Items.Add(workset_name)
                # Set selected item if value exists
                if self.program_volume_workset:
                    if self.program_volume_workset in self._cached_worksets:
                        program_volume_workset_combo.SelectedItem = (
                            self.program_volume_workset
                        )
                    else:
                        program_volume_workset_combo.SelectedIndex = (
                            0  # Default to "Not enforced"
                        )
                else:
                    program_volume_workset_combo.SelectedIndex = (
                        0  # Default to "Not enforced"
                    )
                self.logger.debug("Populated Program Volume Workset ComboBox")

            area_workset_combo = self.find_element_by_name(
                presenter.Content, "AreaWorksetComboBox"
            )
            if area_workset_combo and self._cached_worksets:
                for workset_name in self._cached_worksets:
                    area_workset_combo.Items.Add(workset_name)
                # Set selected item if value exists
                if self.area_workset:
                    if self.area_workset in self._cached_worksets:
                        area_workset_combo.SelectedItem = self.area_workset
                    else:
                        area_workset_combo.SelectedIndex = (
                            0  # Default to "Not enforced"
                        )
                else:
                    area_workset_combo.SelectedIndex = 0  # Default to "Not enforced"
                self.logger.debug("Populated Area Workset ComboBox")

            # Refresh UI for each container
            if self.area_scheme_mappings_container:
                self._refresh_area_scheme_mappings_ui()

            if self.levels_mappings_container:
                self._refresh_levels_mappings_ui()

            # Subcategory mappings removed - will be auto-generated from area schemes

        except Exception as e:
            self.logger.error("Error initializing dynamic containers: {}".format(e))

    # =============================================================================
    # Dynamic UI Refresh Methods
    # =============================================================================

    def _refresh_area_scheme_mappings_ui(self):
        """Refresh area scheme mappings UI container."""
        if not self.area_scheme_mappings_container:
            return

        self.area_scheme_mappings_container.Children.Clear()

        if len(self.area_scheme_mappings) == 0:
            # Show placeholder
            placeholder = TextBlock()
            placeholder.Text = (
                "No area scheme mappings configured. Click 'Add Mapping' to create one."
            )
            placeholder.Foreground = System.Windows.Media.Brushes.Gray
            placeholder.FontStyle = System.Windows.FontStyles.Italic
            placeholder.Margin = Thickness(5, 10, 5, 10)
            self.area_scheme_mappings_container.Children.Add(placeholder)
        else:
            # Add header
            header_grid = self._create_mapping_header("Area Scheme", "Parameter Name")
            self.area_scheme_mappings_container.Children.Add(header_grid)

            # Add mapping rows
            for item in self.area_scheme_mappings:
                row_grid = self._create_area_mapping_row(item)
                self.area_scheme_mappings_container.Children.Add(row_grid)

    # Subcategory mappings removed - will be auto-generated from area schemes
    # def _refresh_subcategory_mappings_ui(self):
    #     """Refresh subcategory mappings UI container."""
    #     ...

    def _refresh_levels_mappings_ui(self):
        """Refresh level mappings UI container."""
        if not self.levels_mappings_container:
            return

        self.levels_mappings_container.Children.Clear()

        if len(self.levels_mappings) == 0:
            # Show placeholder
            placeholder = TextBlock()
            placeholder.Text = (
                "No level mappings configured. Click 'Add Mapping' to create one."
            )
            placeholder.Foreground = System.Windows.Media.Brushes.Gray
            placeholder.FontStyle = System.Windows.FontStyles.Italic
            placeholder.Margin = Thickness(5, 10, 5, 10)
            self.levels_mappings_container.Children.Add(placeholder)
        else:
            # Add header
            header_grid = self._create_mapping_header("Level Identifier", "Revit Level")
            self.levels_mappings_container.Children.Add(header_grid)

            # Add mapping rows
            for item in self.levels_mappings:
                row_grid = self._create_level_mapping_row(item)
                self.levels_mappings_container.Children.Add(row_grid)

    # =============================================================================
    # Dynamic Row Creation Methods
    # =============================================================================

    def _create_mapping_header(self, col1_title, col2_title):
        """Create header grid for mapping containers.

        Args:
            col1_title: Title for first column (key)
            col2_title: Title for second column (value)

        Returns:
            Grid: Header grid with column titles
        """
        grid = Grid()
        grid.Margin = Thickness(0, 5, 0, 5)

        # Define columns
        col1 = ColumnDefinition()
        col1.Width = GridLength(4, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col1)

        col2 = ColumnDefinition()
        col2.Width = GridLength(5, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col2)

        col3 = ColumnDefinition()
        col3.Width = GridLength(1, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col3)

        # Column 1 header
        header1 = TextBlock()
        header1.Text = col1_title
        header1.FontWeight = System.Windows.FontWeights.Bold
        header1.Margin = Thickness(5, 0, 10, 0)
        Grid.SetColumn(header1, 0)
        grid.Children.Add(header1)

        # Column 2 header
        header2 = TextBlock()
        header2.Text = col2_title
        header2.FontWeight = System.Windows.FontWeights.Bold
        header2.Margin = Thickness(0, 0, 10, 0)
        Grid.SetColumn(header2, 1)
        grid.Children.Add(header2)

        # Column 3 header (empty - no Action label)
        # No header for action column

        return grid

    def _create_area_mapping_row(self, mapping_item):
        """Create UI row for area scheme mapping.

        Args:
            mapping_item: ConfigMappingItem instance

        Returns:
            Grid: Row grid with bound controls
        """
        grid = Grid()
        grid.Margin = Thickness(0, 2, 0, 2)

        # Define columns
        col1 = ColumnDefinition()
        col1.Width = GridLength(4, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col1)

        col2 = ColumnDefinition()
        col2.Width = GridLength(5, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col2)

        col3 = ColumnDefinition()
        col3.Width = GridLength(1, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col3)

        # Area Scheme ComboBox
        area_scheme_combo = ComboBox()
        area_scheme_combo.Margin = Thickness(5, 0, 10, 0)
        area_scheme_combo.VerticalAlignment = VerticalAlignment.Center

        # Populate with cached area schemes
        for scheme_data in self._cached_area_schemes:
            item = ComboBoxItem()
            item.Content = scheme_data["name"]
            item.Tag = scheme_data["name"]
            area_scheme_combo.Items.Add(item)

            # Select if matches current key
            if scheme_data["name"] == mapping_item.key:
                area_scheme_combo.SelectedItem = item

        # Handle selection change
        def on_area_scheme_changed(sender, args):
            selected_item = sender.SelectedItem
            if selected_item and hasattr(selected_item, "Tag"):
                mapping_item.key = selected_item.Tag

        area_scheme_combo.SelectionChanged += on_area_scheme_changed
        Grid.SetColumn(area_scheme_combo, 0)
        grid.Children.Add(area_scheme_combo)

        # Parameter Name TextBox
        param_textbox = TextBox()
        param_textbox.Margin = Thickness(0, 0, 10, 0)
        param_textbox.VerticalAlignment = VerticalAlignment.Center

        # Bind to value property
        value_binding = Binding("value")
        value_binding.Source = mapping_item
        value_binding.Mode = System.Windows.Data.BindingMode.TwoWay
        value_binding.UpdateSourceTrigger = (
            System.Windows.Data.UpdateSourceTrigger.PropertyChanged
        )
        param_textbox.SetBinding(TextBox.TextProperty, value_binding)

        Grid.SetColumn(param_textbox, 1)
        grid.Children.Add(param_textbox)

        # Remove button with icon
        remove_button = Button()
        remove_button.Width = 20
        remove_button.Height = 20
        remove_button.Padding = Thickness(1)
        remove_button.VerticalAlignment = VerticalAlignment.Center
        remove_button.HorizontalAlignment = HorizontalAlignment.Center

        # Create image for button
        button_image = Image()
        icon_path = os.path.join(
            generic_utils.lib_path(), "common", "assets", "x_icon.png"
        )
        if os.path.exists(icon_path):
            button_image.Source = generic_utils.load_image_source(icon_path)
        remove_button.Content = button_image

        def on_remove_clicked(sender, args):
            self.area_scheme_mappings.remove(mapping_item)
            self._refresh_area_scheme_mappings_ui()
            self.has_unsaved_changes = True

        remove_button.Click += on_remove_clicked
        Grid.SetColumn(remove_button, 2)
        grid.Children.Add(remove_button)

        return grid

    # Subcategory mappings removed - will be auto-generated from area schemes
    # def _create_subcategory_mapping_row(self, mapping_item):
    #     """Create UI row for subcategory mapping (similar to area mapping)."""
    #     ...

    def _create_level_mapping_row(self, mapping_item):
        """Create UI row for level mapping (identifier -> Level ElementId)."""
        grid = Grid()
        grid.Margin = Thickness(0, 2, 0, 2)

        # Define columns (balanced with area scheme mappings)
        col1 = ColumnDefinition()
        col1.Width = GridLength(4, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col1)

        col2 = ColumnDefinition()
        col2.Width = GridLength(5, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col2)

        col3 = ColumnDefinition()
        col3.Width = GridLength(1, GridUnitType.Star)
        grid.ColumnDefinitions.Add(col3)

        # Level Identifier TextBox (key)
        identifier_textbox = TextBox()
        identifier_textbox.Margin = Thickness(5, 0, 10, 0)
        identifier_textbox.VerticalAlignment = VerticalAlignment.Center
        identifier_textbox.ToolTip = (
            "Enter sortable identifier (e.g., 'Lvl 01', 'B1-03-A2')"
        )

        key_binding = Binding("key")
        key_binding.Source = mapping_item
        key_binding.Mode = System.Windows.Data.BindingMode.TwoWay
        key_binding.UpdateSourceTrigger = (
            System.Windows.Data.UpdateSourceTrigger.PropertyChanged
        )
        identifier_textbox.SetBinding(TextBox.TextProperty, key_binding)

        Grid.SetColumn(identifier_textbox, 0)
        grid.Children.Add(identifier_textbox)

        # Revit Level ComboBox (value)
        level_combo = ComboBox()
        level_combo.Margin = Thickness(0, 0, 10, 0)
        level_combo.VerticalAlignment = VerticalAlignment.Center

        for level_data in self._cached_levels:
            item = ComboBoxItem()
            item.Content = level_data["name"]
            item.Tag = level_data["id"]
            level_combo.Items.Add(item)

            # Select if matches current value (ElementId comparison)
            if (
                mapping_item.value
                and level_data["id"].Value == mapping_item.value.Value
            ):
                level_combo.SelectedItem = item

        def on_level_changed(sender, args):
            selected_item = sender.SelectedItem
            if selected_item and hasattr(selected_item, "Tag"):
                mapping_item.value = selected_item.Tag

        level_combo.SelectionChanged += on_level_changed
        Grid.SetColumn(level_combo, 1)
        grid.Children.Add(level_combo)

        # Remove button with icon
        remove_button = Button()
        remove_button.Width = 20
        remove_button.Height = 20
        remove_button.Padding = Thickness(1)
        remove_button.VerticalAlignment = VerticalAlignment.Center
        remove_button.HorizontalAlignment = HorizontalAlignment.Center

        # Create image for button
        button_image = Image()
        icon_path = os.path.join(
            generic_utils.lib_path(), "common", "assets", "x_icon.png"
        )
        if os.path.exists(icon_path):
            button_image.Source = generic_utils.load_image_source(icon_path)
        remove_button.Content = button_image

        def on_remove_clicked(sender, args):
            self.levels_mappings.remove(mapping_item)
            self._refresh_levels_mappings_ui()
            self.has_unsaved_changes = True

        remove_button.Click += on_remove_clicked
        Grid.SetColumn(remove_button, 2)
        grid.Children.Add(remove_button)

        return grid

    # =============================================================================
    # Save/Cancel Operations
    # =============================================================================

    @staticmethod
    def _do_save_configuration(view_model):
        """Static method wrapper for save operation (called by external event).

        Args:
            view_model: The ViewModel instance
        """
        view_model._save_configuration()

    def _save_configuration(self):
        """Save configuration to extensible storage."""
        self.logger.info("Saving configuration...")

        try:
            # Collect simple field values
            config_updates = {
                "data_silo_family_name": self.data_silo_family_name,
                "uid_pattern": self.uid_pattern,
                "program_volume_family_name_prefix": self.program_volume_family_name_prefix,
                "program_volume_extrusion_height": self.program_volume_extrusion_height,
            }

            # Build worksets dictionary from discrete properties
            config_updates["worksets"] = {
                "data-silos": self.data_silo_workset or "",
                "program-volumes": self.program_volume_workset or "",
                "areas": self.area_workset or "",
            }

            # Build area_scheme_mappings dictionary
            area_mappings = {}
            for item in self.area_scheme_mappings:
                if item.key and item.value:
                    area_mappings[item.key] = item.value
            # Only add if not empty (extensible storage can't handle empty dicts)
            if area_mappings:
                config_updates["area_scheme_mappings"] = area_mappings

            # Subcategory mappings removed - will be auto-generated from area schemes

            # Build levels_mapping dictionary with natural sorting
            level_mappings_list = []
            for item in self.levels_mappings:
                if item.key and item.value:
                    level_mappings_list.append((item.key, item.value))

            # Apply natural sort to keys
            level_mappings_list.sort(
                key=lambda x: ConfigMappingItem.natural_sort_key(x[0])
            )

            # Convert to dictionary
            level_mappings = {}
            for key, value in level_mappings_list:
                level_mappings[key] = value
            # Only add if not empty
            if level_mappings:
                config_updates["levels_mapping"] = level_mappings

            # Log config being saved
            self.logger.info(
                "Saving configuration with {} fields...".format(len(config_updates))
            )
            for key in config_updates.keys():
                self.logger.debug("Config field: {}".format(key))

            # Save to extensible storage (transaction handled by service)
            success = self.ext_storage_service.update_document_config(**config_updates)

            if success:
                self.logger.info("Configuration saved successfully")
                self.logger.info("✅ **Configuration saved successfully**")
                self.has_unsaved_changes = False  # Clear unsaved changes flag
            else:
                self.logger.error(
                    "Failed to save configuration - check PyRevit log for details"
                )
                self.logger.error(
                    "❌ **Failed to save configuration** - check PyRevit log for details"
                )

        except Exception as e:
            self.logger.error("Error saving configuration: {}".format(str(e)))
            self.logger.error("Full traceback:\n{}".format(traceback.format_exc()))
            self.logger.error("❌ **Error saving configuration:** {}".format(str(e)))

    # Cancel functionality removed - using unsaved changes indicator instead
    # def _cancel_configuration(self):
    #     """Cancel changes and reload from storage."""
    #     ...
