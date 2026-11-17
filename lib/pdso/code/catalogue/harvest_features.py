# -*- coding: utf-8 -*-
"""Harvest step: what a unit is like, around and inside its layout.

The unit is an apartment drawn as an Area (a single Room also works). A layout
only transfers to a space like the one it came from: the entry on the same
side, the windows on the same side, the same shape. This module reads those
features so the Recommend step can compare them, and reads what is inside the
unit (partitions, doors, rooms) so the Create step can rebuild it. The feature list follows Oliver Green, "An Introspective Approach to
Apartment Design" (AHMM, 2020), which names them as what designers match on:

    entry location          -> features["entry"]
    external wall location  -> features["edges"], kind "exterior"
    amount of window wall   -> features["window_wall_length"], ["glazed_length"]
    overall shape           -> features["shape_matrix"]
    size                    -> entry.area, features["perimeter"]
    wider context           -> features["edges"], kind "corridor" / "party"
    internal elements       -> features["internal_walls"], ["internal_doors"],
                               ["rooms"], and the entry's items
    metadata (mix, tenure)  -> features["unit"]

How an edge's kind is found: a few points are probed just beyond the wall.
    another room there, named like a corridor  -> "corridor"
    another room there                         -> "party"
    no room, wall is an Exterior or curtain wall -> "exterior"
    no room, anything else                     -> "unroomed"

Limits, so nobody is surprised:
    - A wall's Function is often left at Revit's default (Exterior), so a wall
      facing a space with no Room in it (a shaft, a closet) reads as exterior.
    - An Area's boundary is a line, not a wall. The wall is found by looking
      for a straight wall on the same level that runs along the line. Where
      none is found, an edge with a window in it is taken to be exterior.
    - Only straight walls whose base is on the unit's level are read.
    - Walls in a linked model are not read: their edges come out "unroomed"
      and their doors and windows are not found.
    - Only the outer boundary is read; holes (columns, shafts) are ignored.
"""

import math

from rpw import DB
from rpw.utils.dotnet import List

from pdso.code import defaults

from pdso.code.catalogue.layout_geometry import (
    extent_along,
    overlap_along,
    point_in_polygon,
    point_segment_distance,
    polygon_area,
    rotate,
    shape_category,
    shape_matrix,
)

# A room beyond the wall with one of these in its name is circulation
CORRIDOR_WORDS = ("corridor", "hall", "circulation", "lobby", "vestibule", "breezeway")

PROBE_DISTANCE = 1.5  # ft past the far face of the wall
PROBE_HEIGHT = 1.0  # ft above the level
# Tried in turn until a room is found: a chase or a second wall behind the
# first one must not hide the room on the far side
PROBE_STEPS = (0.0, 2.0, 4.0)  # ft added to PROBE_DISTANCE
SAMPLES = (0.25, 0.5, 0.75)  # where along an edge to probe
SHORT_EDGE = 1.0  # ft; shorter edges are probed once, at the middle
OPENING_REACH = 1.0  # ft; how far past the wall thickness an opening may sit

WALL_REACH = 1.0  # ft; how far from a wall's faces an Area boundary may run
PARALLEL = 0.087  # sine of 5 degrees

EDGE_KINDS = ("exterior", "corridor", "party", "unroomed")

# Words in a room's name that say what the room is
BEDROOM_WORDS = ("bed",)
BATHROOM_WORDS = ("bath", "wc", "toilet", "powder", "shower", "ensuite")
KITCHEN_WORDS = ("kitchen",)

PDSO_PREFIX = "PDSO - "
DATA_SILO_PARAMETER = "PDSO - Program UID"

DATA_SILO = "data silo"
PROGRAM_VOLUME = "program volume"


