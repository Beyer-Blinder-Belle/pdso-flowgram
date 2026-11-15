# -*- coding: utf-8 -*-
"""Unit Layout Catalogue without a UI, for the HTTP API and the MCP server.

The Harvest and Place buttons ask the user to pick a Room and to type names.
These functions take the same inputs as arguments and return plain data. They
use the same harvest, store, recommend and create code as the buttons.

All lengths are decimal feet. All areas are square feet.

Example Usage:
    entry = harvest_layout(doc, 1234, name="Studio", program="Studio Unit")
    matches = find_similar_rooms(doc, entry["id"])
    picture_data = preview_layout_in_room(doc, matches["rooms"][0]["id"], entry["id"])
    place_layout(doc, matches["rooms"][0]["id"], entry["id"])
"""

import math
import os

from rpw import DB

from pdso.code.business.errors import PDSOError
from pdso.code.business.revit_helpers import (
    Transaction,
    find_level,
    id_list,
    id_value,
    to_element_id,
)
from pdso.code.catalogue import revit_layout_service as layout_service
from pdso.code.catalogue.catalogue_settings import catalogue_folders, get_catalogue_folder
from pdso.code.catalogue.catalogue_store import (
    ENTRY_EXTENSION,
    SKETCH_EXTENSION,
    entry_to_svg,
    load_catalogue,
    sample_folder,
    save_entry,
)
from pdso.code.catalogue.layout_geometry import (
    ORIENTATIONS,
    distance_to_polygon,
    orient_angle,
    orient_point,
    orient_size,
    outline_overlap,
    point_in_or_near_polygon,
    point_in_polygon,
    rotate,
)
from pdso.code.catalogue.layout_recommender import (
    FIT_TOLERANCE,
    Placement,
    oriented_items,
    oriented_outline,
    recommend,
    refine_placement,
)

# Number of sample points on each side of the grid that compares two outlines
SHAPE_GRID = 24


# =============================================================================
# Catalogue files
# =============================================================================


def _entries():
    entries, errors = load_catalogue(catalogue_folders())
    return entries, errors


def _entry_folder(entry_id):
    """Folder that holds an entry's JSON file. Later folders win."""
    found = None
    for folder in catalogue_folders():
        if os.path.exists(os.path.join(folder, entry_id + ENTRY_EXTENSION)):
            found = folder
    return found


def get_entry(entry_id):
    """Get a catalogue entry by id, or raise PDSOError(404)."""
    entries, _ = _entries()
    for entry in entries:
        if entry.id == entry_id:
            return entry
    raise PDSOError(
        "No layout has the id '{}'. Use the layout list.".format(entry_id), 404
    )


def entry_summary(entry):
    """Describe an entry without its items and boundary."""
    counts = {}
    for item in entry.items:
        key = "{} : {}".format(item.family, item.type)
        counts[key] = counts.get(key, 0) + 1
    folder = _entry_folder(entry.id)
    return {
        "id": entry.id,
        "name": entry.name,
        "program": entry.program,
        "project_type": entry.project_type,
        "width": entry.width,
        "depth": entry.depth,
        "area": entry.area,
        "item_count": len(entry.items),
        "families": counts,
        "source": entry.source,
        "notes": entry.notes,
        "file": os.path.join(folder, entry.id + ENTRY_EXTENSION) if folder else None,
    }


def list_layouts(program=None, project_type=None):
    """List the layouts in the catalogue.

    Args:
        program (str): Only layouts for this program (optional)
        project_type (str): Only layouts of this project type (optional)

    Returns:
        dict: {"layouts": [summary, ...], "folders": [paths], "errors": [str]}
    """
    entries, errors = _entries()
    wanted_program = (program or "").strip().lower()
    wanted_type = (project_type or "").strip().lower()
    layouts = []
    for entry in entries:
        if wanted_program and entry.program.strip().lower() != wanted_program:
            continue
        if wanted_type and entry.project_type.strip().lower() != wanted_type:
            continue
        layouts.append(entry_summary(entry))
    layouts.sort(key=lambda item: (item["program"], item["name"]))
    return {"layouts": layouts, "folders": catalogue_folders(), "errors": errors}


