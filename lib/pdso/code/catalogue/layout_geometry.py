# -*- coding: utf-8 -*-
"""Plain 2D geometry for the Unit Layout Catalogue.

No Revit imports, so this module can be read, reused and tested outside Revit.
Points are (x, y) tuples in feet. Angles are radians, counter-clockwise.

The idea that makes a layout reusable: every layout is stored in its room's
own frame, not in project coordinates. The frame is the bounding rectangle
aligned to the direction most of the room's boundary runs in (the "most popular
vector"), origin at its corner, X along its longer side. An apartment harvested
from one building can then be placed into a space in another.

The frame, the shape matrix and the eight orientations follow Oliver Green,
"An Introspective Approach to Apartment Design" (AHMM, 2020).
"""

import math

TOLERANCE = 1e-9


def polygon_area(points):
    """Signed area (shoelace). Positive when the points run counter-clockwise."""
    total = 0.0
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def point_in_polygon(point, polygon):
    """Ray-casting test. Points exactly on an edge may go either way."""
    x, y = point
    inside = False
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def distance_to_polygon(point, polygon):
    """Shortest distance from a point to the edges of a polygon."""
    x, y = point
    best = None
    count = len(polygon)
    for i in range(count):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % count]
        dx, dy = bx - ax, by - ay
        length_squared = dx * dx + dy * dy
        if length_squared < TOLERANCE:
            t = 0.0
        else:
            t = ((x - ax) * dx + (y - ay) * dy) / length_squared
            t = max(0.0, min(1.0, t))
        distance = math.hypot(x - ax - t * dx, y - ay - t * dy)
        if best is None or distance < best:
            best = distance
    return best


def point_in_or_near_polygon(point, polygon, tolerance):
    """True if the point is in the polygon, or no farther than ``tolerance`` from it.

    A family that stands against a wall has its insertion point on the room
    boundary. In a second room with the same plan, that point can be a fraction
    of an inch outside the boundary.
    """
    if point_in_polygon(point, polygon):
        return True
    return distance_to_polygon(point, polygon) <= tolerance


def outline_mismatch(outline_a, outline_b):
    """How far two outlines are from each other, in feet. 0.0 for the same outline.

    The value is the mean distance from each corner of one outline to the edges
    of the other outline, in the two directions. It is exact, so it also sees a
    small wall jog that tells two orientations of an almost symmetric room apart.

    Args:
        outline_a, outline_b (list): [(x, y), ...] in the same frame.
    """
    if len(outline_a) < 3 or len(outline_b) < 3:
        return float("inf")
    a_to_b = sum(distance_to_polygon(point, outline_b) for point in outline_a)
    b_to_a = sum(distance_to_polygon(point, outline_a) for point in outline_b)
    return (a_to_b / len(outline_a) + b_to_a / len(outline_b)) / 2.0


def outline_overlap(outline_a, outline_b, grid=16):
    """How well two outlines cover each other: intersection over union, 0 to 1.

    The value is an estimate from a grid of sample points, so two outlines that
    are the same give 1.0 and a small difference gives a small change.

    Args:
        outline_a, outline_b (list): [(x, y), ...] in the same frame.
        grid (int): Number of sample points on each side of the grid.
    """
    if len(outline_a) < 3 or len(outline_b) < 3:
        return 0.0
    xs = [p[0] for p in outline_a] + [p[0] for p in outline_b]
    ys = [p[1] for p in outline_a] + [p[1] for p in outline_b]
    min_x, min_y = min(xs), min(ys)
    step_x = (max(xs) - min_x) / float(grid)
    step_y = (max(ys) - min_y) / float(grid)
    both = 0
    either = 0
    for i in range(grid):
        x = min_x + (i + 0.5) * step_x
        for j in range(grid):
            y = min_y + (j + 0.5) * step_y
            in_a = point_in_polygon((x, y), outline_a)
            in_b = point_in_polygon((x, y), outline_b)
            if in_a and in_b:
                both += 1
            if in_a or in_b:
                either += 1
    return both / float(either) if either else 0.0


