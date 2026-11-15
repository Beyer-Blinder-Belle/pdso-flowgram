# -*- coding: utf-8 -*-
"""Unit grouping: find the dwelling / guest-room units of a model from its Rooms.

PROTOTYPE (lodging). Read only: nothing in the model is changed.

A unit is a set of Rooms that doors connect to each other without going
through shared space (corridors, shafts, stairs, ...). For each unit the
service also lists its entrances (doors to shared space or outside) and its
windows.

Which Rooms a door or window connects is found from geometry, not from
Revit's FromRoom/ToRoom: those come back empty when the openings and the Rooms
are in different phases. A point is probed a short distance on each side of
the opening, along its facing direction, and tested against the Room outlines
on the same level.

All lengths are decimal feet and all areas are square feet (Revit internal units).
"""

# Python standard libraries
import codecs
import os
from collections import OrderedDict
from datetime import datetime

# 3rd Party Modules
from pyrevit import DB, script

# Custom Modules
from pdso.code.business.revit_helpers import id_value, point_in_polygon
from pdso.code.catalogue.catalogue_store import to_json
from pdso.code.catalogue.revit_layout_service import get_outline

logger = script.get_logger()

FORMAT = "pdso-unit-harvest"
FORMAT_VERSION = 1

# Room names (lower case) that are shared space and never part of a unit.
# Matched as whole names or as a leading word ("Corridor 2", "Stair A").
DEFAULT_SHARED_NAMES = (
    "corridor",
    "corredor",
    "hall",
    "hallway",
    "lobby",
    "stair",
    "staircase",
    "escada",
    "elevator",
    "lift",
    "shaft",
    "chase",
    "circulation",
    "circulação",
    "vestibule",
)

# How far from the opening centre to probe on each side (ft). Walls in the
# sample models are about 0.5 ft thick; thicker walls need a larger value.
DEFAULT_PROBE_DISTANCE = 0.75

# Units whose centres are closer than this in Y are on the same row when
# numbering (ft).
ROW_TOLERANCE = 8.0


# ---------------------------------------------------------------------------
# Reading the model
# ---------------------------------------------------------------------------


def _text(value):
    return value if value else ""


def _level_name(doc, element):
    level_id = element.LevelId
    if level_id is None or level_id == DB.ElementId.InvalidElementId:
        return ""
    level = doc.GetElement(level_id)
    return level.Name if level else ""


def _room_name(room):
    parameter = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
    return _text(parameter.AsString()) if parameter else ""


def _read_rooms(doc):
    rooms = []
    collector = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
    )
    for room in collector:
        outline = get_outline(room)
        if len(outline) < 3:
            continue
        rooms.append(
            OrderedDict(
                [
                    ("id", id_value(room.Id)),
                    ("name", _room_name(room)),
                    ("number", _text(room.Number)),
                    ("level", _level_name(doc, room)),
                    ("area_sf", round(room.Area, 3)),
                    ("outline", [[round(x, 4), round(y, 4)] for x, y in outline]),
                ]
            )
        )
    return rooms


def _width(instance):
    for owner in (instance.Symbol, instance):
        for built_in in (
            DB.BuiltInParameter.DOOR_WIDTH,
            DB.BuiltInParameter.WINDOW_WIDTH,
            DB.BuiltInParameter.FAMILY_WIDTH_PARAM,
        ):
            parameter = owner.get_Parameter(built_in)
            if parameter and parameter.HasValue:
                return round(parameter.AsDouble(), 4)
    return None


def _read_openings(doc, category):
    openings = []
    collector = (
        DB.FilteredElementCollector(doc)
        .OfCategory(category)
        .WhereElementIsNotElementType()
    )
    for instance in collector:
        if not isinstance(instance, DB.FamilyInstance):
            continue
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        symbol = instance.Symbol
        type_name = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        mark = instance.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        facing = instance.FacingOrientation
        point = location.Point
        openings.append(
            OrderedDict(
                [
                    ("id", id_value(instance.Id)),
                    ("family", _text(symbol.FamilyName)),
                    ("type", _text(type_name.AsString()) if type_name else ""),
                    ("mark", _text(mark.AsString()) if mark else ""),
                    ("level", _level_name(doc, instance)),
                    ("x", round(point.X, 4)),
                    ("y", round(point.Y, 4)),
                    ("facing", [round(facing.X, 4), round(facing.Y, 4)]),
                    ("width_ft", _width(instance)),
                    ("host_id", id_value(instance.Host.Id) if instance.Host else None),
                ]
            )
        )
    return openings


