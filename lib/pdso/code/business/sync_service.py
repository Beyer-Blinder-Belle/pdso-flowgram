# -*- coding: utf-8 -*-
"""PDSO Sync Service - the three-phase data synchronization, without a UI.

Phases:
    1. Area Harvest - copy area measurements from Areas to Data Silos
    2. Family Geometry - create or update Program Volume families
    3. Instance Updates - create, move and update Program Volume instances

The Run Scripts window and the HTTP API use the same service.

Example Usage:
    service = PDSOSyncService(doc, options={"program_volume_geometry_update": "No"})
    summary = service.run()
"""

# Python standard libraries
import traceback

# 3rd Party Modules
from pyrevit import script, DB
from rpw import db

# Custom Modules
from ..data.extended_data_silo_collection import ExtendedDataSiloCollection
from ..data.extended_area_scheme import ExtendedAreaScheme
from ..data.extended_program_volume_instance_collection import (
    ExtendedProgramVolumeInstanceCollection,
)
from ..data.extended_level_collection import ExtendedLevelCollection
from ..data.extended_program_volume_family_collection import (
    ExtendedProgramVolumeFamilyCollection,
)
from .correlation_service import PDSOCorrelationService
from .data_service import PDSODataService
from .family_manager_service import PDSOFamilyManagerService
from .workset_coordination_service import WorksetCoordinationService
from .extensible_storage_service import PDSOExtensibleStorageService
from .level_mapping_service import PDSOLevelMappingService
from .parameter_registry_service import get_registry


# Option name -> (default value, permitted values)
# "Fix", "Pin", "No" and RECOMPUTE_ALL_GEOMETRY change what the sync does. The
# other values are the remaining choices of the Sync Data window and mean "do not
# do this step" (or, for the geometry update, "use the geometry cache").
RECOMPUTE_ALL_GEOMETRY = "Yes - Recompute All Geometry (slow)"

SYNC_OPTIONS = {
    "area_harvest_workset": ("Fix", ["Fix", "Warn (if configured)", "Do Nothing"]),
    "area_harvest_pinning": ("Pin", ["Pin", "Maintain", "Unpin"]),
    "program_volume_geometry_update": (
        "Yes - Use Geometry Cache",
        [
            "Yes - Use Geometry Cache",
            RECOMPUTE_ALL_GEOMETRY,
            "No",
        ],
    ),
    "program_volume_geometry_workset": (
        "Fix",
        ["Fix", "Warn (if configured)", "Do Nothing"],
    ),
    "program_volume_geometry_pinning": ("Pin", ["Pin", "Maintain", "Unpin"]),
    "program_volume_data_update": ("Yes", ["Yes", "No"]),
}


def _set_pinned(elements, pinned):
    """Pin or unpin elements one by one.

    An element that cannot be changed (unplaced, redundant, in a group) is
    skipped, so it does not stop the others.

    Returns:
        tuple: (number changed, number skipped)
    """
    changed = 0
    skipped = 0
    for element in elements:
        if element is None:
            continue
        try:
            element.Pinned = pinned
            changed += 1
        except Exception:
            skipped += 1
    return changed, skipped


class PDSOSyncError(Exception):
    """A condition that stops a sync phase."""