def pdso_kinds(doc):
    """A function that says if a family instance is a PDSO object.

    "PDSO - Program UID" is bound to the Generic Models category, so each
    generic model (a potted plant, a Program Volume) has the parameter. Thus
    the parameter alone does not make an instance a data silo.

    Returns:
        function: instance -> DATA_SILO, PROGRAM_VOLUME or None
            A Program Volume is an instance of a family whose name starts
            with the configured prefix. A data silo is an instance of the
            configured Data Silo family, or an instance that has a value in
            "PDSO - Program UID" and is not a Program Volume.
    """
    silo_family = None
    volume_prefix = defaults.PROGRAM_VOLUME_PREFIX
    try:
        from pdso.code.business.config_service import get_config

        config = get_config(doc)
        if config.get("exists"):
            silo_family = config.get("data_silo_family_name") or None
            volume_prefix = (
                config.get("program_volume_family_name_prefix") or volume_prefix
            )
    except Exception as error:
        # Say so: in a model with its own names, Program Volumes would
        # otherwise be harvested as furniture
        _warn(
            "Couldn't read the PDSO configuration ({}). Using the default Data "
            "Silo and Program Volume names.".format(error)
        )

    def kind(instance):
        try:
            family_name = instance.Symbol.Family.Name
        except Exception:
            return None
        if family_name.startswith(volume_prefix):
            return PROGRAM_VOLUME
        if silo_family is not None and family_name == silo_family:
            return DATA_SILO
        if family_name.startswith(defaults.DATA_SILO_FAMILY_PREFIX):
            return DATA_SILO
        parameter = instance.LookupParameter(DATA_SILO_PARAMETER)
        if parameter is not None and parameter.HasValue and parameter.AsString():
            return DATA_SILO
        return None

    return kind


def _warn(message):
    try:
        from pyrevit import script

        script.get_logger().warning(message)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(message)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def id_value(element_id):
    """ElementId as a number, on Revit versions before and after 2024."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def type_name(symbol):
    """Type name of a FamilySymbol, read through its parameter for safety."""
    parameter = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    if parameter is not None and parameter.HasValue:
        return parameter.AsString()
    return DB.Element.Name.GetValue(symbol)


def _built_in(name):
    """A BuiltInParameter by name, or None if this Revit version lacks it."""
    return getattr(DB.BuiltInParameter, name, None)


def _length_parameter(instance, names):
    """First of the named length parameters found on the instance or its type."""
    for owner in (instance, instance.Symbol):
        for name in names:
            built_in = _built_in(name)
            if built_in is None:
                continue
            parameter = owner.get_Parameter(built_in)
            if parameter is not None and parameter.HasValue:
                return parameter.AsDouble()
    return 0.0


def _measured_width(instance, direction):
    """Width of a window along its wall, measured from its geometry (ft).

    A width parameter is not always the whole window: a ribbon of several
    lights can carry the width of one. Returns 0.0 when it can't be measured.
    """
    box = instance.get_BoundingBox(None)
    if box is None:
        return 0.0
    width = extent_along(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y, direction)
    return width or 0.0


def _wall_label(wall):
    """ "Family : Type" of a wall, or "" when there is no wall."""
    if wall is None:
        return ""
    try:
        return "{} : {}".format(wall.WallType.FamilyName, wall.Name)
    except Exception:
        return ""


def _room_name(room):
    parameter = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
    if parameter is None or not parameter.HasValue:
        return ""
    return parameter.AsString() or ""


def _room_phase(spatial_element):
    """The phase a Room belongs to, or None (Areas have no phase)."""
    if not isinstance(spatial_element, DB.Architecture.Room):
        return None
    parameter = spatial_element.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
    if parameter is None or not parameter.HasValue:
        return None
    return spatial_element.Document.GetElement(parameter.AsElementId())


def _room_at(doc, x, y, z, phase):
    point = DB.XYZ(x, y, z)
    try:
        if phase is not None:
            return doc.GetRoomAtPoint(point, phase)
        return doc.GetRoomAtPoint(point)
    except Exception:
        return None


def _has_word(name, words):
    lowered = (name or "").lower()
    for word in words:
        if word in lowered:
            return True
    return False


def _is_corridor(name):
    return _has_word(name, CORRIDOR_WORDS)


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------


def boundary_edges(spatial_element):
    """The outer boundary of a Room or Area as straight edges, in order.

    Curved segments are split into short straight edges. Each edge keeps the
    id of the element that bounds it (a wall, a separation line, an area
    boundary line).

    Returns:
        list: (start, end, element_id), points as (x, y) in project feet.
            Empty if the element is unplaced or not enclosed.
    """
    if spatial_element.Location is None or spatial_element.Area <= 0:
        return []

    loops = spatial_element.GetBoundarySegments(DB.SpatialElementBoundaryOptions())
    if not loops or loops.Count == 0:
        return []

    edges = []
    # Revit returns the outer loop first
    for segment in loops[0]:
        points = list(segment.GetCurve().Tessellate())
        for i in range(len(points) - 1):
            edges.append(
                (
                    (points[i].X, points[i].Y),
                    (points[i + 1].X, points[i + 1].Y),
                    segment.ElementId,
                )
            )
    return edges


def _outward_normal(start, end, counter_clockwise):
    """Unit vector pointing out of the room, square to an edge."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (0.0, 0.0)
    if counter_clockwise:
        return (dy / length, -dx / length)
    return (-dy / length, dx / length)


