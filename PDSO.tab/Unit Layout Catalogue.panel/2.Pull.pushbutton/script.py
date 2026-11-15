# -*- coding: utf-8 -*-
"""Pull from Catalogue: put a catalogue layout into a Room.

One operation with two ways to start:
    - from a Room: the layouts that fit the Room, ranked. This is the start
      if a Room, or objects in one Room, are selected.
    - from a layout: the Rooms of the model that the layout suits, ranked.
With no selection, the Room table shows first; a button there starts from a
layout. The two ways end in the same placement.
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
ROOMS = 5


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


def pull_layout(doc):
    """Choose a layout, show the Rooms that it suits, ranked, and place it in one."""
    layouts = catalogue.list_layouts()["layouts"]
    if not layouts:
        windows.show_message(
            "The catalogue is empty", [("Harvest a layout first.", None)], commands.owner()
        )
        return
    # Newest harvest first
    layouts.sort(key=lambda item: (item["source"].get("harvested", ""), item["id"]), reverse=True)
    sketches = catalogue.layout_sketches()
    layout = windows.PickerWindow(
        "Pull from Catalogue",
        "Choose the layout to pull. The newest is first.",
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
        "Show the Rooms it suits",
        None,
        commands.owner(),
    ).show()
    if layout is None:
        return

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
        return

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
        "Pull the layout into this Room",
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


if __name__ == "__main__":
    document = revit.doc
    if not catalogue.list_layouts()["layouts"]:
        windows.show_message(
            "The catalogue is empty",
            [("Harvest a layout first: Harvest Layout or Harvest All Units.", None)],
            commands.owner(),
        )
    else:
        # A selected Room or Area is the target. It does not have to be
        # harvested: the target only receives a layout.
        target_id = commands.selected_target_id()
        index = None
        if target_id is None:
            index = commands.RoomIndex()
            number = index.number_of_selection()
            if number:
                target_id = index.by_number[number]["id"]
        if target_id is not None:
            pull_into_room(document, target_id)
        else:
            picker = windows.PickerWindow(
                "Pull from Catalogue",
                "Choose the Room that gets a layout. Or start from a layout.",
                [
                    ("Number", 0.8),
                    ("Name", 2.6),
                    ("Level", 1.6),
                    ("Area", 1.0),
                    ("Items now", 0.8),
                ],
                index.picker_rows(),
                "Show the layouts that fit",
                None,
                commands.owner(),
                "Start from a layout",
            )
            picked = picker.show()
            if picker.alternate:
                pull_layout(document)
            elif picked is not None:
                pull_into_room(document, picked["id"])
