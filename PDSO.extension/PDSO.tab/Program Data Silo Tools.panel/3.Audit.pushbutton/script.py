# -*- coding: utf-8 -*-
"""PDSO Audit - Detailed Preflight Report.

Generates comprehensive validation report with element-level details including:
- Summary statistics with overall status
- Configuration validation (schema, family, UID pattern, area schemes)
- Model element counts (data silos, areas by scheme, program volumes)
- Correlation analysis table with linkified element IDs
- Level mapping validation details
- Element issues (area boundaries, grouped areas, orphaned elements, worksharing)
- Geometry tracking status
"""

# Python standard libraries
import traceback

# 3rd Party Modules
from pyrevit import script, revit, DB
from collections import defaultdict
from rpw.utils.dotnet import List as DotNetList

# Custom Modules
from pdso.code.business.area_boundary_service import (
    STATUS_INVALID_PROFILE,
    STATUS_NOT_ENCLOSED,
    STATUS_NOT_PLACED,
    STATUS_OK,
    STATUS_REPAIRED,
    STATUS_UNCLOSABLE,
    check_area_boundary,
)
from pdso.code.business.extensible_storage_service import PDSOExtensibleStorageService
from pdso.code.business.correlation_service import PDSOCorrelationService
from pdso.code.business.level_mapping_service import PDSOLevelMappingService
from pdso.code.data.extended_data_silo_collection import ExtendedDataSiloCollection
from pdso.code.data.extended_area_scheme import ExtendedAreaScheme
from pdso.code.data.extended_program_volume_instance_collection import (
    ExtendedProgramVolumeInstanceCollection,
)
from pdso.code.data.extended_level_collection import ExtendedLevelCollection
from pdso.code.business.parameter_registry_service import (
    check_parameter_bindings,
    get_registry,
)
from pdso.code.data.extended_shared_parameter import parameter_type_label
from pdso.code.data.extended_program_volume_family_collection import (
    ExtendedProgramVolumeFamilyCollection,
)


# Area boundary problems, most severe first: (icon, label, how to fix)
AREA_BOUNDARY_ISSUES = [
    (
        STATUS_NOT_PLACED,
        "❌",
        "Not placed",
        "The area isn't placed in any area plan. Place it or delete it.",
    ),
    (
        STATUS_NOT_ENCLOSED,
        "❌",
        "Not enclosed or redundant",
        "Revit reports 0 SF: the area boundary lines don't fully enclose it, or it"
        " shares an enclosure with another area. Close the boundary or delete the"
        " duplicate area.",
    ),
    (
        STATUS_UNCLOSABLE,
        "❌",
        "Boundary can't be closed",
        "The boundary has gaps or overlaps the sync can't repair. Trim and join the"
        " area boundary lines so they meet end to end.",
    ),
    (
        STATUS_INVALID_PROFILE,
        "❌",
        "Self-intersecting boundary",
        "The boundary closes but crosses or overlaps itself, so no solid can be"
        " built. Remove overlapping or crossing area boundary lines.",
    ),
    (
        STATUS_REPAIRED,
        "⚠️",
        "Gaps or out-of-order boundary lines",
        "The sync repairs these automatically, so they don't block it, but the"
        " Program Volume may not match the area exactly. Clean up the boundary"
        " lines where they meet.",
    ),
]


def check_area_boundaries(area_scheme_objects, logger):
    """Check every area in the configured schemes for boundary problems.

    Args:
        area_scheme_objects (list[ExtendedAreaScheme]): Configured area schemes.
        logger: pyRevit logger.
    Returns:
        tuple[list[dict], int, int]: The areas with problems, the number of areas
            checked, and the number that couldn't be checked.
    """
    boundary_issues = []
    checked_count = 0
    failed_count = 0

    for scheme in area_scheme_objects:
        for ext_area in scheme.extended_areas:
            area = ext_area.rpw_area.unwrap()
            checked_count += 1

            try:
                check = check_area_boundary(area)
            except Exception as check_error:
                failed_count += 1
                logger.warning(
                    "  Boundary check failed for Area {}: {}".format(
                        ext_area.Id, str(check_error)
                    )
                )
                continue

            if check.status == STATUS_OK:
                continue

            try:
                number = area.Number or ""
                name = area.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString()
                label = " - ".join(part for part in (number, name or "") if part)
            except Exception:
                label = ""

            boundary_issues.append(
                {
                    "id": ext_area.Id,
                    "scheme": scheme.area_scheme_name,
                    "level": ext_area.Level.Name if ext_area.Level else "N/A",
                    "label": label,
                    "check": check,
                }
            )

    return boundary_issues, checked_count, failed_count