def _far_face(start, end, normal, stack):
    """How far beyond the edge the outermost wall face of a stack lies (ft)."""
    middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    far = 0.0
    for wall, a, _b in stack:
        offset = (a[0] - middle[0]) * normal[0] + (a[1] - middle[1]) * normal[1]
        far = max(far, offset + wall.Width / 2.0)
    return far


def _classify_edge(doc, spatial_element, start, end, normal, stack, z, phase):
    """Kind of one edge and the name of the room beyond it.

    Args:
        stack (list): (wall, start, end) for every wall layered along the edge.
    """
    is_curtain = any(w.WallType.Kind == DB.WallKind.Curtain for w, _, _ in stack)
    # The thickest wall is the real one; a finish layered on it does not count
    main = max(stack, key=lambda item: item[0].Width)[0] if stack else None
    is_exterior_wall = is_curtain or (
        main is not None and main.WallType.Function == DB.WallFunction.Exterior
    )
    reach = _far_face(start, end, normal, stack) + PROBE_DISTANCE

    length = math.hypot(end[0] - start[0], end[1] - start[1])
    samples = SAMPLES if length >= SHORT_EDGE else (0.5,)

    votes, beyond = {}, {}
    for t in samples:
        room = None
        for step in PROBE_STEPS:
            x = start[0] + t * (end[0] - start[0]) + normal[0] * (reach + step)
            y = start[1] + t * (end[1] - start[1]) + normal[1] * (reach + step)
            room = _room_at(doc, x, y, z, phase)
            if room is not None:
                break
        if room is None:
            kind, name = ("exterior" if is_exterior_wall else "unroomed"), ""
        else:
            name = _room_name(room)
            # The room wrapping round on itself counts as a wall to a neighbour
            is_self = id_value(room.Id) == id_value(spatial_element.Id)
            kind = "corridor" if (not is_self and _is_corridor(name)) else "party"
        votes[kind] = votes.get(kind, 0) + 1
        if name:
            beyond[kind] = name

    kind = max(EDGE_KINDS, key=lambda k: votes.get(k, 0))
    # Glass is only as long as the curtain wall itself: a storefront set into
    # a brick wall does not make the whole edge glass
    curtain_length = min(
        length,
        sum(
            overlap_along(start, end, a, b)
            for w, a, b in stack
            if w.WallType.Kind == DB.WallKind.Curtain
        ),
    )
    return kind, beyond.get(kind, ""), curtain_length


# ---------------------------------------------------------------------------
# Walls
# ---------------------------------------------------------------------------


def _level_walls(doc, level):
    """Straight walls based on a level, as (wall, start, end) in project feet."""
    walls = []
    for wall in DB.FilteredElementCollector(doc).OfClass(DB.Wall):
        if wall.LevelId != level.Id:
            continue
        location = wall.Location
        if not isinstance(location, DB.LocationCurve):
            continue
        curve = location.Curve
        if not isinstance(curve, DB.Line):
            continue
        a, b = curve.GetEndPoint(0), curve.GetEndPoint(1)
        walls.append((wall, (a.X, a.Y), (b.X, b.Y)))
    return walls