def get_layout(entry_id):
    """Get one layout completely: the JSON entry and its SVG plan sketch.

    Returns:
        dict: {"summary", "entry" (the stored JSON), "svg"}
    """
    entry = get_entry(entry_id)
    return {
        "summary": entry_summary(entry),
        "entry": entry.to_dict(),
        "svg": entry_to_svg(entry),
    }


def delete_layout(entry_id):
    """Delete a layout file. A layout in the sample folder cannot be deleted."""
    get_entry(entry_id)
    folder = _entry_folder(entry_id)
    if folder is None or os.path.normcase(folder) == os.path.normcase(sample_folder()):
        raise PDSOError("A sample layout cannot be deleted.", 409)
    removed = []
    for extension in (ENTRY_EXTENSION, SKETCH_EXTENSION):
        path = os.path.join(folder, entry_id + extension)
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    return {"deleted": entry_id, "files": removed}


# =============================================================================
# Rooms and Areas
# =============================================================================


def get_spatial_element(doc, reference):
    """Get a Room or Area by element id, or a Room by its number.

    Args:
        doc: Revit Document
        reference: Element id (int or digits), or a Room number such as "304"
    """
    text = str(reference).strip()
    if text.isdigit():
        element = doc.GetElement(to_element_id(text))
        if element is not None and layout_service.is_spatial_element(element):
            return element
    matches = [
        room
        for room in DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
        if room.Number == text and room.Area > 0
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise PDSOError(
            "{} Rooms have the number '{}'. Use the element id: {}".format(
                len(matches), text, ", ".join(str(id_value(m.Id)) for m in matches)
            ),
            409,
        )
    raise PDSOError(
        "'{}' is not the element id of a Room or Area, and no Room has that"
        " number.".format(reference),
        404,
    )


def _spatial_info(element):
    level = layout_service.get_level(element)
    return {
        "id": id_value(element.Id),
        "kind": "Area" if isinstance(element, DB.Area) else "Room",
        "number": element.Number,
        "name": layout_service.get_room_name(element),
        "level": level.Name if level is not None else None,
        "area": element.Area,
    }


def _room_instances(element):
    """The free-standing family instances that a harvest of the element takes.

    This uses the same rules as revit_layout_service.harvest.
    """
    doc = element.Document
    outline = layout_service.get_outline(element)
    if len(outline) < 3:
        return []
    level_id = element.LevelId
    categories = layout_service.List[DB.BuiltInCategory]()
    for category in layout_service.HARVEST_CATEGORIES:
        categories.Add(category)
    instances = []
    pdso_kind = layout_service.pdso_kinds(doc)
    for instance in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    ):
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        # PDSO data silos and Program Volumes are not furniture. They must
        # not be listed, counted or deleted.
        if pdso_kind(instance) is not None:
            continue
        point = location.Point
        if not point_in_polygon((point.X, point.Y), outline):
            continue
        if layout_service._instance_level_id(instance) != level_id:
            continue
        if instance.SuperComponent is not None:
            continue
        host = instance.Host
        if host is not None and not isinstance(host, DB.Level):
            continue
        if instance.HostFace is not None:
            continue
        instances.append(instance)
    return instances


def list_room_items(doc, reference):
    """List the free-standing families in a Room or Area (what a harvest takes).

    Returns:
        dict: {"room": {...}, "items": [{"id", "category", "family", "type",
            "x", "y", "z" (project elevation), "z_offset" (height above the
            level), "rotation"}]}
    """
    element = get_spatial_element(doc, reference)
    level = layout_service.get_level(element)
    items = []
    for instance in _room_instances(element):
        point = instance.Location.Point
        items.append(
            {
                "id": id_value(instance.Id),
                "category": instance.Category.Name,
                "family": instance.Symbol.Family.Name,
                "type": layout_service.get_type_name(instance.Symbol),
                "x": point.X,
                "y": point.Y,
                "z": point.Z,
                "z_offset": layout_service._z_offset(instance, point, level),
                "rotation": instance.Location.Rotation,
            }
        )
    items.sort(key=lambda item: (item["family"], item["type"], item["x"], item["y"]))
    return {"room": _spatial_info(element), "items": items}