def print_area_boundary_issues(
    output, boundary_issues, checked_count, failed_count, geometry_scheme_name
):
    """Print the Area Boundaries subsection of the report."""
    output.print_md("### Area Boundaries")
    output.print_md(
        "Program Volume geometry is built from each Data Silo's **{}** area. Errors"
        " in that scheme mean the sync skips the Data Silo's Program Volume; errors"
        " in any scheme mean the area's value can't be trusted.".format(
            geometry_scheme_name
        )
    )
    output.print_md("")

    if failed_count:
        output.print_md(
            "ℹ️ {} areas couldn't be checked (see the log)".format(failed_count)
        )

    if not boundary_issues:
        output.print_md(
            "✓ All {} areas have closed, clean boundaries".format(
                checked_count - failed_count
            )
        )
        return

    error_count = sum(1 for issue in boundary_issues if issue["check"].is_error)
    repaired_count = len(boundary_issues) - error_count
    if error_count:
        output.print_md(
            "❌ **{} areas have boundaries the sync can't use**".format(error_count)
        )
    if repaired_count:
        output.print_md(
            "⚠️ **{} areas have boundary gaps or out-of-order lines**".format(
                repaired_count
            )
        )

    issue_ids = [issue["id"] for issue in boundary_issues]
    output.print_md(
        output.linkify(
            DotNetList[DB.ElementId](issue_ids),
            title="Select All {} Areas".format(len(issue_ids)),
        )
    )
    output.print_md("")

    status_order = [status for status, _, _, _ in AREA_BOUNDARY_ISSUES]
    status_labels = {
        status: "{} {}".format(icon, label)
        for status, icon, label, _ in AREA_BOUNDARY_ISSUES
    }
    sorted_issues = sorted(
        boundary_issues,
        key=lambda issue: (
            status_order.index(issue["check"].status),
            issue["scheme"],
            issue["level"],
        ),
    )

    table_data = []
    for issue in sorted_issues:
        check = issue["check"]
        if check.status == STATUS_REPAIRED:
            details = []
            if check.gaps:
                details.append(
                    "{} gap{} (largest {:.2f} in)".format(
                        len(check.gaps),
                        "s" if len(check.gaps) != 1 else "",
                        max(check.gaps) * 12,
                    )
                )
            if check.reordered:
                details.append("lines out of order")
            detail = "; ".join(details)
        else:
            # Revit messages can run to several lines ("Parameter name: ...")
            detail = check.detail.splitlines()[0] if check.detail else ""

        table_data.append(
            [
                status_labels[check.status],
                issue["scheme"],
                issue["level"],
                output.linkify(issue["id"]),
                issue["label"],
                detail,
            ]
        )

    output.print_table(
        table_data=table_data,
        title="Areas with Boundary Problems",
        columns=["Issue", "Area Scheme", "Level", "Area ID", "Area", "Details"],
    )
    output.print_md("")

    # How to fix each kind of problem found
    found_statuses = set(issue["check"].status for issue in boundary_issues)
    output.print_md("**How to fix:**")
    for status, icon, label, how_to_fix in AREA_BOUNDARY_ISSUES:
        if status in found_statuses:
            output.print_md("- {} **{}:** {}".format(icon, label, how_to_fix))


