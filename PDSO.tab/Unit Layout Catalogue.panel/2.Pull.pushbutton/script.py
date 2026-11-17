# -*- coding: utf-8 -*-
"""Pull from Catalogue: put a catalogue layout into the selected unit.

A unit is an Area with a PDSO data silo inside. The button needs a unit to
compare the layouts with: select its Area or data silo, or objects that are
in one unit. It is not available with no selection. The window shows the unit
as it is now and the layouts that fit it, ranked. The unit does not have to
be harvested.
"""

import sys

from pyrevit import revit

# pyRevit keeps imported library modules in memory. Drop the catalogue modules,
# so that this button uses the code that is on disk now (no pyRevit reload).
for _name in [name for name in sys.modules if name.startswith("pdso.code.catalogue")]:
    del sys.modules[_name]

from pdso.code.catalogue import catalogue_api_service as catalogue  # noqa: E402
from pdso.code.catalogue import catalogue_commands as commands  # noqa: E402
from pdso.code.catalogue import catalogue_windows as windows  # noqa: E402

LAYOUTS = 5


def pull_into_unit(doc, unit_id):
    """Show the layouts that fit a unit, ranked, and place the chosen one."""
    recommended = catalogue.recommend_layouts(doc, unit_id, limit=LAYOUTS)
    target = recommended["target"]
    rows = []
    # The model doesn't change while the rows are built: collect it once
    lookups = {}
    for rank, item in enumerate(recommended["recommendations"], start=1):
        preview = catalogue.preview_layout_in_unit(
            doc, target["id"], item["layout_id"], lookups=lookups
        )
        layout = preview["layout"]
        fit, state = windows.fit_line(preview)
        rows.append(
            {
                "preview": preview,
                "image": layout.get("image"),
                "title": "{}.  {}{}".format(
                    rank,
                    item["name"],
                    # Numbers are per folder: name the folder too
                    "   (catalogue {}, {})".format(
                        layout["catalogue"]["number"], layout["catalogue"].get("folder")
                    )
                    if layout.get("catalogue")
                    else "",
                ),
                "lines": [
                    (
                        "{}   |   {}   |   from {}, {}".format(
                            layout["program"],
                            layout["project_type"],
                            layout["source"].get("project", "?"),
                            layout["source"].get("level", "?"),
                        ),
                        "soft",
                    ),
                    (fit, state),
                    (
                        "Layout: {:,.0f} SF   ({:.1f} x {:.1f} ft)   {} items".format(
                            layout["area"], layout["width"], layout["depth"], layout["item_count"]
                        ),
                        None,
                    ),
                ],
                "right": commands.match_right(preview["match"], item["score"]),
                "match": "Match: {}".format(windows.percent(preview["match"]["similarity"])),
                "enabled": True,
                "data": item["layout_id"],
            }
        )
    if not rows:
        windows.show_message(
            "No layout fits this unit",
            [
                (
                    "{} layouts were checked. None fits Area {} ({:.1f} x {:.1f} ft).".format(
                        len(recommended["rejected"]), target["number"], target["width"], target["depth"]
                    ),
                    "bad",
                )
            ],
            commands.owner(),
        )
        return

    current = len(rows[0]["preview"]["current_items"])
    window = windows.SuggestionWindow(
        "Suggested Layouts  (the best {} that fit; {} layouts of the catalogue do not fit)".format(
            len(rows), len(recommended["rejected"])
        ),
        [
            ("Unit", "Area {}: {}".format(target["number"], target["name"])),
            ("Level", str(target["level"])),
            ("Size", "{:,.0f} SF, {:.1f} x {:.1f} ft".format(target["area"], target["width"], target["depth"])),
            ("Items in the unit now", str(current)),
        ],
        rows,
        "Pull this layout into the unit",
        "Delete the furniture that is in the unit now" if current else None,
        {
            "preview": dict(
                rows[0]["preview"],
                items=[],
                layout_outline=rows[0]["preview"]["unit"]["outline"],
            ),
            "unit": True,
            "tag": "UNIT NOW",
            "title": "Area {}: {}".format(target["number"], target["name"]),
            "lines": [
                "Level {}".format(target["level"]),
                "Area: {:,.0f} SF   ({:.1f} x {:.1f} ft)".format(
                    target["area"], target["width"], target["depth"]
                ),
                "{} items in the unit now (grey). Same plan scale as the layouts below.".format(
                    current
                ),
            ],
            "right": [],
        },
        commands.owner(),
    )
    layout_id = window.show()
    if layout_id is not None:
        commands.place(target, layout_id, window.option)


if __name__ == "__main__":
    document = revit.doc
    # A selected unit (its Area or data silo) is the target. It does not have
    # to be harvested: the target only receives a layout.
    target_id = commands.selected_target_id()
    if target_id is None:
        index = commands.UnitIndex()
        uid = index.uid_of_selection()
        if uid:
            target_id = index.by_uid[uid]["id"]

    if target_id is None:
        windows.show_message(
            "Select a unit first",
            [
                ("The selection is not in one unit.", "bad"),
                (
                    "Select a unit's Area or data silo, or objects that are in one unit.",
                    None,
                ),
            ],
            commands.owner(),
        )
    elif not catalogue.list_layouts()["layouts"]:
        windows.show_message(
            "The catalogue is empty",
            [("Harvest a layout first: Harvest Layout or Harvest All Units.", None)],
            commands.owner(),
        )
    else:
        pull_into_unit(document, target_id)
