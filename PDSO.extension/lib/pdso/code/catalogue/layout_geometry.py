# -*- coding: utf-8 -*-
"""Plain 2D geometry for the Unit Layout Catalogue.

No Revit imports, so this module can be read, reused and tested outside Revit.
Points are (x, y) tuples in feet. Angles are radians, counter-clockwise.

The idea that makes a layout reusable: every layout is stored in its room's
own frame, not in project coordinates. The frame is the room's minimum-area
bounding rectangle, origin at its corner, X along its longer side. A classroom
harvested from one building can then be placed into a room in another.
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
    """A room's own coordinate frame: its minimum-area bounding rectangle.

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
        """Fit the minimum-area bounding rectangle, trying every edge direction.

        Rooms are mostly rectilinear, so one of the edge directions gives the
        tight rectangle. A brute-force search is fine for a few dozen edges.
        """
        best = None
        count = len(polygon)
        for i in range(count):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % count]
            if abs(x2 - x1) < TOLERANCE and abs(y2 - y1) < TOLERANCE:
                continue
            angle = math.atan2(y2 - y1, x2 - x1)
            rotated = [rotate(p, -angle) for p in polygon]
            xs = [p[0] for p in rotated]
            ys = [p[1] for p in rotated]
            width = max(xs) - min(xs)
            depth = max(ys) - min(ys)
            area = width * depth
            if best is None or area < best[0] - TOLERANCE:
                best = (area, angle, (min(xs), min(ys)), width, depth)

        if best is None:
            raise ValueError("polygon has no edges")

        _, angle, offset, width, depth = best

        # Keep X along the longer side, so "width" means the same thing everywhere
        if depth > width + TOLERANCE:
            angle += math.pi / 2.0
            rotated = [rotate(p, -angle) for p in polygon]
            xs = [p[0] for p in rotated]
            ys = [p[1] for p in rotated]
            offset = (min(xs), min(ys))
            width, depth = depth, width

        return cls(normalize_angle(angle), offset, width, depth)


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