def clear_room_items(doc, reference):
    """Delete the free-standing families in a Room or Area.

    This removes what a harvest of the room takes. Use it to make a furnished
    room empty before a layout is placed.

    Returns:
        dict: {"room": {...}, "deleted": number of elements}
    """
    element = get_spatial_element(doc, reference)
    ids = [instance.Id for instance in _room_instances(element)]
    if ids:
        with Transaction(doc, "PDSO - Clear Room Layout"):
            doc.Delete(id_list(ids))
    return {"room": _spatial_info(element), "deleted": len(ids)}


def describe_item(doc, element_id):
    """Describe one family instance: position, flips, plan box and instance parameters.

    Returns:
        dict: {"id", "category", "family", "type", "x", "y", "z", "rotation",
            "hand_flipped", "facing_flipped", "mirrored", "can_flip_hand",
            "can_flip_facing", "box" [min_x, min_y, max_x, max_y] in project
            coordinates, "parameters" {name: number}}
    """
    instance = doc.GetElement(to_element_id(str(element_id)))
    if instance is None or not isinstance(instance, DB.FamilyInstance):
        raise PDSOError("Element {} is not a family instance.".format(element_id), 404)
    location = instance.Location
    point = location.Point if isinstance(location, DB.LocationPoint) else None
    box = instance.get_BoundingBox(None)
    return {
        "id": id_value(instance.Id),
        "category": instance.Category.Name,
        "family": instance.Symbol.Family.Name,
        "type": layout_service.get_type_name(instance.Symbol),
        "x": point.X if point else None,
        "y": point.Y if point else None,
        "z": point.Z if point else None,
        "rotation": location.Rotation if point else None,
        "hand_flipped": bool(instance.HandFlipped),
        "facing_flipped": bool(instance.FacingFlipped),
        "mirrored": bool(instance.Mirrored),
        "can_flip_hand": bool(instance.CanFlipHand),
        "can_flip_facing": bool(instance.CanFlipFacing),
        "level_id": id_value(instance.LevelId),
        "level_id_for_room": id_value(layout_service._instance_level_id(instance)),
        "host": (
            None
            if instance.Host is None
            else "{} {}".format(type(instance.Host).__name__, id_value(instance.Host.Id))
        ),
        "host_face": instance.HostFace is not None,
        "super_component": instance.SuperComponent is not None,
        "box": [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y] if box else None,
        "parameters": layout_service.instance_parameters(instance),
    }


def rooms_overview(doc):
    """All placed Rooms with their outline and the plan boxes of their free-standing families.

    One pass over the model, for a Room list with plan thumbnails.

    Returns:
        list: [{"id", "number", "name", "level", "level_id", "area", "outline"
            [[x, y]], "boxes" [[[x, y] x 4]], "item_count"}], sorted by level elevation
            and Room number
    """
    rooms = []
    by_level = {}
    for element in _spatial_elements(doc, "rooms", None):
        outline = layout_service.get_outline(element)
        if len(outline) < 3:
            continue
        level = layout_service.get_level(element)
        info = _spatial_info(element)
        info.update(
            {
                "outline": [[x, y] for x, y in outline],
                "boxes": [],
                "item_count": 0,
                "level_id": id_value(element.LevelId),
            }
        )
        xs = [point[0] for point in outline]
        ys = [point[1] for point in outline]
        record = (info, outline, min(xs), min(ys), max(xs), max(ys))
        by_level.setdefault(id_value(element.LevelId), []).append(record)
        rooms.append((level.ProjectElevation if level is not None else 0.0, info))

    categories = layout_service.List[DB.BuiltInCategory]()
    for category in layout_service.HARVEST_CATEGORIES:
        categories.Add(category)
    pdso_kind = layout_service.pdso_kinds(doc)
    for instance in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    ):
        location = instance.Location
        if not isinstance(location, DB.LocationPoint) or instance.SuperComponent is not None:
            continue
        if pdso_kind(instance) is not None:
            continue
        host = instance.Host
        if (host is not None and not isinstance(host, DB.Level)) or instance.HostFace is not None:
            continue
        candidates = by_level.get(id_value(layout_service._instance_level_id(instance)))
        if not candidates:
            continue
        x, y = location.Point.X, location.Point.Y
        for info, outline, min_x, min_y, max_x, max_y in candidates:
            if x < min_x or x > max_x or y < min_y or y > max_y:
                continue
            if not point_in_polygon((x, y), outline):
                continue
            info["item_count"] += 1
            box = instance.get_BoundingBox(None)
            if box is not None:
                info["boxes"].append(
                    [
                        [box.Min.X, box.Min.Y],
                        [box.Max.X, box.Min.Y],
                        [box.Max.X, box.Max.Y],
                        [box.Min.X, box.Max.Y],
                    ]
                )
            break
    rooms.sort(key=lambda pair: (pair[0], pair[1]["number"]))
    return [info for _, info in rooms]


