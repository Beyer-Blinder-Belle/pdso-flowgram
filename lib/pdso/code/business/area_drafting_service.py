# -*- coding: utf-8 -*-
"""PDSO Area Drafting Service - draw, read, change and delete Area outlines.

An Area is in one area scheme and on one level. Its outline is a closed loop of
area boundary lines in the area plan of that scheme and level.

All coordinates are decimal feet in the project coordinate system. All areas are
square feet. An outline is a list of [x, y] points, without a repeat of the
first point at the end.

Example Usage:
    area = create_area(doc, "PDSO - GSF", "L1", [[0, 0], [40, 0], [40, 30], [0, 30]])
    set_area_outline(doc, area["id"], [[0, 0], [50, 0], [50, 30], [0, 30]])
"""

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from . import config_service, program_service
from .bootstrap_service import ensure_area_plan, find_area_plan
from pdso.code.setup.revit_utils import find_area_scheme
from .errors import PDSOError
from pdso.code.catalogue.layout_geometry import distance_to_polygon
from .revit_helpers import (
    Transaction,
    TransactionGroup,
    find_level,
    get_element,
    id_list,
    id_value,
    point_in_area,
    point_in_polygon,
    polygon_area,
    polygon_interior_point,
    to_element_id,
    to_number,
)

logger = script.get_logger()

# Two points nearer than this (feet) are the same point
POINT_TOLERANCE = 0.005


# =============================================================================
# Read
# =============================================================================


def list_area_schemes(doc):
    """List the area schemes and the PDSO parameter that each one feeds.

    Returns:
        list[dict]: One dict per scheme with keys "id", "name",
            "is_gross_building_area", "pdso_parameter" (or None) and "area_count"
    """
    config = config_service.get_config(doc)
    parameter_by_scheme = dict(
        (scheme, parameter)
        for parameter, scheme in config.get("area_scheme_mappings", {}).items()
    )
    counts = {}
    for area in _all_areas(doc):
        name = _scheme_name(area)
        counts[name] = counts.get(name, 0) + 1
    return [
        {
            "id": id_value(scheme.Id),
            "name": scheme.Name,
            "is_gross_building_area": scheme.IsGrossBuildingArea,
            "pdso_parameter": parameter_by_scheme.get(scheme.Name),
            "area_count": counts.get(scheme.Name, 0),
        }
        for scheme in DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme)
    ]


def _all_areas(doc):
    return [
        area
        for area in DB.FilteredElementCollector(doc).OfCategory(
            DB.BuiltInCategory.OST_Areas
        )
        if isinstance(area, DB.Area)
    ]


def _scheme_name(area):
    try:
        return area.AreaScheme.Name
    except Exception:
        return None


def _loops(area):
    """Boundary loops of an Area.

    Returns:
        list[dict]: One dict per loop with keys "points" (list of [x, y]) and
            "line_ids" (ids of the boundary line elements)
    """
    result = []
    loops = area.GetBoundarySegments(DB.SpatialElementBoundaryOptions())
    if loops is None:
        return result
    for loop in loops:
        points = []
        line_ids = []
        for segment in loop:
            tessellated = list(segment.GetCurve().Tessellate())
            for point in tessellated[:-1]:
                points.append([point.X, point.Y])
            if segment.ElementId != DB.ElementId.InvalidElementId:
                line_ids.append(id_value(segment.ElementId))
        result.append({"points": points, "line_ids": line_ids})
    return result


def _outer_loop(loops):
    """The loop with the largest area, or None."""
    best = None
    best_area = -1.0
    for loop in loops:
        if len(loop["points"]) < 3:
            continue
        size = abs(polygon_area(loop["points"]))
        if size > best_area:
            best = loop
            best_area = size
    return best


def area_info(doc, area, with_outline=True):
    """Describe an Area as a dict."""
    level = doc.GetElement(area.LevelId)
    name_parameter = area.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
    location = getattr(area.Location, "Point", None) if area.Location else None
    info = {
        "id": id_value(area.Id),
        "number": area.Number,
        "name": name_parameter.AsString() if name_parameter else None,
        "scheme": _scheme_name(area),
        "level": level.Name if level is not None else None,
        "area": area.Area,
        "perimeter": area.Perimeter,
        "placed": location is not None,
        "location": {"x": location.X, "y": location.Y} if location else None,
    }
    if with_outline:
        loops = _loops(area)
        outer = _outer_loop(loops)
        info["outline"] = outer["points"] if outer else []
        info["holes"] = [
            loop["points"] for loop in loops if outer is not None and loop is not outer
        ]
    return info