def _walls_along_edge(start, end, level_walls):
    """Every wall a boundary edge runs along, as (wall, start, end).

    Walls are often layered: a finish or furring wall in front of the real
    one. All of them belong to the edge, so a door in the real wall is found
    and none of them is taken for a partition inside the unit.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return []
    middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)

    found = []
    for wall, a, b in level_walls:
        wx, wy = b[0] - a[0], b[1] - a[1]
        wall_length = math.hypot(wx, wy)
        if wall_length < 1e-9:
            continue
        if abs(dx * wy - dy * wx) / (length * wall_length) > PARALLEL:
            continue
        distance, _t = point_segment_distance(middle, a, b)
        if distance <= wall.Width / 2.0 + WALL_REACH:
            found.append((wall, a, b))
    return found


# ---------------------------------------------------------------------------
# Doors and windows
# ---------------------------------------------------------------------------


def _find_openings(doc, level, edges, walls):
    """Doors and windows that sit in the room's boundary.

    Args:
        edges (list): (start, end, element_id) in project feet.
        walls (list): For each edge, the ids of the walls layered along it.
    Returns:
        list: (instance, kind, edge index, t along the edge).
    """
    categories = List[DB.BuiltInCategory]()
    categories.Add(DB.BuiltInCategory.OST_Doors)
    categories.Add(DB.BuiltInCategory.OST_Windows)
    doors_id = int(DB.BuiltInCategory.OST_Doors)

    candidates = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
    )

    found = []
    for instance in candidates:
        host = instance.Host
        if not isinstance(host, DB.Wall):
            continue
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        if (
            instance.LevelId != DB.ElementId.InvalidElementId
            and instance.LevelId != level.Id
        ):
            continue

        point = (location.Point.X, location.Point.Y)
        reach = host.Width + OPENING_REACH
        best = None
        for index, (start, end, _) in enumerate(edges):
            # Where the edge is a wall, the opening must be in that wall
            if walls[index] and id_value(host.Id) not in walls[index]:
                continue
            distance, t = point_segment_distance(point, start, end)
            if distance <= reach and (best is None or distance < best[0]):
                best = (distance, index, t)
        if best is None:
            continue

        is_door = id_value(instance.Category.Id) == doors_id
        found.append((instance, "door" if is_door else "window", best[1], best[2]))
    return found


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def nearest_edge(point, edges):
    """Index of the edge closest to a point, and the distance to it."""
    best_index, best_distance = -1, None
    for index, (start, end, _) in enumerate(edges):
        distance, _t = point_segment_distance(point, start, end)
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    return best_index, best_distance


def build_features(spatial_element, edges, frame, level):
    """Read a room's surroundings into the features dictionary of an entry.

    Args:
        spatial_element: The Room or Area.
        edges (list): From boundary_edges.
        frame (RoomFrame): The room frame built from the same edges.
        level: The room's Level.
    Returns:
        dict: See LayoutEntry.features. All points are in the room frame.
    """
    doc = spatial_element.Document
    outline = [start for start, _, _ in edges]
    counter_clockwise = polygon_area(outline) > 0
    phase = _room_phase(spatial_element)
    z = level.ProjectElevation + PROBE_HEIGHT

    def local(point):
        x, y = frame.to_local(point)
        return [round(x, 4), round(y, 4)]

    def uv(x, y):
        u = x / frame.width if frame.width else 0.0
        v = y / frame.depth if frame.depth else 0.0
        return round(u, 4), round(v, 4)

    level_walls = _level_walls(doc, level)
    wall_ids, normals, edge_data = [], [], []
    edge_lengths = dict((kind, 0.0) for kind in EDGE_KINDS)
    for start, end, element_id in edges:
        stack = _walls_along_edge(start, end, level_walls)
        ids = set(id_value(w.Id) for w, _, _ in stack)
        element = doc.GetElement(element_id)
        if isinstance(element, DB.Wall) and id_value(element.Id) not in ids:
            for candidate in level_walls:
                if id_value(candidate[0].Id) == id_value(element.Id):
                    stack.append(candidate)
                    ids.add(id_value(element.Id))
        # The thickest wall of the stack is the real one; the rest are finishes
        wall = max(stack, key=lambda item: item[0].Width)[0] if stack else None
        normal = _outward_normal(start, end, counter_clockwise)
        kind, beyond, curtain_length = _classify_edge(
            doc, spatial_element, start, end, normal, stack, z, phase
        )
        wall_ids.append(ids)
        normals.append(normal)
        edge_data.append(
            {
                "start": local(start),
                "end": local(end),
                "length": round(math.hypot(end[0] - start[0], end[1] - start[1]), 4),
                "kind": kind,
                "beyond": beyond,
                "wall_type": _wall_label(wall),
                "glazed": curtain_length >= 0.5 * math.hypot(end[0] - start[0], end[1] - start[1]),
                "curtain_length": round(curtain_length, 4),
            }
        )

    openings = []
    for instance, kind, index, t in _find_openings(doc, level, edges, wall_ids):
        start, end, _ = edges[index]
        on_edge = (start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1]))
        x, y = local(on_edge)
        u, v = uv(x, y)
        if kind == "door":
            width = _length_parameter(instance, ("DOOR_WIDTH", "FAMILY_WIDTH_PARAM", "GENERIC_WIDTH"))
        else:
            width = _length_parameter(instance, ("WINDOW_WIDTH", "FAMILY_WIDTH_PARAM", "GENERIC_WIDTH"))
            edge_length = math.hypot(end[0] - start[0], end[1] - start[1])
            measured = _measured_width(instance, (end[0] - start[0], end[1] - start[1]))
            width = min(max(width, measured), edge_length)
        openings.append(
            {
                "kind": kind,
                "family": instance.Symbol.Family.Name,
                "type": type_name(instance.Symbol),
                "x": x,
                "y": y,
                "u": u,
                "v": v,
                "width": round(width, 4),
                "sill_height": round(_length_parameter(instance, ("INSTANCE_SILL_HEIGHT_PARAM",)), 4),
                "edge": index,
            }
        )
        # An Area's edge has no wall to ask; a window in it means outside
        if kind == "window" and edge_data[index]["kind"] == "unroomed":
            edge_data[index]["kind"] = "exterior"

    for edge in edge_data:
        edge_lengths[edge["kind"]] += edge["length"]

    # Only glass to the outside counts; a window to a corridor or a shop does not
    # A window drawn in a curtain wall must not count twice: per edge, the
    # longer of the two is the glass
    glazed_length = 0.0
    for index, edge in enumerate(edge_data):
        windows = sum(
            o["width"] for o in openings if o["kind"] == "window" and o["edge"] == index
        )
        edge["glazed_length"] = 0.0
        if edge["kind"] == "exterior":
            edge["glazed_length"] = round(
                min(edge["length"], max(windows, edge["curtain_length"])), 4
            )
        glazed_length += edge["glazed_length"]

    # The entrance: a door to the corridor if there is one, else the widest door
    entry = None
    doors = [o for o in openings if o["kind"] == "door"]
    if doors:
        door = max(
            doors,
            key=lambda o: (edge_data[o["edge"]]["kind"] == "corridor", o["width"]),
        )
        normal = normals[door["edge"]]
        inward = rotate((-normal[0], -normal[1]), -frame.angle)
        entry = {
            "x": door["x"],
            "y": door["y"],
            "u": door["u"],
            "v": door["v"],
            "edge": door["edge"],
            "direction": [round(inward[0], 4), round(inward[1], 4)],
        }

    boundary_wall_ids = set()
    for ids in wall_ids:
        boundary_wall_ids.update(ids)
    internal_walls, internal_wall_ids = _internal_walls(
        level_walls, boundary_wall_ids, outline, local
    )
    rooms = _rooms_inside(doc, spatial_element, level, outline, frame)

    local_outline = [frame.to_local(point) for point in outline]
    return {
        "perimeter": round(sum(e["length"] for e in edge_data), 4),
        "edges": edge_data,
        "edge_lengths": dict((k, round(v, 4)) for k, v in edge_lengths.items()),
        "window_wall_length": round(edge_lengths["exterior"], 4),
        "glazed_length": round(glazed_length, 4),
        "openings": openings,
        "entry": entry,
        "shape_matrix": shape_matrix(local_outline, frame.width, frame.depth),
        "shape": shape_category(local_outline),
        "unit": _unit_data(doc, level, outline, rooms),
        "rooms": rooms,
        "internal_walls": internal_walls,
        "internal_doors": _internal_doors(doc, level, internal_wall_ids, frame, local),
        "hosted_items": [],
        "nested_items": [],
    }


# ---------------------------------------------------------------------------
# Inside the unit
# ---------------------------------------------------------------------------


def _internal_walls(level_walls, boundary_wall_ids, outline, local):
    """Partitions inside the unit: walls whose middle is inside the outline.

    Returns:
        tuple: (list of wall dicts, {wall id: index into that list}).
    """
    found, index_by_id = [], {}
    for wall, a, b in level_walls:
        if id_value(wall.Id) in boundary_wall_ids:
            continue
        middle = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        if not point_in_polygon(middle, outline):
            continue
        index_by_id[id_value(wall.Id)] = len(found)
        found.append(
            {
                "start": local(a),
                "end": local(b),
                "width": round(wall.Width, 4),
                "wall_type": _wall_label(wall),
            }
        )
    return found, index_by_id


def _internal_doors(doc, level, internal_wall_ids, frame, local):
    """Doors in the unit's partitions, each with the partition it sits in."""
    found = []
    doors = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Doors)
        .OfClass(DB.FamilyInstance)
    )
    for door in doors:
        host = door.Host
        if host is None or id_value(host.Id) not in internal_wall_ids:
            continue
        location = door.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        if door.LevelId != DB.ElementId.InvalidElementId and door.LevelId != level.Id:
            continue
        x, y = local((location.Point.X, location.Point.Y))
        found.append(
            {
                "family": door.Symbol.Family.Name,
                "type": type_name(door.Symbol),
                "x": x,
                "y": y,
                "width": round(
                    _length_parameter(door, ("DOOR_WIDTH", "FAMILY_WIDTH_PARAM", "GENERIC_WIDTH")), 4
                ),
                "rotation": round(location.Rotation - frame.angle, 6),
                "hand_flipped": bool(door.HandFlipped),
                "facing_flipped": bool(door.FacingFlipped),
                "wall": internal_wall_ids[id_value(host.Id)],
            }
        )
    return found


