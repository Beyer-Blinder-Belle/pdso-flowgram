# -*- coding: utf-8 -*-
"""Shared steps of the Unit Layout Catalogue buttons (no window code)."""

from pyrevit import DB, HOST_APP, revit
from rpw.utils.dotnet import List

from pdso.code.business.errors import PDSOError
from pdso.code.business.revit_helpers import id_value, to_element_id
from pdso.code.catalogue import catalogue_api_service as catalogue
from pdso.code.catalogue import catalogue_windows as windows
from pdso.code.catalogue import revit_layout_service as layout_service
from pdso.code.catalogue.layout_geometry import point_in_polygon


def owner():
    """Window handle of Revit, so that a window stays in front of it."""
    try:
        return HOST_APP.uiapp.MainWindowHandle
    except Exception:
        return None


def _plan_point(element):
    """(x, y, level id or None) of an element, or None."""
    location = element.Location
    if isinstance(location, DB.LocationPoint):
        point = location.Point
    elif isinstance(location, DB.LocationCurve):
        point = location.Curve.Evaluate(0.5, True)
    else:
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        point = (box.Min + box.Max) * 0.5
    level_id = None
    try:
        if isinstance(element, DB.FamilyInstance):
            level_id = id_value(layout_service._instance_level_id(element))
        else:
            level_id = id_value(element.LevelId)
    except Exception:
        level_id = None
    if level_id is not None and level_id < 0:
        level_id = None
    return (point.X, point.Y, level_id)


def selected_target_id():
    """Element id of the one selected Room or Area, or None.

    A Room or an Area (a unit) can get a layout. It does not have to be in the
    catalogue.
    """
    found = []
    for element_id in revit.uidoc.Selection.GetElementIds():
        element = revit.doc.GetElement(element_id)
        if isinstance(element, (DB.Architecture.Room, DB.Area)) and element.Area > 0:
            found.append(id_value(element.Id))
    return found[0] if len(found) == 1 else None


class RoomIndex(object):
    """The Rooms of the model with their plan data, read one time."""

    def __init__(self):
        self.rooms = catalogue.rooms_overview(revit.doc)
        counts = {}
        for room in self.rooms:
            counts[room["number"]] = counts.get(room["number"], 0) + 1
        # A number that two Rooms have cannot select a Room
        self.by_number = dict(
            (room["number"], room) for room in self.rooms if counts[room["number"]] == 1
        )

    def number_of_selection(self):
        """Number of the Room of the selection, or "".

        The selection can be the Room itself, or objects that are in the Room
        (furniture, a Room tag, a door). All of them must be in one Room.
        """
        numbers = []
        for element_id in revit.uidoc.Selection.GetElementIds():
            element = revit.doc.GetElement(element_id)
            if element is None:
                continue
            number = None
            if isinstance(element, DB.Architecture.Room):
                number = element.Number
            else:
                tagged = getattr(element, "Room", None)
                if isinstance(tagged, DB.Architecture.Room):
                    number = tagged.Number
            if number is None:
                located = _plan_point(element)
                if located is None:
                    continue
                x, y, level_id = located
                found = [
                    room["number"]
                    for room in self.rooms
                    if (level_id is None or room["level_id"] == level_id)
                    and point_in_polygon((x, y), room["outline"])
                ]
                if len(found) != 1:
                    continue
                number = found[0]
            if number not in numbers:
                numbers.append(number)
        if len(numbers) == 1 and numbers[0] in self.by_number:
            return numbers[0]
        return ""

    def describe(self, number):
        """Data of the Room with this number for a window, or None."""
        room = self.by_number.get(number)
        if room is None:
            return None
        described = dict(room)
        described["items"] = room["item_count"]
        return described

    def picker_rows(self):
        rows = []
        for room in self.rooms:
            rows.append(
                {
                    "key": room["number"],
                    "values": [
                        room["number"],
                        room["name"],
                        str(room["level"]),
                        "{:,.0f} SF".format(room["area"]),
                        str(room["item_count"]),
                    ],
                    "thumb": (
                        lambda width, height, r=room: windows.sketch_canvas(
                            r["outline"], width, height, r["boxes"]
                        )
                    ),
                    "data": room,
                }
            )
        return rows


def place(room, layout_id, clear_first):
    """Place a layout, select the placed elements, show the result."""
    doc = revit.doc
    try:
        result = catalogue.place_layout(doc, room["id"], layout_id, clear_first)
    except PDSOError as error:
        windows.show_message("The layout was not placed", [(str(error), "bad")], owner())
        return None
    deleted = result["deleted"]
    ids = List[type(to_element_id("1"))]()
    for element_id in result["placed"]:
        ids.Add(to_element_id(str(element_id)))
    revit.uidoc.Selection.SetElementIds(ids)
    missing = sum(result["missing"].values())
    lines = [
        (
            "{} items placed in Room {} ({}). They are selected.".format(
                len(result["placed"]), room["number"], result["placement"]["description"]
            ),
            "good",
        )
    ]
    if clear_first:
        lines.append(("{} items that were in the Room were deleted first.".format(deleted), None))
    if missing:
        names = ", ".join(sorted(result["missing"]))
        lines.append(
            ("{} items not placed. Family not loaded: {}".format(missing, names), "warn")
        )
    for warning in result["warnings"][:3]:
        lines.append((warning, "warn"))
    if result["tagged_programs"]:
        lines.append(
            ("The layout id is on {} PDSO data silo(s).".format(len(result["tagged_programs"])), None)
        )
    windows.show_message("Layout placed", lines, owner())
    return result


def match_right(match, score=None):
    lines = [
        "Area match: {}".format(windows.percent(match["area_match"])),
        "Shape match: {}".format(windows.percent(match["shape_match"])),
        "similarity {:.3f}".format(match["similarity"]),
    ]
    if score is not None:
        lines[2] = "fit score {:.3f}".format(score)
    return lines
