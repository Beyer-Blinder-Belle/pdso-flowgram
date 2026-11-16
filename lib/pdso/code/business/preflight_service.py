# -*- coding: utf-8 -*-
"""PDSO Preflight Service - validation checks that must pass before a sync.

This is the UI-free form of the preflight checks. The Run Scripts window and the
HTTP API use the same service.

Example Usage:
    service = PDSOPreflightService(doc)
    service.run_preflight_checks()
    if service.can_run_sync:
        ...
    for check in service.results():
        print(check["key"], check["level"], check["message"])
"""

# Python standard libraries
import re
from datetime import datetime

# 3rd Party Modules
from pyrevit import script, DB
from rpw.utils.dotnet import List

# Custom Modules
from ..data.extended_data_silo_collection import ExtendedDataSiloCollection
from ..data.extended_area_scheme import ExtendedAreaScheme
from ..data.extended_program_volume_instance_collection import (
    ExtendedProgramVolumeInstanceCollection,
)
from ..data.extended_level_collection import ExtendedLevelCollection
from .parameter_registry_service import (
    check_parameter_bindings,
    get_registry,
)
from .correlation_service import PDSOCorrelationService
from .extensible_storage_service import PDSOExtensibleStorageService
from .level_mapping_service import PDSOLevelMappingService


# (check key, status attribute, label), in the order that the checks run
PREFLIGHT_CHECKS = [
    ("config", "check_config_status", "Configuration"),
    ("level_mappings", "check_level_mappings_status", "Level mappings"),
    (
        "generic_model_params",
        "check_generic_model_parameters_status",
        "Generic Model parameters",
    ),
    ("area_schemes", "check_area_schemes_status", "Area schemes"),
    ("area_instances", "check_area_instances_status", "Area instances"),
    ("grouped_areas", "check_grouped_areas_status", "Grouped areas"),
    (
        "data_silo_family",
        "check_data_silo_family_loaded_status",
        "Data Silo family loaded",
    ),
    (
        "data_silo_instances",
        "check_data_silo_instances_status",
        "Data Silo instances",
    ),
    ("uid_uniqueness", "check_uid_uniqueness_status", "UID uniqueness"),
    ("uid_pattern", "check_uid_pattern_status", "UID pattern"),
    (
        "areas_multiple_silos",
        "check_areas_multiple_data_silos_status",
        "Areas with multiple Data Silos",
    ),
    (
        "areas_no_silos",
        "check_areas_no_data_silos_status",
        "Areas without Data Silos",
    ),
    (
        "silos_partial_containment",
        "check_data_silos_partial_containment_status",
        "Data Silos with partial area scheme coverage",
    ),
    (
        "silos_no_containment",
        "check_data_silos_no_containment_status",
        "Data Silos not contained by an area",
    ),
    (
        "data_silos_availability",
        "check_data_silos_availability_status",
        "Data Silos availability",
    ),
    (
        "program_volumes_availability",
        "check_program_volumes_availability_status",
        "Program Volumes availability",
    ),
    ("areas_availability", "check_areas_availability_status", "Areas availability"),
]

STATUS_ATTRIBUTE_BY_KEY = dict((key, attr) for key, attr, _ in PREFLIGHT_CHECKS)


def status_level(status_text):
    """Classify a check status string.

    Returns:
        str: "error", "warning", "ok" or "unknown"
    """
    text = status_text or ""
    if text.startswith(u"\u2717"):
        return "error"
    if text.startswith(u"\u26a0"):
        return "warning"
    if text.startswith(u"\u2713"):
        return "ok"
    return "unknown"


def uid_pattern_status(pattern, uids, max_listed=5):
    """Status text for the UID pattern check.

    Args:
        pattern (str): The configured UID pattern (a regular expression), or blank
        uids (list[str]): The Program UID of each Data Silo
        max_listed (int): How many failing UIDs to name in the message

    Returns:
        str: An ok or warning status. A blank pattern is ok.
    """
    if not pattern:
        return "✓ No UID pattern configured"
    try:
        # The whole UID must match (re.match alone only anchors the start)
        regex = re.compile(r"(?:{})\Z".format(pattern))
    except re.error as error:
        return "⚠️ UID pattern {} is not a valid regular expression: {}".format(
            pattern, error
        )
    failing = sorted(set(uid or "" for uid in uids if not regex.match(uid or "")))
    if not failing:
        return "✓ All Data Silo UIDs match the UID pattern"
    listed = ", ".join("'{}'".format(uid) for uid in failing[:max_listed])
    if len(failing) > max_listed:
        listed += ", ..."
    return "⚠️ {} UID{} don't match the pattern {}: {}".format(
        len(failing), "s" if len(failing) != 1 else "", pattern, listed
    )


