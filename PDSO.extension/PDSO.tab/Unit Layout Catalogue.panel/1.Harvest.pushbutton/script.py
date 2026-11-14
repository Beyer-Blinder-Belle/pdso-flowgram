# -*- coding: utf-8 -*-
"""Harvest: save a Room's or Area's layout into the Unit Layout Catalogue."""

from pyrevit import forms, script

from pdso.code.catalogue.catalogue_settings import get_catalogue_folder
from pdso.code.catalogue.catalogue_store import save_entry
from pdso.code.catalogue.catalogue_ui import ask_project_type, pick_spatial_element
from pdso.code.catalogue.revit_layout_service import (
    get_room_department,
    get_room_name,
    harvest,
)

output = script.get_output()

if __name__ == "__main__":
    room = pick_spatial_element("Pick the Room or Area to harvest")
    if room is None:
        script.exit()

    room_name = get_room_name(room)
    program = forms.ask_for_string(
        default=room_name or get_room_department(room),
        prompt="Program this layout serves (e.g. Classroom, Science Lab):",
        title="Harvest layout",
    )
    if not program:
        script.exit()

    project_type = ask_project_type()
    if not project_type:
        script.exit()

    name = forms.ask_for_string(
        default="{} - {:.0f} sf".format(program, room.Area),
        prompt="Name for this layout:",
        title="Harvest layout",
    )
    if not name:
        script.exit()

    result = harvest(room, name, program, project_type)
    if result is None:
        forms.alert(
            "This Room or Area is not placed or not enclosed, so it has no boundary.",
            exitscript=True,
        )

    entry = result.entry
    if not entry.items:
        if not forms.alert(
            "No free-standing families were found in this room. Save an empty layout?",
            yes=True,
            no=True,
        ):
            script.exit()

    folder = get_catalogue_folder()
    path = save_entry(entry, folder)

    output.print_md("## Harvested: {}".format(entry.name))
    output.print_md(
        "**{}** · {} · {:.1f} x {:.1f} ft · {:.0f} sf · **{} items**".format(
            entry.program, entry.project_type, entry.width, entry.depth, entry.area, len(entry.items)
        )
    )
    output.print_md("Saved to `{}`".format(path))

    counts = {}
    for item in entry.items:
        key = (item.category, item.family, item.type)
        counts[key] = counts.get(key, 0) + 1
    if counts:
        output.print_table(
            [[c, f, t, n] for (c, f, t), n in sorted(counts.items())],
            columns=["Category", "Family", "Type", "Count"],
            title="Harvested",
        )

    if result.skipped:
        output.print_table(
            [[output.linkify(eid), label, reason] for eid, label, reason in result.skipped],
            columns=["Element", "Family : Type", "Why skipped"],
            title="Skipped (version 1 harvests free-standing families only)",
        )
