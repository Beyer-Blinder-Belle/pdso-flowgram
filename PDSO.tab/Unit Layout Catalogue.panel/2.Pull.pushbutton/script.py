# -*- coding: utf-8 -*-
"""Pull from Catalogue: put a catalogue layout into the selected Room.

The button needs a Room to compare the layouts with: select a Room, an Area,
or objects that are in one Room. It is not available with no selection. The
window shows the Room as it is now and the layouts that fit it, ranked. The
Room does not have to be harvested.
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


def pull_into_room(doc, room_id):
    """Show the layouts that fit a Room, ranked, and place the chosen one."""
    recommended = catalogue.recommend_layouts(doc, room_id, limit=LAYOUTS)
    target = recommended["target"]
    rows = []
    for rank, item in enumerate(recommended["recommendations"], start=1):
        preview = catalogue.preview_layout_in_room(doc, target["id"], item["layout_id"])
        layout = preview["layout"]
        fit, state = windows.fit_line(preview)
        rows.append(
            {
                "preview": preview,
                "image": layout.get("image"),
                "title": "{}.  {}{}".format(
                    rank,
                    item["name"],
                    "   (catalogue {})".format(layout["catalogue"]["number"])
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
            "No layout fits this Room",
            [
                (
                    "{} layouts were checked. None fits Room {} ({:.1f} x {:.1f} ft).".format(
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
            ("Room", "{}: {}".format(target["number"], target["name"])),
            ("Level", str(target["level"])),
            ("Size", "{:,.0f} SF, {:.1f} x {:.1f} ft".format(target["area"], target["width"], target["depth"])),
            ("Items in the Room now", str(current)),
        ],
        rows,
        "Pull this layout into the Room",
        "Delete the furniture that is in the Room now" if current else None,
        {
            "preview": dict(
                rows[0]["preview"],
                items=[],
                layout_outline=rows[0]["preview"]["room"]["outline"],
            ),
            "room": True,
            "tag": "ROOM NOW",
            "title": "Room {}: {}".format(target["number"], target["name"]),
            "lines": [
                "Level {}".format(target["level"]),
                "Area: {:,.0f} SF   ({:.1f} x {:.1f} ft)".format(
                    target["area"], target["width"], target["depth"]
                ),
                "{} items in the Room now (grey). Same plan scale as the layouts below.".format(
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
    # A selected Room or Area is the target. It does not have to be harvested:
    # the target only receives a layout.
    target_id = commands.selected_target_id()
    if target_id is None:
        index = commands.RoomIndex()
        number = index.number_of_selection()
        if number:
            target_id = index.by_number[number]["id"]

    if target_id is None:
        windows.show_message(
            "Select a Room first",
            [
                ("The selection is not in one Room.", "bad"),
                ("Select a Room, an Area, or objects that are in one Room.", None),
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
        pull_into_room(document, target_id)
