# -*- coding: utf-8 -*-
"""Area boundary validation and repair.

Shared by the sync (which builds Program Volume geometry from GSF Area boundaries)
and the audit (which reports Areas whose boundaries are unclosed or poorly built),
so the audit predicts exactly what the sync will do with each Area.
"""

# 3rd Party Modules
from rpw import revit, DB
from rpw.utils.dotnet import List

# Program Volume extrusion height used by the sync (ft)
PROGRAM_VOLUME_EXTRUSION_HEIGHT = 8.0

# Kinds of repair recorded by build_closed_curve_loop
REPAIR_REORDER = "reorder"
REPAIR_SNAP = "snap"
REPAIR_BRIDGE = "bridge"

# Results of check_area_boundary, most severe first
STATUS_NOT_PLACED = "not_placed"
STATUS_NOT_ENCLOSED = "not_enclosed"
STATUS_UNCLOSABLE = "unclosable"
STATUS_INVALID_PROFILE = "invalid_profile"
STATUS_REPAIRED = "repaired"
STATUS_OK = "ok"

ERROR_STATUSES = (
    STATUS_NOT_PLACED,
    STATUS_NOT_ENCLOSED,
    STATUS_UNCLOSABLE,
    STATUS_INVALID_PROFILE,
)


class AreaBoundaryCheck(object):
    """Result of checking one Area's boundary.

    Attributes:
        status (str): One of the STATUS_* constants.
        detail (str): Revit's error message when the boundary can't be used, else "".
        repairs (list[tuple[str, float]]): (REPAIR_* kind, gap in ft) for each repair
            the sync would make to close the boundary.
    """

    def __init__(self, status, detail="", repairs=None):
        self.status = status
        self.detail = detail
        self.repairs = repairs or []

    @property
    def is_error(self):
        """True if the sync can't build Program Volume geometry from this Area."""
        return self.status in ERROR_STATUSES

    @property
    def gaps(self):
        """Sizes (ft) of the gaps that were snapped or bridged."""
        return [gap for kind, gap in self.repairs if kind != REPAIR_REORDER]

    @property
    def reordered(self):
        """True if boundary segments were out of order or reversed."""
        return any(kind == REPAIR_REORDER for kind, gap in self.repairs)


def get_boundary_options():
    """Boundary options used to build Program Volume geometry (boundary centerline)."""
    opts = DB.SpatialElementBoundaryOptions()
    opts.StoreFreeBoundaryFaces = False
    opts.SpatialElementBoundaryLocation = DB.SpatialElementBoundaryLocation.Center
    return opts


def check_area_boundary(area):
    """Check whether an Area's boundary can be turned into Program Volume geometry.

    Builds the same profile the sync builds, without modifying the model.

    Args:
        area (DB.Area): The Area to check.
    Returns:
        AreaBoundaryCheck: The result.
    """
    if area.Location is None:
        return AreaBoundaryCheck(STATUS_NOT_PLACED)

    boundary_segments = area.GetBoundarySegments(get_boundary_options())

    # Revit reports Not Enclosed and Redundant Areas with zero area
    if area.Area <= 0 or not boundary_segments or len(boundary_segments) == 0:
        return AreaBoundaryCheck(STATUS_NOT_ENCLOSED)

    repairs = []
    curve_loops = List[DB.CurveLoop]()
    try:
        for polygon in boundary_segments:
            curves = [segment.GetCurve() for segment in polygon]
            curve_loops.Add(build_closed_curve_loop(curves, repairs))
    except Exception as error:
        return AreaBoundaryCheck(STATUS_UNCLOSABLE, str(error), repairs)

    try:
        DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            curve_loops, DB.XYZ.BasisZ, PROGRAM_VOLUME_EXTRUSION_HEIGHT
        )
    except Exception as error:
        return AreaBoundaryCheck(STATUS_INVALID_PROFILE, str(error), repairs)

    if repairs:
        return AreaBoundaryCheck(STATUS_REPAIRED, repairs=repairs)

    return AreaBoundaryCheck(STATUS_OK)