class PDSOSyncService(object):
    """Runs the data synchronization without a UI.

    Attributes:
        doc: Revit Document to sync
        logger: PyRevit logger
        on_progress (callable): Optional callback ``on_progress(service)``, called
            when the progress attributes change
        current_step (str): Name of the step in progress
        progress_status (str): Detail of the step in progress
        progress_value (int): Progress of the current step (0-100)
        completion_message (str): Message to show when the sync is complete
        errors (list[str]): Errors that stopped a phase
        phases (list[str]): State of each phase: "pending", "complete", "skipped"
            or "error"
    """

    def __init__(self, doc, options=None, logger=None, on_progress=None):
        """Initialize the service.

        Args:
            doc: Revit Document to sync
            options (dict): Values for the keys of SYNC_OPTIONS (optional)
            logger: PyRevit logger (optional)
            on_progress (callable): Optional ``on_progress(service)``
        """
        self.doc = doc
        self.logger = logger or script.get_logger()
        self.on_progress = on_progress

        options = options or {}
        for name in options:
            if name not in SYNC_OPTIONS:
                raise ValueError(
                    "Unknown sync option '{}'. Permitted: {}".format(
                        name, ", ".join(sorted(SYNC_OPTIONS))
                    )
                )
        for name, (default, _) in SYNC_OPTIONS.items():
            setattr(self, "_{}_option".format(name), options.get(name) or default)

        self.current_step = "Waiting to start..."
        self.progress_value = 0
        self.progress_max = 100
        self.progress_status = ""
        self.completion_message = ""
        self.completion_message_visible = False
        self.validation_warning_message = None

        self.completed = False
        self.errors = []
        self.phases = ["pending", "pending", "pending"]

    def _notify(self):
        """Tell the listener that the progress attributes changed."""
        if self.on_progress:
            self.on_progress(self)

    def options(self):
        """Get the options in use, keyed as in SYNC_OPTIONS."""
        return dict(
            (name, getattr(self, "_{}_option".format(name))) for name in SYNC_OPTIONS
        )

    def summary(self):
        """Get the result of the run.

        Returns:
            dict: "completed", "phases", "errors", "options", "message" and
                "validation_warning"
        """
        names = ["area_harvest", "family_geometry", "instance_updates"]
        return {
            "completed": self.completed,
            "success": self.completed and not self.errors,
            "phases": dict(zip(names, self.phases)),
            "errors": list(self.errors),
            "options": self.options(),
            "message": self.completion_message or self.current_step,
            "validation_warning": self.validation_warning_message,
        }

    def run(self):
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
            self.completion_message_visible = False

            # Re-read the tracked parameter list (it may have changed since the last run)
            get_registry(self.doc, refresh=True)

            # Instantiate services
            self.logger.info("Instantiating services...")
            storage_service = PDSOExtensibleStorageService(self.doc)
            correlation_service = PDSOCorrelationService()
            data_service = PDSODataService(self.doc)
            family_manager = PDSOFamilyManagerService(self.doc)
            level_mapping = PDSOLevelMappingService(self.doc, storage_service)
            workset_coordinator = WorksetCoordinationService(self.doc)

            # Load configuration from extensible storage
            self.logger.info("Loading configuration from extensible storage...")
            config = storage_service.get_document_config()
            if config is None:
                self.logger.error("Failed to load PDSO configuration from document")
                self.current_step = "ERROR: Configuration not found"
                self.progress_status = "Failed to load configuration"
                self.errors.append("Configuration not found in the document")
                self._notify()
                return self.summary()

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
                self.phases[0] = "complete"
                self.logger.info("✓ Phase 1 complete")
            except Exception as e:
                self.logger.error("Phase 1 error: {}".format(str(e)))
                self.errors.append("Phase 1: {}".format(str(e)))
                self.phases[0] = "error"
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
                if self.phases[1] != "skipped":
                    self.phases[1] = "complete"
                self.logger.info("✓ Phase 2 complete")
            except Exception as e:
                self.logger.error("Phase 2 error: {}".format(str(e)))
                self.errors.append("Phase 2: {}".format(str(e)))
                self.phases[1] = "error"
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
                if self.phases[2] != "skipped":
                    self.phases[2] = "complete"
                self.logger.info("✓ Phase 3 complete")
            except Exception as e:
                self.logger.error("Phase 3 error: {}".format(str(e)))
                self.errors.append("Phase 3: {}".format(str(e)))
                self.phases[2] = "error"
                self.logger.error(traceback.format_exc())

            # Mark complete
            self.completed = True
            self.current_step = "Complete!"
            self.progress_status = "Sync process completed"
            self.completion_message = "✓ Data synchronization completed!"
            self.completion_message_visible = True

            self.logger.info("\n" + "=" * 80)
            self.logger.info("SYNC PROCESS COMPLETE")
            self.logger.info("=" * 80)

            # Add validation warnings to completion message if present
            if (
                self.validation_warning_message
            ):
                warning_summary = "\n\nWARNING: Some data silos had level mapping errors and were skipped.\nCheck console for details."
                if self.completion_message:
                    self.completion_message += warning_summary
                else:
                    self.completion_message = (
                        "Sync complete with warnings." + warning_summary
                    )
                    self.completion_message_visible = True

        except Exception as e:
            self.logger.error("Critical error in sync process: {}".format(str(e)))
            self.errors.append("Critical: {}".format(str(e)))
            self.logger.error(traceback.format_exc())
            self.current_step = "ERROR: {}".format(str(e))
            self.progress_status = "Sync process failed"

        self._notify()
        return self.summary()

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
            self.doc, config.data_silo_family_name
        )
        ext_level_collection = ExtendedLevelCollection(self.doc)

        self.logger.info(
            "Found {} PDSOs".format(len(ext_data_silo_collection.extended_data_silos))
        )

        if not ext_data_silo_collection.extended_data_silos:
            self.logger.warning("No PDSOs found - skipping area harvest")
            raise PDSOSyncError("No Data Silos found in the model")

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
                    self.doc, area_scheme_name, pdso_scheme_name
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
            raise PDSOSyncError("No configured area schemes found in the model")

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
            self._notify()
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
        self._notify()

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
        self._notify()

        with db.Transaction("PDSO - Harvest Area Data", doc=self.doc):
            for ext_data_silo in ext_data_silo_collection.extended_data_silos:
                ext_data_silo.commit_pending_changes()

            self.logger.info(
                "Committed area data to {} PDSOs".format(
                    len(ext_data_silo_collection.extended_data_silos)
                )
            )

        # Apply workset enforcement and pinning together in single transaction
        # "Maintain" leaves each element pinned or unpinned as it is
        if self._area_harvest_workset_option == "Fix" or (
            self._area_harvest_pinning_option in ("Pin", "Unpin")
        ):
            self.current_step = "Enforcing worksets and pinning elements"
            self.progress_status = "PDSOs and Areas"
            self.progress_value = 75
            self._notify()

            try:
                with db.Transaction("PDSO - Worksets and Pinning", doc=self.doc):
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
                    if self._area_harvest_pinning_option in ("Pin", "Unpin"):
                        pinned = self._area_harvest_pinning_option == "Pin"
                        verb = "Pinned" if pinned else "Unpinned"
                        self.logger.info("{} PDSOs and Areas".format(verb))

                        silo_count, silo_skipped = _set_pinned(
                            [
                                ds.rpw_element
                                for ds in ext_data_silo_collection.extended_data_silos
                            ],
                            pinned,
                        )
                        self.logger.info(
                            "{} {} PDSOs ({} skipped)".format(
                                verb, silo_count, silo_skipped
                            )
                        )

                        # Unplaced or redundant Areas are skipped
                        area_count, area_skipped = _set_pinned(
                            [
                                ext_area.rpw_area
                                for area_scheme in area_scheme_objects
                                for ext_area in area_scheme.extended_areas
                            ],
                            pinned,
                        )
                        self.logger.info(
                            "{} {} Areas ({} skipped)".format(
                                verb, area_count, area_skipped
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

        self._notify()

    def _link_existing_families(self, config, ext_data_silo_collection):
        """Link each Data Silo to its Program Volume family, without changing it.

        Used when the geometry update is off. Phase 3 then updates the data of
        the instances of families that are in the model. Only a family with
        the expected name (prefix + UID) is linked: a family that phase 2
        would rename or build is not, and that Data Silo is skipped with a
        warning in phase 3.
        """
        prefix = config.program_volume_family_name_prefix
        families = ExtendedProgramVolumeFamilyCollection(self.doc, prefix)
        by_name = {}
        for family in families.extended_program_volume_families:
            if family.rpw_element is not None:
                by_name[family.rpw_element.Name] = family
        linked = 0
        for ext_data_silo in ext_data_silo_collection.extended_data_silos:
            uid = ext_data_silo.parameters["PDSO - Program UID"].value
            if not uid:
                continue
            family = by_name.get("{}{}".format(prefix, uid))
            if family is None or family.parent_data_silo is not None:
                continue
            ext_data_silo.related_program_volume_family = family
            family.parent_data_silo = ext_data_silo
            linked += 1
        self.logger.info(
            "Linked {} PDSOs to their existing Program Volume families".format(linked)
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
            self.phases[1] = "skipped"
            # Phase 3 still needs each Data Silo's existing family
            self._link_existing_families(config, ext_data_silo_collection)
            return

        # Get families collection (but use existing data silo collection from Phase 1)
        self.logger.info(
            "Searching for Program Volume families with prefix: '{}'".format(
                config.program_volume_family_name_prefix
            )
        )

        ext_program_volume_families_collection = ExtendedProgramVolumeFamilyCollection(
            self.doc, config.program_volume_family_name_prefix
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
        self._notify()

        correlation_service.correlate_data_silos_to_program_volume_families(
            ext_data_silo_collection, ext_program_volume_families_collection
        )
        self.logger.info("Correlation complete")

        # Create/update program volume families
        self.current_step = "Updating Program Volume family geometry"
        self.progress_status = "Starting geometry updates..."
        self.progress_value = 0
        self._notify()

        force_recompute = (
            self._program_volume_geometry_update_option == RECOMPUTE_ALL_GEOMETRY
        )
        if force_recompute:
            self.logger.info("Recomputing all Program Volume geometry (cache ignored)")

        new_families = family_manager.create_or_update_program_volume_families(
            ext_data_silo_collection,
            ext_program_volume_families_collection,
            progress_callback=self._update_family_geometry_progress,
            extrusion_height=getattr(config, "program_volume_extrusion_height", None),
            force_recompute=force_recompute,
        )

        if new_families:
            self.logger.info("Created {} new families".format(len(new_families)))

        # Commit family changes
        self.current_step = "Committing Program Volume family updates"
        self.progress_status = "Writing extensible storage data"
        self.progress_value = 100
        self._notify()

        with db.Transaction("PDSO - Update Family Geometry", doc=self.doc):
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
            self._notify()

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
                    workset_coordinator = WorksetCoordinationService(self.doc)
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

        # Apply pinning if configured. "Maintain" leaves each family as it is.
        if self._program_volume_geometry_pinning_option in ("Pin", "Unpin"):
            pinned = self._program_volume_geometry_pinning_option == "Pin"
            verb = "Pinned" if pinned else "Unpinned"
            self.current_step = "{} Program Volume families".format(
                "Pinning" if pinned else "Unpinning"
            )
            self.progress_status = "{} families".format(
                len(
                    ext_program_volume_families_collection.extended_program_volume_families
                )
            )
            self.progress_value = 100
            self._notify()

            try:
                with db.Transaction("PDSO - Pin Families", doc=self.doc):
                    count, skipped = _set_pinned(
                        [
                            pvf.rpw_element
                            for pvf in ext_program_volume_families_collection.extended_program_volume_families
                        ],
                        pinned,
                    )
                    self.logger.info(
                        "{} {} families ({} skipped)".format(verb, count, skipped)
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
            self.phases[2] = "skipped"
            return

        # Get instance collection (use existing data silo and level collections from Phase 1)
        self.logger.info(
            "Searching for Program Volume instances with prefix: '{}'".format(
                config.program_volume_family_name_prefix
            )
        )

        ext_program_volume_instance_collection = (
            ExtendedProgramVolumeInstanceCollection(
                self.doc, config.program_volume_family_name_prefix
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
        storage_service_phase3 = PDSOExtensibleStorageService(self.doc)
        level_mapping_service = PDSOLevelMappingService(
            self.doc, storage_service_phase3
        )

        # Correlate data silos to program volume instances (with level mapping validation)
        self.current_step = "Correlating PDSOs to Program Volume instances"
        self.progress_status = "Validating level mappings and spatial relationships"
        self.progress_value = 0
        self._notify()

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
                self.validation_warning_message = ext_program_volume_instance_collection.validation_result.get_summary_message()
            self.logger.warning(
                "Continuing with sync. Data Silos with stacking errors get "
                "Program Volumes on the levels that resolved; their instances "
                "on other levels are kept, not deleted, until the stacking is fixed."
            )
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
        self._notify()

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
        self._notify()

        # Remember which instances were pinned, for the "Maintain" option
        self.logger.info("Unpinning Program Volume instances for updates")
        pinned_before = []
        with db.Transaction("PDSO - Unpin Instances", doc=self.doc):
            for ext_instance in (
                ext_program_volume_instance_collection.extended_program_volume_instances
            ):
                if ext_instance.rpw_element is None:
                    continue  # Skip instances that don't exist yet
                try:
                    if ext_instance.rpw_element.Pinned:
                        pinned_before.append(ext_instance)
                    ext_instance.rpw_element.Pinned = False
                except Exception:
                    pass  # An instance that cannot be unpinned is left as it is

        # Commit changes (creates new instances, updates locations, and deletes orphans)
        self.current_step = "Committing Program Volume instance updates"
        self.progress_status = "Creating, moving, and updating instances"
        self.progress_value = 75
        self._notify()

        with db.Transaction("PDSO - Update Program Volume Instances", doc=self.doc):
            # Commit all pending changes (location updates, instance creation, parameter updates)
            ext_program_volume_instance_collection.commit_pending_changes()

            self.logger.info("Committed instance updates")

        # Apply workset enforcement if configured
        if self._program_volume_geometry_workset_option == "Fix":
            self.current_step = "Enforcing worksets for Program Volume instances"
            self.progress_status = "Assigning to configured workset"
            self.progress_value = 90
            self._notify()

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

        # Apply pinning. The instances were unpinned for the update above.
        # "Pin" pins every instance, "Maintain" pins again the instances that
        # were pinned before, "Unpin" leaves them unpinned.
        pinning = self._program_volume_geometry_pinning_option
        if pinning == "Pin":
            to_pin = [
                pvi
                for pvi in ext_program_volume_instance_collection.extended_program_volume_instances
                if not pvi.should_be_deleted
            ]
        elif pinning == "Maintain":
            to_pin = [pvi for pvi in pinned_before if not pvi.should_be_deleted]
        else:
            to_pin = []
        if to_pin:
            self.current_step = "Pinning Program Volume instances"
            self.progress_status = "Pinning {} instances".format(len(to_pin))
            self.progress_value = 100
            self._notify()

            self.logger.info("Pinning Program Volume instances")

            try:
                with db.Transaction("PDSO - Pin Instances", doc=self.doc):
                    count, skipped = _set_pinned(
                        [pvi.rpw_element for pvi in to_pin], True
                    )
                    self.logger.info(
                        "Pinned {} instances ({} skipped)".format(count, skipped)
                    )
            except Exception as e:
                self.logger.error("Pinning failed: {}".format(str(e)))