def rotate(point, angle):
    """Rotate a point about the origin."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    x, y = point
    return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)


def normalize_angle(angle):
    """Bring an angle into [0, 2*pi)."""
    full = 2.0 * math.pi
    angle = angle % full
    if angle < 0:
        angle += full
    return angle


class RoomFrame(object):
    """A room's own coordinate frame: a bounding rectangle lined up with its walls.

    Attributes:
        angle (float): Direction of the frame's X axis in project coordinates.
        offset (tuple): Rectangle min corner, in coordinates rotated by -angle.
        width (float): Rectangle size along X (the longer side).
        depth (float): Rectangle size along Y.
    """

    def __init__(self, angle, offset, width, depth):
        self.angle = angle
        self.offset = offset
        self.width = width
        self.depth = depth

    def to_local(self, point):
        """Project coordinates -> frame coordinates."""
        x, y = rotate(point, -self.angle)
        return (x - self.offset[0], y - self.offset[1])

    def to_world(self, point):
        """Frame coordinates -> project coordinates."""
        x, y = point
        return rotate((x + self.offset[0], y + self.offset[1]), self.angle)

    @classmethod
    def from_polygon(cls, polygon):
        """Fit the bounding rectangle aligned to the room's dominant direction.

        The X axis follows the direction that the greatest length of boundary
        runs in (see dominant_direction). For a rectangular room this is the
        same rectangle as before; for an irregular room it stays lined up with
        the walls furniture is set out against, where a minimum-area rectangle
        can end up on a diagonal.
        """
        angle = dominant_direction(polygon)
        if angle is None:
            raise ValueError("polygon has no edges")

        offset, width, depth = _bounds_at(polygon, angle)

        # Keep X along the longer side, so "width" means the same thing everywhere
        if depth > width + TOLERANCE:
            angle += math.pi / 2.0
            offset, width, depth = _bounds_at(polygon, angle)

        return cls(normalize_angle(angle), offset, width, depth)


def _bounds_at(polygon, angle):
    """Min corner and size of the polygon's bounding box turned to an angle."""
    rotated = [rotate(p, -angle) for p in polygon]
    xs = [p[0] for p in rotated]
    ys = [p[1] for p in rotated]
    return (min(xs), min(ys)), max(xs) - min(xs), max(ys) - min(ys)


# Edges within this angle of each other count as running the same way
DIRECTION_TOLERANCE = math.radians(1.0)


def dominant_direction(polygon):
    """The direction most of a polygon's boundary runs in: the "most popular vector".

    Edges are grouped by direction, ignoring which way they point and treating
    perpendicular edges as one group (a room's walls mostly sit on one
    orthogonal grid). The group with the greatest total length wins.

    Returns:
        float: Angle in [0, pi/2), or None for a polygon with no edges.
    """
    quarter = math.pi / 2.0
    groups = []  # [angle of first edge, total length]
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        length = math.hypot(x2 - x1, y2 - y1)
        if length < TOLERANCE:
            continue
        angle = math.atan2(y2 - y1, x2 - x1) % quarter
        for group in groups:
            difference = abs(angle - group[0])
            if min(difference, quarter - difference) < DIRECTION_TOLERANCE:
                group[1] += length
                break
        else:
            groups.append([angle, length])

    if not groups:
        return None
    return max(groups, key=lambda group: group[1])[0]


def point_segment_distance(point, start, end):
    """Distance from a point to a segment, and where along it the closest point is.

    Returns:
        tuple: (distance, t), t from 0 at start to 1 at end.
    """
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared < TOLERANCE:
        return math.hypot(px - x1, py - y1), 0.0
    t = ((px - x1) * dx + (py - y1) * dy) / length_squared
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)), t


# ---------------------------------------------------------------------------
# Shape matrix
# ---------------------------------------------------------------------------

MATRIX_SIZE = 10


def shape_matrix(polygon, width, depth, size=MATRIX_SIZE):
    """A room's shape as a size x size grid over its frame rectangle.

    Each cell is 1 when its centre is inside the room, else 0. A rectangular
    room is all ones; an L-shaped room has a block of zeros in one corner.
    Two rooms' shapes are compared cell by cell (see matrix_difference).

    Args:
        polygon (list): Room outline in the room frame.
    Returns:
        list: Rows from y = 0 upward, each a list of 0/1 from x = 0 upward.
    """
    rows = []
    for row in range(size):
        y = (row + 0.5) * depth / size
        cells = []
        for column in range(size):
            x = (column + 0.5) * width / size
            cells.append(1 if point_in_polygon((x, y), polygon) else 0)
        rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# Shape category
# ---------------------------------------------------------------------------

# Corners closer together than this are one corner: a pilaster or a small jog
# in a wall does not change what shape a unit is
SHAPE_TOLERANCE = 1.0

# Corner sequences, in their canonical form, that have a name.
# C is a convex corner (the outline turns toward the inside), R a reflex one.
SHAPE_NAMES = {
    "CCCC": "rectangle",
    "CCCCCR": "L",
    "CCCCRCCR": "T",
    "CCCRCCCR": "Z",
    "CCCCCCRR": "U",
}


def _simplify(polygon, tolerance):
    """Snap corners to a grid, then drop repeated and in-line points."""
    snapped = []
    for x, y in polygon:
        point = (round(x / tolerance) * tolerance, round(y / tolerance) * tolerance)
        if not snapped or point != snapped[-1]:
            snapped.append(point)
    if len(snapped) > 1 and snapped[0] == snapped[-1]:
        snapped.pop()

    changed = True
    while changed and len(snapped) > 3:
        changed = False
        count = len(snapped)
        for i in range(count):
            ax, ay = snapped[i - 1]
            bx, by = snapped[i]
            cx, cy = snapped[(i + 1) % count]
            cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
            if abs(cross) < TOLERANCE:
                del snapped[i]
                changed = True
                break
    return snapped


