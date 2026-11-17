# -*- coding: utf-8 -*-
"""Harvest All Units: save the layout of every unit in the active area plan.

A unit is an Area with a PDSO data silo inside it. Nothing is picked: open the
area plan and run. To harvest other elements (a Room, an Area with no silo),
select them first; a selection of Rooms or Areas is harvested as it is.

Running again replaces the earlier harvest of the same unit. The harvest
itself is catalogue_api_service.harvest_units, which the API uses too; this
button chooses the units, asks for the project type and prints the report.
"""

import sys

from pyrevit import forms, revit, script

# pyRevit keeps imported library modules in memory. Drop the catalogue modules,
# so that this button uses the code that is on disk now (no pyRevit reload).
for _name in [name for name in sys.modules if name.startswith("pdso.code.catalogue")]:
    del sys.modules[_name]

from pdso.code.business.errors import PDSOError  # noqa: E402
from pdso.code.catalogue import catalogue_api_service as catalogue  # noqa: E402
from pdso.code.catalogue.catalogue_windows import PROJECT_TYPES  # noqa: E402
from pdso.code.catalogue.harvest_image import thumbnail_data_uri  # noqa: E402
from pdso.code.catalogue.revit_layout_service import (  # noqa: E402
    get_room_name,
    is_area_plan,
    is_spatial_element,
)

output = script.get_output()

ROW_IMAGE_HEIGHT = 110  # px, the plan in front of each row of the table


def html_table(columns, table_rows):
    """A table as HTML. print_table can't hold a picture in a cell; this can."""
    cell = "padding:6px 10px;border-bottom:1px solid #ccc;text-align:left;vertical-align:middle;"
    parts = ['<table style="border-collapse:collapse;width:100%;font-size:13px;">', "<tr>"]
    for name in columns:
        parts.append(
            '<th style="{}background:#263238;color:#fff;">{}</th>'.format(cell, name)
        )
    parts.append("</tr>")
    for number, table_row in enumerate(table_rows):
        shade = "background:#f2f2f2;" if number % 2 == 0 else ""
        parts.append("<tr>")
        for value in table_row:
            parts.append('<td style="{}{}">{}</td>'.format(cell, shade, value))
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def units_to_harvest():
    """Selected Rooms/Areas if there are any, else the active area plan's units.

    Returns:
        tuple: (elements, areas without a silo, where they came from).
    """
    selected = [
        revit.doc.GetElement(element_id)
        for element_id in revit.uidoc.Selection.GetElementIds()
    ]
    selected = [element for element in selected if is_spatial_element(element)]
    if selected:
        return selected, [], "the selection"

    view = revit.doc.ActiveView
    if not is_area_plan(view):
        forms.alert(
            "Open an area plan and run Harvest All Units again: every Area in it with a "
            "PDSO data silo inside is harvested.\n\n"
            "Or select the Rooms or Areas to harvest first.",
            exitscript=True,
        )
    areas, without = catalogue.units_in_view(revit.doc, view)
    return areas, without, "area plan '{}'".format(view.Name)


def print_details(entry, result):
    """The full report for one unit, to check against the plan."""
    features = entry.features
    unit = features.get("unit", {})
    lengths = features.get("edge_lengths", {})
    openings = features.get("openings", [])

    output.print_table(
        [
            ["Shape", "{} ({} corners)".format(
                features.get("shape", {}).get("category", ""),
                features.get("shape", {}).get("corners", 0),
            )],
            ["Size", "{:.1f} x {:.1f} ft, {:.0f} sf".format(entry.width, entry.depth, entry.area)],
            ["Perimeter", "{:.1f} ft".format(features.get("perimeter", 0.0))],
            ["Exterior wall", "{:.1f} ft".format(lengths.get("exterior", 0.0))],
            ["Corridor wall", "{:.1f} ft".format(lengths.get("corridor", 0.0))],
            ["Party wall (a neighbour beyond)", "{:.1f} ft".format(lengths.get("party", 0.0))],
            ["Wall with no room beyond", "{:.1f} ft".format(lengths.get("unroomed", 0.0))],
            ["Glazing (windows + curtain wall)", "{:.1f} ft".format(features.get("glazed_length", 0.0))],
            ["Doors in the unit's boundary", len([o for o in openings if o["kind"] == "door"])],
            ["Windows", len([o for o in openings if o["kind"] == "window"])],
            ["Entry door found", "yes" if features.get("entry") else "no"],
        ],
        columns=["Around the unit", "Value"],
        title="Around the unit (used to match it to other spaces)",
    )

    output.print_table(
        [
            ["Rooms", unit.get("room_count", 0)],
            ["Bedrooms / bathrooms / kitchens (by room name)", "{} / {} / {}".format(
                unit.get("bedrooms", 0), unit.get("bathrooms", 0), unit.get("kitchens", 0)
            )],
            ["Partitions", len(features.get("internal_walls", []))],
            ["Doors in partitions", len(features.get("internal_doors", []))],
            ["Free-standing families", len(entry.items)],
            ["Pieces inside nested families", len(features.get("nested_items", []))],
            ["Hosted families", len(features.get("hosted_items", []))],
        ],
        columns=["Inside the unit", "Value"],
        title="Inside the unit",
    )

    if features.get("rooms"):
        output.print_table(
            [[r["name"], "{:.0f} sf".format(r["area"])] for r in features["rooms"]],
            columns=["Room", "Area"],
            title="Rooms",
        )

    if unit.get("pdso"):
        output.print_table(
            [[p["key"], p["display"]] for p in unit["pdso"]],
            columns=["PDSO parameter", "Value"],
            title="From the unit's data silo",
        )

    counts = {}
    for item in entry.items:
        key = (item.category, item.family, item.type)
        counts[key] = counts.get(key, 0) + 1
    if counts:
        output.print_table(
            [[c, f, t, n] for (c, f, t), n in sorted(counts.items())],
            columns=["Category", "Family", "Type", "Count"],
            title="Free-standing families",
        )

    if result.skipped:
        output.print_table(
            [[output.linkify(eid), label, reason] for eid, label, reason in result.skipped],
            columns=["Element", "Family : Type", "Why"],
            title="Recorded but not placed by Pull from Catalogue (hosted families)",
        )


