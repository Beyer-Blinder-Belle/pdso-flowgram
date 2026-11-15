# -*- coding: utf-8 -*-
"""Harvest Units (temporary): group Rooms into units and save JSON + preview."""

import os
import sys
import traceback

from pyrevit import forms, revit, script

# pyRevit keeps imported library modules in memory. Drop this prototype's
# module, so that the button uses the code that is on disk now (no reload).
for _name in [n for n in sys.modules if n == "pdso.code.business.unit_grouping_service"]:
    del sys.modules[_name]

from pdso.code.business import unit_grouping_service as units_service  # noqa: E402
from pdso.code.business.revit_helpers import to_element_id  # noqa: E402

FT2_TO_M2 = 0.09290304


def _default_name(doc):
    title = os.path.splitext(doc.Title)[0]
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    return "{} - units".format(safe or "model")


def _report(output, result, json_path, html_path):
    summary = result["summary"]
    output.print_md("# Harvest Units (prototype)")
    output.print_md(
        "**{}** units, **{}** rooms in units, **{}** shared rooms, "
        "**{}** entrances, **{}** windows in units".format(
            summary["units"],
            summary["rooms_in_units"],
            summary["shared_rooms"],
            summary["entrances"],
            summary["windows_in_units"],
        )
    )
    if summary["units_to_check"]:
        output.print_md("**{} units to check** (see Issues).".format(summary["units_to_check"]))
    if summary["unresolved_openings"]:
        output.print_md(
            "{} doors or windows are not next to any Room.".format(summary["unresolved_openings"])
        )

    rows = []
    for unit in result["units"]:
        entrance = unit["entrances"][0] if unit["entrances"] else None
        rows.append(
            [
                unit["uid"],
                unit["room_count"],
                "{:.2f}".format(unit["area_sf"] * FT2_TO_M2),
                ", ".join(r["name"] for r in unit["rooms"]),
                output.linkify(to_element_id(entrance["id"])) if entrance else "-",
                len(unit["windows"]),
                ", ".join(unit["issues"]) or "",
            ]
        )
    output.print_table(
        rows,
        columns=["Unit", "Rooms", "m2", "Room names", "Entrance", "Windows", "Issues"],
    )
    output.print_md("Saved:\n\n- `{}`\n- `{}`".format(json_path, html_path))


if __name__ == "__main__":
    doc = revit.doc
    output = script.get_output()

    json_path = forms.save_file(
        file_ext="json",
        default_name=_default_name(doc),
        title="Save the unit harvest (an HTML preview is saved next to it)",
    )
    if not json_path:
        script.exit()

    try:
        with forms.ProgressBar(title="Harvesting units...", indeterminate=True):
            result = units_service.harvest_units(doc)
        json_path, html_path = units_service.write_outputs(result, json_path)
    except Exception as error:  # prototype: show anything that goes wrong
        output.print_md("# Harvest Units failed")
        output.print_md("`{}`".format(error))
        output.print_code(traceback.format_exc())
        script.exit()

    _report(output, result, json_path, html_path)
    os.startfile(html_path)