def shape_category(polygon, tolerance=SHAPE_TOLERANCE):
    """Name a unit's shape so that turned, mirrored and stretched copies match.

    The outline is walked counter-clockwise and each corner is written down as
    C (convex) or R (reflex). The sequence does not depend on how long the
    sides are, so a long L and a short L give the same one. Turning the unit
    only changes where the sequence starts, and mirroring only reverses it, so
    the sequence is put in one canonical form: the alphabetically first of all
    its rotations, forward and reversed. An L-shaped unit is "CCCCCR" however
    it is turned, mirrored or proportioned.

    Args:
        polygon (list): Outline in the room frame.
    Returns:
        dict: category (a name from SHAPE_NAMES, else "other"), corners,
            reflex_corners, turns (the canonical sequence), and orthogonal
            (False when a corner is more than 5 degrees off a right angle).
    """
    points = _simplify(polygon, tolerance)
    if len(points) < 3:
        return {"category": "other", "corners": len(points), "reflex_corners": 0,
                "turns": "", "orthogonal": False}
    if polygon_area(points) < 0:
        points.reverse()

    letters, orthogonal = [], True
    count = len(points)
    for i in range(count):
        ax, ay = points[i - 1]
        bx, by = points[i]
        cx, cy = points[(i + 1) % count]
        ux, uy = bx - ax, by - ay
        vx, vy = cx - bx, cy - by
        cross = ux * vy - uy * vx
        dot = ux * vx + uy * vy
        letters.append("C" if cross > 0 else "R")
        # sine of 5 degrees: how far off square a corner may be
        if abs(dot) > 0.087 * math.hypot(ux, uy) * math.hypot(vx, vy):
            orthogonal = False

    sequence = "".join(letters)
    candidates = []
    for text in (sequence, sequence[::-1]):
        for start in range(count):
            candidates.append(text[start:] + text[:start])
    turns = min(candidates)

    category = SHAPE_NAMES.get(turns, "other") if orthogonal else "other"
    return {
        "category": category,
        "corners": count,
        "reflex_corners": letters.count("R"),
        "turns": turns,
        "orthogonal": orthogonal,
    }


def orient_matrix(matrix, turns, mirror):
    """Map a square shape matrix through one orientation, as orient_point does."""
    size = len(matrix)
    result = [[0] * size for _ in range(size)]
    for row in range(size):
        for column in range(size):
            x, y = orient_point((column + 0.5, row + 0.5), size, size, turns, mirror)
            result[int(y)][int(x)] = matrix[row][column]
    return result


def matrix_difference(first, second):
    """Share of cells that differ between two matrices of the same size, 0-1."""
    total, different = 0, 0
    for row_a, row_b in zip(first, second):
        for a, b in zip(row_a, row_b):
            total += 1
            if a != b:
                different += 1
    return float(different) / total if total else 0.0


# The eight ways a rectangle maps onto itself: four turns, each with or without
# a mirror. A layout may be placed in any of them.
ORIENTATIONS = [(turns, mirror) for mirror in (False, True) for turns in range(4)]


def orient_point(point, width, depth, turns, mirror):
    """Map a point inside a width x depth rectangle through one orientation.

    The mirror is applied first (across the rectangle's long axis), then the
    quarter turns. The result stays in the positive quadrant.

    Returns:
        tuple: (x, y) in the oriented rectangle.
    """
    x, y = point
    if mirror:
        y = depth - y
    turns = turns % 4
    if turns == 0:
        return (x, y)
    if turns == 1:
        return (depth - y, x)
    if turns == 2:
        return (width - x, depth - y)
    return (y, width - x)


def orient_size(width, depth, turns):
    """Size of the rectangle after the quarter turns."""
    if turns % 2 == 0:
        return (width, depth)
    return (depth, width)


def orient_angle(angle, turns, mirror):
    """Map an element's rotation through one orientation."""
    if mirror:
        angle = -angle
    return normalize_angle(angle + turns * math.pi / 2.0)


def extent_along(size_x, size_y, direction):
    """Length of a thin box along its own direction, from its upright bounds.

    A window's bounding box is given square to the model axes. When the wall
    is turned, that box is larger than the window; this takes the turn back
    out. direction is the wall's direction, (dx, dy).

    Returns:
        float: The length along direction, or None when it can't be told
            (a wall at 45 degrees gives a square box for any window).
    """
    length = math.hypot(direction[0], direction[1])
    if length < 1e-9:
        return None
    dx, dy = abs(direction[0]) / length, abs(direction[1]) / length
    spread = dx * dx - dy * dy
    if abs(spread) < 0.2:
        return None
    return max(0.0, (size_x * dx - size_y * dy) / spread)


def overlap_along(start, end, a, b):
    """How much of the edge start-end the segment a-b covers, laid onto it (ft)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0
    ux, uy = dx / length, dy / length
    first = (a[0] - start[0]) * ux + (a[1] - start[1]) * uy
    second = (b[0] - start[0]) * ux + (b[1] - start[1]) * uy
    low, high = min(first, second), max(first, second)
    return max(0.0, min(high, length) - max(low, 0.0))