class PDSOPreflightService(object):
    """Runs the preflight validation checks without a UI.

    Attributes:
        doc: Revit Document to check
        logger: PyRevit logger
        on_check (callable): Optional callback ``on_check(check_key, status_text)``,
            called when a check gets its result
        has_errors (bool): True if a check failed
        has_warnings (bool): True if a check gave a warning
        can_run_sync (bool): True if the sync can run (no errors)
        preflight_last_run_timestamp (str): Time of the last run (HH:MM:SS)
    """

    def __init__(self, doc, logger=None, on_check=None):
        """Initialize the service.

        Args:
            doc: Revit Document to check
            logger: PyRevit logger (optional)
            on_check (callable): Optional ``on_check(check_key, status_text)``
        """
        self.doc = doc
        self.logger = logger or script.get_logger()
        self.on_check = on_check

        not_checked = "[ - ] Not checked"
        for _, attribute, _ in PREFLIGHT_CHECKS:
            setattr(self, attribute, not_checked)

        self.has_errors = False
        self.has_warnings = False
        self.can_run_sync = False
        self.preflight_last_run_timestamp = "Not yet run"

    def _update_check_icon_and_color(self, check_key, status_text):
        """Report the result of one check to the listener."""
        if self.on_check:
            self.on_check(check_key, status_text)

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
        self.check_uid_pattern_status = running_status
        self.check_areas_multiple_data_silos_status = running_status
        self.check_areas_no_data_silos_status = running_status
        self.check_data_silos_partial_containment_status = running_status
        self.check_data_silos_no_containment_status = running_status
        self.check_data_silos_availability_status = running_status
        self.check_program_volumes_availability_status = running_status
        self.check_areas_availability_status = running_status

    def _update_preflight_status(self, has_errors, has_warnings):
        """Store the overall result of the run."""
        self.has_errors = has_errors
        self.has_warnings = has_warnings
        self.can_run_sync = not has_errors

    def results(self):
        """Get the result of each check.

        Returns:
            list[dict]: One dict per check with keys "key", "label", "level"
                ("ok", "warning", "error" or "unknown") and "message"
        """
        return [
            {
                "key": key,
                "label": label,
                "level": status_level(getattr(self, attribute)),
                "message": getattr(self, attribute),
            }
            for key, attribute, label in PREFLIGHT_CHECKS
        ]

    def summary(self):
        """Get the overall result of the run.

        Returns:
            dict: Counts of errors and warnings, "can_run_sync" and "checks"
        """
        checks = self.results()
        return {
            "can_run_sync": self.can_run_sync,
            "error_count": len([c for c in checks if c["level"] == "error"]),
            "warning_count": len([c for c in checks if c["level"] == "warning"]),
            "last_run": self.preflight_last_run_timestamp,
            "checks": checks,
        }

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
            storage_service = PDSOExtensibleStorageService(self.doc)
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
                    self.doc, config.data_silo_family_name
                )
            except Exception as e:
                self.logger.error(
                    "Error loading data silo collection: {}".format(str(e))
                )

        # Load level collection (independent of other loads)
        try:
            self.logger.debug("Loading level collection...")
            ext_level_collection = ExtendedLevelCollection(self.doc)
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
                self.doc, pv_prefix
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
                            self.doc, area_scheme_name, pdso_scheme_name
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
                storage_service = PDSOExtensibleStorageService(self.doc)
                level_mapping_service = PDSOLevelMappingService(
                    self.doc, storage_service
                )
                ext_data_silo_collection = ExtendedDataSiloCollection(
                    self.doc, config.data_silo_family_name
                )
                ext_level_collection = ExtendedLevelCollection(self.doc)

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
                registry = get_registry(self.doc, refresh=True)
                binding_check = check_parameter_bindings(self.doc, registry)

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

        # CHECK 9b: UID Pattern (a warning: the sync can still run)
        try:
            if config is None or ext_data_silo_collection is None:
                self.check_uid_pattern_status = (
                    "✗ Cannot check UIDs - data silos not loaded"
                )
                has_errors = True
            else:
                data_silos = ext_data_silo_collection.extended_data_silos
                self.check_uid_pattern_status = uid_pattern_status(
                    config.uid_pattern,
                    [
                        data_silo.parameters["PDSO - Program UID"].value
                        for data_silo in data_silos
                    ],
                )
                if status_level(self.check_uid_pattern_status) == "warning":
                    has_warnings = True

            self._update_check_icon_and_color(
                "uid_pattern", self.check_uid_pattern_status
            )
        except Exception as e:
            self.check_uid_pattern_status = "✗ Script Error"
            self.logger.error("UID pattern check error: {}".format(str(e)))
            has_errors = True
            self._update_check_icon_and_color(
                "uid_pattern", self.check_uid_pattern_status
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
            elif not self.doc.IsWorkshared:
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
            elif not self.doc.IsWorkshared:
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
            elif not self.doc.IsWorkshared:
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
                    self.doc, typed_list_of_areas
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

        # Update timestamp BEFORE the overall status so the message includes it
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
