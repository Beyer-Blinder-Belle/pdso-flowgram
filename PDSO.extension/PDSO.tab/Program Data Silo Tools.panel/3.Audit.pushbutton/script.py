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
from pyrevit import script

# Custom Modules
from pdso.code.business.audit_service import generate_detailed_report


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
