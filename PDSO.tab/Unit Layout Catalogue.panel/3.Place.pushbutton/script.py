# -*- coding: utf-8 -*-
"""Place: show the catalogue layouts for a Room, ranked, and place the chosen one."""

import sys

from pyrevit import revit, script

# pyRevit keeps imported library modules in memory. Drop the catalogue modules,
# so that this button uses the code that is on disk now (no pyRevit reload).
for _name in [name for name in sys.modules if name.startswith("pdso.code.catalogue")]:
    del sys.modules[_name]

from pdso.code.catalogue import catalogue_api_service as catalogue  # noqa: E402
from pdso.code.catalogue import catalogue_commands as commands  # noqa: E402
from pdso.code.catalogue import catalogue_windows as windows  # noqa: E402

LAYOUTS = 5

if __name__ == "__main__":
    doc = revit.doc
    rooms = commands.RoomIndex()
    picked = windows.PickerWindow(
        "Place Layout",
        "Choose the Room that gets a layout. A selection in the model selects its Room here.",
        [("Number", 0.8), ("Name", 2.6), ("Level", 1.6), ("Area", 1.0), ("Items now", 0.8)],
        rooms.picker_rows(),
        "Show layouts",
        rooms.number_of_selection(),
        commands.owner(),
    ).show()
    if picked is None:
        script.exit()
    number = picked["id"]
    recommended = catalogue.recommend_layouts(doc, number, limit=LAYOUTS)
    target = recommended["target"]
    rows = []
    for rank, item in enumerate(recommended["recommendations"], start=1):
        preview = catalogue.preview_layout_in_room(doc, target["id"], item["layout_id"])
        layout = preview["layout"]
        fit, state = windows.fit_line(preview)
        rows.append(
            {
                "preview": preview,
                "title": "{}.  {}".format(rank, item["name"]),
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
        script.exit()

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
        "Place this layout",
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