def list_areas(doc, scheme=None, level=None, with_outline=True):
    """List the Areas, with their outlines.

    Args:
        doc: Revit Document
        scheme (str): Area scheme name (optional)
        level: Level name or id (optional)
        with_outline (bool): Include "outline" and "holes"

    Returns:
        list[dict]: One dict per Area (see area_info)
    """
    level_element = find_level(doc, level) if level is not None else None
    if scheme is not None and find_area_scheme(doc, scheme) is None:
        raise PDSOError("Area scheme '{}' not found.".format(scheme), 404)
    areas = []
    for area in _all_areas(doc):
        if scheme is not None and _scheme_name(area) != scheme:
            continue
        if level_element is not None and area.LevelId != level_element.Id:
            continue
        areas.append(area_info(doc, area, with_outline))
    return sorted(
        areas, key=lambda item: (item["scheme"] or "", item["level"] or "", item["id"])
    )


def get_area(doc, area_id):
    """Get an Area element by id, or raise PDSOError(404)."""
    return get_element(doc, area_id, DB.Area, "Area")


# =============================================================================
# Outline checks
# =============================================================================


def _clean_outline(points):
    """Validate an outline. Return a list of (x, y) floats."""
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise PDSOError("An outline needs at least 3 points: [[x, y], ...].")
    outline = []
    for point in points:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            raise PDSOError(
                "Each outline point must be [x, y]. Got: {!r}".format(point)
            )
        if outline and _same_point(outline[-1], (x, y)):
            continue
        outline.append((x, y))
    if len(outline) > 1 and _same_point(outline[0], outline[-1]):
        outline.pop()
    if len(outline) < 3:
        raise PDSOError("An outline needs at least 3 different points.")
    if abs(polygon_area(outline)) < 0.01:
        raise PDSOError("The outline has no area.")
    count = len(outline)
    for i in range(count):
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or (i + 1) % count == j:
                continue
            if _segments_cross(
                outline[i],
                outline[(i + 1) % count],
                outline[j],
                outline[(j + 1) % count],
            ):
                raise PDSOError(
                    "The outline crosses itself (edge {} and edge {}).".format(i, j)
                )
    return outline


def _same_point(a, b):
    return abs(a[0] - b[0]) < POINT_TOLERANCE and abs(a[1] - b[1]) < POINT_TOLERANCE


def _segments_cross(p1, p2, p3, p4):
    def orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(value) < 1e-9:
            return 0
        return 1 if value > 0 else -1

    return orientation(p1, p2, p3) != orientation(p1, p2, p4) and orientation(
        p3, p4, p1
    ) != orientation(p3, p4, p2)


# =============================================================================
# Write
# =============================================================================


def _area_plan(doc, scheme_name, level):
    """Get the area plan (a ViewPlan) of a scheme on a level. Make it if needed."""
    scheme = find_area_scheme(doc, scheme_name)
    if scheme is None:
        raise PDSOError("Area scheme '{}' not found.".format(scheme_name), 404)
    view = find_area_plan(doc, scheme, level)
    if view is None:
        ensure_area_plan(doc, scheme_name, level)
        view = find_area_plan(doc, scheme, level)
    return view


def _existing_boundary_segments(doc, view):
    """Straight area boundary lines in a view, as ((x1, y1), (x2, y2))."""
    segments = []
    collector = DB.FilteredElementCollector(doc, view.Id).OfCategory(
        DB.BuiltInCategory.OST_AreaSchemeLines
    )
    for element in collector:
        curve = getattr(element, "GeometryCurve", None)
        if isinstance(curve, DB.Line) and curve.IsBound:
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            segments.append(((start.X, start.Y), (end.X, end.Y)))
    return segments


def _uncovered_intervals(start, end, segments):
    """Parts of the edge start-end that no segment in ``segments`` covers.

    Returns:
        list[tuple]: (t1, t2) distances along the edge from ``start``
    """
    length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    ux = (end[0] - start[0]) / length
    uy = (end[1] - start[1]) / length
    intervals = [(0.0, length)]
    for seg_start, seg_end in segments:
        params = []
        collinear = True
        for point in (seg_start, seg_end):
            dx = point[0] - start[0]
            dy = point[1] - start[1]
            if abs(dx * uy - dy * ux) > POINT_TOLERANCE:
                collinear = False
                break
            params.append(dx * ux + dy * uy)
        if not collinear:
            continue
        low, high = min(params), max(params)
        remaining = []
        for t1, t2 in intervals:
            if high <= t1 + POINT_TOLERANCE or low >= t2 - POINT_TOLERANCE:
                remaining.append((t1, t2))
                continue
            if low > t1 + POINT_TOLERANCE:
                remaining.append((t1, low))
            if high < t2 - POINT_TOLERANCE:
                remaining.append((high, t2))
        intervals = remaining
    return [(t1, t2) for t1, t2 in intervals if t2 - t1 > POINT_TOLERANCE]


