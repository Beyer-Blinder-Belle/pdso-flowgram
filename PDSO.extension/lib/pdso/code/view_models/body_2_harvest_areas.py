# -*- coding: utf-8 -*-
"""Data Harvesting & Sync View Model.

This view model coordinates the UI for running validation checks and
synchronizing program data from areas to data silos and program volumes.
"""

# Python standard libraries
# None

# System/ Windows Modules
import System
from System.Windows import Visibility
from System.Windows.Threading import Dispatcher, DispatcherPriority
from System.Windows.Media import SolidColorBrush, ColorConverter

# 3rd Party Modules
from pyrevit import revit

# Custom Modules
from base_view_model import PDSO_Base_View_Model
from ..business.preflight_service import (
    PDSOPreflightService,
    STATUS_ATTRIBUTE_BY_KEY,
)
from ..business.sync_service import PDSOSyncService


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

        The checks run in PDSOPreflightService. This method copies each result
        to the UI status properties.
        """
        # Reset all check statuses to "running"
        self._reset_check_statuses()

        service = PDSOPreflightService(
            revit.doc, logger=self.logger, on_check=self._on_preflight_check
        )
        service.run_preflight_checks()

        # Update timestamp BEFORE updating overall status so it's included in the message
        self.preflight_last_run_timestamp = service.preflight_last_run_timestamp

        # Update overall preflight status
        self._update_preflight_status(service.has_errors, service.has_warnings)

    def _on_preflight_check(self, check_key, status_text):
        """Copy the result of one check to its status property and icon.

        Args:
            check_key (str): Key of the check (see PREFLIGHT_CHECKS)
            status_text (str): Status message of the check
        """
        setattr(self, STATUS_ATTRIBUTE_BY_KEY[check_key], status_text)
        self._update_check_icon_and_color(check_key, status_text)


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

        The sync runs in PDSOSyncService. This method gives it the options from
        the ComboBoxes and copies its progress to the UI properties.
        """
        self.completion_message_visibility = Visibility.Collapsed
        self._validation_warning_message = None

        service = PDSOSyncService(
            revit.doc,
            options={
                "area_harvest_workset": self._area_harvest_workset_option,
                "area_harvest_pinning": self._area_harvest_pinning_option,
                "program_volume_geometry_update": self._program_volume_geometry_update_option,
                "program_volume_geometry_workset": self._program_volume_geometry_workset_option,
                "program_volume_geometry_pinning": self._program_volume_geometry_pinning_option,
                "program_volume_data_update": self._program_volume_data_update_option,
            },
            logger=self.logger,
            on_progress=self._on_sync_progress,
        )
        service.run()
        self._validation_warning_message = service.validation_warning_message
        self._on_sync_progress(service)

    def _on_sync_progress(self, service):
        """Copy the progress of the sync service to the UI properties.

        Args:
            service (PDSOSyncService): The service that runs the sync
        """
        self.current_step = service.current_step
        self.progress_status = service.progress_status
        self.progress_value = service.progress_value
        self.progress_max = service.progress_max
        self.completion_message = service.completion_message
        self.completion_message_visibility = (
            Visibility.Visible
            if service.completion_message_visible
            else Visibility.Collapsed
        )

        # Force WPF to process pending UI updates so progress bar actually displays
        Dispatcher.CurrentDispatcher.Invoke(
            System.Action(lambda: None), DispatcherPriority.Background
        )


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