if __name__ == "__main__":
    elements, without_silo, origin = units_to_harvest()
    if not elements:
        forms.alert(
            "No Area with a PDSO data silo inside was found in {}.\n\n"
            "{} placed Area(s) have no silo. Select Areas or Rooms first to "
            "harvest them anyway.".format(origin, len(without_silo)),
            exitscript=True,
        )

    project_type = forms.CommandSwitchWindow.show(
        PROJECT_TYPES, message="Project type these layouts come from:"
    )
    if not project_type:
        script.exit()

    try:
        # Plan images come from the active view (the imager reports a view
        # that is not a plan)
        report = catalogue.harvest_units(
            revit.doc, elements, project_type, view=revit.doc.ActiveView
        )
    except PDSOError as error:
        forms.alert(str(error), exitscript=True)

    folder = report["folder"]
    replaced = report["replaced"]
    images = report["images"]
    image_error = report["image_error"]
    saved = report["units"]
    failures = [(unit["id"], unit["reason"]) for unit in report["failures"]]
    last = (saved[0]["_entry"], saved[0]["_result"]) if saved else None

    rows = []
    for unit in saved:
        entry = unit["_entry"]
        features = entry.features
        details = features.get("unit", {})
        lengths = features.get("edge_lengths", {})
        picture = ""
        if unit["image"]:
            # The plan goes in front of the unit's row of the table
            data = thumbnail_data_uri(unit["image"], ROW_IMAGE_HEIGHT * 2)
            if data:
                picture = '<img src="{}" height="{}">'.format(data, ROW_IMAGE_HEIGHT)
        rows.append(
            [
                picture,
                output.linkify(unit["_element"].Id),
                unit["catalogue_number"],
                entry.name,
                features.get("shape", {}).get("category", ""),
                "{:.1f} x {:.1f}".format(entry.width, entry.depth),
                "{:.0f}".format(lengths.get("exterior", 0.0)),
                "{:.0f}".format(lengths.get("corridor", 0.0)),
                "{:.0f}".format(lengths.get("party", 0.0)),
                "yes" if features.get("entry") else "no",
                details.get("room_count", 0),
                len(features.get("internal_walls", [])),
                len(entry.items),
                len(features.get("nested_items", [])),
                len([p for p in details.get("pdso", []) if p["value"] is not None]),
            ]
        )

    output.print_md("## Harvested {} unit(s) from {}".format(len(rows), origin))
    output.print_md("**{}** · saved to `{}`".format(project_type, folder))
    if replaced:
        output.print_md("{} earlier harvest(s) of the same units were replaced.".format(replaced))
    output.print_md(
        "Plan images: {} of {}{}".format(
            images, len(saved), " ({})".format(image_error) if image_error else ""
        )
    )

    if rows:
        output.print_md("### Units")
        output.print_html(
            html_table(
                [
                    "Plan", "Unit", "Catalogue", "Name", "Shape", "Size (ft)", "Exterior (ft)",
                    "Corridor (ft)", "Party (ft)", "Entry", "Rooms", "Partitions", "Families",
                    "Nested pieces", "PDSO values set",
                ],
                rows,
            )
        )

    if failures:
        output.print_table(
            [[output.linkify(eid), reason] for eid, reason in failures],
            columns=["Element", "Why it was not harvested"],
            title="Not harvested",
        )

    if without_silo:
        output.print_table(
            [[output.linkify(area.Id), get_room_name(area), "{:.0f} sf".format(area.Area)]
             for area in without_silo],
            columns=["Area", "Name", "Size"],
            title="Areas left out: no PDSO data silo inside",
        )

    # One unit: show everything that was read, to check it against the plan
    if len(rows) == 1 and last:
        print_details(*last)