def _draw_curves(doc, view, level, curves):
    """Create area boundary lines along curves (lines, arcs, ...). Return their ids.

    Two boundary lines on top of each other make Revit report 0 SF. Thus the
    parts of a straight edge that a boundary line in the view covers are not
    drawn. A curve that is not straight is drawn as it is.
    """
    elevation = level.ProjectElevation
    sketch_plane = view.SketchPlane
    existing = _existing_boundary_segments(doc, view)
    line_ids = []
    for curve in curves:
        # Boundary lines must be in the plane of the level
        z = curve.GetEndPoint(0).Z
        if abs(z - elevation) > 1e-9:
            curve = curve.CreateTransformed(
                DB.Transform.CreateTranslation(DB.XYZ(0, 0, elevation - z))
            )
        if not isinstance(curve, DB.Line):
            boundary = doc.Create.NewAreaBoundaryLine(sketch_plane, curve, view)
            line_ids.append(boundary.Id)
            continue
        start_point = curve.GetEndPoint(0)
        end_point = curve.GetEndPoint(1)
        start = (start_point.X, start_point.Y)
        end = (end_point.X, end_point.Y)
        length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        if length <= POINT_TOLERANCE:
            continue
        ux = (end[0] - start[0]) / length
        uy = (end[1] - start[1]) / length
        for t1, t2 in _uncovered_intervals(start, end, existing):
            line = DB.Line.CreateBound(
                DB.XYZ(start[0] + ux * t1, start[1] + uy * t1, elevation),
                DB.XYZ(start[0] + ux * t2, start[1] + uy * t2, elevation),
            )
            boundary = doc.Create.NewAreaBoundaryLine(sketch_plane, line, view)
            line_ids.append(boundary.Id)
    return line_ids


def _draw_outline(doc, view, level, outline):
    """Create the area boundary lines of a polygon outline. Return their ids."""
    elevation = level.ProjectElevation
    count = len(outline)
    lines = []
    for index in range(count):
        start = outline[index]
        end = outline[(index + 1) % count]
        lines.append(
            DB.Line.CreateBound(
                DB.XYZ(start[0], start[1], elevation),
                DB.XYZ(end[0], end[1], elevation),
            )
        )
    return _draw_curves(doc, view, level, lines)


def _exclusive_line_ids(doc, area, loops=None):
    """Ids of the boundary lines that bound this Area and no other Area.

    Args:
        loops (list): Only the lines of these loops (from _loops). Default:
            every loop of the Area, holes included.
    """
    if loops is None:
        loops = _loops(area)
    own = set()
    for loop in loops:
        own.update(loop["line_ids"])
    scheme_name = _scheme_name(area)
    for other in _all_areas(doc):
        if other.Id == area.Id or other.LevelId != area.LevelId:
            continue
        if _scheme_name(other) != scheme_name:
            continue
        for loop in _loops(other):
            own.difference_update(loop["line_ids"])
    return sorted(own)


# How far (feet) a kept shared line may be from the new outline and still lie on it
ON_OUTLINE_TOLERANCE = 0.01


def _shared_lines(doc, area, loops):
    """Lines of these loops that another Area of the scheme also uses.

    Returns:
        dict: line id -> labels ("number name") of the other Areas
    """
    line_ids = set()
    for loop in loops:
        line_ids.update(loop["line_ids"])
    shared = {}
    scheme_name = _scheme_name(area)
    for other in _all_areas(doc):
        if other.Id == area.Id or other.LevelId != area.LevelId:
            continue
        if _scheme_name(other) != scheme_name:
            continue
        name_parameter = other.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
        label = "{} {}".format(
            other.Number or "", (name_parameter.AsString() if name_parameter else "") or ""
        ).strip() or "id {}".format(id_value(other.Id))
        for loop in _loops(other):
            for line_id in line_ids.intersection(loop["line_ids"]):
                shared.setdefault(line_id, []).append(label)
    return shared