def _read_walls(doc):
    """Wall location lines, for drawing the plan preview only."""
    walls = []
    for wall in DB.FilteredElementCollector(doc).OfClass(DB.Wall):
        location = wall.Location
        if not isinstance(location, DB.LocationCurve):
            continue
        points = [[round(p.X, 4), round(p.Y, 4)] for p in location.Curve.Tessellate()]
        walls.append(
            OrderedDict(
                [
                    ("id", id_value(wall.Id)),
                    ("level", _level_name(doc, wall)),
                    ("points", points),
                    ("width_ft", round(wall.Width, 4)),
                ]
            )
        )
    return walls


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _is_shared(name, shared_names):
    text = name.strip().lower()
    if text in shared_names:
        return True
    first_word = text.split(" ")[0] if text else ""
    return first_word in shared_names


class _RoomIndex(object):
    """Rooms by level, to find the Room at a point."""

    def __init__(self, rooms):
        self.by_level = {}
        for room in rooms:
            self.by_level.setdefault(room["level"], []).append(room)

    def room_at(self, level, x, y):
        for room in self.by_level.get(level, []):
            if point_in_polygon(x, y, room["outline"]):
                return room["id"]
        return None


def _sides(index, opening, probe):
    """Room ids on the facing side and on the back side of an opening."""
    fx, fy = opening["facing"]
    x, y, level = opening["x"], opening["y"], opening["level"]
    front = index.room_at(level, x + fx * probe, y + fy * probe)
    back = index.room_at(level, x - fx * probe, y - fy * probe)
    return front, back


class _UnionFind(object):
    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _centroid(points):
    count = float(len(points))
    return (
        sum(p[0] for p in points) / count,
        sum(p[1] for p in points) / count,
    )


def _level_order(doc):
    levels = DB.FilteredElementCollector(doc).OfClass(DB.Level)
    ordered = sorted(levels, key=lambda level: level.Elevation)
    return dict((level.Name, i) for i, level in enumerate(ordered))


def _number_units(units, level_order):
    """Give units ids like "<level>-U01": rows north to south, then west to east."""
    by_level = OrderedDict()
    for unit in sorted(units, key=lambda u: level_order.get(u["level"], 0)):
        by_level.setdefault(unit["level"], []).append(unit)

    for level, members in by_level.items():
        members.sort(key=lambda u: -u["center"][1])
        rows, row = [], []
        for unit in members:
            if row and abs(row[-1]["center"][1] - unit["center"][1]) > ROW_TOLERANCE:
                rows.append(row)
                row = []
            row.append(unit)
        if row:
            rows.append(row)
        number = 0
        for row in rows:
            for unit in sorted(row, key=lambda u: u["center"][0]):
                number += 1
                unit["uid"] = "{}-U{:02d}".format(level, number)


def _opening_record(opening, room, other_name):
    record = OrderedDict(
        [
            ("id", opening["id"]),
            ("family", opening["family"]),
            ("type", opening["type"]),
            ("mark", opening["mark"]),
            ("width_ft", opening["width_ft"]),
            ("x", opening["x"]),
            ("y", opening["y"]),
            ("room", room["name"]),
            ("room_id", room["id"]),
        ]
    )
    if other_name is not None:
        record["opens_to"] = other_name
    return record