def layout_sketches():
    """Outline and item points of each catalogue layout, for plan thumbnails.

    Returns:
        dict: {layout id: {"outline" [[x, y]], "points" [[x, y, rotation]]}}
    """
    entries, _ = _entries()
    sketches = {}
    for entry in entries:
        sketches[entry.id] = {
            "outline": [[x, y] for x, y in entry.boundary],
            "points": [[item.x, item.y, item.rotation] for item in entry.items],
        }
    return sketches


# =============================================================================
# Harvest
# =============================================================================


def harvest_layout(
    doc, reference, name=None, program=None, project_type="Other", notes="", folder=None
):
    """Save the layout of a Room or Area into the catalogue.

    Args:
        doc: Revit Document
        reference: Element id of a Room or Area, or a Room number
        name (str): Name of the layout. Default: "<program> - <area> sf"
        program (str): Program that the layout serves. Default: the Room name
        project_type (str): For example "Residential" or "K-12"
        notes (str): Free text
        folder (str): Folder for the files. Default: the configured folder

    Returns:
        dict: {"layout": summary, "file": path of the JSON file, "entry": the
            stored JSON, "skipped": [{"id", "label", "reason"}]}
    """
    element = get_spatial_element(doc, reference)
    program = program or layout_service.get_room_name(element)
    if not program:
        raise PDSOError("Give a program. The Room has no name.")
    name = name or "{} - {:.0f} sf".format(program, element.Area)

    result = layout_service.harvest(element, name, program, project_type, notes)
    if result is None:
        raise PDSOError(
            "The Room or Area is not placed or not enclosed. It has no boundary.", 409
        )
    target_folder = folder or get_catalogue_folder()
    path = save_entry(result.entry, target_folder)
    # The family files let the layout go into a model that has not the families
    family_files = layout_service.save_family_files(doc, result.entry, target_folder)
    return {
        "layout": entry_summary(result.entry),
        "file": path,
        "family_files": family_files,
        "entry": result.entry.to_dict(),
        "skipped": [
            {"id": id_value(element_id), "label": label, "reason": reason}
            for element_id, label, reason in result.skipped
        ],
    }


# =============================================================================
# Recommend and place
# =============================================================================


def _placement_info(placement):
    return {
        "turns": placement.turns,
        "mirror": placement.mirror,
        "description": placement.describe(),
    }