def _lies_on_outline(doc, line_id, outline):
    """True if a boundary line lies along the polygon (ends and middle on it)."""
    element = doc.GetElement(to_element_id(line_id))
    curve = getattr(element, "GeometryCurve", None)
    if curve is None:
        return False
    for parameter in (0.0, 0.5, 1.0):
        point = curve.Evaluate(parameter, True)
        if distance_to_polygon((point.X, point.Y), outline) > ON_OUTLINE_TOLERANCE:
            return False
    return True


def _refuse_shared_edge_change(area, neighbours):
    """Raise a 409: the change would leave a shared boundary line behind (K15)."""
    raise PDSOError(
        "Area {} shares a boundary line with {}, and this change would move it "
        "off that line. The neighbour still needs the line, and it would keep "
        "bounding this Area too (Revit would also split the neighbour with the "
        "new line). Change the shared edge in Revit, or redraw both Areas.".format(
            area.Number or id_value(area.Id),
            ", ".join("Area {}".format(label) for label in sorted(set(neighbours))),
        ),
        409,
    )


def create_area(doc, scheme, level, points, name=None, number=None):
    """Draw an outline and place an Area in it.

    Args:
        doc: Revit Document
        scheme (str): Area scheme name
        level: Level name or id
        points (list): Outline as [[x, y], ...] in feet
        name (str): Area name (optional)
        number (str): Area number (optional)

    Returns:
        dict: The new Area (see area_info), plus "warnings"
    """
    outline = _clean_outline(points)
    level_element = find_level(doc, level)

    # Revit computes an Area next to other Areas only after its boundary lines
    # are committed. Thus the lines and the Area are in two transactions. A new
    # area plan is made inside the group, so a failure removes it too.
    warnings = []
    with TransactionGroup(doc, "PDSO - Create Area"):
        view = _area_plan(doc, scheme, level_element)
        with Transaction(doc, "PDSO - Draw Area Outline") as transaction:
            _draw_outline(doc, view, level_element, outline)
        warnings.extend(transaction.warnings)
        with Transaction(doc, "PDSO - Place Area") as transaction:
            x, y = polygon_interior_point(outline)
            area = doc.Create.NewArea(view, DB.UV(x, y))
            if name is not None:
                area.get_Parameter(DB.BuiltInParameter.ROOM_NAME).Set(str(name))
            if number is not None:
                area.Number = str(number)
            doc.Regenerate()
            if area.Area <= 0:
                raise PDSOError(
                    "Revit gave the new Area 0 SF: the outline is not closed, or an"
                    " Area is in this outline already. No change was made."
                )
        warnings.extend(transaction.warnings)
    info = area_info(doc, area)
    info["warnings"] = warnings
    return info


def _inside_outline(loop_points, outline):
    """True if a loop lies wholly inside an outline (no point out, no crossing)."""
    if not all(point_in_polygon(x, y, outline) for x, y in loop_points):
        return False
    count = len(loop_points)
    outline_count = len(outline)
    for i in range(count):
        for j in range(outline_count):
            if _segments_cross(
                loop_points[i],
                loop_points[(i + 1) % count],
                outline[j],
                outline[(j + 1) % outline_count],
            ):
                return False
    return True


