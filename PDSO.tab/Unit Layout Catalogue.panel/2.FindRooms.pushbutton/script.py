# -*- coding: utf-8 -*-
"""Find Rooms: show the Rooms that are similar to a layout, and place it in one."""

import sys

from pyrevit import revit, script

# pyRevit keeps imported library modules in memory. Drop the catalogue modules,
# so that this button uses the code that is on disk now (no pyRevit reload).
for _name in [name for name in sys.modules if name.startswith("pdso.code.catalogue")]:
    del sys.modules[_name]

from pdso.code.catalogue import catalogue_api_service as catalogue  # noqa: E402
from pdso.code.catalogue import catalogue_commands as commands  # noqa: E402
from pdso.code.catalogue import catalogue_windows as windows  # noqa: E402

ROOMS = 5

if __name__ == "__main__":
    doc = revit.doc
    layouts = catalogue.list_layouts()["layouts"]
    if not layouts:
        windows.show_message(
            "The catalogue is empty", [("Harvest a layout first.", None)], commands.owner()
        )
        script.exit()
    # Newest harvest first
    layouts.sort(key=lambda item: (item["source"].get("harvested", ""), item["id"]), reverse=True)
    sketches = catalogue.layout_sketches()
    layout = windows.PickerWindow(
        "Find Rooms",
        "Choose the layout. The newest is first.",
        [
            ("Layout", 2.6),
            ("Program", 1.5),
            ("Project type", 1.0),
            ("Size", 1.5),
            ("Items", 0.5),
            ("Harvested", 1.2),
            ("Source", 1.6),
        ],
        [
            {
                "values": [
                    item["name"],
                    item["program"],
                    item["project_type"],
                    "{:,.0f} SF, {:.0f} x {:.0f} ft".format(
                        item["area"], item["width"], item["depth"]
                    ),
                    str(item["item_count"]),
                    item["source"].get("harvested", ""),
                    "{}, {}".format(
                        item["source"].get("project", ""), item["source"].get("level", "")
                    ),
                ],
                "thumb": (
                    lambda width, height, sketch=sketches.get(item["id"], {}): (
                        windows.sketch_canvas(
                            sketch.get("outline"), width, height, None, sketch.get("points")
                        )
                    )
                ),
                "data": item,
            }
            for item in layouts
        ],
        "Find similar Rooms",
        None,
        commands.owner(),
    ).show()
    if layout is None:
        script.exit()

    similar = catalogue.find_similar_rooms(doc, layout["id"], limit=ROOMS + 1)
    rooms = [room for room in similar["rooms"] if not room["is_source"]][:ROOMS]
    rows = []
    for rank, room in enumerate(rooms, start=1):
        preview = catalogue.preview_layout_in_room(doc, room["id"], layout["id"])
        fit, state = windows.fit_line(preview)
        rows.append(
            {
                "preview": preview,
                "title": "{}.  Room {}: {}".format(rank, room["number"], room["name"]),
                "lines": [
                    ("Level {}".format(room["level"]), "soft"),
                    (fit, state),
                    (
                        "Area: {:,.0f} SF   ({:.1f} x {:.1f} ft)   {} items in the Room now".format(
                            room["area"],
                            preview["room"]["width"],
                            preview["room"]["depth"],
                            len(preview["current_items"]),
                        ),
                        None,
                    ),
                ],
                "right": commands.match_right(preview["match"]),
                "match": "Match: {}".format(windows.percent(preview["match"]["similarity"])),
                "enabled": preview["match"]["fits"],
                "data": preview["room"],
            }
        )
    if not rows:
        windows.show_message(
            "No Room to compare", [("This model has no other placed Room.", None)], commands.owner()
        )
        script.exit()

    source = layout["source"]
    window = windows.SuggestionWindow(
        "Suggested Rooms  (best {} of {} Rooms)".format(len(rows), similar["rooms_checked"]),
        [
            ("Layout", layout["name"]),
            ("Program", layout["program"]),
            ("Project type", layout["project_type"]),
            ("Size", "{:,.0f} SF, {:.1f} x {:.1f} ft".format(layout["area"], layout["width"], layout["depth"])),
            ("Items", str(layout["item_count"])),
            ("Source", "{}, {}".format(source.get("project", "?"), source.get("level", "?"))),
        ],
        rows,
        "Place layout in this Room",
        "Delete the furniture that is in the Room now",
        {
            "preview": rows[0]["preview"],
            "room": False,
            "tag": "SOURCE",
            "title": "{}  (the Room that the layout came from)".format(layout["name"]),
            "lines": [
                "{}   |   {}   |   from {}, {}".format(
                    layout["program"],
                    layout["project_type"],
                    source.get("project", "?"),
                    source.get("level", "?"),
                ),
                "Area: {:,.0f} SF   ({:.1f} x {:.1f} ft)   {} items in the layout".format(
                    layout["area"], layout["width"], layout["depth"], layout["item_count"]
                ),
                "Same plan scale as the Rooms below. Shown in the orientation of Room {}.".format(
                    rows[0]["data"]["number"]
                ),
            ],
            "right": ["harvested", source.get("harvested", "?")],
        },
        commands.owner(),
    )
    room = window.show()
    if room is not None:
        commands.place(room, layout["id"], window.option)
