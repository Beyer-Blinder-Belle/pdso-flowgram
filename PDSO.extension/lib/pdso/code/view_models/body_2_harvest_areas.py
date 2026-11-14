# -*- coding: utf-8 -*-
"""Data Harvesting & Sync View Model.

This view model coordinates the UI for running validation checks and
synchronizing program data from areas to data silos and program volumes.
"""

# Python standard libraries
import traceback  # For detailed error logging
from datetime import datetime

# System/ Windows Modules
import System
from System.Windows import Visibility
from System.Windows.Threading import Dispatcher, DispatcherPriority
from System.Windows.Media import SolidColorBrush, ColorConverter

# 3rd Party Modules
from pyrevit import revit, DB
from rpw import db
from rpw.utils.dotnet import List

# Custom Modules
from base_view_model import PDSO_Base_View_Model
from ..data.extended_data_silo_collection import ExtendedDataSiloCollection
from ..data.extended_area_scheme import ExtendedAreaScheme
from ..data.extended_program_volume_instance_collection import (
    ExtendedProgramVolumeInstanceCollection,
)
from ..data.extended_level_collection import ExtendedLevelCollection
from ..data.extended_program_volume_family_collection import (
    ExtendedProgramVolumeFamilyCollection,
)
from ..business.correlation_service import PDSOCorrelationService
from ..business.data_service import PDSODataService
from ..business.family_manager_service import PDSOFamilyManagerService
from ..business.workset_coordination_service import WorksetCoordinationService
from ..business.extensible_storage_service import PDSOExtensibleStorageService
from ..business.level_mapping_service import PDSOLevelMappingService
from ..business.parameter_registry_service import (
    check_parameter_bindings,
    get_registry,
)