def recommend_layouts(doc, reference, program=None, limit=10):
    """Rank the catalogue layouts for a target Room or Area.

    Args:
        doc: Revit Document
        reference: Element id of a Room or Area, or a Room number
        program (str): Layouts for this program come first. Default: the Room name
        limit (int): Largest number of layouts in the reply

    Returns:
        dict: {"target": {...}, "recommendations": [{"layout_id", "name",
            "program", "score", "area_score", "snug_score", "program_match",
            "shape_match", "placement"}], "rejected": [{"layout_id", "reason"}]}
    """
    element = get_spatial_element(doc, reference)
    frame, local_outline, area = layout_service.describe_target(element)
    if frame is None:
        raise PDSOError(
            "The Room or Area is not placed or not enclosed. It has no boundary.", 409
        )
    if program is None:
        program = layout_service.get_room_name(element)

    entries, errors = _entries()
    recommendations, rejected = recommend(
        entries, frame.width, frame.depth, area, local_outline, program
    )
    target = _spatial_info(element)
    target.update({"width": frame.width, "depth": frame.depth, "program": program})
    return {
        "target": target,
        "recommendations": [
            {
                "layout_id": item.entry.id,
                "name": item.entry.name,
                "program": item.entry.program,
                "score": item.score,
                "area_score": item.area_score,
                "snug_score": item.snug_score,
                "program_match": item.program_match,
                "shape_match": shape_match(item.entry, frame, local_outline)[0],
                "placement": _placement_info(item.placement),
            }
            for item in recommendations[: int(limit)]
        ],
        "rejected": [
            {"layout_id": entry.id, "reason": reason} for entry, reason in rejected
        ],
        "errors": errors,
    }


def place_layout(doc, reference, entry_id):
    """Place a catalogue layout into a target Room or Area.

    The recommender selects the orientation. If the layout fits in no
    orientation, nothing is placed.

    Returns:
        dict: {"target": {...}, "layout_id", "placement", "placed": [element
            ids], "missing": {"Family : Type": count}, "loaded_families":
            {family name: where it came from}, "tagged_programs":
            [element ids], "warnings": [str]}
    """
    element = get_spatial_element(doc, reference)
    entry = get_entry(entry_id)
    frame, local_outline, area = layout_service.describe_target(element)
    if frame is None:
        raise PDSOError(
            "The Room or Area is not placed or not enclosed. It has no boundary.", 409
        )
    recommendations, rejected = recommend(
        [entry], frame.width, frame.depth, area, local_outline, entry.program
    )
    if not recommendations:
        raise PDSOError(
            "Layout '{}' does not fit this target: {}".format(entry_id, rejected[0][1]),
            409,
        )
    chosen = recommendations[0]
    placement = refine_placement(
        entry, chosen.placement, frame.width, frame.depth, local_outline
    )
    # Families that the model has not are loaded first: from the family files
    # of the catalogue, or from a different open model
    families = layout_service.load_missing_families(doc, entry, catalogue_folders())
    with Transaction(doc, "PDSO - Place Unit Layout") as transaction:
        result = layout_service.create(element, entry, placement)
    return {
        "target": _spatial_info(element),
        "layout_id": entry.id,
        "score": chosen.score,
        "placement": _placement_info(placement),
        "placed": [id_value(element_id) for element_id in result.placed],
        "missing": result.missing,
        "loaded_families": families["loaded"],
        "tagged_programs": [id_value(element_id) for element_id in result.tagged_silos],
        "warnings": list(result.warnings) + list(transaction.warnings),
    }


# =============================================================================
# Similar rooms
# =============================================================================


def shape_match(entry, frame, local_outline):
    """How well the outline of an entry covers the outline of a target.

    The entry outline is put in the middle of the target frame in each of the
    eight orientations. The result is the largest intersection over union.

    Args:
        entry (LayoutEntry): The layout
        frame (RoomFrame): Frame of the target
        local_outline (list): Target outline in its frame

    Returns:
        tuple: (match from 0.0 to 1.0, (turns, mirror) of the best orientation)
    """
    if not entry.boundary or not local_outline:
        return 0.0, (0, False)
    best = 0.0
    best_orientation = (0, False)
    for turns, mirror in ORIENTATIONS:
        width, depth = orient_size(entry.width, entry.depth, turns)
        shift_x = (frame.width - width) / 2.0
        shift_y = (frame.depth - depth) / 2.0
        outline = []
        for point in entry.boundary:
            x, y = orient_point(point, entry.width, entry.depth, turns, mirror)
            outline.append((x + shift_x, y + shift_y))
        match = outline_overlap(outline, local_outline, SHAPE_GRID)
        if match > best:
            best = match
            best_orientation = (turns, mirror)
    return best, best_orientation