def _rooms_inside(doc, spatial_element, level, outline, frame):
    """Rooms that stand inside the unit, with their outlines in the unit frame."""
    found = []
    rooms = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
    )
    for room in rooms:
        if room.Area <= 0 or room.LevelId != level.Id:
            continue
        if id_value(room.Id) == id_value(spatial_element.Id):
            continue
        location = room.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        if not point_in_polygon((location.Point.X, location.Point.Y), outline):
            continue
        x, y = frame.to_local((location.Point.X, location.Point.Y))
        boundary = []
        for start, _end, _id in boundary_edges(room):
            bx, by = frame.to_local(start)
            boundary.append([round(bx, 4), round(by, 4)])
        found.append(
            {
                "name": _room_name(room),
                "area": round(room.Area, 2),
                "x": round(x, 4),
                "y": round(y, 4),
                "boundary": boundary,
            }
        )
    return found


def _unit_data(doc, level, outline, rooms):
    """What kind of unit this is: room counts, and the PDSO data silo's values.

    The data silo (see pdso_kinds) is the one that stands inside the unit. All of its "PDSO - ..." parameters (program type,
    market segment, bedroom count...) are kept, set or not, as a list of
    {key, value, display}: value is the stored value (None when empty; numbers
    in Revit's internal units), display is how Revit shows it, with its unit.
    """
    unit = {
        "bedrooms": len([r for r in rooms if _has_word(r["name"], BEDROOM_WORDS)]),
        "bathrooms": len([r for r in rooms if _has_word(r["name"], BATHROOM_WORDS)]),
        "kitchens": len([r for r in rooms if _has_word(r["name"], KITCHEN_WORDS)]),
        "room_count": len(rooms),
        "pdso": [],
    }

    kind = pdso_kinds(doc)
    for instance in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        if kind(instance) != DATA_SILO:
            continue
        if instance.LevelId != level.Id:
            continue
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        if not point_in_polygon((location.Point.X, location.Point.Y), outline):
            continue
        for parameter in instance.Parameters:
            name = parameter.Definition.Name
            if not name.startswith(PDSO_PREFIX):
                continue
            value, display = None, ""
            if parameter.HasValue:
                if parameter.StorageType == DB.StorageType.String:
                    value = parameter.AsString() or None
                    display = value or ""
                elif parameter.StorageType == DB.StorageType.Double:
                    value = round(parameter.AsDouble(), 4)
                    display = parameter.AsValueString() or ""
                elif parameter.StorageType == DB.StorageType.Integer:
                    value = parameter.AsInteger()
                    display = parameter.AsValueString() or ""
                elif parameter.StorageType == DB.StorageType.ElementId:
                    display = parameter.AsValueString() or ""
                    value = display or None
            unit["pdso"].append({"key": name, "value": value, "display": display})
        unit["pdso"].sort(key=lambda pair: pair["key"])
        break
    return unit


def pdso_value(unit, key):
    """One PDSO parameter's value from a unit's list, or None."""
    for pair in unit.get("pdso", []):
        if pair["key"] == key:
            return pair["value"]
    return None