def generate_detailed_report(output, logger):
    """Generate comprehensive detailed preflight validation report."""
    output.print_md("# PDSO Preflight Audit - Detailed Report")
    output.print_md("=" * 80)
    output.print_md("\n")

    # Initialize services and load collections (read-only operations)
    try:
        storage_service = PDSOExtensibleStorageService(revit.doc)
        config = storage_service.get_document_config()

        if config is None:
            output.print_md("## ❌ ERROR: Configuration Not Found")
            output.print_md(
                "Cannot generate report - PDSO configuration not loaded in document."
            )
            output.print_md(
                "\nPlease run the **1. Config** button first to set up PDSO configuration."
            )
            return

        # Load all collections for analysis (these are read-only operations)
        logger.debug("Loading data silo collection...")
        ext_data_silo_collection = ExtendedDataSiloCollection(
            revit.doc, config.data_silo_family_name
        )

        logger.debug("Loading level collection...")
        ext_level_collection = ExtendedLevelCollection(revit.doc)

        logger.debug("Loading program volume instance collection...")
        ext_pv_instance_collection = ExtendedProgramVolumeInstanceCollection(
            revit.doc, config.program_volume_family_name_prefix
        )

        logger.debug("Loading program volume family collection...")
        ext_pv_family_collection = ExtendedProgramVolumeFamilyCollection(
            revit.doc, config.program_volume_family_name_prefix
        )

        # Get configured area schemes
        area_scheme_mappings_dict = (
            dict(config.area_scheme_mappings) if config.area_scheme_mappings else {}
        )
        configured_schemes = list(area_scheme_mappings_dict.keys())

        # Load area schemes (read-only)
        logger.debug("Loading area schemes...")
        area_scheme_objects = []
        for pdso_scheme_name, area_scheme_name in area_scheme_mappings_dict.items():
            try:
                ext_area_scheme = ExtendedAreaScheme(
                    revit.doc, area_scheme_name, pdso_scheme_name
                )
                if ext_area_scheme.area_scheme_exists_in_model:
                    area_scheme_objects.append(ext_area_scheme)
                    logger.debug("  Loaded scheme: {}".format(area_scheme_name))
            except Exception as scheme_error:
                logger.warning(
                    "  Failed to load scheme {}: {}".format(
                        area_scheme_name, str(scheme_error)
                    )
                )

        # Perform correlations for detailed analysis (in-memory only, no transactions)
        logger.debug("Performing correlations...")
        correlation_service = PDSOCorrelationService()
        for area_scheme in area_scheme_objects:
            try:
                correlation_service.correlate_data_silos_to_areas(
                    ext_level_collection, ext_data_silo_collection, area_scheme
                )
            except Exception as corr_error:
                logger.warning(
                    "  Correlation failed for {}: {}".format(
                        area_scheme.area_scheme_name, str(corr_error)
                    )
                )

        # Correlate to program volumes (in-memory only)
        try:
            correlation_service.correlate_data_silos_to_program_volume_families(
                ext_data_silo_collection, ext_pv_family_collection
            )
        except Exception as pv_corr_error:
            logger.warning(
                "  PV family correlation failed: {}".format(str(pv_corr_error))
            )

        # Validate level mappings (read-only)
        level_mapping_service = PDSOLevelMappingService(revit.doc, storage_service)
        try:
            correlation_service.correlate_data_silos_to_program_volume_instances(
                ext_level_collection,
                ext_data_silo_collection,
                ext_pv_instance_collection,
                level_mapping_service,
            )
        except Exception as level_corr_error:
            logger.warning(
                "  PV instance correlation failed: {}".format(str(level_corr_error))
            )

        logger.debug("Collections loaded successfully")

        # Check area boundaries (read-only - builds geometry in memory only)
        logger.debug("Checking area boundaries...")
        (
            boundary_issues,
            boundary_checked_count,
            boundary_failed_count,
        ) = check_area_boundaries(area_scheme_objects, logger)

    except Exception as e:
        output.print_md("## ❌ ERROR: Failed to Load Collections")
        output.print_md("Error: {}".format(str(e)))
        logger.error("Collection loading error: {}".format(str(e)))
        logger.error(traceback.format_exc())
        return

    # === SECTION 1: SUMMARY STATISTICS ===
    output.print_md("---")
    output.print_md("")
    output.print_md("## 1. Summary Statistics")
    total_checks = 14
    passed_count = 0
    warning_count = 0
    error_count = 0

    # Simple pass/fail logic based on basic checks
    if ext_data_silo_collection.family_exists_in_model:
        passed_count += 1
    else:
        error_count += 1

    if ext_data_silo_collection.extended_data_silos:
        passed_count += 1
    else:
        error_count += 1

    if ext_data_silo_collection.are_data_silo_uids_unique():
        passed_count += 1
    else:
        error_count += 1

    # Area checks
    if area_scheme_objects:
        passed_count += len(configured_schemes)
    else:
        warning_count += len(configured_schemes)

    if boundary_issues:
        warning_count += 1

    # Calculate remaining
    passed_count = min(passed_count, total_checks)
    remaining = total_checks - passed_count - warning_count - error_count
    if remaining > 0:
        passed_count += remaining

    overall_status = "✓ PASSED" if error_count == 0 else "❌ FAILED"
    if error_count == 0 and warning_count > 0:
        overall_status = "⚠️ PASSED WITH WARNINGS"

    output.print_md("- **Total Checks:** {}".format(total_checks))
    output.print_md("- **Passed:** {}".format(passed_count))
    output.print_md("- **Warnings:** {}".format(warning_count))
    output.print_md("- **Errors:** {}".format(error_count))
    output.print_md("- **Overall Status:** {}".format(overall_status))
    output.print_md("\n")

    # === SECTION 2: CONFIGURATION ===
    output.print_md("---")
    output.print_md("")
    output.print_md("## 2. Configuration Validation")
    output.print_md("Configuration loaded from extensible storage.")
    output.print_md("")
    output.print_md("- **Family Name:** {}".format(config.data_silo_family_name))
    output.print_md(
        "- **UID Pattern:** {}".format(
            config.uid_pattern if config.uid_pattern else "Not configured"
        )
    )
    output.print_md("- **Configured Area Schemes:**")
    for pdso_scheme, area_scheme in area_scheme_mappings_dict.items():
        output.print_md("  - {} → {}".format(pdso_scheme, area_scheme))
    output.print_md("")

    # Check Generic Model Parameters
    output.print_md("### Generic Model Parameters")

    registry = get_registry(revit.doc, refresh=True)
    binding_check = check_parameter_bindings(revit.doc, registry)

    if not binding_check["configured"]:
        output.print_md(
            "❌ Parameters haven't been set up for this model. Run the "
            "**Parameter Manager** before syncing."
        )
    else:
        if binding_check["missing_core"]:
            output.print_md("❌ Required parameters that aren't tracked:")
            for name in binding_check["missing_core"]:
                output.print_md("  - {}".format(name))
            output.print_md("")

        if binding_check["missing"]:
            output.print_md(
                "❌ {} of {} tracked parameters are missing or set up incorrectly:".format(
                    len(binding_check["missing"]), len(registry.parameters)
                )
            )
            for name in binding_check["missing"]:
                output.print_md(
                    "  - {} ({})".format(name, binding_check["problems"][name])
                )
            output.print_md("")
            output.print_md("Run the **Parameter Manager** to fix these.")
            output.print_md("")

        output.print_md(
            "✓ {} tracked parameters found on Generic Models:".format(
                len(binding_check["found"])
            )
        )
        for name in binding_check["found"]:
            parameter = registry.get(name)
            output.print_md(
                "  - {} ({}, {})".format(
                    name, parameter_type_label(parameter.parameter_type), parameter.role
                )
            )

    output.print_md("\n")

    # === SECTION 3: MODEL ELEMENTS ===
    output.print_md("---")
    output.print_md("")
    output.print_md("## 3. Model Elements")
    output.print_md("Element counts and validation status.")
    output.print_md("")

    output.print_md("### Areas")
    if area_scheme_objects:
        total_areas = sum(len(scheme.extended_areas) for scheme in area_scheme_objects)
        output.print_md("✓ Total areas found: {}".format(total_areas))
        for scheme in area_scheme_objects:
            output.print_md(
                "  - {}: {} areas".format(
                    scheme.area_scheme_name, len(scheme.extended_areas)
                )
            )

        # Check for grouped areas
        all_grouped_areas = []
        for scheme in area_scheme_objects:
            all_grouped_areas.extend(scheme.get_grouped_areas())
        if all_grouped_areas:
            output.print_md(
                "⚠️ {} grouped areas detected (will be skipped during sync)".format(
                    len(all_grouped_areas)
                )
            )
        else:
            output.print_md("✓ No grouped areas detected")

        if boundary_issues:
            output.print_md(
                "⚠️ {} areas have boundary problems (see **6. Element Issues**)".format(
                    len(boundary_issues)
                )
            )
        else:
            output.print_md("✓ No area boundary problems detected")
    else:
        output.print_md("⚠️ No configured area schemes found in model")

    output.print_md("")
    output.print_md("### Data Silos")
    if ext_data_silo_collection.family_exists_in_model:
        output.print_md("✓ Family loaded: {}".format(config.data_silo_family_name))
        output.print_md(
            "✓ Instances found: {}".format(
                len(ext_data_silo_collection.extended_data_silos)
            )
        )
        if ext_data_silo_collection.are_data_silo_uids_unique():
            output.print_md("✓ All UIDs are unique")
        else:
            output.print_md("❌ Duplicate UIDs detected")

        # Check worksharing availability
        try:
            unavailable_ids = ext_data_silo_collection.check_worksharing_availability()
            if unavailable_ids:
                output.print_md(
                    "⚠️ {} Data Silos unavailable for editing".format(
                        len(unavailable_ids)
                    )
                )
            else:
                output.print_md("✓ All Data Silos available for editing")
        except Exception as ws_error:
            logger.debug("Worksharing check skipped: {}".format(str(ws_error)))
            output.print_md("ℹ️ Worksharing availability check skipped")
    else:
        output.print_md("❌ Family not loaded in model")

    output.print_md("")
    output.print_md("### Program Volumes")
    output.print_md(
        "✓ Families found: {}".format(
            len(ext_pv_family_collection.extended_program_volume_families)
        )
    )
    output.print_md(
        "✓ Instances found: {}".format(
            len(ext_pv_instance_collection.extended_program_volume_instances)
        )
    )

    # Check worksharing availability for Program Volumes
    try:
        unavailable_pv_ids = ext_pv_instance_collection.check_worksharing_availability()
        if unavailable_pv_ids:
            output.print_md(
                "⚠️ {} Program Volumes unavailable for editing".format(
                    len(unavailable_pv_ids)
                )
            )
        else:
            output.print_md("✓ All Program Volumes available for editing")
    except Exception as ws_error:
        logger.debug("PV worksharing check skipped: {}".format(str(ws_error)))
        output.print_md("ℹ️ Worksharing availability check skipped")

    output.print_md("")

    # Check worksharing availability for Areas
    if area_scheme_objects:
        output.print_md("### Areas Availability")
        try:
            if not revit.doc.IsWorkshared:
                output.print_md("ℹ️ Model not workshared - availability check N/A")
            else:
                # Collect all area element IDs from all schemes
                area_ids_to_checkout = []
                for scheme in area_scheme_objects:
                    for ext_area in scheme.extended_areas:
                        area_ids_to_checkout.append(ext_area.Id)

                # Try to check out all areas
                typed_list_of_areas = DotNetList[DB.ElementId](area_ids_to_checkout)
                checked_out_areas = DB.WorksharingUtils.CheckoutElements(
                    revit.doc, typed_list_of_areas
                )

                # Count unavailable areas
                unavailable_count = len(area_ids_to_checkout) - len(checked_out_areas)

                if unavailable_count > 0:
                    output.print_md(
                        "⚠️ {} Areas unavailable for editing".format(unavailable_count)
                    )
                else:
                    output.print_md("✓ All Areas available for editing")
        except Exception as ws_error:
            logger.debug("Areas worksharing check skipped: {}".format(str(ws_error)))
            output.print_md("ℹ️ Worksharing availability check skipped")

    output.print_md("\n")

    # === SECTION 4: CORRELATION ANALYSIS ===
    output.print_md("---")
    output.print_md("")
    output.print_md("## 4. Correlation Analysis")
    output.print_md("Data Silo to Area spatial relationships.")
    output.print_md("")

    # Build correlation table
    sorted_data_silos = sorted(
        ext_data_silo_collection.extended_data_silos,
        key=lambda ds: ds.Level.Elevation if ds.Level else float("-inf"),
    )

    table_data = []
    columns = ["Level", "Data Silo ID"] + [
        scheme.area_scheme_name for scheme in area_scheme_objects
    ]

    for ext_data_silo in sorted_data_silos:
        level_name = ext_data_silo.Level.Name if ext_data_silo.Level else "N/A"
        data_silo_id = output.linkify(ext_data_silo.Id)

        row = [level_name, data_silo_id]

        for area_scheme in area_scheme_objects:
            scheme_name = area_scheme.area_scheme_name
            pdso_scheme_key = None
            for pdso_key, area_val in area_scheme_mappings_dict.items():
                if area_val == scheme_name:
                    pdso_scheme_key = pdso_key
                    break

            if (
                pdso_scheme_key
                and pdso_scheme_key in ext_data_silo.related_area_elements
            ):
                ext_area = ext_data_silo.related_area_elements[pdso_scheme_key]
                if ext_area:
                    row.append(output.linkify(ext_area.rpw_area.Id))
                else:
                    row.append("X")
            else:
                row.append("X")

        table_data.append(row)

    if table_data:
        output.print_table(
            table_data=table_data,
            title="Data Silo → Area Correlations",
            columns=columns,
        )

    output.print_md("")

    # Correlation issues
    output.print_md("### Correlation Issues")

    # Areas without data silos - one table per scheme
    all_areas_without_silos = []
    for scheme in area_scheme_objects:
        all_areas_without_silos.extend(scheme.get_areas_without_data_silos())

    if all_areas_without_silos:
        output.print_md(
            "⚠️ **{} total areas without data silos**".format(
                len(all_areas_without_silos)
            )
        )

        # Create master linkify list for all areas
        area_ids = [area["id"] for area in all_areas_without_silos]
        if area_ids:
            id_list = DotNetList[DB.ElementId](area_ids)
            multi_link = output.linkify(
                id_list, title="Select All {} Areas".format(len(area_ids))
            )
            output.print_md(multi_link)

        output.print_md("")

        # Group by scheme
        by_scheme = defaultdict(list)
        for area_info in all_areas_without_silos:
            by_scheme[area_info["scheme"]].append(area_info)

        # Create one table per scheme
        for scheme_name in sorted(by_scheme.keys()):
            areas_in_scheme = by_scheme[scheme_name]

            # Group by level for this scheme
            by_level = defaultdict(list)
            for area_info in areas_in_scheme:
                by_level[area_info["level"]].append(area_info)

            # Build table data for this scheme
            table_data = []
            for level_name in sorted(by_level.keys()):
                areas_on_level = by_level[level_name]

                # Create clickable links for area IDs on this level
                area_element_ids = [area["id"] for area in areas_on_level]
                id_list_for_level = DotNetList[DB.ElementId](area_element_ids)

                # Individual area links (max 4 per line, then line break)
                area_links = [output.linkify(area["id"]) for area in areas_on_level]
                # Group into chunks of 4
                chunked_links = []
                for i in range(0, len(area_links), 4):
                    chunk = area_links[i : i + 4]
                    chunked_links.append(" ".join(chunk))
                areas_display = "<br>".join(chunked_links)

                # Count with "select all" link for this level
                count_link = output.linkify(
                    id_list_for_level, title="{} Area(s)".format(len(areas_on_level))
                )

                table_data.append([level_name, count_link, areas_display])

            # Print table for this scheme
            output.print_table(
                table_data=table_data,
                title="{} - Areas Without Data Silos".format(scheme_name),
                columns=["Level", "Count", "Area IDs"],
            )
            output.print_md("")
    else:
        output.print_md("✓ No areas without data silos")

    output.print_md("")

    # Areas with multiple data silos
    areas_with_multiple = []
    for scheme in area_scheme_objects:
        for ext_area in scheme.extended_areas:
            if len(ext_area.related_data_silos) > 1:
                areas_with_multiple.append(
                    {
                        "id": ext_area.Id,
                        "scheme": scheme.area_scheme_name,
                        "level": ext_area.Level.Name if ext_area.Level else "N/A",
                        "silo_count": len(ext_area.related_data_silos),
                    }
                )

    if areas_with_multiple:
        output.print_md(
            "❌ **{} areas contain multiple Data Silos**".format(
                len(areas_with_multiple)
            )
        )

        # Create master linkify list
        area_ids = [area["id"] for area in areas_with_multiple]
        if area_ids:
            id_list = DotNetList[DB.ElementId](area_ids)
            multi_link = output.linkify(
                id_list, title="Select All {} Areas".format(len(area_ids))
            )
            output.print_md(multi_link)

        output.print_md("")

        # Build table
        table_data = []
        for area_info in areas_with_multiple[:20]:  # Show first 20
            table_data.append(
                [
                    area_info["scheme"],
                    area_info["level"],
                    output.linkify(area_info["id"]),
                    str(area_info["silo_count"]),
                ]
            )

        output.print_table(
            table_data=table_data,
            title="Areas with Multiple Data Silos",
            columns=["Area Scheme", "Level", "Area ID", "Silo Count"],
        )

        if len(areas_with_multiple) > 20:
            output.print_md("_(... and {} more)_".format(len(areas_with_multiple) - 20))
    else:
        output.print_md("✓ No areas with multiple Data Silos")

    output.print_md("")

    # Data silos with partial area scheme coverage
    configured_scheme_names = [
        scheme.bound_silo_parameter_name for scheme in area_scheme_objects
    ]
    silos_with_partial_coverage = []

    for ext_silo in ext_data_silo_collection.extended_data_silos:
        related_schemes = set(ext_silo.related_area_elements.keys())
        if related_schemes and not related_schemes.issuperset(configured_scheme_names):
            missing_schemes = set(configured_scheme_names) - related_schemes
            silos_with_partial_coverage.append(
                {
                    "id": ext_silo.Id,
                    "level": ext_silo.Level.Name if ext_silo.Level else "N/A",
                    "missing_schemes": list(missing_schemes),
                    "has_schemes": list(related_schemes),
                }
            )

    if silos_with_partial_coverage:
        output.print_md(
            "⚠️ **{} Data Silos missing some area schemes**".format(
                len(silos_with_partial_coverage)
            )
        )

        # Create master linkify list
        silo_ids = [silo["id"] for silo in silos_with_partial_coverage]
        if silo_ids:
            id_list = DotNetList[DB.ElementId](silo_ids)
            multi_link = output.linkify(
                id_list, title="Select All {} Data Silos".format(len(silo_ids))
            )
            output.print_md(multi_link)

        output.print_md("")

        # Build table
        table_data = []
        for silo_info in silos_with_partial_coverage[:20]:  # Show first 20
            table_data.append(
                [
                    silo_info["level"],
                    output.linkify(silo_info["id"]),
                    ", ".join(silo_info["has_schemes"]),
                    ", ".join(silo_info["missing_schemes"]),
                ]
            )

        output.print_table(
            table_data=table_data,
            title="Data Silos with Partial Coverage",
            columns=["Level", "Data Silo ID", "Has Schemes", "Missing Schemes"],
        )

        if len(silos_with_partial_coverage) > 20:
            output.print_md(
                "_(... and {} more)_".format(len(silos_with_partial_coverage) - 20)
            )
    else:
        output.print_md("✓ All Data Silos have full area scheme coverage")

    output.print_md("")

    # Data silos with no area containment
    silos_with_no_containment = []

    for ext_silo in ext_data_silo_collection.extended_data_silos:
        if not ext_silo.related_area_elements:
            silos_with_no_containment.append(
                {
                    "id": ext_silo.Id,
                    "level": ext_silo.Level.Name if ext_silo.Level else "N/A",
                }
            )

    if silos_with_no_containment:
        output.print_md(
            "⚠️ **{} Data Silos not contained by any areas**".format(
                len(silos_with_no_containment)
            )
        )

        # Create master linkify list
        silo_ids = [silo["id"] for silo in silos_with_no_containment]
        if silo_ids:
            id_list = DotNetList[DB.ElementId](silo_ids)
            multi_link = output.linkify(
                id_list, title="Select All {} Data Silos".format(len(silo_ids))
            )
            output.print_md(multi_link)

        output.print_md("")

        # Build table
        table_data = []
        for silo_info in silos_with_no_containment[:20]:  # Show first 20
            table_data.append([silo_info["level"], output.linkify(silo_info["id"])])

        output.print_table(
            table_data=table_data,
            title="Data Silos with No Containment",
            columns=["Level", "Data Silo ID"],
        )

        if len(silos_with_no_containment) > 20:
            output.print_md(
                "_(... and {} more)_".format(len(silos_with_no_containment) - 20)
            )
    else:
        output.print_md("✓ All Data Silos contained by areas")

    output.print_md("\n")

    # === SECTION 5: LEVEL MAPPING VALIDATION ===
    output.print_md("---")
    output.print_md("")
    output.print_md("## 5. Level Mapping Validation")
    output.print_md(
        "Shows data silos with stacking parameters and their resulting program volume instances."
    )
    output.print_md("")

    logger.debug("Section 5: Starting Level Mapping Validation table generation")
    logger.debug(
        "  Total PV instances in collection: {}".format(
            len(ext_pv_instance_collection.extended_program_volume_instances)
        )
    )

    # Build reverse lookup: ElementId -> key from level mappings
    element_id_to_key = {}
    for key, element_id in level_mapping_service.level_mappings_cache.items():
        element_id_to_key[element_id.Value] = key

    # Build detailed table showing data silos, stacking params, and resulting PV instances
    silo_data = {}  # {data_silo_id: {'families': {family_name: {'levels': {level: [instances]}}}, 'element_ids': list}}

    orphaned_count = 0

    try:
        logger.debug("  Starting iteration over PV instances...")
        for i, ext_pv_instance in enumerate(
            ext_pv_instance_collection.extended_program_volume_instances
        ):
            if i == 0:
                logger.debug("    Processing first instance...")

            # Skip if no parent data silo
            if not ext_pv_instance.parent_data_silo:
                orphaned_count += 1
                continue

            # Skip if rpw_element is None (invalid instance)
            if not ext_pv_instance.rpw_element:
                logger.info("    Skipping instance with None rpw_element")
                continue

            # Skip if element doesn't have Symbol (not a FamilyInstance)
            if (
                not hasattr(ext_pv_instance.rpw_element, "Symbol")
                or not ext_pv_instance.rpw_element.Symbol
            ):
                logger.info("    Skipping instance without Symbol attribute")
                continue

            data_silo_id = ext_pv_instance.parent_data_silo.Id
            family_name = ext_pv_instance.rpw_element.Symbol.Family.Name
            level_name = ext_pv_instance.Level.Name if ext_pv_instance.Level else "N/A"
            element_id = ext_pv_instance.Id

            # Get the mapping key for this level (if available)
            level_id = ext_pv_instance.LevelId.Value if ext_pv_instance.Level else None
            mapping_key = element_id_to_key.get(level_id, None)

            # Format level display with key if available
            if mapping_key:
                level_display = "{} → {}".format(mapping_key, level_name)
            else:
                level_display = level_name

            # Initialize data silo entry
            if data_silo_id not in silo_data:
                silo_data[data_silo_id] = {
                    "families": {},
                    "element_ids": [],
                    "parent_data_silo": ext_pv_instance.parent_data_silo,
                }

            # Initialize family entry under this data silo
            if family_name not in silo_data[data_silo_id]["families"]:
                silo_data[data_silo_id]["families"][family_name] = {"levels": {}}

            # Initialize level entry under this family (using level_display instead of level_name)
            if (
                level_display
                not in silo_data[data_silo_id]["families"][family_name]["levels"]
            ):
                silo_data[data_silo_id]["families"][family_name]["levels"][
                    level_display
                ] = []

            # Add instance to this level with is_primary_instance flag
            silo_data[data_silo_id]["families"][family_name]["levels"][
                level_display
            ].append(
                {
                    "element_id": element_id,
                    "is_primary": ext_pv_instance.is_primary_instance,
                }
            )
            silo_data[data_silo_id]["element_ids"].append(element_id)

        logger.debug("  Iteration complete")
    except Exception as e:
        logger.error("  ERROR during PV instance iteration: {}".format(str(e)))
        logger.error("  Exception type: {}".format(type(e).__name__))
        logger.error(traceback.format_exc())
        output.print_md(
            "❌ Error processing program volume instances: {}".format(str(e))
        )

    logger.debug("  Orphaned PV instances (skipped): {}".format(orphaned_count))
    logger.debug("  Data silos with PV instances: {}".format(len(silo_data)))

    if silo_data:
        logger.debug("  Building table with {} rows...".format(len(silo_data)))
        # Build table rows, sorted by Program UID
        table_data = []
        # Sort by PDSO - Program UID parameter
        sorted_silo_ids = sorted(
            silo_data.keys(),
            key=lambda x: (
                silo_data[x]["parent_data_silo"].parameters["PDSO - Program UID"].value
                or ""
            ),
        )

        for data_silo_id in sorted_silo_ids:
            data = silo_data[data_silo_id]

            logger.debug(
                "    Processing data silo ID: {} with {} families".format(
                    data_silo_id.Value, len(data["families"])
                )
            )

            # Get stacking range from data silo parameter
            stacking_range = "N/A"
            if "parent_data_silo" in data and data["parent_data_silo"]:
                stacking_param = data["parent_data_silo"].rpw_element.LookupParameter(
                    "PDSO - Program Stacking"
                )
                if stacking_param and stacking_param.HasValue:
                    stacking_range = stacking_param.AsString() or "N/A"

            # Build family name display (comma separated if multiple)
            family_names = sorted(data["families"].keys())
            family_names_display = ", ".join(family_names)

            # Build instance display with levels
            instance_displays = []
            for family_name in family_names:
                family_info = data["families"][family_name]

                # Build level display for this family
                for level_name in sorted(family_info["levels"].keys()):
                    instance_data_list = family_info["levels"][level_name]
                    # Format each instance, marking canonical (primary) one with asterisk
                    id_links = []
                    for inst_data in instance_data_list:
                        eid = inst_data["element_id"]
                        link = output.linkify(eid)
                        id_links.append(link)

                    # Check if any instance on this level is primary (canonical)
                    has_primary = any(
                        inst_data["is_primary"] for inst_data in instance_data_list
                    )
                    level_marker = " *" if has_primary else ""

                    instance_display = "{} [{}]{}".format(
                        ", ".join(id_links), level_name, level_marker
                    )
                    instance_displays.append(instance_display)

            instances_column = "<br>".join(instance_displays)

            # Build select all link for count column
            total_count = len(data["element_ids"])
            element_ids_list = DotNetList[DB.ElementId](data["element_ids"])
            count_link = output.linkify(
                element_ids_list, title="{} Instance(s)".format(total_count)
            )

            # Linkify data silo ID
            data_silo_id_display = output.linkify(data_silo_id)

            table_data.append(
                [
                    data_silo_id_display,
                    stacking_range,
                    family_names_display,
                    count_link,
                    instances_column,
                ]
            )

        logger.debug("  Printing table with {} rows...".format(len(table_data)))
        # Display the table
        output.print_table(
            table_data=table_data,
            title="Program Volume Families Summary",
            columns=[
                "Data Silo ID",
                "Stacking Range",
                "Program Volume Family Name",
                "Instances Count",
                "Program Volume Instances",
            ],
        )
        logger.debug("  Table printed successfully")
        output.print_md("")
    else:
        logger.debug("  No silo_data found - printing info message")
        output.print_md("ℹ️ No program volume instances found in model.")
        output.print_md("")

    logger.debug("Section 5: Checking for validation errors...")
    # Show validation errors if any exist
    try:
        if (
            hasattr(ext_pv_instance_collection, "validation_result")
            and ext_pv_instance_collection.validation_result
        ):
            validation = ext_pv_instance_collection.validation_result
            logger.debug(
                "  Validation result exists, passed: {}".format(validation.passed)
            )
            if not validation.passed:
                output.print_md("⚠️ **Level Mapping Issues Found**")
                output.print_md("")

                # Count errors
                parsing_error_count = (
                    len(validation.errors_by_data_silo_uid)
                    if hasattr(validation, "errors_by_data_silo_uid")
                    and validation.errors_by_data_silo_uid
                    else 0
                )
                missing_mapping_count = (
                    len(validation.missing_mappings_by_data_silo_uid)
                    if hasattr(validation, "missing_mappings_by_data_silo_uid")
                    and validation.missing_mappings_by_data_silo_uid
                    else 0
                )

                logger.debug(
                    "  Parsing errors: {}, Missing mappings: {}".format(
                        parsing_error_count, missing_mapping_count
                    )
                )

                output.print_md("**Summary:**")
                output.print_md(
                    "- Data silos with parsing errors: {}".format(parsing_error_count)
                )
                output.print_md(
                    "- Data silos with missing level mappings: {}".format(
                        missing_mapping_count
                    )
                )
                output.print_md("")

                if (
                    hasattr(validation, "errors_by_data_silo_uid")
                    and validation.errors_by_data_silo_uid
                ):
                    logger.debug("  Printing parsing errors...")
                    output.print_md("### Parsing Errors")
                    for uid, errors in validation.errors_by_data_silo_uid.items():
                        output.print_md("**{}:**".format(uid))
                        for error in errors:
                            output.print_md("  - {}".format(error))
                    output.print_md("")

                if (
                    hasattr(validation, "missing_mappings_by_data_silo_uid")
                    and validation.missing_mappings_by_data_silo_uid
                ):
                    logger.debug("  Printing missing level mappings...")
                    output.print_md("### Missing Level Mappings")
                    for (
                        uid,
                        missing_keys,
                    ) in validation.missing_mappings_by_data_silo_uid.items():
                        output.print_md(
                            "**{}:** Missing keys: {}".format(
                                uid, ", ".join(str(k) for k in missing_keys)
                            )
                        )
        else:
            logger.debug("  No validation result found or validation_result is None")
    except Exception as e:
        logger.error("  ERROR during validation error processing: {}".format(str(e)))
        logger.error(traceback.format_exc())

    logger.debug("Section 5: Complete")
    output.print_md("\n")

    # === SECTION 6: ELEMENT ISSUES ===
    output.print_md("---")
    output.print_md("")
    logger.debug("Starting Section 6: Element Issues")
    try:
        output.print_md("## 6. Element Issues")
        output.print_md("")

        # Area boundaries
        if area_scheme_objects:
            print_area_boundary_issues(
                output,
                boundary_issues,
                boundary_checked_count,
                boundary_failed_count,
                area_scheme_mappings_dict.get("PDSO - GSF", "PDSO - GSF"),
            )
            output.print_md("")

        # Orphaned program volume instances
        orphaned_instances = ext_pv_instance_collection.get_orphaned_instances()
        if orphaned_instances:
            output.print_md("### Orphaned Program Volume Instances")
            output.print_md(
                "⚠️ {} instances without parent data silo".format(
                    len(orphaned_instances)
                )
            )
            orphaned_ids = [inst["id"] for inst in orphaned_instances]
            if orphaned_ids:
                id_list = DotNetList[DB.ElementId](orphaned_ids)
                multi_link = output.linkify(
                    id_list,
                    title="Select All {} Orphaned Instances".format(len(orphaned_ids)),
                )
                output.print_md("  {}".format(multi_link))
            for inst_info in orphaned_instances[:10]:
                output.print_md(
                    "  - {} on {} [Family: {}]".format(
                        output.linkify(inst_info["id"]),
                        inst_info["level"],
                        inst_info["family"],
                    )
                )
            if len(orphaned_instances) > 10:
                output.print_md(
                    "  _(... and {} more)_".format(len(orphaned_instances) - 10)
                )
        else:
            output.print_md("### Orphaned Program Volume Instances")
            output.print_md("✓ No orphaned instances detected")

        output.print_md("")

        # Orphaned program volume families with instance counts
        orphaned_families = ext_pv_family_collection.get_orphaned_families()
        if orphaned_families:
            output.print_md("### Orphaned Program Volume Families")
            output.print_md(
                "⚠️ {} families without parent data silo".format(len(orphaned_families))
            )

            # Count instances for each orphaned family
            for fam_info in orphaned_families:
                family_name = fam_info["name"]
                # Count instances of this family
                instance_count = sum(
                    1
                    for pv_inst in ext_pv_instance_collection.extended_program_volume_instances
                    if pv_inst.rpw_element
                    and hasattr(pv_inst.rpw_element, "Symbol")
                    and pv_inst.rpw_element.Symbol
                    and pv_inst.rpw_element.Symbol.Family.Name == family_name
                )
                output.print_md(
                    "  - {} ({} instance{})".format(
                        family_name, instance_count, "s" if instance_count != 1 else ""
                    )
                )
        else:
            output.print_md("### Orphaned Program Volume Families")
            output.print_md("✓ No orphaned families detected")

        logger.debug("Section 6: Complete")
    except Exception as e:
        logger.error("ERROR in Section 6: {}".format(str(e)))
        logger.error(traceback.format_exc())
        output.print_md("❌ Error in Element Issues section: {}".format(str(e)))

    output.print_md("\n")

    # === SECTION 7: GEOMETRY TRACKING ===
    output.print_md("---")
    output.print_md("")
    logger.debug("Starting Section 7: Geometry Tracking")
    try:
        output.print_md("## 7. Geometry Tracking")
        output.print_md("Program volume family geometry hash status.")
        output.print_md("")

        families_with_hashes = [
            f
            for f in ext_pv_family_collection.extended_program_volume_families
            if f.entity and f.entity.hashed_area_curve_geometry
        ]
        families_without_hashes = [
            f
            for f in ext_pv_family_collection.extended_program_volume_families
            if not f.entity or not f.entity.hashed_area_curve_geometry
        ]

        output.print_md(
            "- Families with geometry hashes: {}".format(len(families_with_hashes))
        )
        output.print_md(
            "- Families without hashes: {}".format(len(families_without_hashes))
        )

        if families_without_hashes:
            output.print_md("")
            output.print_md(
                "⚠️ **Families without geometry hashes (will be regenerated):**"
            )
            for fam in families_without_hashes[:10]:
                if fam.rpw_element is None:
                    continue
                output.print_md("  - {}".format(fam.rpw_element.Name))
            if len(families_without_hashes) > 10:
                output.print_md(
                    "  _(... and {} more)_".format(len(families_without_hashes) - 10)
                )

        logger.debug("Section 7: Complete")
    except Exception as e:
        logger.error("ERROR in Section 7: {}".format(str(e)))
        logger.error(traceback.format_exc())
        output.print_md("❌ Error in Geometry Tracking section: {}".format(str(e)))

    output.print_md("\n")
    output.print_md("=" * 80)
    output.print_md("**Report Complete**")
    logger.info("Audit report generation complete")


if __name__ == "__main__":
    # Get script output and logger
    output = script.get_output()
    logger = script.get_logger()

    logger.info("Starting PDSO Audit - Detailed Report")

    try:
        generate_detailed_report(output, logger)
    except Exception as e:
        logger.error("Unexpected error during report generation: {}".format(str(e)))
        logger.error(traceback.format_exc())
        output.print_md("# ❌ Unexpected Error")
        output.print_md("An unexpected error occurred. Check the log for details.")