def set_area_outline(doc, area_id, points):
    """Replace the outer outline of an Area.

    The boundary lines of the outer outline that only this Area uses are
    deleted. New straight lines are drawn along the points. A hole that lies
    wholly inside the new outline keeps its lines; a hole that the new outline
    leaves or crosses is removed like the old outline (lines it shares with
    another Area stay). If the Area's location point is outside the new
    outline, the Area moves into it.

    Returns:
        dict: The Area after the change (see area_info), plus "previous_area"
            and "warnings"
    """
    area = get_area(doc, area_id)
    outline = _clean_outline(points)
    level = doc.GetElement(area.LevelId)
    previous_area = area.Area

    location = getattr(area.Location, "Point", None)
    if location is None:
        raise PDSOError("The Area is not placed. Delete it and create a new Area.")

    loops = _loops(area)
    outer = _outer_loop(loops)
    to_remove = [outer] if outer is not None else []
    warnings = []
    for loop in loops:
        if loop is outer or len(loop["points"]) < 3:
            continue
        if not _inside_outline(loop["points"], outline):
            to_remove.append(loop)
            warnings.append(
                "A hole of the Area was outside or across the new outline. "
                "Its boundary lines were removed."
            )

    # A line shared with a neighbour stays. That's right only if it is still
    # on the new outline; otherwise it would bound both Areas wrongly (K15).
    stranded = [
        label
        for line_id, labels in _shared_lines(doc, area, to_remove).items()
        if not _lies_on_outline(doc, line_id, outline)
        for label in labels
    ]
    if stranded:
        _refuse_shared_edge_change(area, stranded)

    with TransactionGroup(doc, "PDSO - Set Area Outline"):
        view = _area_plan(doc, _scheme_name(area), level)
        with Transaction(doc, "PDSO - Redraw Area Outline") as transaction:
            old_lines = _exclusive_line_ids(doc, area, to_remove)
            if old_lines:
                doc.Delete(id_list(old_lines))
                doc.Regenerate()
            _draw_outline(doc, view, level, outline)
        warnings.extend(transaction.warnings)
        with Transaction(doc, "PDSO - Check Area") as transaction:
            if not point_in_polygon(location.X, location.Y, outline):
                x, y = polygon_interior_point(outline)
                _move_location(area, location, x, y)
            doc.Regenerate()
            if area.Area <= 0:
                raise PDSOError(
                    "Revit did not enclose the Area with the new outline (0 SF)."
                    " No change was made."
                )
        warnings.extend(transaction.warnings)
    info = area_info(doc, area)
    info["previous_area"] = previous_area
    info["warnings"] = warnings
    return info


def _move_location(area, location, x, y):
    """Move the location point of an Area to (x, y). A pinned Area stays pinned."""
    was_pinned = area.Pinned
    if was_pinned:
        area.Pinned = False
    area.Location.Move(DB.XYZ(x - location.X, y - location.Y, 0))
    if was_pinned:
        area.Pinned = True


def _polygon_centroid(points):
    """Centroid of a polygon (the vertex average if it has no area)."""
    area_sum = 0.0
    cx = 0.0
    cy = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % count][0], points[(index + 1) % count][1]
        cross = x1 * y2 - x2 * y1
        area_sum += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area_sum) < 1e-9:
        return (
            sum(point[0] for point in points) / count,
            sum(point[1] for point in points) / count,
        )
    return cx / (3.0 * area_sum), cy / (3.0 * area_sum)


def _transform_area(doc, area, transform, name):
    """Move or scale an Area with a transform: its outline, holes and location.

    Each boundary curve keeps its kind: an arc stays an arc. The lines that
    only this Area uses are deleted. An Area that shares a boundary line with
    another Area is refused (409): the neighbour needs the line, so it would
    stay, keep bounding the moved Area, and the moved copy would split the
    neighbour (audit K15).

    Args:
        transform (DB.Transform): A move, or a uniform scale (conformal).
        name (str): Name of the undo step.

    Returns:
        dict: The Area after the change (see area_info), plus "previous_area"
            and "warnings"
    """
    location = getattr(area.Location, "Point", None)
    if location is None:
        raise PDSOError("The Area is not placed. Delete it and create a new Area.")
    level = doc.GetElement(area.LevelId)
    previous_area = area.Area

    shared = _shared_lines(doc, area, _loops(area))
    if shared:
        _refuse_shared_edge_change(
            area, [label for labels in shared.values() for label in labels]
        )

    curves = []
    for loop in area.GetBoundarySegments(DB.SpatialElementBoundaryOptions()) or []:
        for segment in loop:
            curves.append(segment.GetCurve().CreateTransformed(transform))
    if not curves:
        raise PDSOError("The Area has no closed outline.", 409)
    new_location = transform.OfPoint(location)

    warnings = []
    with TransactionGroup(doc, name):
        view = _area_plan(doc, _scheme_name(area), level)
        with Transaction(doc, "PDSO - Redraw Area Outline") as transaction:
            old_lines = _exclusive_line_ids(doc, area)
            if old_lines:
                doc.Delete(id_list(old_lines))
                doc.Regenerate()
            _draw_curves(doc, view, level, curves)
        warnings.extend(transaction.warnings)
        with Transaction(doc, "PDSO - Check Area") as transaction:
            _move_location(area, location, new_location.X, new_location.Y)
            doc.Regenerate()
            if area.Area <= 0:
                raise PDSOError(
                    "Revit did not enclose the Area with the new outline (0 SF)."
                    " No change was made."
                )
        warnings.extend(transaction.warnings)
    info = area_info(doc, area)
    info["previous_area"] = previous_area
    info["warnings"] = warnings
    return info


