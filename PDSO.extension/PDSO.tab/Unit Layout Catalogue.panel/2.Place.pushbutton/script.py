# -*- coding: utf-8 -*-
"""Place: recommend catalogue layouts for a Room or Area, and create the chosen one."""

from pyrevit import forms, revit, script

from pdso.code.catalogue.catalogue_settings import catalogue_folders
from pdso.code.catalogue.catalogue_store import load_catalogue
from pdso.code.catalogue.catalogue_ui import pick_spatial_element
from pdso.code.catalogue.layout_recommender import recommend
from pdso.code.catalogue.revit_layout_service import (
    create,
    describe_target,
    get_room_department,
    get_room_name,
)

output = script.get_output()

if __name__ == "__main__":
    room = pick_spatial_element("Pick the Room or Area to fill")
    if room is None:
        script.exit()

    frame, local_outline, area = describe_target(room)
    if frame is None:
        forms.alert(
            "This Room or Area is not placed or not enclosed, so it has no boundary.",
            exitscript=True,
        )

    entries, load_errors = load_catalogue(catalogue_folders())
    if not entries:
        forms.alert(
            "The catalogue is empty. Harvest a layout first.\nRead from:\n{}".format(
                "\n".join(catalogue_folders())
            ),
            exitscript=True,
        )

    program = forms.ask_for_string(
        default=get_room_name(room) or get_room_department(room),
        prompt="Program for this room (layouts for it are listed first):",
        title="Place layout",
    )
    if program is None:
        script.exit()

    recommendations, rejected = recommend(
        entries, frame.width, frame.depth, area, local_outline, program
    )

    if not recommendations:
        output.print_md("## No layout fits {:.1f} x {:.1f} ft".format(frame.width, frame.depth))
        output.print_table(
            [[entry.name, entry.program, reason] for entry, reason in rejected],
            columns=["Layout", "Program", "Why not"],
        )
        script.exit()

    chosen = forms.SelectFromList.show(
        recommendations,
        name_attr="label",
        title="Layouts for {:.1f} x {:.1f} ft, {:.0f} sf".format(frame.width, frame.depth, area),
        button_name="Place",
        multiselect=False,
    )
    if chosen is None:
        script.exit()

    with revit.Transaction("PDSO - Place Unit Layout"):
        result = create(room, chosen.entry, chosen.placement)

    output.print_md("## Placed: {}".format(chosen.entry.name))
    output.print_md(
        "{} items placed, {}. Score {:.0f}%.".format(
            len(result.placed), chosen.placement.describe(), chosen.score * 100
        )
    )
    if result.placed:
        output.print_md("Select placed items: {}".format(output.linkify(result.placed)))
    if result.tagged_silos:
        output.print_md(
            "`PDSO - Unit Layout Type` set to `{}` on {} data silo(s): {}".format(
                chosen.entry.id, len(result.tagged_silos), output.linkify(result.tagged_silos)
            )
        )
    if result.missing:
        output.print_table(
            [[key, count] for key, count in sorted(result.missing.items())],
            columns=["Family : Type not loaded in this model", "Count"],
            title="Missing families",
        )
    for warning in result.warnings:
        output.print_md("- {}".format(warning))
    for error in load_errors:
        output.print_md("- Catalogue file skipped: {}".format(error))