def area_match(entry, area):
    """How well the area of a target agrees with the area of an entry (0 to 1)."""
    if entry.area <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(area - entry.area) / entry.area)


def _spatial_elements(doc, kind, level):
    level_element = find_level(doc, level) if level is not None else None
    category = (
        DB.BuiltInCategory.OST_Areas if kind == "areas" else DB.BuiltInCategory.OST_Rooms
    )
    elements = []
    for element in (
        DB.FilteredElementCollector(doc)
        .OfCategory(category)
        .WhereElementIsNotElementType()
    ):
        if not layout_service.is_spatial_element(element) or element.Area <= 0:
            continue
        if level_element is not None and element.LevelId != level_element.Id:
            continue
        elements.append(element)
    return elements


def find_similar_rooms(doc, entry_id, kind="rooms", level=None, limit=20):
    """Find the Rooms (or Areas) of a model that are similar to a layout.

    Each Room gets these numbers:
        similarity: the average of area_match and shape_match (0.0 to 1.0). It
            does not depend on the items of the layout
        area_match: how well the two areas agree
        shape_match: how well the two outlines cover each other
        fits: True if all items of the layout land in the Room in an orientation
        score: the recommender's score if the layout fits, else 0.0

    Args:
        doc: Revit Document
        entry_id (str): Id of the layout
        kind (str): "rooms" or "areas"
        level: Level name or id (optional)
        limit (int): Largest number of Rooms in the reply

    Returns:
        dict: {"layout": summary, "rooms_checked": int, "rooms": [{"id",
            "number", "name", "level", "area", "similarity", "area_match",
            "shape_match", "fits", "score", "placement", "reason"}]} ranked by
            similarity
    """
    if kind not in ("rooms", "areas"):
        raise PDSOError("kind must be 'rooms' or 'areas'.")
    entry = get_entry(entry_id)
    source_id = str(entry.source.get("element_id", ""))
    source_project = entry.source.get("project")

    rooms = []
    for element in _spatial_elements(doc, kind, level):
        frame, local_outline, area = layout_service.describe_target(element)
        if frame is None:
            continue
        info = _spatial_info(element)
        info["is_source"] = (
            str(info["id"]) == source_id and doc.Title == source_project
        )
        recommendations, rejected = recommend(
            [entry], frame.width, frame.depth, area, local_outline, ""
        )
        shape, _ = shape_match(entry, frame, local_outline)
        area_score = area_match(entry, area)
        info.update(
            {
                "similarity": (area_score + shape) / 2.0,
                "area_match": area_score,
                "shape_match": shape,
                "fits": bool(recommendations),
                "score": recommendations[0].score if recommendations else 0.0,
                "placement": (
                    _placement_info(recommendations[0].placement)
                    if recommendations
                    else None
                ),
                "reason": None if recommendations else rejected[0][1],
            }
        )
        rooms.append(info)

    rooms.sort(key=lambda item: (-item["similarity"], -item["shape_match"]))
    return {
        "layout": entry_summary(entry),
        "rooms_checked": len(rooms),
        "rooms": rooms[: int(limit)],
    }



# =============================================================================
# Preview (read-only)
# =============================================================================

# An angle this near to a multiple of 90 degrees counts as square (radians)
SQUARE_ANGLE_TOLERANCE = 1e-3


def _is_square(angle):
    rest = angle % (math.pi / 2.0)
    return min(rest, math.pi / 2.0 - rest) < SQUARE_ANGLE_TOLERANCE


def _own_box(instance):
    """Plan box of an instance in the frame of its location point and rotation.

    The box comes from the bounding box of the instance, which is parallel to
    the project axes. Thus it is exact only if the rotation is square.

    Returns:
        tuple: (min_x, min_y, max_x, max_y), or None
    """
    box = instance.get_BoundingBox(None)
    if box is None:
        return None
    point = instance.Location.Point
    angle = instance.Location.Rotation
    corners = [
        rotate((x - point.X, y - point.Y), -angle)
        for x, y in (
            (box.Min.X, box.Min.Y),
            (box.Max.X, box.Min.Y),
            (box.Max.X, box.Max.Y),
            (box.Min.X, box.Max.Y),
        )
    ]
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_corners(box, x, y, angle):
    """The four corners of a box in project coordinates: [[x, y], ...]."""
    corners = []
    for local in (
        (box[0], box[1]),
        (box[2], box[1]),
        (box[2], box[3]),
        (box[0], box[3]),
    ):
        dx, dy = rotate(local, angle)
        corners.append([x + dx, y + dy])
    return corners