def _current_outline(doc, area):
    outer = _outer_loop(_loops(area))
    if outer is None:
        raise PDSOError("The Area has no closed outline.", 409)
    return [list(point) for point in outer["points"]]


def move_area_vertex(doc, area_id, index, x, y):
    """Move one point of the outer outline of an Area to a new position.

    The index is a point of "outline" in area_info. The outer outline is
    redrawn with straight lines, so an arc in it becomes straight segments.
    The holes are kept.
    """
    area = get_area(doc, area_id)
    outline = _current_outline(doc, area)
    if not isinstance(index, int) or index < 0 or index >= len(outline):
        raise PDSOError(
            "Vertex index must be 0 to {}. Got: {!r}".format(len(outline) - 1, index)
        )
    outline[index] = [to_number(x, "x"), to_number(y, "y")]
    return set_area_outline(doc, area_id, outline)


def move_area(doc, area_id, dx, dy, move_programs=True):
    """Move an Area, its outline and its holes by (dx, dy) feet.

    The Area and its programs move in one undo step. If a program cannot be
    moved, nothing changes.

    Args:
        doc: Revit Document
        area_id (int): Area element id
        dx (float): Move along X (feet)
        dy (float): Move along Y (feet)
        move_programs (bool): Also move the programs (Data Silos) in the Area

    Returns:
        dict: The Area after the change (see area_info), plus "moved_programs"
    """
    dx = to_number(dx, "dx")
    dy = to_number(dy, "dy")
    area = get_area(doc, area_id)
    old_outline = _current_outline(doc, area)
    # A program in a hole belongs to the island Area there, not to this one
    old_holes = [
        loop["points"]
        for loop in _loops(area)
        if len(loop["points"]) >= 3 and loop["points"] != old_outline
    ]

    programs = []
    if move_programs and config_service.config_exists(doc):
        for program in program_service.list_programs(doc):
            location = program["location"]
            element = get_element(doc, program["id"])
            if (
                location
                and element.LevelId == area.LevelId
                and point_in_area(location["x"], location["y"], old_outline, old_holes)
            ):
                programs.append(program)

    transform = DB.Transform.CreateTranslation(DB.XYZ(dx, dy, 0))
    moved = []
    with TransactionGroup(doc, "PDSO - Move Area"):
        info = _transform_area(doc, area, transform, "PDSO - Move Area Outline")
        for program in programs:
            location = program["location"]
            program_service.update_program(
                doc,
                program["id"],
                x=location["x"] + dx,
                y=location["y"] + dy,
            )
            moved.append(program["id"])
    info["moved_programs"] = moved
    return info


def scale_area(doc, area_id, factor):
    """Scale an Area (its outline and holes) about the centroid of its outline.

    Args:
        factor (float): Linear scale factor. 2.0 makes the area four times larger.
    """
    factor = to_number(factor, "factor")
    if factor <= 0:
        raise PDSOError("The scale factor must be more than 0.")
    area = get_area(doc, area_id)
    outline = _current_outline(doc, area)
    cx, cy = _polygon_centroid(outline)
    level = doc.GetElement(area.LevelId)
    center = DB.XYZ(cx, cy, level.ProjectElevation)
    # Move the centre to the origin, scale, and move it back
    transform = (
        DB.Transform.CreateTranslation(center)
        .Multiply(DB.Transform.Identity.ScaleBasisAndOrigin(factor))
        .Multiply(DB.Transform.CreateTranslation(center.Negate()))
    )
    return _transform_area(doc, area, transform, "PDSO - Scale Area")


def update_area(doc, area_id, name=None, number=None):
    """Change the name and the number of an Area."""
    area = get_area(doc, area_id)
    if name is None and number is None:
        raise PDSOError("Give a name or a number to change.")
    with Transaction(doc, "PDSO - Update Area"):
        if name is not None:
            area.get_Parameter(DB.BuiltInParameter.ROOM_NAME).Set(str(name))
        if number is not None:
            area.Number = str(number)
    return area_info(doc, area)


def delete_area(doc, area_id, delete_outline=True):
    """Delete an Area and the boundary lines that only it uses.

    Returns:
        dict: {"deleted": area id, "deleted_lines": [ids]}
    """
    area = get_area(doc, area_id)
    lines = _exclusive_line_ids(doc, area) if delete_outline else []
    with Transaction(doc, "PDSO - Delete Area"):
        if area.Pinned:
            area.Pinned = False
        doc.Delete(area.Id)
        if lines:
            doc.Delete(id_list(lines))
    return {"deleted": int(area_id), "deleted_lines": lines}