def group_units(rooms, doors, windows, shared_names=None, probe=None, level_order=None):
    """Group Rooms into units and attach doors and windows. Pure Python.

    Args:
        rooms, doors, windows (list[dict]): As read by harvest_units.
        shared_names (iterable[str]): Room names that are shared space.
        probe (float): Probe distance on each side of an opening (ft).
        level_order (dict): {level name: sort index}.

    Returns:
        dict: units, shared_rooms, shared_openings, unresolved_openings
    """
    shared_names = set(n.lower() for n in (shared_names or DEFAULT_SHARED_NAMES))
    probe = probe or DEFAULT_PROBE_DISTANCE
    level_order = level_order or {}

    rooms_by_id = dict((room["id"], room) for room in rooms)
    shared_ids = set(r["id"] for r in rooms if _is_shared(r["name"], shared_names))
    index = _RoomIndex(rooms)

    def side_kind(room_id):
        if room_id is None:
            return "outside"
        return "shared" if room_id in shared_ids else "unit"

    groups = _UnionFind()
    for room in rooms:
        if room["id"] not in shared_ids:
            groups.find(room["id"])

    for opening in doors + windows:
        opening["sides"] = list(_sides(index, opening, probe))
    for door in doors:
        front, back = door["sides"]
        if side_kind(front) == "unit" and side_kind(back) == "unit":
            groups.union(front, back)

    members_by_root = OrderedDict()
    for room in rooms:
        if room["id"] not in shared_ids:
            members_by_root.setdefault(groups.find(room["id"]), []).append(room)

    unit_of_room = {}
    units = []
    for members in members_by_root.values():
        member_ids = set(r["id"] for r in members)
        largest = max(members, key=lambda r: r["area_sf"])
        unit = OrderedDict(
            [
                ("uid", ""),
                ("level", members[0]["level"]),
                ("center", [round(v, 3) for v in _centroid(largest["outline"])]),
                ("area_sf", round(sum(r["area_sf"] for r in members), 3)),
                ("room_count", len(members)),
                ("rooms", [
                    OrderedDict([("id", r["id"]), ("name", r["name"]),
                                 ("number", r["number"]), ("area_sf", r["area_sf"])])
                    for r in sorted(members, key=lambda r: r["name"])
                ]),
                ("entrances", []),
                ("interior_doors", []),
                ("windows", []),
                ("issues", []),
            ]
        )
        for room_id in member_ids:
            unit_of_room[room_id] = unit
        units.append(unit)

    shared_openings, unresolved = [], []
    for kind, openings in (("door", doors), ("window", windows)):
        for opening in openings:
            front, back = opening["sides"]
            unit = unit_of_room.get(front) or unit_of_room.get(back)
            if unit is None:
                if front is None and back is None:
                    unresolved.append(OrderedDict([("id", opening["id"]), ("kind", kind)]))
                else:
                    shared_openings.append(OrderedDict([("id", opening["id"]), ("kind", kind)]))
                continue
            inside_id = front if unit_of_room.get(front) is unit else back
            other_id = back if inside_id == front else front
            other_name = "outside" if other_id is None else rooms_by_id[other_id]["name"]
            if kind == "window":
                unit["windows"].append(
                    _opening_record(opening, rooms_by_id[inside_id], other_name)
                )
            elif unit_of_room.get(other_id) is unit:
                unit["interior_doors"].append(
                    _opening_record(opening, rooms_by_id[inside_id], other_name)
                )
            else:
                unit["entrances"].append(
                    _opening_record(opening, rooms_by_id[inside_id], other_name)
                )

    for unit in units:
        if len(unit["entrances"]) != 1:
            unit["issues"].append("{} entrances".format(len(unit["entrances"])))
        if unit["room_count"] == 1 and not unit["interior_doors"]:
            unit["issues"].append("single room with no interior door")

    _number_units(units, level_order)
    units.sort(key=lambda u: (level_order.get(u["level"], 0), u["uid"]))

    return OrderedDict(
        [
            ("units", units),
            ("shared_rooms", [
                OrderedDict([("id", r["id"]), ("name", r["name"]), ("level", r["level"])])
                for r in rooms if r["id"] in shared_ids
            ]),
            ("shared_openings", shared_openings),
            ("unresolved_openings", unresolved),
        ]
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def harvest_units(doc, shared_names=None, probe=None, with_geometry=True):
    """Read the Rooms, doors and windows of a model and group them into units.

    Args:
        doc: Revit project document.
        shared_names (iterable[str]): Room names that are shared space.
        probe (float): Probe distance on each side of an opening (ft).
        with_geometry (bool): Also return Room outlines, walls and openings,
            for drawing a preview.

    Returns:
        OrderedDict: format, model, summary, units, ... (see group_units)
    """
    rooms = _read_rooms(doc)
    doors = _read_openings(doc, DB.BuiltInCategory.OST_Doors)
    windows = _read_openings(doc, DB.BuiltInCategory.OST_Windows)
    logger.debug(
        "Unit harvest: {} rooms, {} doors, {} windows".format(
            len(rooms), len(doors), len(windows)
        )
    )

    grouped = group_units(
        rooms, doors, windows, shared_names, probe, level_order=_level_order(doc)
    )
    units = grouped["units"]

    result = OrderedDict(
        [
            ("format", FORMAT),
            ("version", FORMAT_VERSION),
            ("model", doc.Title),
            ("harvested", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("units_of_measure", "feet / square feet"),
            ("summary", OrderedDict(
                [
                    ("units", len(units)),
                    ("rooms_in_units", sum(u["room_count"] for u in units)),
                    ("shared_rooms", len(grouped["shared_rooms"])),
                    ("entrances", sum(len(u["entrances"]) for u in units)),
                    ("windows_in_units", sum(len(u["windows"]) for u in units)),
                    ("units_to_check", sum(1 for u in units if u["issues"])),
                    ("unresolved_openings", len(grouped["unresolved_openings"])),
                ]
            )),
        ]
    )
    result.update(grouped)
    if with_geometry:
        result["geometry"] = OrderedDict(
            [("rooms", rooms), ("walls", _read_walls(doc)), ("doors", doors), ("windows", windows)]
        )
    return result


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------

PREVIEW_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
    "unit_preview.html",
)


def write_outputs(result, json_path):
    """Write the harvest as JSON, and an HTML preview next to it.

    Returns:
        tuple: (json path, html path)
    """
    text = to_json(result)
    with codecs.open(json_path, "w", encoding="utf-8") as stream:
        stream.write(text)

    html_path = os.path.splitext(json_path)[0] + ".html"
    with codecs.open(PREVIEW_TEMPLATE, "r", encoding="utf-8") as stream:
        template = stream.read()
    # The JSON text is pure ASCII (see to_json); "</" must not close the script tag
    with codecs.open(html_path, "w", encoding="utf-8") as stream:
        stream.write(template.replace("__DATA__", text.replace("</", "<\\/")))
    return json_path, html_path