def _type_boxes(doc, keys):
    """Plan boxes of family types, measured on instances that are in the model.

    Args:
        doc: Revit Document
        keys (set): (family name, type name) pairs

    Returns:
        dict: key -> list of (exact, hand_flipped, facing_flipped, box,
            can_flip_hand, can_flip_facing)
    """
    boxes = {}
    if not keys:
        return boxes
    categories = layout_service.List[DB.BuiltInCategory]()
    for category in layout_service.HARVEST_CATEGORIES:
        categories.Add(category)
    for instance in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    ):
        if not isinstance(instance.Location, DB.LocationPoint):
            continue
        try:
            key = (
                instance.Symbol.Family.Name,
                layout_service.get_type_name(instance.Symbol),
            )
        except Exception:
            continue
        if key not in keys:
            continue
        known = boxes.setdefault(key, [])
        exact = _is_square(instance.Location.Rotation)
        hand = bool(instance.HandFlipped)
        facing = bool(instance.FacingFlipped)
        if any(k[0] >= exact and k[1] == hand and k[2] == facing for k in known):
            continue
        box = _own_box(instance)
        if box is not None:
            known.append(
                (
                    exact,
                    hand,
                    facing,
                    box,
                    bool(instance.CanFlipHand),
                    bool(instance.CanFlipFacing),
                )
            )
    return boxes


def _box_for(known, hand, facing):
    """Select the box of a family type for an item with the given flips."""
    if not known:
        return None

    def rank(candidate):
        return (not candidate[0], candidate[1] != hand, candidate[2] != facing)

    _, known_hand, known_facing, box = sorted(known, key=rank)[0][:4]
    min_x, min_y, max_x, max_y = box
    if known_hand != hand:
        min_x, max_x = -max_x, -min_x
    if known_facing != facing:
        min_y, max_y = -max_y, -min_y
    return (min_x, min_y, max_x, max_y)