def build_closed_curve_loop(curves, repairs=None):
    """Chain boundary curves into a closed CurveLoop, healing small defects.

    Area boundaries can come back out of order, reversed, or with small gaps
    where boundary lines don't quite meet, which makes DB.CurveLoop.Create fail
    ("curves are not contiguous") or produce an open profile. Curves are chained
    nearest-endpoint first, reversed where needed, and gaps are closed by snapping
    line endpoints (gaps too short for a new curve) or bridging with a new line.

    Args:
        curves (list[DB.Curve]): Boundary curves of a single polygon.
        repairs (list, optional): If given, a (REPAIR_* kind, gap in ft) tuple is
            appended for each repair made.
    Returns:
        DB.CurveLoop: A closed curve loop.
    Raises:
        Exception: If the curves can't be made into a closed loop.
    """
    if repairs is None:
        repairs = []

    vertex_tolerance = revit.app.VertexTolerance
    short_curve_tolerance = revit.app.ShortCurveTolerance

    remaining = list(curves)
    ordered = [remaining.pop(0)]

    while remaining:
        end_point = ordered[-1].GetEndPoint(1)

        # Pick the curve with the nearest endpoint, reversing it if needed
        best_index, best_reverse, best_distance = None, False, None
        for index, curve in enumerate(remaining):
            for reverse, point in (
                (False, curve.GetEndPoint(0)),
                (True, curve.GetEndPoint(1)),
            ):
                distance = end_point.DistanceTo(point)
                if best_distance is None or distance < best_distance:
                    best_index, best_reverse, best_distance = (
                        index,
                        reverse,
                        distance,
                    )

        if best_index != 0 or best_reverse:
            repairs.append((REPAIR_REORDER, 0.0))

        next_curve = remaining.pop(best_index)
        if best_reverse:
            next_curve = next_curve.CreateReversed()

        ordered.extend(
            _join_curves(
                ordered.pop(),
                next_curve,
                vertex_tolerance,
                short_curve_tolerance,
                repairs,
            )
        )

    # Close the loop between the last and first curve
    if len(ordered) > 1:
        first_curve = ordered.pop(0)
        closing = _join_curves(
            ordered.pop(),
            first_curve,
            vertex_tolerance,
            short_curve_tolerance,
            repairs,
        )
        ordered = [closing[-1]] + ordered + closing[:-1]

    temp_loop = List[DB.Curve]()
    for curve in ordered:
        temp_loop.Add(curve)

    curve_loop = DB.CurveLoop.Create(temp_loop)
    if curve_loop.IsOpen():
        raise Exception("profile is still open after healing")

    return curve_loop


def _join_curves(
    previous_curve, next_curve, vertex_tolerance, short_curve_tolerance, repairs
):
    """Make next_curve start where previous_curve ends.

    Returns:
        list[DB.Curve]: The curves to use in place of previous_curve and next_curve,
            in order (a bridging line may be inserted between them).
    """
    end_point = previous_curve.GetEndPoint(1)
    start_point = next_curve.GetEndPoint(0)
    gap = end_point.DistanceTo(start_point)

    if gap <= vertex_tolerance:
        return [previous_curve, next_curve]

    if gap >= short_curve_tolerance:
        repairs.append((REPAIR_BRIDGE, gap))
        return [
            previous_curve,
            DB.Line.CreateBound(end_point, start_point),
            next_curve,
        ]

    # Gap is too short for its own curve; snap a line endpoint across it
    if isinstance(next_curve, DB.Line):
        repairs.append((REPAIR_SNAP, gap))
        return [
            previous_curve,
            DB.Line.CreateBound(end_point, next_curve.GetEndPoint(1)),
        ]
    if isinstance(previous_curve, DB.Line):
        repairs.append((REPAIR_SNAP, gap))
        return [
            DB.Line.CreateBound(previous_curve.GetEndPoint(0), start_point),
            next_curve,
        ]

    raise Exception(
        "{:.3f} in gap between two curved boundary lines".format(gap * 12)
    )