class PDSO_View_Model_2_Harvest_Areas(PDSO_Base_View_Model):
    """ViewModel for Data Harvesting & Sync UI.

    Manages validation checks, configuration options, and execution of the
    data synchronization process.

    Attributes:
        output: PyRevit rich output for user-friendly display
        logger: PyRevit logger for debug-level logging
        dynamic_ui_elements: List of dynamically created UI components
        custom_event_handler: Handler for external events
        process_controller: Handler for business logic operations
    """

    def __init__(self):
        """Initialize the view model."""
        super(PDSO_View_Model_2_Harvest_Areas, self).__init__()

        # Preflight check status properties (backing fields)
        self._check_config_status = "[ - ] Not checked"
        self._check_level_mappings_status = "[ - ] Not checked"
        self._check_generic_model_parameters_status = "[ - ] Not checked"
        self._check_area_schemes_status = "[ - ] Not checked"
        self._check_area_instances_status = "[ - ] Not checked"
        self._check_grouped_areas_status = "[ - ] Not checked"
        self._check_data_silo_family_loaded_status = "[ - ] Not checked"
        self._check_data_silo_instances_status = "[ - ] Not checked"
        self._check_uid_uniqueness_status = "[ - ] Not checked"
        self._check_areas_multiple_data_silos_status = "[ - ] Not checked"
        self._check_areas_no_data_silos_status = "[ - ] Not checked"
        self._check_data_silos_partial_containment_status = "[ - ] Not checked"
        self._check_data_silos_no_containment_status = "[ - ] Not checked"

        # Split element ownership into three separate checks
        self._check_data_silos_availability_status = "[ - ] Not checked"
        self._check_program_volumes_availability_status = "[ - ] Not checked"
        self._check_areas_availability_status = "[ - ] Not checked"

        # Preflight control properties
        self._preflight_has_issues = False
        self._preflight_checks_passed = False
        self._preflight_status_message = "Preflight checks have not been run yet."
        self._preflight_status_icon_path = "common\\assets\\feedback_unknown.png"
        self._preflight_status_background = "#FFF3E0"  # Light orange
        self._preflight_status_foreground = "#E65100"  # Dark orange
        self._preflight_last_run_timestamp = "Not yet run"

        # Running control properties
        self._can_run_sync = False
        self._current_step = "Waiting to start..."
        self._progress_value = 0
        self._progress_max = 100
        self._progress_status = ""
        self._completion_message = ""
        self._completion_message_visibility = Visibility.Collapsed

        # Configuration options (will be read from ComboBoxes)
        self._area_harvest_workset_option = "Fix"
        self._area_harvest_pinning_option = "Pin"
        self._program_volume_geometry_update_option = "Yes - Use Geometry Cache"
        self._program_volume_geometry_workset_option = "Fix"
        self._program_volume_geometry_pinning_option = "Pin"
        self._program_volume_data_update_option = "Yes"
        self._validation_warning_message = None

    def setup(self):
        """Set up the UI components and event handlers."""
        super(PDSO_View_Model_2_Harvest_Areas, self).setup("2_harvest_areas.xaml")

        # Wire up event handlers
        self._wire_event_handlers()

        # Run initial preflight checks on load
        self.logger.info("Running initial preflight checks on window load")
        self.run_preflight_checks()

    def _wire_event_handlers(self):
        """Wire up button click events and ComboBox selection changes."""
        presenter = self.FindName("BodyContent")
        if not presenter or not presenter.Content:
            self.logger.warning("Could not find BodyContent presenter")
            return

        content = presenter.Content

        # Wire up Refresh Preflight button (new refresh icon button)
        refresh_button = self.find_element_by_name(content, "RefreshPreflightButton")
        if refresh_button:
            refresh_button.Click += self._on_refresh_preflight_clicked

        # Wire up Run Sync button
        run_button = self.find_element_by_name(content, "RunSyncButton")
        if run_button:
            run_button.Click += self._on_run_sync_clicked

        # Wire up ComboBox selection changed events to capture settings
        self._wire_combobox_handlers(content)

        # Wire up icon updates
        self._wire_icon_elements(content)

    def _wire_combobox_handlers(self, content):
        """Wire up ComboBox selection changed events."""
        # Area Harvest ComboBoxes
        area_workset_combo = self.find_element_by_name(
            content, "AreaHarvestWorksetComboBox"
        )
        if area_workset_combo:
            area_workset_combo.SelectionChanged += self._on_area_harvest_workset_changed

        area_pinning_combo = self.find_element_by_name(
            content, "AreaHarvestPinningComboBox"
        )
        if area_pinning_combo:
            area_pinning_combo.SelectionChanged += self._on_area_harvest_pinning_changed

        # Program Volume Geometry ComboBoxes
        pv_geo_update_combo = self.find_element_by_name(
            content, "ProgramVolumeGeometryUpdateComboBox"
        )
        if pv_geo_update_combo:
            pv_geo_update_combo.SelectionChanged += self._on_pv_geometry_update_changed

        pv_geo_workset_combo = self.find_element_by_name(
            content, "ProgramVolumeGeometryWorksetComboBox"
        )
        if pv_geo_workset_combo:
            pv_geo_workset_combo.SelectionChanged += (
                self._on_pv_geometry_workset_changed
            )

        pv_geo_pinning_combo = self.find_element_by_name(
            content, "ProgramVolumeGeometryPinningComboBox"
        )
        if pv_geo_pinning_combo:
            pv_geo_pinning_combo.SelectionChanged += (
                self._on_pv_geometry_pinning_changed
            )

        # Program Volume Data ComboBox
        pv_data_update_combo = self.find_element_by_name(
            content, "ProgramVolumeDataUpdateComboBox"
        )
        if pv_data_update_combo:
            pv_data_update_combo.SelectionChanged += self._on_pv_data_update_changed

    def _wire_icon_elements(self, content):
        """Store references to icon and text elements for dynamic updates."""
        # Store all check icon and text element references
        self.check_elements = {
            "config": {
                "icon": self.find_element_by_name(content, "CheckConfigIcon"),
                "text": self.find_element_by_name(content, "CheckConfigText"),
            },
            "level_mappings": {
                "icon": self.find_element_by_name(content, "CheckLevelMappingsIcon"),
                "text": self.find_element_by_name(content, "CheckLevelMappingsText"),
            },
            "area_params": {
                "icon": self.find_element_by_name(content, "CheckAreaParamsIcon"),
                "text": self.find_element_by_name(content, "CheckAreaParamsText"),
            },
            "generic_model_params": {
                "icon": self.find_element_by_name(
                    content, "CheckGenericModelParamsIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckGenericModelParamsText"
                ),
            },
            "area_schemes": {
                "icon": self.find_element_by_name(content, "CheckAreaSchemesIcon"),
                "text": self.find_element_by_name(content, "CheckAreaSchemesText"),
            },
            "area_instances": {
                "icon": self.find_element_by_name(content, "CheckAreaInstancesIcon"),
                "text": self.find_element_by_name(content, "CheckAreaInstancesText"),
            },
            "grouped_areas": {
                "icon": self.find_element_by_name(content, "CheckGroupedAreasIcon"),
                "text": self.find_element_by_name(content, "CheckGroupedAreasText"),
            },
            "data_silo_family": {
                "icon": self.find_element_by_name(content, "CheckDataSiloFamilyIcon"),
                "text": self.find_element_by_name(content, "CheckDataSiloFamilyText"),
            },
            "data_silo_instances": {
                "icon": self.find_element_by_name(
                    content, "CheckDataSiloInstancesIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckDataSiloInstancesText"
                ),
            },
            "uid_uniqueness": {
                "icon": self.find_element_by_name(content, "CheckUidUniquenessIcon"),
                "text": self.find_element_by_name(content, "CheckUidUniquenessText"),
            },
            "areas_multiple_silos": {
                "icon": self.find_element_by_name(
                    content, "CheckAreasMultipleDataSilosIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckAreasMultipleDataSilosText"
                ),
            },
            "areas_no_silos": {
                "icon": self.find_element_by_name(content, "CheckAreasNoDataSilosIcon"),
                "text": self.find_element_by_name(content, "CheckAreasNoDataSilosText"),
            },
            "silos_partial_containment": {
                "icon": self.find_element_by_name(
                    content, "CheckDataSilosPartialContainmentIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckDataSilosPartialContainmentText"
                ),
            },
            "silos_no_containment": {
                "icon": self.find_element_by_name(
                    content, "CheckDataSilosNoContainmentIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckDataSilosNoContainmentText"
                ),
            },
            "data_silos_availability": {
                "icon": self.find_element_by_name(
                    content, "CheckDataSilosAvailabilityIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckDataSilosAvailabilityText"
                ),
            },
            "program_volumes_availability": {
                "icon": self.find_element_by_name(
                    content, "CheckProgramVolumesAvailabilityIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckProgramVolumesAvailabilityText"
                ),
            },
            "areas_availability": {
                "icon": self.find_element_by_name(
                    content, "CheckAreasAvailabilityIcon"
                ),
                "text": self.find_element_by_name(
                    content, "CheckAreasAvailabilityText"
                ),
            },
        }

        # Store preflight status icon
        self.preflight_status_icon = self.find_element_by_name(
            content, "PreflightStatusIcon"
        )

    def _update_check_icon_and_color(self, check_key, status_text):
        """Update both the icon and text color for a check based on its status.

        Args:
            check_key (str): Key in self.check_elements dict
            status_text (str): Status string (starts with ✓, ⚠, or ✗)
        """
        if check_key not in self.check_elements:
            return

        elements = self.check_elements[check_key]
        icon_element = elements.get("icon")
        text_element = elements.get("text")

        if not icon_element or not text_element:
            return

        # Determine status from text
        if status_text.startswith("✓"):
            icon_path = "common\\assets\\feedback_ok.png"
            text_color = "#000000"  # Black for OK
        elif status_text.startswith("⚠"):
            icon_path = "common\\assets\\feedback_warning.png"
            text_color = "#FF9800"  # Warning orange
        elif status_text.startswith("✗"):
            icon_path = "common\\assets\\feedback_error.png"
            text_color = "#F44336"  # Error red
        else:
            icon_path = "common\\assets\\feedback_unknown.png"
            text_color = "#9E9E9E"  # Gray for unknown

        # Update icon
        icon_element.Tag = icon_path

        # Update text color
        try:
            converter = ColorConverter()
            color = converter.ConvertFromString(text_color)
            text_element.Foreground = SolidColorBrush(color)
        except Exception as e:
            self.logger.warning("Failed to set text color: {}".format(str(e)))

    # ========================================
    # Event Handlers
    # ========================================

    def _on_refresh_preflight_clicked(self, sender, args):
        """Handle Refresh Preflight button click."""
        self.logger.info("Refresh Preflight button clicked")
        self.run_preflight_checks()

    def _on_run_sync_clicked(self, sender, args):
        """Handle Run Sync Process button click.

        Uses external event pattern to execute sync in Revit API context.
        """
        self.logger.info("Run Sync Process button clicked")
        if self.can_run_sync:
            # Raise external event to execute sync in Revit API context
            self.custom_event_handler.raise_event(self._execute_sync_process)
        else:
            self.logger.warning("Cannot run sync - preflight checks have not passed")

    def _on_area_harvest_workset_changed(self, sender, args):
        """Handle Area Harvest Workset ComboBox selection change."""
        if sender.SelectedItem and hasattr(sender.SelectedItem, "Content"):
            self._area_harvest_workset_option = str(sender.SelectedItem.Content)
            self.logger.debug(
                "Area Harvest Workset: {}".format(self._area_harvest_workset_option)
            )

    def _on_area_harvest_pinning_changed(self, sender, args):
        """Handle Area Harvest Pinning ComboBox selection change."""
        if sender.SelectedItem and hasattr(sender.SelectedItem, "Content"):
            self._area_harvest_pinning_option = str(sender.SelectedItem.Content)
            self.logger.debug(
                "Area Harvest Pinning: {}".format(self._area_harvest_pinning_option)
            )

    def _on_pv_geometry_update_changed(self, sender, args):
        """Handle Program Volume Geometry Update ComboBox selection change."""
        if sender.SelectedItem and hasattr(sender.SelectedItem, "Content"):
            self._program_volume_geometry_update_option = str(
                sender.SelectedItem.Content
            )
            self.logger.debug(
                "PV Geometry Update: {}".format(
                    self._program_volume_geometry_update_option
                )
            )

    def _on_pv_geometry_workset_changed(self, sender, args):
        """Handle Program Volume Geometry Workset ComboBox selection change."""
        if sender.SelectedItem and hasattr(sender.SelectedItem, "Content"):
            self._program_volume_geometry_workset_option = str(
                sender.SelectedItem.Content
            )
            self.logger.debug(
                "PV Geometry Workset: {}".format(
                    self._program_volume_geometry_workset_option
                )
            )

    def _on_pv_geometry_pinning_changed(self, sender, args):
        """Handle Program Volume Geometry Pinning ComboBox selection change."""
        if sender.SelectedItem and hasattr(sender.SelectedItem, "Content"):
            self._program_volume_geometry_pinning_option = str(
                sender.SelectedItem.Content
            )
            self.logger.debug(
                "PV Geometry Pinning: {}".format(
                    self._program_volume_geometry_pinning_option
                )
            )

    def _on_pv_data_update_changed(self, sender, args):
        """Handle Program Volume Data Update ComboBox selection change."""
        if sender.SelectedItem and hasattr(sender.SelectedItem, "Content"):
            self._program_volume_data_update_option = str(sender.SelectedItem.Content)
            self.logger.debug(
                "PV Data Update: {}".format(self._program_volume_data_update_option)
            )

    # ========================================
    # Business Logic Methods
    # ========================================

    def run_preflight_checks(self):
        """Run all preflight validation checks.

        This method performs all validation checks sequentially and updates
        the UI status for each check.
        """
        self.logger.info("=" * 60)
        self.logger.info("Starting Preflight Checks")
        self.logger.info("=" * 60)

        # Reset all check statuses to "running"
        self._reset_check_statuses()

        # Track overall pass/fail status
        has_errors = False
        has_warnings = False

        # Initialize variables for collections (will be populated if loading succeeds)
        storage_service = None
        config = None
        ext_data_silo_collection = None
        ext_level_collection = None
        ext_pv_instance_collection = None
        area_scheme_objects = []
        correlation_service = None
        correlation_succeeded = False

        # Load configuration
        try:
            self.logger.debug("Loading PDSO configuration from extensible storage...")
            storage_service = PDSOExtensibleStorageService(revit.doc)
            config = storage_service.get_document_config()

            if config is not None:
                self.logger.debug("Config loaded successfully")
            else:
                self.logger.warning("Config not found in extensible storage")
        except Exception as e:
            self.logger.error("Error loading config: {}".format(str(e)))

        # Load data silo collection (independent of other loads)
        if config is not None:
            try:
                self.logger.debug("Loading data silo collection...")
                ext_data_silo_collection = ExtendedDataSiloCollection(
                    revit.doc, config.data_silo_family_name
                )
            except Exception as e:
                self.logger.error(
                    "Error loading data silo collection: {}".format(str(e))
                )

        # Load level collection (independent of other loads)
        try:
            self.logger.debug("Loading level collection...")
            ext_level_collection = ExtendedLevelCollection(revit.doc)
        except Exception as e:
            self.logger.error("Error loading level collection: {}".format(str(e)))

        # Load program volume instance collection (independent of other loads)
        try:
            self.logger.debug("Loading program volume instance collection...")
            pv_prefix = (
                config.program_volume_family_name_prefix
                if config is not None
                and getattr(config, "program_volume_family_name_prefix", None)
                else "PDSO_ProgramVolume-"
            )
            ext_pv_instance_collection = ExtendedProgramVolumeInstanceCollection(
                revit.doc, pv_prefix
            )
        except Exception as e:
            self.logger.error(
                "Error loading program volume instance collection: {}".format(str(e))
            )

        # Load area schemes (independent of other loads)
        if config is not None:
            try:
                self.logger.debug("Loading area schemes...")
                area_scheme_mappings_dict = (
                    dict(config.area_scheme_mappings)
                    if config.area_scheme_mappings
                    else {}
                )

                for (
                    pdso_scheme_name,
                    area_scheme_name,
                ) in area_scheme_mappings_dict.items():
                    try:
                        ext_area_scheme = ExtendedAreaScheme(
                            revit.doc, area_scheme_name, pdso_scheme_name
                        )
                        if ext_area_scheme.area_scheme_exists_in_model:
                            area_scheme_objects.append(ext_area_scheme)
                            self.logger.debug(
                                "  Loaded scheme: {}".format(area_scheme_name)
                            )
                        else:
                            self.logger.warning(
                                "  Scheme '{}' not found in model".format(
                                    area_scheme_name
                                )
                            )
                    except Exception as scheme_error:
                        self.logger.warning(
                            "  Failed to load scheme {}: {}".format(
                                area_scheme_name, str(scheme_error)
                            )
                        )
            except Exception as e:
                self.logger.error("Error loading area schemes: {}".format(str(e)))

        # CHECK 1: Config Validation
        try:
            if config is None:
                self.check_config_status = "✗ Config not found in extensible storage"
                has_errors = True
            else:
                self.check_config_status = "✓ Config loaded successfully"
            self._update_check_icon_and_color("config", self.check_config_status)
        except Exception as e:
            self.check_config_status = "✗ Script Error"
            self.logger.error("Config check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color("config", self.check_config_status)

        # CHECK 2: Level Mappings Completeness
        try:
            if config is None:
                self.check_level_mappings_status = (
                    "✗ Config not found - cannot validate level mappings"
                )
                has_errors = True
            else:
                storage_service = PDSOExtensibleStorageService(revit.doc)
                level_mapping_service = PDSOLevelMappingService(
                    revit.doc, storage_service
                )
                ext_data_silo_collection = ExtendedDataSiloCollection(
                    revit.doc, config.data_silo_family_name
                )
                ext_level_collection = ExtendedLevelCollection(revit.doc)

                # Validate all stacking parameters against loaded mappings
                validation_result = (
                    level_mapping_service.validate_all_stacking_parameters(
                        ext_data_silo_collection, ext_level_collection
                    )
                )

                if validation_result.passed:
                    self.check_level_mappings_status = "✓ All level mappings complete"
                else:
                    # Count data silos with errors
                    data_silos_with_errors = set()
                    data_silos_with_errors.update(
                        validation_result.errors_by_data_silo_uid.keys()
                    )
                    data_silos_with_errors.update(
                        validation_result.missing_mappings_by_data_silo_uid.keys()
                    )

                    error_count = len(data_silos_with_errors)
                    self.check_level_mappings_status = (
                        "⚠️ {} Data Silo{} have missing level mappings".format(
                            error_count, "s" if error_count != 1 else ""
                        )
                    )
                    self.logger.warning("Level mapping validation failed:")
                    self.logger.warning(validation_result.get_summary_message())
                    has_warnings = True

            self._update_check_icon_and_color(
                "level_mappings", self.check_level_mappings_status
            )
        except Exception as e:
            self.check_level_mappings_status = "✗ Script Error"
            self.logger.error("Level mappings check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "level_mappings", self.check_level_mappings_status
            )

        # CHECK 3: Generic Model Parameters (tracked in the Parameter Manager)
        try:
            if config is None:
                self.check_generic_model_parameters_status = (
                    "✗ Config not found - cannot validate parameters"
                )
                has_errors = True
            else:
                registry = get_registry(revit.doc, refresh=True)
                binding_check = check_parameter_bindings(revit.doc, registry)

                if not binding_check["configured"]:
                    self.check_generic_model_parameters_status = (
                        "✗ Parameters not set up - run the Parameter Manager first"
                    )
                    has_errors = True
                elif binding_check["missing_core"]:
                    self.check_generic_model_parameters_status = (
                        "✗ Required parameters not tracked: {} - run the Parameter "
                        "Manager".format(", ".join(binding_check["missing_core"]))
                    )
                    has_errors = True
                elif binding_check["missing"]:
                    for name in binding_check["missing"]:
                        self.logger.warning(
                            "Tracked parameter '{}': {}".format(
                                name, binding_check["problems"][name]
                            )
                        )
                    self.check_generic_model_parameters_status = (
                        "✗ {} of {} tracked parameters missing on Generic Models - "
                        "run the Parameter Manager".format(
                            len(binding_check["missing"]), len(registry.parameters)
                        )
                    )
                    has_errors = True
                else:
                    self.check_generic_model_parameters_status = (
                        "✓ All {} tracked parameters found".format(
                            len(binding_check["found"])
                        )
                    )

            self._update_check_icon_and_color(
                "generic_model_params", self.check_generic_model_parameters_status
            )
        except Exception as e:
            self.check_generic_model_parameters_status = "✗ Script Error"
            self.logger.error("Generic model parameters check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "generic_model_params", self.check_generic_model_parameters_status
            )

        # CHECK 4: Area Schemes
        try:
            if config is None:
                self.check_area_schemes_status = (
                    "✗ Config not found - cannot validate area schemes"
                )
                has_errors = True
            else:
                area_scheme_mappings_dict = (
                    dict(config.area_scheme_mappings)
                    if config.area_scheme_mappings
                    else {}
                )
                configured_count = len(area_scheme_mappings_dict)
                found_count = len(area_scheme_objects)

                if found_count == 0:
                    self.check_area_schemes_status = (
                        "✗ No area schemes found (0/{} configured)".format(
                            configured_count
                        )
                    )
                    has_errors = True
                elif found_count < configured_count:
                    self.check_area_schemes_status = (
                        "✗ Missing area schemes ({}/{} found)".format(
                            found_count, configured_count
                        )
                    )
                    has_errors = True
                else:
                    self.check_area_schemes_status = (
                        "✓ All area schemes found ({} schemes)".format(found_count)
                    )

            self._update_check_icon_and_color(
                "area_schemes", self.check_area_schemes_status
            )
        except Exception as e:
            self.check_area_schemes_status = "✗ Script Error"
            self.logger.error("Area schemes check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "area_schemes", self.check_area_schemes_status
            )

        # CHECK 5: Area Instances
        try:
            if config is None or not area_scheme_objects:
                self.check_area_instances_status = (
                    "✗ Cannot count areas - no area schemes loaded"
                )
                has_errors = True
            else:
                total_areas = sum(
                    len(scheme.extended_areas) for scheme in area_scheme_objects
                )

                if total_areas == 0:
                    self.check_area_instances_status = "✗ No areas found in any scheme"
                    has_errors = True
                else:
                    self.check_area_instances_status = (
                        "✓ Areas found in all schemes ({} total)".format(total_areas)
                    )

            self._update_check_icon_and_color(
                "area_instances", self.check_area_instances_status
            )
        except Exception as e:
            self.check_area_instances_status = "✗ Script Error"
            self.logger.error("Area instances check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "area_instances", self.check_area_instances_status
            )

        # CHECK 6: Grouped Areas
        try:
            if config is None or not area_scheme_objects:
                self.check_grouped_areas_status = (
                    "✗ Cannot check grouped areas - no area schemes loaded"
                )
                has_errors = True
            else:
                grouped_areas = []
                for scheme in area_scheme_objects:
                    grouped_areas.extend(scheme.get_grouped_areas())

                if len(grouped_areas) > 0:
                    self.check_grouped_areas_status = (
                        "✗ {} areas are in groups (will be skipped)".format(
                            len(grouped_areas)
                        )
                    )
                    has_errors = True
                else:
                    self.check_grouped_areas_status = "✓ No grouped areas detected"

            self._update_check_icon_and_color(
                "grouped_areas", self.check_grouped_areas_status
            )
        except Exception as e:
            self.check_grouped_areas_status = "✗ Script Error"
            self.logger.error("Grouped areas check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "grouped_areas", self.check_grouped_areas_status
            )

        # CHECK 7: Data Silo Family Loaded
        try:
            if config is None:
                self.check_data_silo_family_loaded_status = (
                    "✗ Config not found - cannot check family"
                )
                has_errors = True
            elif ext_data_silo_collection is None:
                self.check_data_silo_family_loaded_status = (
                    "✗ Failed to load data silo collection"
                )
                has_errors = True
            else:
                if ext_data_silo_collection.family_exists_in_model:
                    family_name = config.data_silo_family_name
                    self.check_data_silo_family_loaded_status = (
                        "✓ Data Silo family loaded ({})".format(family_name)
                    )
                else:
                    self.check_data_silo_family_loaded_status = (
                        "✗ Data Silo family not loaded in model"
                    )
                    has_errors = True

            self._update_check_icon_and_color(
                "data_silo_family", self.check_data_silo_family_loaded_status
            )
        except Exception as e:
            self.check_data_silo_family_loaded_status = "✗ Script Error"
            self.logger.error("Data silo family check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "data_silo_family", self.check_data_silo_family_loaded_status
            )

        # CHECK 8: Data Silo Instances
        try:
            if config is None:
                self.check_data_silo_instances_status = (
                    "✗ Config not found - cannot count instances"
                )
                has_errors = True
            elif ext_data_silo_collection is None:
                self.check_data_silo_instances_status = (
                    "✗ Failed to load data silo collection"
                )
                has_errors = True
            else:
                silo_count = len(ext_data_silo_collection.extended_data_silos)

                if silo_count == 0:
                    self.check_data_silo_instances_status = (
                        "✗ No Data Silo instances found"
                    )
                    has_errors = True
                else:
                    self.check_data_silo_instances_status = (
                        "✓ {} Data Silos found".format(silo_count)
                    )

            self._update_check_icon_and_color(
                "data_silo_instances", self.check_data_silo_instances_status
            )
        except Exception as e:
            self.check_data_silo_instances_status = "✗ Script Error"
            self.logger.error("Data silo instances check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "data_silo_instances", self.check_data_silo_instances_status
            )

        # CHECK 9: UID Uniqueness
        try:
            if config is None or ext_data_silo_collection is None:
                self.check_uid_uniqueness_status = (
                    "✗ Cannot check UIDs - data silos not loaded"
                )
                has_errors = True
            else:
                if ext_data_silo_collection.are_data_silo_uids_unique():
                    self.check_uid_uniqueness_status = "✓ All Data Silo UIDs are unique"
                else:
                    self.check_uid_uniqueness_status = "✗ Duplicate UIDs detected"
                    has_errors = True

            self._update_check_icon_and_color(
                "uid_uniqueness", self.check_uid_uniqueness_status
            )
        except Exception as e:
            self.check_uid_uniqueness_status = "✗ Script Error"
            self.logger.error("UID uniqueness check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "uid_uniqueness", self.check_uid_uniqueness_status
            )

        # SPATIAL CORRELATION - Required for remaining checks
        try:
            if (
                config is not None
                and ext_data_silo_collection is not None
                and ext_level_collection is not None
                and area_scheme_objects
            ):
                self.logger.debug("Performing spatial correlations...")
                correlation_service = PDSOCorrelationService()

                for area_scheme in area_scheme_objects:
                    try:
                        correlation_service.correlate_data_silos_to_areas(
                            ext_level_collection, ext_data_silo_collection, area_scheme
                        )
                    except Exception as corr_error:
                        self.logger.warning(
                            "  Correlation failed for {}: {}".format(
                                area_scheme.area_scheme_name, str(corr_error)
                            )
                        )
                        raise  # Re-raise to trigger overall correlation failure

                correlation_succeeded = True
                self.logger.debug("Correlations completed successfully")
            else:
                self.logger.warning(
                    "Cannot perform correlations - missing required collections"
                )

        except Exception as e:
            self.logger.error("Correlation error: {}".format(str(e)))
            correlation_succeeded = False

        # CHECK 10: Areas with Multiple Data Silos
        try:
            if not correlation_succeeded:
                self.check_areas_multiple_data_silos_status = (
                    "✗ Unable to Correlate - Script Error"
                )
                has_errors = True
            elif config is None or not area_scheme_objects:
                self.check_areas_multiple_data_silos_status = (
                    "✗ Cannot check - correlation not performed"
                )
                has_errors = True
            else:
                areas_with_multiple = []
                for scheme in area_scheme_objects:
                    for ext_area in scheme.extended_areas:
                        if len(ext_area.related_data_silos) > 1:
                            areas_with_multiple.append(ext_area)

                if len(areas_with_multiple) > 0:
                    self.check_areas_multiple_data_silos_status = (
                        "✗ {} areas contain multiple Data Silos".format(
                            len(areas_with_multiple)
                        )
                    )
                    has_errors = True
                else:
                    self.check_areas_multiple_data_silos_status = (
                        "✓ No areas with multiple Data Silos"
                    )

            self._update_check_icon_and_color(
                "areas_multiple_silos", self.check_areas_multiple_data_silos_status
            )
        except Exception as e:
            self.check_areas_multiple_data_silos_status = "✗ Script Error"
            self.logger.error(
                "Areas with multiple silos check error: {}".format(str(e))
            )
            has_errors = True
            self._update_check_icon_and_color(
                "areas_multiple_silos", self.check_areas_multiple_data_silos_status
            )

        # CHECK 11: Areas without Data Silos
        try:
            if not correlation_succeeded:
                self.check_areas_no_data_silos_status = (
                    "✗ Unable to Correlate - Script Error"
                )
                has_errors = True
            elif config is None or not area_scheme_objects:
                self.check_areas_no_data_silos_status = (
                    "✗ Cannot check - correlation not performed"
                )
                has_errors = True
            else:
                areas_without_silos = []
                for scheme in area_scheme_objects:
                    areas_without_silos.extend(scheme.get_areas_without_data_silos())

                if len(areas_without_silos) > 0:
                    self.check_areas_no_data_silos_status = (
                        "⚠️ {} areas contain no Data Silos".format(
                            len(areas_without_silos)
                        )
                    )
                    has_warnings = True
                else:
                    self.check_areas_no_data_silos_status = (
                        "✓ All areas contain Data Silos"
                    )

            self._update_check_icon_and_color(
                "areas_no_silos", self.check_areas_no_data_silos_status
            )
        except Exception as e:
            self.check_areas_no_data_silos_status = "✗ Script Error"
            self.logger.error("Areas without silos check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "areas_no_silos", self.check_areas_no_data_silos_status
            )

        # CHECK 12: Partial Area Scheme Coverage
        try:
            if not correlation_succeeded:
                self.check_data_silos_partial_containment_status = (
                    "✗ Unable to Correlate - Script Error"
                )
                has_errors = True
            elif (
                config is None
                or ext_data_silo_collection is None
                or not area_scheme_objects
            ):
                self.check_data_silos_partial_containment_status = (
                    "✗ Cannot check - correlation not performed"
                )
                has_errors = True
            else:
                configured_scheme_names = [
                    scheme.bound_silo_parameter_name for scheme in area_scheme_objects
                ]
                silos_with_partial_coverage = []

                for ext_silo in ext_data_silo_collection.extended_data_silos:
                    related_schemes = set(ext_silo.related_area_elements.keys())
                    if related_schemes and not related_schemes.issuperset(
                        configured_scheme_names
                    ):
                        silos_with_partial_coverage.append(ext_silo)

                if len(silos_with_partial_coverage) > 0:
                    self.check_data_silos_partial_containment_status = (
                        "⚠️ {} Data Silos missing some area schemes".format(
                            len(silos_with_partial_coverage)
                        )
                    )
                    has_warnings = True
                else:
                    self.check_data_silos_partial_containment_status = (
                        "✓ All Data Silos have full area scheme coverage"
                    )

            self._update_check_icon_and_color(
                "silos_partial_containment",
                self.check_data_silos_partial_containment_status,
            )
        except Exception as e:
            self.check_data_silos_partial_containment_status = "✗ Script Error"
            self.logger.error("Partial coverage check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "silos_partial_containment",
                self.check_data_silos_partial_containment_status,
            )

        # CHECK 13: No Area Containment
        try:
            if not correlation_succeeded:
                self.check_data_silos_no_containment_status = (
                    "✗ Unable to Correlate - Script Error"
                )
                has_errors = True
            elif config is None or ext_data_silo_collection is None:
                self.check_data_silos_no_containment_status = (
                    "✗ Cannot check - correlation not performed"
                )
                has_errors = True
            else:
                silos_with_no_containment = []

                for ext_silo in ext_data_silo_collection.extended_data_silos:
                    if not ext_silo.related_area_elements:
                        silos_with_no_containment.append(ext_silo)

                if len(silos_with_no_containment) > 0:
                    self.check_data_silos_no_containment_status = (
                        "⚠️ {} Data Silos not contained by any areas".format(
                            len(silos_with_no_containment)
                        )
                    )
                    has_warnings = True
                else:
                    self.check_data_silos_no_containment_status = (
                        "✓ All Data Silos contained by areas"
                    )

            self._update_check_icon_and_color(
                "silos_no_containment", self.check_data_silos_no_containment_status
            )
        except Exception as e:
            self.check_data_silos_no_containment_status = "✗ Script Error"
            self.logger.error("No containment check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "silos_no_containment", self.check_data_silos_no_containment_status
            )

        # CHECK 14: Data Silos Availability (Worksharing)
        try:
            if config is None or ext_data_silo_collection is None:
                self.check_data_silos_availability_status = (
                    "✗ Cannot check availability - data silos not loaded"
                )
                has_errors = True
            elif not revit.doc.IsWorkshared:
                self.check_data_silos_availability_status = (
                    "✓ N/A - Model not workshared"
                )
            else:
                unavailable_ids = (
                    ext_data_silo_collection.check_worksharing_availability()
                )

                if len(unavailable_ids) > 0:
                    self.check_data_silos_availability_status = (
                        "⚠️ {} Data Silos unavailable for editing".format(
                            len(unavailable_ids)
                        )
                    )
                    has_warnings = True
                else:
                    self.check_data_silos_availability_status = (
                        "✓ All Data Silos available for editing"
                    )

            self._update_check_icon_and_color(
                "data_silos_availability", self.check_data_silos_availability_status
            )
        except Exception as e:
            self.check_data_silos_availability_status = "✗ Script Error"
            self.logger.error("Silos availability check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "data_silos_availability", self.check_data_silos_availability_status
            )

        # CHECK 15: Program Volumes Availability (Worksharing)
        try:
            if config is None or ext_pv_instance_collection is None:
                self.check_program_volumes_availability_status = (
                    "✗ Cannot check availability - program volumes not loaded"
                )
                has_errors = True
            elif not revit.doc.IsWorkshared:
                self.check_program_volumes_availability_status = (
                    "✓ N/A - Model not workshared"
                )
            else:
                unavailable_ids = (
                    ext_pv_instance_collection.check_worksharing_availability()
                )

                if len(unavailable_ids) > 0:
                    self.check_program_volumes_availability_status = (
                        "⚠️ {} Program Volumes unavailable for editing".format(
                            len(unavailable_ids)
                        )
                    )
                    has_warnings = True
                else:
                    self.check_program_volumes_availability_status = (
                        "✓ All Program Volumes available for editing"
                    )

            self._update_check_icon_and_color(
                "program_volumes_availability",
                self.check_program_volumes_availability_status,
            )
        except Exception as e:
            self.check_program_volumes_availability_status = "✗ Script Error"
            self.logger.error("PVs availability check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "program_volumes_availability",
                self.check_program_volumes_availability_status,
            )

        # CHECK 16: Areas Availability (Worksharing)
        try:
            if config is None or not area_scheme_objects:
                self.check_areas_availability_status = (
                    "✗ Cannot check availability - areas not loaded"
                )
                has_errors = True
            elif not revit.doc.IsWorkshared:
                self.check_areas_availability_status = "✓ N/A - Model not workshared"
            else:
                # Collect all area element IDs from all schemes
                area_ids_to_checkout = []
                for scheme in area_scheme_objects:
                    for ext_area in scheme.extended_areas:
                        area_ids_to_checkout.append(ext_area.Id)

                # Try to check out all areas
                typed_list_of_areas = List[DB.ElementId](area_ids_to_checkout)
                checked_out_areas = DB.WorksharingUtils.CheckoutElements(
                    revit.doc, typed_list_of_areas
                )

                # Count unavailable areas
                unavailable_count = len(area_ids_to_checkout) - len(checked_out_areas)

                if unavailable_count > 0:
                    self.check_areas_availability_status = (
                        "⚠️ {} Areas unavailable for editing".format(unavailable_count)
                    )
                    has_warnings = True
                else:
                    self.check_areas_availability_status = (
                        "✓ All Areas available for editing"
                    )

            self._update_check_icon_and_color(
                "areas_availability", self.check_areas_availability_status
            )
        except Exception as e:
            self.check_areas_availability_status = "✗ Script Error"
            self.logger.error("Areas availability check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "areas_availability", self.check_areas_availability_status
            )

        # Update timestamp BEFORE updating overall status so it's included in the message
        self.preflight_last_run_timestamp = datetime.now().strftime("%H:%M:%S")

        # Update overall preflight status
        self._update_preflight_status(has_errors, has_warnings)

        self.logger.info(
            "Preflight checks completed - Errors: {}, Warnings: {}".format(
                "Yes" if has_errors else "No", "Yes" if has_warnings else "No"
            )
        )

        self.logger.info("=" * 60)
        self.logger.info("Preflight Checks Complete")
        self.logger.info("=" * 60)

    def _reset_check_statuses(self):
        """Reset all check statuses to 'running' state."""
        running_status = "[ ... ] Checking..."

        self.check_config_status = running_status
        self.check_level_mappings_status = running_status
        self.check_generic_model_parameters_status = running_status
        self.check_area_schemes_status = running_status
        self.check_area_instances_status = running_status
        self.check_grouped_areas_status = running_status
        self.check_data_silo_family_loaded_status = running_status
        self.check_data_silo_instances_status = running_status
        self.check_uid_uniqueness_status = running_status
        self.check_areas_multiple_data_silos_status = running_status
        self.check_areas_no_data_silos_status = running_status
        self.check_data_silos_partial_containment_status = running_status
        self.check_data_silos_no_containment_status = running_status
        self.check_data_silos_availability_status = running_status
        self.check_program_volumes_availability_status = running_status
        self.check_areas_availability_status = running_status

    def _update_preflight_status(self, has_errors, has_warnings):
        """Update the overall preflight status based on check results.

        Args:
            has_errors (bool): Whether any checks had errors
            has_warnings (bool): Whether any checks had warnings
        """
        # Count errors and warnings by checking status strings
        error_count = 0
        warning_count = 0

        # Check all status properties for ✗ (error) or ⚠️ (warning)
        for prop_name in dir(self):
            if prop_name.startswith("check_") and prop_name.endswith("_status"):
                status_value = getattr(self, prop_name)
                if status_value and status_value.startswith("✗"):
                    error_count += 1
                elif status_value and status_value.startswith("⚠️"):
                    warning_count += 1

        # Build status message with timestamp
        timestamp_text = ""
        if (
            self.preflight_last_run_timestamp
            and self.preflight_last_run_timestamp != "Not yet run"
        ):
            timestamp_text = " • Last checked: {}".format(
                self.preflight_last_run_timestamp
            )
        elif self.preflight_last_run_timestamp == "Not yet run":
            self.preflight_status_message = "Not yet run"
            self.preflight_status_icon_path = "common\\assets\\feedback_unknown.png"
            self.preflight_status_background = "#FFF9C4"  # Light yellow
            self.preflight_status_foreground = "#F57C00"  # Orange text
            self.preflight_has_issues = False
            self.preflight_checks_passed = False
            self.can_run_sync = False
            if self.preflight_status_icon:
                self.preflight_status_icon.Tag = self.preflight_status_icon_path
            return

        # Set message and colors based on results
        if error_count > 0:
            self.preflight_status_message = "✗ {} error{} found{}{}".format(
                error_count,
                "s" if error_count != 1 else "",
                " ({} warning{})".format(
                    warning_count, "s" if warning_count != 1 else ""
                )
                if warning_count > 0
                else "",
                timestamp_text,
            )
            self.preflight_status_background = "#FFEBEE"  # Light red
            self.preflight_status_foreground = "#C62828"  # Dark red text
            self.preflight_status_icon_path = "common\\assets\\feedback_error.png"
            self.preflight_has_issues = True
            self.preflight_checks_passed = False
            self.can_run_sync = False
        elif warning_count > 0:
            self.preflight_status_message = "⚠️ {} warning{} found{}".format(
                warning_count, "s" if warning_count != 1 else "", timestamp_text
            )
            self.preflight_status_background = "#FFF9C4"  # Light yellow
            self.preflight_status_foreground = "#F57C00"  # Orange text
            self.preflight_status_icon_path = "common\\assets\\feedback_warning.png"
            self.preflight_has_issues = False
            self.preflight_checks_passed = True
            self.can_run_sync = True
        else:
            self.preflight_status_message = "✓ All checks passed{}".format(
                timestamp_text
            )
            self.preflight_status_background = "#E8F5E9"  # Light green
            self.preflight_status_foreground = "#2E7D32"  # Dark green text
            self.preflight_status_icon_path = "common\\assets\\feedback_ok.png"
            self.preflight_has_issues = False
            self.preflight_checks_passed = True
            self.can_run_sync = True

        # Update the main preflight status icon
        if self.preflight_status_icon:
            self.preflight_status_icon.Tag = self.preflight_status_icon_path

    def _execute_sync_process(self):
        """Main sync process execution method (runs in Revit API context via external event).

        This method orchestrates the three-phase data synchronization:
        1. Area Harvest - Create/update Areas from PDSOs
        2. Family Geometry - Update Program Volume family geometry
        3. Instance Updates - Update Program Volume instance data

        Each phase runs independently with error logging. Progress updates are
        made directly to UI properties (no threading/Dispatcher needed).
        """
        try:
            self.logger.info("=" * 80)
            self.logger.info("STARTING DATA SYNC PROCESS")
            self.logger.info("=" * 80)

            # Reset progress UI
            self.progress_value = 0
            self.progress_max = 100
            self.current_step = "Initializing..."
            self.progress_status = "Starting sync process..."
            self.completion_message_visibility = Visibility.Collapsed

            # Re-read the tracked parameter list (it may have changed since the last run)
            get_registry(revit.doc, refresh=True)

            # Instantiate services
            self.logger.info("Instantiating services...")
            storage_service = PDSOExtensibleStorageService(revit.doc)
            correlation_service = PDSOCorrelationService()
            data_service = PDSODataService(revit.doc)
            family_manager = PDSOFamilyManagerService(revit.doc)
            level_mapping = PDSOLevelMappingService(revit.doc, storage_service)
            workset_coordinator = WorksetCoordinationService(revit.doc)

            # Load configuration from extensible storage
            self.logger.info("Loading configuration from extensible storage...")
            config = storage_service.get_document_config()
            if config is None:
                self.logger.error("Failed to load PDSO configuration from document")
                self.current_step = "ERROR: Configuration not found"
                self.progress_status = "Failed to load configuration"
                return

            # Log configuration
            self.logger.info("Configuration loaded:")
            self.logger.info(
                "  Family Type Name: {}".format(config.data_silo_family_name)
            )
            self.logger.info("  Area Scheme Mappings:")
            # Convert .NET Dictionary to Python dict
            area_scheme_mappings_dict = (
                dict(config.area_scheme_mappings) if config.area_scheme_mappings else {}
            )
            for pdso_scheme, area_scheme in area_scheme_mappings_dict.items():
                self.logger.info("    {} -> {}".format(pdso_scheme, area_scheme))

            # Create area schemes list dynamically from config
            area_schemes = [
                {"pdso": k, "area": v} for k, v in area_scheme_mappings_dict.items()
            ]

            # Log UI settings (for conditional execution)
            self.logger.info("UI Settings:")
            self.logger.info(
                "  Area Harvest Workset: {}".format(self._area_harvest_workset_option)
            )
            self.logger.info(
                "  Area Harvest Pinning: {}".format(self._area_harvest_pinning_option)
            )
            self.logger.info(
                "  PV Geometry Update: {}".format(
                    self._program_volume_geometry_update_option
                )
            )
            self.logger.info(
                "  PV Geometry Workset: {}".format(
                    self._program_volume_geometry_workset_option
                )
            )
            self.logger.info(
                "  PV Geometry Pinning: {}".format(
                    self._program_volume_geometry_pinning_option
                )
            )
            self.logger.info(
                "  PV Data Update: {}".format(self._program_volume_data_update_option)
            )

            # Phase 1: Area Harvest (0-33%)
            try:
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 1: AREA HARVEST")
                self.logger.info("=" * 80)
                self.current_step = "Phase 1: Area Harvest"
                self.progress_value = 0
                self.progress_status = "Harvesting areas from PDSOs..."

                # Execute Phase 1 and capture collections for later phases
                ext_data_silo_collection, ext_level_collection = (
                    self._execute_phase_1_area_harvest(
                        storage_service,
                        correlation_service,
                        data_service,
                        level_mapping,
                        workset_coordinator,
                        config,
                        area_schemes,
                    )
                )

                self.progress_value = 33
                self.logger.info("✓ Phase 1 complete")
            except Exception as e:
                self.logger.error("Phase 1 error: {}".format(str(e)))
                self.logger.error(traceback.format_exc())
                # Continue to next phase

            # Phase 2: Family Geometry (33-66%)
            try:
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 2: FAMILY GEOMETRY UPDATES")
                self.logger.info("=" * 80)
                self.current_step = "Phase 2: Family Geometry"
                self.progress_value = 33
                self.progress_status = "Updating Program Volume geometry..."

                self._execute_phase_2_family_geometry(
                    storage_service,
                    data_service,
                    family_manager,
                    config,
                    ext_data_silo_collection,  # Pass collection from Phase 1
                )

                # Progress is now set by callback, just log completion
                self.logger.info("✓ Phase 2 complete")
            except Exception as e:
                self.logger.error("Phase 2 error: {}".format(str(e)))
                self.logger.error(traceback.format_exc())
                # Continue to next phase

            # Phase 3: Instance Updates (66-100%)
            try:
                self.logger.info("\n" + "=" * 80)
                self.logger.info("PHASE 3: INSTANCE DATA UPDATES")
                self.logger.info("=" * 80)
                self.current_step = "Phase 3: Instance Updates"
                self.progress_value = 66
                self.progress_status = "Updating Program Volume instance data..."

                self._execute_phase_3_instance_updates(
                    storage_service,
                    data_service,
                    workset_coordinator,
                    config,
                    ext_data_silo_collection,  # Pass collection from Phase 1
                    ext_level_collection,  # Pass collection from Phase 1
                )

                self.progress_value = 100
                self.logger.info("✓ Phase 3 complete")
            except Exception as e:
                self.logger.error("Phase 3 error: {}".format(str(e)))
                self.logger.error(traceback.format_exc())

            # Mark complete
            self.current_step = "Complete!"
            self.progress_status = "Sync process completed"
            self.completion_message = "✓ Data synchronization completed!"
            self.completion_message_visibility = Visibility.Visible

            self.logger.info("\n" + "=" * 80)
            self.logger.info("SYNC PROCESS COMPLETE")
            self.logger.info("=" * 80)

            # Add validation warnings to completion message if present
            if (
                hasattr(self, "_validation_warning_message")
                and self._validation_warning_message
            ):
                warning_summary = "\n\nWARNING: Some data silos had level mapping errors and were skipped.\nCheck console for details."
                if self.completion_message:
                    self.completion_message += warning_summary
                else:
                    self.completion_message = (
                        "Sync complete with warnings." + warning_summary
                    )
                    self.completion_message_visibility = Visibility.Visible

        except Exception as e:
            self.logger.error("Critical error in sync process: {}".format(str(e)))
            self.logger.error(traceback.format_exc())
            self.current_step = "ERROR: {}".format(str(e))
            self.progress_status = "Sync process failed"

    def _execute_phase_1_area_harvest(
        self,
        storage_service,
        correlation_service,
        data_service,
        level_mapping,
        workset_coordinator,
        config,
        area_schemes,
    ):
        """Execute Phase 1: Area Harvest.

        Harvests area measurements FROM existing Areas TO PDSOs.
        Steps:
        1. Correlate PDSOs to existing Areas (spatial relationships)
        2. Queue area measurement updates to PDSOs
        3. Commit PDSO parameter updates
        4. Optionally enforce worksets
        5. Optionally pin elements

        Args:
            storage_service: PDSOExtensibleStorageService instance
            correlation_service: PDSOCorrelationService instance
            data_service: PDSODataService instance
            level_mapping: PDSOLevelMappingService instance
            workset_coordinator: WorksetCoordinationService instance
            config: PDSOConfig object from extensible storage
            area_schemes: List of dicts with 'pdso' and 'area' scheme names

        Returns:
            tuple: (ext_data_silo_collection, ext_level_collection) for use in later phases
        """
        # Get data silos and level collection
        ext_data_silo_collection = ExtendedDataSiloCollection(
            revit.doc, config.data_silo_family_name
        )
        ext_level_collection = ExtendedLevelCollection(revit.doc)

        self.logger.info(
            "Found {} PDSOs".format(len(ext_data_silo_collection.extended_data_silos))
        )

        if not ext_data_silo_collection.extended_data_silos:
            self.logger.warning("No PDSOs found - skipping area harvest")
            return

        # Get area scheme objects
        area_scheme_objects = []
        for scheme_mapping in area_schemes:
            pdso_scheme_name = scheme_mapping["pdso"]
            area_scheme_name = scheme_mapping["area"]

            self.logger.info(
                "Processing scheme: {} -> {}".format(pdso_scheme_name, area_scheme_name)
            )

            try:
                # ExtendedAreaScheme(doc, area_scheme_name, bound_silo_parameter_name)
                # Use area_scheme_name for both since they map to same parameter
                ext_area_scheme = ExtendedAreaScheme(
                    revit.doc, area_scheme_name, pdso_scheme_name
                )
                if ext_area_scheme.area_scheme_exists_in_model:
                    area_scheme_objects.append(ext_area_scheme)
                    self.logger.info(
                        "  Found {} areas in scheme".format(
                            len(ext_area_scheme.extended_areas)
                        )
                    )
                else:
                    self.logger.warning(
                        "  Area scheme '{}' not found - skipping".format(
                            area_scheme_name
                        )
                    )
            except Exception as e:
                self.logger.error("  Failed to load scheme: {}".format(str(e)))

        if not area_scheme_objects:
            self.logger.warning("No area schemes loaded - skipping Phase 1")
            return

        # Correlate data silos to areas for each scheme
        self.logger.info(
            "Starting correlation with {} PDSOs and {} area schemes".format(
                len(ext_data_silo_collection.extended_data_silos),
                len(area_scheme_objects),
            )
        )

        total_schemes = len(area_scheme_objects)
        for scheme_index, area_scheme in enumerate(area_scheme_objects, start=1):
            self.current_step = "Correlating PDSOs to Areas ({}/{})".format(
                scheme_index, total_schemes
            )
            self.progress_status = area_scheme.area_scheme_name
            self.progress_value = int((scheme_index / float(total_schemes)) * 100)
            Dispatcher.CurrentDispatcher.Invoke(
                System.Action(lambda: None), DispatcherPriority.Background
            )
            try:
                self.logger.info(
                    "  Correlating to scheme '{}' with {} areas".format(
                        area_scheme.area_scheme_name, len(area_scheme.extended_areas)
                    )
                )

                # Show sample area scheme names from actual areas
                if area_scheme.extended_areas:
                    sample_area = area_scheme.extended_areas[0]
                    self.logger.info(
                        "    Sample area's scheme name: '{}'".format(
                            sample_area.area_scheme_name
                        )
                    )

                correlation_service.correlate_data_silos_to_areas(
                    ext_level_collection, ext_data_silo_collection, area_scheme
                )
                self.logger.info(
                    "  ✓ Correlation complete for '{}'".format(
                        area_scheme.area_scheme_name
                    )
                )
            except Exception as e:
                self.logger.error(
                    "Failed to correlate '{}': {}".format(
                        area_scheme.area_scheme_name, str(e)
                    )
                )

        # Debug: Show what keys were actually stored in related_area_elements
        self.logger.info("\nDEBUG: Checking correlation results...")
        sample_size = min(5, len(ext_data_silo_collection.extended_data_silos))
        self.logger.info("Checking first {} PDSOs:".format(sample_size))
        for i, ext_data_silo in enumerate(
            ext_data_silo_collection.extended_data_silos[:sample_size]
        ):
            keys = list(ext_data_silo.related_area_elements.keys())
            self.logger.info(
                "  PDSO {}: related_area_elements keys = {} (count: {})".format(
                    ext_data_silo.Id.Value, keys, len(keys)
                )
            )

        # Queue area updates (prepares PDSO parameter updates)
        self.current_step = "Queuing area measurements to PDSOs"
        self.progress_status = "Processing {} PDSOs".format(
            len(ext_data_silo_collection.extended_data_silos)
        )
        self.progress_value = 0
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        all_extended_areas = []
        for area_scheme in area_scheme_objects:
            all_extended_areas.extend(area_scheme.extended_areas)

        self.logger.info(
            "Queuing area measurements for {} PDSOs from {} areas".format(
                len(ext_data_silo_collection.extended_data_silos),
                len(all_extended_areas),
            )
        )

        data_service.queue_area_updates(
            ext_data_silo_collection.extended_data_silos, all_extended_areas
        )

        # Commit changes to data silos
        self.current_step = "Committing PDSO parameter updates"
        self.progress_status = "Writing to {} PDSOs".format(
            len(ext_data_silo_collection.extended_data_silos)
        )
        self.progress_value = 50
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        with db.Transaction("PDSO - Harvest Area Data", doc=revit.doc):
            for ext_data_silo in ext_data_silo_collection.extended_data_silos:
                ext_data_silo.commit_pending_changes()

            self.logger.info(
                "Committed area data to {} PDSOs".format(
                    len(ext_data_silo_collection.extended_data_silos)
                )
            )

        # Apply workset enforcement and pinning together in single transaction
        if (
            self._area_harvest_workset_option == "Fix"
            or self._area_harvest_pinning_option == "Pin"
        ):
            self.current_step = "Enforcing worksets and pinning elements"
            self.progress_status = "PDSOs and Areas"
            self.progress_value = 75
            Dispatcher.CurrentDispatcher.Invoke(
                System.Action(lambda: None), DispatcherPriority.Background
            )

            try:
                with db.Transaction("PDSO - Worksets and Pinning", doc=revit.doc):
                    # Workset enforcement
                    if self._area_harvest_workset_option == "Fix":
                        self.logger.info("Enforcing worksets for PDSOs and Areas")

                        # Get workset names from config
                        worksets_dict = dict(config.worksets) if config.worksets else {}

                        # Assign data silos to workset
                        data_silo_workset = worksets_dict.get("Data Silo")
                        if data_silo_workset and data_silo_workset != "Not enforced":
                            workset = workset_coordinator.get_workset_by_name(
                                data_silo_workset
                            )
                            if workset:
                                for ds in ext_data_silo_collection.extended_data_silos:
                                    try:
                                        param = ds.rpw_element.unwrap().get_Parameter(
                                            DB.BuiltInParameter.ELEM_PARTITION_PARAM
                                        )
                                        if param and not param.IsReadOnly:
                                            param.Set(workset.Id.IntegerValue)
                                    except:
                                        pass
                                self.logger.info(
                                    "Assigned {} PDSOs to workset '{}'".format(
                                        len(
                                            ext_data_silo_collection.extended_data_silos
                                        ),
                                        data_silo_workset,
                                    )
                                )

                        # Assign areas to workset
                        area_workset = worksets_dict.get("Area")
                        if area_workset and area_workset != "Not enforced":
                            workset = workset_coordinator.get_workset_by_name(
                                area_workset
                            )
                            if workset:
                                area_count = 0
                                for scheme in area_scheme_objects:
                                    for area in scheme.extended_areas:
                                        try:
                                            param = area.rpw_element.unwrap().get_Parameter(
                                                DB.BuiltInParameter.ELEM_PARTITION_PARAM
                                            )
                                            if param and not param.IsReadOnly:
                                                param.Set(workset.Id.IntegerValue)
                                                area_count += 1
                                        except:
                                            pass
                                self.logger.info(
                                    "Assigned {} Areas to workset '{}'".format(
                                        area_count, area_workset
                                    )
                                )

                    # Pinning (directly set Pinned property, don't use collection method)
                    if self._area_harvest_pinning_option == "Pin":
                        self.logger.info("Pinning PDSOs and Areas")

                        # Pin data silos directly
                        for ds in ext_data_silo_collection.extended_data_silos:
                            ds.rpw_element.Pinned = True
                        self.logger.info(
                            "Pinned {} PDSOs".format(
                                len(ext_data_silo_collection.extended_data_silos)
                            )
                        )

                        # Pin areas directly (with individual error handling)
                        area_pinned_count = 0
                        area_failed_count = 0
                        for area_scheme in area_scheme_objects:
                            for ext_area in area_scheme.extended_areas:
                                try:
                                    ext_area.rpw_area.Pinned = True
                                    area_pinned_count += 1
                                except Exception:
                                    area_failed_count += 1
                                    # Skip areas that can't be pinned (unplaced, redundant, etc.)

                        self.logger.info(
                            "Pinned {} Areas ({} skipped)".format(
                                area_pinned_count, area_failed_count
                            )
                        )

            except Exception as e:
                self.logger.error("Workset/pinning operation failed: {}".format(str(e)))

        # Return collections for use in Phase 2 and 3
        return ext_data_silo_collection, ext_level_collection

    def _update_family_geometry_progress(self, current, total, family_name):
        """Update progress for family geometry processing.

        Args:
            current (int): Current family index (1-based)
            total (int): Total number of families
            family_name (str): Name of the family being processed
        """
        self.current_step = "Updating Program Volume family geometry ({}/{})".format(
            current, total
        )
        self.progress_status = family_name
        # Map to 0-100% for this specific step
        step_progress = int((current / float(total)) * 100)
        self.progress_value = step_progress

        # Force WPF to process pending UI updates so progress bar actually displays
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

    def _execute_phase_2_family_geometry(
        self,
        storage_service,
        data_service,
        family_manager,
        config,
        ext_data_silo_collection,
    ):
        """Execute Phase 2: Program Volume Family Geometry.

        Updates Program Volume family geometry from PDSO-linked Area data.
        Can be skipped via UI setting.

        Args:
            storage_service: PDSOExtensibleStorageService instance
            data_service: PDSODataService instance
            family_manager: PDSOFamilyManagerService instance
            config: PDSOConfig object from extensible storage
            ext_data_silo_collection: ExtendedDataSiloCollection from Phase 1 (with correlation data)
        """
        # Check skip flag
        if self._program_volume_geometry_update_option == "No":
            self.logger.info("Skipping family geometry updates (per UI setting)")
            return

        # Get families collection (but use existing data silo collection from Phase 1)
        self.logger.info(
            "Searching for Program Volume families with prefix: '{}'".format(
                config.program_volume_family_name_prefix
            )
        )

        ext_program_volume_families_collection = ExtendedProgramVolumeFamilyCollection(
            revit.doc, config.program_volume_family_name_prefix
        )

        self.logger.info(
            "Found {} Program Volume families".format(
                len(
                    ext_program_volume_families_collection.extended_program_volume_families
                )
            )
        )

        if not ext_program_volume_families_collection.extended_program_volume_families:
            self.logger.warning(
                "No existing Program Volume families found with prefix '{}'; new families will be created where possible".format(
                    config.program_volume_family_name_prefix
                )
            )

        # Get correlation service
        correlation_service = PDSOCorrelationService()

        # Correlate data silos to program volume families
        self.current_step = "Correlating PDSOs to Program Volume families"
        self.progress_status = "Building spatial relationships"
        self.progress_value = 0
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        correlation_service.correlate_data_silos_to_program_volume_families(
            ext_data_silo_collection, ext_program_volume_families_collection
        )
        self.logger.info("Correlation complete")

        # Create/update program volume families
        self.current_step = "Updating Program Volume family geometry"
        self.progress_status = "Starting geometry updates..."
        self.progress_value = 0
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        new_families = family_manager.create_or_update_program_volume_families(
            ext_data_silo_collection,
            ext_program_volume_families_collection,
            progress_callback=self._update_family_geometry_progress,
        )

        if new_families:
            self.logger.info("Created {} new families".format(len(new_families)))

        # Commit family changes
        self.current_step = "Committing Program Volume family updates"
        self.progress_status = "Writing extensible storage data"
        self.progress_value = 100
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        with db.Transaction("PDSO - Update Family Geometry", doc=revit.doc):
            committed_family_count = 0
            skipped_family_count = 0
            for extended_program_volume_family in (
                ext_program_volume_families_collection.extended_program_volume_families
            ):
                if extended_program_volume_family.rpw_element is None:
                    skipped_family_count += 1
                    continue
                extended_program_volume_family.commit_pending_changes()
                committed_family_count += 1

            self.logger.info(
                "Committed {} family updates ({} skipped - no family element)".format(
                    committed_family_count, skipped_family_count
                )
            )

        # Apply workset enforcement if configured
        if self._program_volume_geometry_workset_option == "Fix":
            self.current_step = "Enforcing worksets for Program Volume families"
            self.progress_status = "Assigning to configured workset"
            self.progress_value = 100
            Dispatcher.CurrentDispatcher.Invoke(
                System.Action(lambda: None), DispatcherPriority.Background
            )

            self.logger.info("Enforcing worksets for Program Volume families")

            try:
                # Get workset name from config
                worksets_dict = dict(config.worksets) if config.worksets else {}
                self.logger.debug(
                    "[DEBUG] Config worksets dict (Phase 2): {}".format(worksets_dict)
                )
                pv_workset = worksets_dict.get("Program Volume")
                self.logger.debug(
                    "[DEBUG] Retrieved pv_workset value (Phase 2): '{}'".format(
                        pv_workset
                    )
                )

                if pv_workset and pv_workset != "Not enforced":
                    family_elements = [
                        fam.rpw_element.unwrap()
                        for fam in ext_program_volume_families_collection.extended_program_volume_families
                        if fam.rpw_element is not None
                    ]
                    workset_coordinator = WorksetCoordinationService(revit.doc)
                    workset_coordinator.assign_elements_to_workset(
                        family_elements, pv_workset
                    )
                    self.logger.info(
                        "Assigned {} families to workset '{}'".format(
                            len(family_elements), pv_workset
                        )
                    )
                else:
                    self.logger.info("Program Volume workset not enforced")
            except Exception as e:
                self.logger.error("Workset enforcement failed: {}".format(str(e)))

        # Apply pinning if configured
        if self._program_volume_geometry_pinning_option == "Pin":
            self.current_step = "Pinning Program Volume families"
            self.progress_status = "Pinning {} families".format(
                len(
                    ext_program_volume_families_collection.extended_program_volume_families
                )
            )
            self.progress_value = 100
            Dispatcher.CurrentDispatcher.Invoke(
                System.Action(lambda: None), DispatcherPriority.Background
            )

            self.logger.info("Pinning Program Volume families")

            try:
                with db.Transaction("PDSO - Pin Families", doc=revit.doc):
                    # Pin families directly
                    pinned_count = 0
                    for pvf in ext_program_volume_families_collection.extended_program_volume_families:
                        if pvf.rpw_element is None:
                            continue
                        pvf.rpw_element.Pinned = True
                        pinned_count += 1
                    self.logger.info(
                        "Pinned {} families".format(pinned_count)
                    )
            except Exception as e:
                self.logger.error("Pinning failed: {}".format(str(e)))

    def _execute_phase_3_instance_updates(
        self,
        storage_service,
        data_service,
        workset_coordinator,
        config,
        ext_data_silo_collection,
        ext_level_collection,
    ):
        """Execute Phase 3: Program Volume Instance Updates.

        Updates Program Volume instance parameters from PDSO data and Area measurements.
        Handles workset assignment and pinning.

        Args:
            storage_service: PDSOExtensibleStorageService instance
            data_service: PDSODataService instance
            workset_coordinator: WorksetCoordinationService instance
            config: PDSOConfig object from extensible storage
            ext_data_silo_collection: ExtendedDataSiloCollection from Phase 1 (with correlation data)
            ext_level_collection: ExtendedLevelCollection from Phase 1
        """
        # Check skip flag
        if self._program_volume_data_update_option == "No":
            self.logger.info("Skipping instance data updates (per UI setting)")
            return

        # Get instance collection (use existing data silo and level collections from Phase 1)
        self.logger.info(
            "Searching for Program Volume instances with prefix: '{}'".format(
                config.program_volume_family_name_prefix
            )
        )

        ext_program_volume_instance_collection = (
            ExtendedProgramVolumeInstanceCollection(
                revit.doc, config.program_volume_family_name_prefix
            )
        )

        self.logger.info(
            "Found {} Program Volume instances".format(
                len(
                    ext_program_volume_instance_collection.extended_program_volume_instances
                )
            )
        )

        if not ext_program_volume_instance_collection.extended_program_volume_instances:
            self.logger.warning(
                "No existing Program Volume instances found with prefix '{}'; required instances will be created during correlation".format(
                    config.program_volume_family_name_prefix
                )
            )

        # Get services
        correlation_service = PDSOCorrelationService()
        storage_service_phase3 = PDSOExtensibleStorageService(revit.doc)
        level_mapping_service = PDSOLevelMappingService(
            revit.doc, storage_service_phase3
        )

        # Correlate data silos to program volume instances (with level mapping validation)
        self.current_step = "Correlating PDSOs to Program Volume instances"
        self.progress_status = "Validating level mappings and spatial relationships"
        self.progress_value = 0
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        correlation_service.correlate_data_silos_to_program_volume_instances(
            ext_level_collection,
            ext_data_silo_collection,
            ext_program_volume_instance_collection,
            level_mapping_service,
        )

        # Check validation result and display prominently
        if not ext_program_volume_instance_collection.validation_passed:
            self.logger.warning("Level mapping validation failed - see details below")
            if ext_program_volume_instance_collection.validation_result:
                self.logger.warning(
                    ext_program_volume_instance_collection.validation_result.get_summary_message()
                )
                # Store validation message for UI display
                self._validation_warning_message = ext_program_volume_instance_collection.validation_result.get_summary_message()
            self.logger.warning(
                "Continuing with sync - PDSOs with complete level mappings will process normally"
            )
            # Store validation message for UI display
            self._validation_warning_message = ext_program_volume_instance_collection.validation_result.get_summary_message()
            # Continue processing instead of aborting

        self.logger.info(
            "Correlation complete. Instances without Data Silo: {}".format(
                len(
                    ext_program_volume_instance_collection.ext_program_volumes_without_data_silo
                )
            )
        )

        # Queue data updates
        self.current_step = "Queuing Program Volume instance parameter updates"
        self.progress_status = "Preparing {} instances".format(
            len(
                ext_program_volume_instance_collection.extended_program_volume_instances
            )
        )
        self.progress_value = 25
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        data_service.queue_program_volume_instance_data_updates(
            ext_data_silo_collection.extended_data_silos,
            ext_program_volume_instance_collection.extended_program_volume_instances,
        )
        self.logger.info("Queued data updates")

        # Queue location updates (outside transaction)
        data_service.queue_program_volume_instance_location_updates(
            ext_data_silo_collection.extended_data_silos,
            ext_program_volume_instance_collection,
        )

        # Unpin all instances before attempting to move them
        self.current_step = "Unpinning Program Volume instances for updates"
        self.progress_status = "Preparing {} instances for relocation".format(
            len(
                ext_program_volume_instance_collection.extended_program_volume_instances
            )
        )
        self.progress_value = 50
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        self.logger.info("Unpinning Program Volume instances for updates")
        with db.Transaction("PDSO - Unpin Instances", doc=revit.doc):
            for ext_instance in (
                ext_program_volume_instance_collection.extended_program_volume_instances
            ):
                if ext_instance.rpw_element is None:
                    continue  # Skip instances that don't exist yet
                try:
                    ext_instance.rpw_element.Pinned = False
                except:
                    pass  # Ignore if already unpinned

        # Commit changes (creates new instances, updates locations, and deletes orphans)
        self.current_step = "Committing Program Volume instance updates"
        self.progress_status = "Creating, moving, and updating instances"
        self.progress_value = 75
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )

        with db.Transaction("PDSO - Update Program Volume Instances", doc=revit.doc):
            # Commit all pending changes (location updates, instance creation, parameter updates)
            ext_program_volume_instance_collection.commit_pending_changes()

            self.logger.info("Committed instance updates")

        # Apply workset enforcement if configured
        if self._program_volume_geometry_workset_option == "Fix":
            self.current_step = "Enforcing worksets for Program Volume instances"
            self.progress_status = "Assigning to configured workset"
            self.progress_value = 90
            Dispatcher.CurrentDispatcher.Invoke(
                System.Action(lambda: None), DispatcherPriority.Background
            )

            self.logger.info("Enforcing worksets for Program Volume instances")

            # Debug: Log collection state before workset enforcement
            total_instances = len(
                ext_program_volume_instance_collection.extended_program_volume_instances
            )
            instances_with_element = sum(
                1
                for inst in ext_program_volume_instance_collection.extended_program_volume_instances
                if inst.rpw_element is not None
            )
            self.logger.debug(
                "[DEBUG] Before workset enforcement: {} total instances, {} with rpw_element set".format(
                    total_instances, instances_with_element
                )
            )

            try:
                # Get workset name from config
                worksets_dict = dict(config.worksets) if config.worksets else {}
                self.logger.debug(
                    "[DEBUG] Config worksets dict: {}".format(worksets_dict)
                )
                pv_workset = worksets_dict.get("Program Volume")
                self.logger.debug(
                    "[DEBUG] Retrieved pv_workset value: '{}'".format(pv_workset)
                )

                if pv_workset and pv_workset != "Not enforced":
                    # Debug: Explicitly filter and log which instances are included
                    instance_elements = []
                    skipped_none_count = 0
                    skipped_unwrap_count = 0

                    for inst in ext_program_volume_instance_collection.extended_program_volume_instances:
                        if inst.rpw_element is None:
                            skipped_none_count += 1
                            self.logger.debug(
                                "[DEBUG] Skipping instance with rpw_element=None"
                            )
                            continue

                        try:
                            unwrapped = inst.rpw_element.unwrap()
                            instance_elements.append(unwrapped)
                            self.logger.debug(
                                "[DEBUG] Added instance {} to workset enforcement list".format(
                                    unwrapped.Id
                                )
                            )
                        except Exception as e:
                            skipped_unwrap_count += 1
                            self.logger.debug(
                                "[DEBUG] Failed to unwrap instance: {}".format(str(e))
                            )

                    self.logger.debug(
                        "[DEBUG] Workset enforcement list: {} instances (skipped {} None, {} unwrap failures)".format(
                            len(instance_elements),
                            skipped_none_count,
                            skipped_unwrap_count,
                        )
                    )
                    self.logger.debug(
                        "[DEBUG] Calling assign_elements_to_workset with workset='{}'".format(
                            pv_workset
                        )
                    )

                    workset_coordinator.assign_elements_to_workset(
                        instance_elements, pv_workset
                    )
                    self.logger.info(
                        "Assigned {} instances to workset '{}'".format(
                            len(instance_elements), pv_workset
                        )
                    )
                else:
                    self.logger.info("Program Volume workset not enforced")
            except Exception as e:
                self.logger.error("Workset enforcement failed: {}".format(str(e)))

        # Apply pinning if configured
        if self._program_volume_geometry_pinning_option == "Pin":
            self.current_step = "Pinning Program Volume instances"
            self.progress_status = "Pinning {} instances".format(
                len(
                    ext_program_volume_instance_collection.extended_program_volume_instances
                )
            )
            self.progress_value = 100
            Dispatcher.CurrentDispatcher.Invoke(
                System.Action(lambda: None), DispatcherPriority.Background
            )

            self.logger.info("Pinning Program Volume instances")

            try:
                with db.Transaction("PDSO - Pin Instances", doc=revit.doc):
                    # Pin instances directly
                    pinned_count = 0
                    for pvi in ext_program_volume_instance_collection.extended_program_volume_instances:
                        if pvi.rpw_element is None:
                            continue
                        pvi.rpw_element.Pinned = True
                        pinned_count += 1
                    self.logger.info(
                        "Pinned {} instances".format(pinned_count)
                    )
            except Exception as e:
                self.logger.error("Pinning failed: {}".format(str(e)))

    # ========================================
    # Properties - Preflight Check Statuses
    # ========================================

    @property
    def check_config_status(self):
        """Get config validation status."""
        return self._check_config_status

    @check_config_status.setter
    def check_config_status(self, value):
        """Set config validation status and notify UI."""
        if self._check_config_status != value:
            self._check_config_status = value
            self.on_property_changed("check_config_status")

    @property
    def check_level_mappings_status(self):
        """Get level mappings validation status."""
        return self._check_level_mappings_status

    @check_level_mappings_status.setter
    def check_level_mappings_status(self, value):
        """Set level mappings validation status and notify UI."""
        if self._check_level_mappings_status != value:
            self._check_level_mappings_status = value
            self.on_property_changed("check_level_mappings_status")

    @property
    def check_generic_model_parameters_status(self):
        """Get generic model parameters status."""
        return self._check_generic_model_parameters_status

    @check_generic_model_parameters_status.setter
    def check_generic_model_parameters_status(self, value):
        """Set generic model parameters status and notify UI."""
        if self._check_generic_model_parameters_status != value:
            self._check_generic_model_parameters_status = value
            self.on_property_changed("check_generic_model_parameters_status")

    @property
    def check_area_schemes_status(self):
        """Get area schemes status."""
        return self._check_area_schemes_status

    @check_area_schemes_status.setter
    def check_area_schemes_status(self, value):
        """Set area schemes status and notify UI."""
        if self._check_area_schemes_status != value:
            self._check_area_schemes_status = value
            self.on_property_changed("check_area_schemes_status")

    @property
    def check_area_instances_status(self):
        """Get area instances status."""
        return self._check_area_instances_status

    @check_area_instances_status.setter
    def check_area_instances_status(self, value):
        """Set area instances status and notify UI."""
        if self._check_area_instances_status != value:
            self._check_area_instances_status = value
            self.on_property_changed("check_area_instances_status")

    @property
    def check_grouped_areas_status(self):
        """Get grouped areas status."""
        return self._check_grouped_areas_status

    @check_grouped_areas_status.setter
    def check_grouped_areas_status(self, value):
        """Set grouped areas status and notify UI."""
        if self._check_grouped_areas_status != value:
            self._check_grouped_areas_status = value
            self.on_property_changed("check_grouped_areas_status")

    @property
    def check_data_silo_family_loaded_status(self):
        """Get data silo family loaded status."""
        return self._check_data_silo_family_loaded_status

    @check_data_silo_family_loaded_status.setter
    def check_data_silo_family_loaded_status(self, value):
        """Set data silo family loaded status and notify UI."""
        if self._check_data_silo_family_loaded_status != value:
            self._check_data_silo_family_loaded_status = value
            self.on_property_changed("check_data_silo_family_loaded_status")

    @property
    def check_data_silo_instances_status(self):
        """Get data silo instances status."""
        return self._check_data_silo_instances_status

    @check_data_silo_instances_status.setter
    def check_data_silo_instances_status(self, value):
        """Set data silo instances status and notify UI."""
        if self._check_data_silo_instances_status != value:
            self._check_data_silo_instances_status = value
            self.on_property_changed("check_data_silo_instances_status")

    @property
    def check_uid_uniqueness_status(self):
        """Get UID uniqueness status."""
        return self._check_uid_uniqueness_status

    @check_uid_uniqueness_status.setter
    def check_uid_uniqueness_status(self, value):
        """Set UID uniqueness status and notify UI."""
        if self._check_uid_uniqueness_status != value:
            self._check_uid_uniqueness_status = value
            self.on_property_changed("check_uid_uniqueness_status")

    @property
    def check_areas_multiple_data_silos_status(self):
        """Get areas with multiple data silos status."""
        return self._check_areas_multiple_data_silos_status

    @check_areas_multiple_data_silos_status.setter
    def check_areas_multiple_data_silos_status(self, value):
        """Set areas with multiple data silos status and notify UI."""
        if self._check_areas_multiple_data_silos_status != value:
            self._check_areas_multiple_data_silos_status = value
            self.on_property_changed("check_areas_multiple_data_silos_status")

    @property
    def check_areas_no_data_silos_status(self):
        """Get areas with no data silos status."""
        return self._check_areas_no_data_silos_status

    @check_areas_no_data_silos_status.setter
    def check_areas_no_data_silos_status(self, value):
        """Set areas with no data silos status and notify UI."""
        if self._check_areas_no_data_silos_status != value:
            self._check_areas_no_data_silos_status = value
            self.on_property_changed("check_areas_no_data_silos_status")

    @property
    def check_data_silos_partial_containment_status(self):
        """Get data silos partial containment status."""
        return self._check_data_silos_partial_containment_status

    @check_data_silos_partial_containment_status.setter
    def check_data_silos_partial_containment_status(self, value):
        """Set data silos partial containment status and notify UI."""
        if self._check_data_silos_partial_containment_status != value:
            self._check_data_silos_partial_containment_status = value
            self.on_property_changed("check_data_silos_partial_containment_status")

    @property
    def check_data_silos_no_containment_status(self):
        """Get data silos no containment status."""
        return self._check_data_silos_no_containment_status

    @check_data_silos_no_containment_status.setter
    def check_data_silos_no_containment_status(self, value):
        """Set data silos no containment status and notify UI."""
        if self._check_data_silos_no_containment_status != value:
            self._check_data_silos_no_containment_status = value
            self.on_property_changed("check_data_silos_no_containment_status")

    @property
    def check_data_silos_availability_status(self):
        """Get data silos availability status."""
        return self._check_data_silos_availability_status

    @check_data_silos_availability_status.setter
    def check_data_silos_availability_status(self, value):
        """Set data silos availability status and notify UI."""
        if self._check_data_silos_availability_status != value:
            self._check_data_silos_availability_status = value
            self.on_property_changed("check_data_silos_availability_status")

    @property
    def check_program_volumes_availability_status(self):
        """Get program volumes availability status."""
        return self._check_program_volumes_availability_status

    @check_program_volumes_availability_status.setter
    def check_program_volumes_availability_status(self, value):
        """Set program volumes availability status and notify UI."""
        if self._check_program_volumes_availability_status != value:
            self._check_program_volumes_availability_status = value
            self.on_property_changed("check_program_volumes_availability_status")

    @property
    def check_areas_availability_status(self):
        """Get areas availability status."""
        return self._check_areas_availability_status

    @check_areas_availability_status.setter
    def check_areas_availability_status(self, value):
        """Set areas availability status and notify UI."""
        if self._check_areas_availability_status != value:
            self._check_areas_availability_status = value
            self.on_property_changed("check_areas_availability_status")

    # ========================================
    # Properties - Preflight Control
    # ========================================

    @property
    def preflight_has_issues(self):
        """Get whether preflight checks have issues."""
        return self._preflight_has_issues

    @preflight_has_issues.setter
    def preflight_has_issues(self, value):
        """Set whether preflight checks have issues and notify UI."""
        if self._preflight_has_issues != value:
            self._preflight_has_issues = value
            self.on_property_changed("preflight_has_issues")

    @property
    def preflight_checks_passed(self):
        """Get whether preflight checks passed."""
        return self._preflight_checks_passed

    @preflight_checks_passed.setter
    def preflight_checks_passed(self, value):
        """Set whether preflight checks passed and notify UI."""
        if self._preflight_checks_passed != value:
            self._preflight_checks_passed = value
            self.on_property_changed("preflight_checks_passed")

    @property
    def preflight_status_message(self):
        """Get preflight status message."""
        return self._preflight_status_message

    @preflight_status_message.setter
    def preflight_status_message(self, value):
        """Set preflight status message and notify UI."""
        if self._preflight_status_message != value:
            self._preflight_status_message = value
            self.on_property_changed("preflight_status_message")

    @property
    def preflight_status_background(self):
        """Get preflight status background color."""
        return self._preflight_status_background

    @preflight_status_background.setter
    def preflight_status_background(self, value):
        """Set preflight status background color and notify UI."""
        if self._preflight_status_background != value:
            self._preflight_status_background = value
            self.on_property_changed("preflight_status_background")

    @property
    def preflight_status_foreground(self):
        """Get preflight status foreground color."""
        return self._preflight_status_foreground

    @preflight_status_foreground.setter
    def preflight_status_foreground(self, value):
        """Set preflight status foreground color and notify UI."""
        if self._preflight_status_foreground != value:
            self._preflight_status_foreground = value
            self.on_property_changed("preflight_status_foreground")

    @property
    def preflight_last_run_timestamp(self):
        """Get preflight last run timestamp."""
        return self._preflight_last_run_timestamp

    @preflight_last_run_timestamp.setter
    def preflight_last_run_timestamp(self, value):
        """Set preflight last run timestamp and notify UI."""
        if self._preflight_last_run_timestamp != value:
            self._preflight_last_run_timestamp = value
            self.on_property_changed("preflight_last_run_timestamp")

    # ========================================
    # Properties - Running Control
    # ========================================

    @property
    def can_run_sync(self):
        """Get whether sync process can run."""
        return self._can_run_sync

    @can_run_sync.setter
    def can_run_sync(self, value):
        """Set whether sync process can run and notify UI."""
        if self._can_run_sync != value:
            self._can_run_sync = value
            self.on_property_changed("can_run_sync")

    @property
    def current_step(self):
        """Get current step."""
        return self._current_step

    @current_step.setter
    def current_step(self, value):
        """Set current step and notify UI."""
        if self._current_step != value:
            self._current_step = value
            self.on_property_changed("current_step")

    @property
    def progress_value(self):
        """Get progress value."""
        return self._progress_value

    @progress_value.setter
    def progress_value(self, value):
        """Set progress value and notify UI."""
        if self._progress_value != value:
            self._progress_value = value
            self.on_property_changed("progress_value")

    @property
    def progress_max(self):
        """Get progress maximum."""
        return self._progress_max

    @progress_max.setter
    def progress_max(self, value):
        """Set progress maximum and notify UI."""
        if self._progress_max != value:
            self._progress_max = value
            self.on_property_changed("progress_max")

    @property
    def progress_status(self):
        """Get progress status message."""
        return self._progress_status

    @progress_status.setter
    def progress_status(self, value):
        """Set progress status message and notify UI."""
        if self._progress_status != value:
            self._progress_status = value
            self.on_property_changed("progress_status")

    @property
    def completion_message(self):
        """Get completion message."""
        return self._completion_message

    @completion_message.setter
    def completion_message(self, value):
        """Set completion message and notify UI."""
        if self._completion_message != value:
            self._completion_message = value
            self.on_property_changed("completion_message")

    @property
    def completion_message_visibility(self):
        """Get completion message visibility."""
        return self._completion_message_visibility

    @completion_message_visibility.setter
    def completion_message_visibility(self, value):
        """Set completion message visibility and notify UI."""
        if self._completion_message_visibility != value:
            self._completion_message_visibility = value
            self.on_property_changed("completion_message_visibility")