def preview_layout_in_room(doc, reference, entry_id):
    """Data for a picture of a layout in a Room or Area. The model does not change.

    The items of the layout get the positions and rotations that place_layout
    would use. If the layout does not fit, the orientation with the best
    outline match is used, and each item says if it is inside the Room.
    All points are in project coordinates.

    Returns:
        dict: {"room": {..., "outline", "width", "depth", "frame_angle"},
            "layout": summary, "match": {"similarity", "area_match",
            "shape_match", "fits", "score", "reason", "placement",
            "orientation_from", "items_outside", "items_not_loaded"},
            "layout_outline": [[x, y], ...], "items": [{"category", "family",
            "type", "x", "y", "rotation", "inside", "loaded", "footprint"}],
            "current_items": [{"id", "category", "family", "type", "x", "y",
            "rotation", "footprint"}]}
    """
    element = get_spatial_element(doc, reference)
    entry = get_entry(entry_id)
    outline = layout_service.get_outline(element)
    frame, local_outline, area = layout_service.describe_target(element)
    if frame is None:
        raise PDSOError(
            "The Room or Area is not placed or not enclosed. It has no boundary.", 409
        )

    recommendations, rejected = recommend(
        [entry], frame.width, frame.depth, area, local_outline, ""
    )
    shape, shape_orientation = shape_match(entry, frame, local_outline)
    area_score = area_match(entry, area)
    if recommendations:
        placement = refine_placement(
            entry, recommendations[0].placement, frame.width, frame.depth, local_outline
        )
        orientation_from = "placement"
    else:
        turns, mirror = shape_orientation
        width, depth = orient_size(entry.width, entry.depth, turns)
        shift = ((frame.width - width) / 2.0, (frame.depth - depth) / 2.0)
        placement = Placement(turns, mirror, shift)
        orientation_from = "best outline match"

    symbols = layout_service._symbol_lookup(doc)
    # Families that place_layout loads first count as loaded
    loadable = layout_service.loadable_families(doc, entry, catalogue_folders())
    to_load = 0
    boxes = _type_boxes(doc, set(item.symbol_key for item in entry.items))
    items = []
    outside = 0
    not_loaded = 0
    across = 0
    for item, local_point in oriented_items(entry, placement):
        x, y = frame.to_world(local_point)
        angle = frame.angle + orient_angle(
            item.rotation, placement.turns, placement.mirror
        )
        inside = point_in_or_near_polygon(local_point, local_outline, FIT_TOLERANCE)
        loaded = item.symbol_key in symbols
        if not loaded and item.family in loadable:
            loaded = True
            to_load += 1
        outside += 0 if inside else 1
        not_loaded += 0 if loaded else 1
        known = boxes.get(item.symbol_key)
        hand = item.hand_flipped != placement.mirror
        facing = item.facing_flipped
        box = _box_for(known, hand, facing)
        footprint = _box_corners(box, x, y, angle) if box else None
        # place_layout checks the insertion point only. The body of an item
        # can be in a wall: a corner of its plan box outside the Room outline.
        is_across = bool(
            inside
            and footprint
            and any(
                not point_in_polygon((corner[0], corner[1]), outline)
                and distance_to_polygon((corner[0], corner[1]), outline) > FIT_TOLERANCE
                for corner in footprint
            )
        )
        across += 1 if is_across else 0
        items.append(
            {
                "category": item.category,
                "family": item.family,
                "type": item.type,
                "x": x,
                "y": y,
                "rotation": angle % (2.0 * math.pi),
                "hand_flipped": hand,
                "facing_flipped": facing,
                "inside": inside,
                "loaded": loaded,
                "across": is_across,
                "footprint": footprint,
            }
        )

    current = []
    for instance in _room_instances(element):
        point = instance.Location.Point
        angle = instance.Location.Rotation
        box = _own_box(instance)
        current.append(
            {
                "id": id_value(instance.Id),
                "category": instance.Category.Name,
                "family": instance.Symbol.Family.Name,
                "type": layout_service.get_type_name(instance.Symbol),
                "x": point.X,
                "y": point.Y,
                "rotation": angle,
                "hand_flipped": bool(instance.HandFlipped),
                "facing_flipped": bool(instance.FacingFlipped),
                "mirrored": bool(instance.Mirrored),
                "can_flip_hand": bool(instance.CanFlipHand),
                "can_flip_facing": bool(instance.CanFlipFacing),
                "footprint": _box_corners(box, point.X, point.Y, angle) if box else None,
            }
        )
    current.sort(key=lambda item: (item["family"], item["type"], item["x"], item["y"]))

    room = _spatial_info(element)
    room.update(
        {
            "outline": [[x, y] for x, y in outline],
            "width": frame.width,
            "depth": frame.depth,
            "frame_angle": frame.angle,
            "is_source": (
                str(room["id"]) == str(entry.source.get("element_id", ""))
                and doc.Title == entry.source.get("project")
            ),
        }
    )
    return {
        "room": room,
        "layout": entry_summary(entry),
        "match": {
            "similarity": (area_score + shape) / 2.0,
            "area_match": area_score,
            "shape_match": shape,
            "fits": bool(recommendations),
            "score": recommendations[0].score if recommendations else 0.0,
            "reason": None if recommendations else rejected[0][1],
            "placement": _placement_info(placement),
            "orientation_from": orientation_from,
            "items_outside": outside,
            "items_across_outline": across,
            "items_not_loaded": not_loaded,
            "items_to_load": to_load,
            "families_to_load": sorted(loadable),
        },
        "layout_outline": [
            list(frame.to_world(point))
            for point in oriented_outline(entry, placement)
        ],
        "items": items,
        "current_items": current,
    }
