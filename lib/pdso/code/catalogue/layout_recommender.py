# -*- coding: utf-8 -*-
"""Recommend step: rank catalogue entries for a target room.

No Revit imports. A target is described the same way an entry is: its room
frame and its outline in that frame. For each entry, all eight orientations
are tried and the best one that fits is kept.

An orientation fits when the oriented layout rectangle is no larger than the
target rectangle (within a tolerance) and every item lands inside the target
outline (within the same tolerance, because a family against a wall has its
insertion point on the boundary). The layout is centred in the target
rectangle. When more than one orientation fits equally snugly, the one whose
room outline covers the target outline best is kept.

Score (0-1):
    60%  area match     1 - |target - entry| / entry, floored at 0
    40%  snugness       how little spare room is left around the layout
Entries for another program are kept but listed after matching ones.
"""

from pdso.code.catalogue.layout_geometry import (
    ORIENTATIONS,
    orient_point,
    orient_size,
    outline_mismatch,
    point_in_or_near_polygon,
    point_in_polygon,
)

# How much larger than the target rectangle a layout may be (ft). Room
# boundaries are measured to finish faces or wall centrelines, so a few inches
# of difference between source and target is normal.
FIT_TOLERANCE = 0.5

# Positions on each axis that refine_placement tries between two sides
REFINE_STEPS = 8

AREA_WEIGHT = 0.6
SNUG_WEIGHT = 0.4


class Placement(object):
    """Where an entry's items go in the target room frame.

    Attributes:
        turns (int): Quarter turns, 0-3.
        mirror (bool): Mirrored across the entry's long axis first.
        shift (tuple): Added to oriented item points to centre the layout.
    """

    def __init__(self, turns, mirror, shift):
        self.turns = turns
        self.mirror = mirror
        self.shift = shift

    def describe(self):
        text = "rotated {} deg".format(self.turns * 90) if self.turns else "as harvested"
        if self.mirror:
            text += ", mirrored"
        return text


class Recommendation(object):
    """One ranked entry for a target room."""

    def __init__(self, entry, placement, score, area_score, snug_score, program_match):
        self.entry = entry
        self.placement = placement
        self.score = score
        self.area_score = area_score
        self.snug_score = snug_score
        self.program_match = program_match

    @property
    def label(self):
        """One line for a pick list."""
        tag = "" if self.program_match else "  [other program: {}]".format(
            self.entry.program
        )
        return "{:3.0f}%  {}  ({:.0f} sf, {:.0f}% area match, {}){}".format(
            self.score * 100,
            self.entry.name,
            self.entry.area,
            self.area_score * 100,
            self.placement.describe(),
            tag,
        )


def oriented_items(entry, placement):
    """Item points in the target room frame for a placement.

    Returns:
        list: (item, (x, y)) pairs.
    """
    result = []
    for item in entry.items:
        x, y = orient_point(
            (item.x, item.y), entry.width, entry.depth, placement.turns, placement.mirror
        )
        result.append((item, (x + placement.shift[0], y + placement.shift[1])))
    return result


def oriented_outline(entry, placement):
    """The entry's room outline in the target room frame for a placement."""
    outline = []
    for point in entry.boundary:
        x, y = orient_point(
            point, entry.width, entry.depth, placement.turns, placement.mirror
        )
        outline.append((x + placement.shift[0], y + placement.shift[1]))
    return outline


def best_placement(entry, target_width, target_depth, target_outline):
    """The snuggest orientation of an entry that fits the target, or None."""
    best = None
    for turns, mirror in ORIENTATIONS:
        width, depth = orient_size(entry.width, entry.depth, turns)
        if width > target_width + FIT_TOLERANCE or depth > target_depth + FIT_TOLERANCE:
            continue

        shift = ((target_width - width) / 2.0, (target_depth - depth) / 2.0)
        placement = Placement(turns, mirror, shift)

        if target_outline and not all(
            point_in_or_near_polygon(point, target_outline, FIT_TOLERANCE)
            for _, point in oriented_items(entry, placement)
        ):
            continue

        spare = max(0.0, target_width - width) + max(0.0, target_depth - depth)
        mismatch = 0.0
        if target_outline and entry.boundary:
            mismatch = outline_mismatch(
                oriented_outline(entry, placement), target_outline
            )
        # Prefer the snuggest fit, then the best outline match, then no mirror
        # and fewer turns
        rank = (round(spare, 2), round(mismatch, 2), mirror, turns)
        if best is None or rank < best[0]:
            best = (rank, placement, spare)

    if best is None:
        return None, None
    return best[1], best[2]


def refine_placement(entry, placement, target_width, target_depth, target_outline):
    """Move a placement to where the layout outline agrees best with the target.

    best_placement puts the layout in the middle of the target rectangle. If
    the target is larger than the layout (for example a wall jog makes its
    rectangle deeper), the items are then away from the walls that they were
    against. This function tries a grid of positions between the sides of the
    target rectangle and keeps the one with the smallest outline mismatch.
    A position is used only if it puts no more insertion points outside the
    target outline than the middle position does.

    Returns:
        Placement: The same orientation; the shift can be different.
    """
    if not target_outline or not entry.boundary:
        return placement
    width, depth = orient_size(entry.width, entry.depth, placement.turns)

    def shifts(spare, middle):
        # A layout that is larger than the target (in the tolerance) stays centred
        if spare <= 0.01:
            return [middle]
        return [spare * step / float(REFINE_STEPS) for step in range(REFINE_STEPS + 1)]

    def outside(candidate):
        return len(
            [
                1
                for _, point in oriented_items(entry, candidate)
                if not point_in_polygon(point, target_outline)
            ]
        )

    best = placement
    best_mismatch = outline_mismatch(oriented_outline(entry, placement), target_outline)
    allowed = outside(placement)
    for shift_x in shifts(target_width - width, placement.shift[0]):
        for shift_y in shifts(target_depth - depth, placement.shift[1]):
            candidate = Placement(placement.turns, placement.mirror, (shift_x, shift_y))
            if outside(candidate) > allowed:
                continue
            mismatch = outline_mismatch(
                oriented_outline(entry, candidate), target_outline
            )
            if mismatch < best_mismatch - 0.01:
                best, best_mismatch = candidate, mismatch
    return best


def recommend(entries, target_width, target_depth, target_area, target_outline, program):
    """Rank entries for a target room.

    Args:
        entries (list[LayoutEntry]): The catalogue.
        target_width, target_depth (float): Target room frame size (ft).
        target_area (float): Target room area (ft2).
        target_outline (list): Target outline in its room frame, [(x, y), ...].
        program (str): Target program; entries for it are listed first.
    Returns:
        tuple: (recommendations, rejected). rejected is a list of
            (entry, reason) for entries that fit in no orientation.
    """
    wanted = (program or "").strip().lower()
    recommendations, rejected = [], []

    for entry in entries:
        placement, spare = best_placement(
            entry, target_width, target_depth, target_outline
        )
        if placement is None:
            rejected.append(
                (
                    entry,
                    "{:.1f} x {:.1f} ft does not fit {:.1f} x {:.1f} ft".format(
                        entry.width, entry.depth, target_width, target_depth
                    ),
                )
            )
            continue

        area_score = 0.0
        if entry.area > 0:
            area_score = max(0.0, 1.0 - abs(target_area - entry.area) / entry.area)
        snug_score = 1.0 / (1.0 + spare / 10.0)
        score = AREA_WEIGHT * area_score + SNUG_WEIGHT * snug_score
        program_match = not wanted or entry.program.strip().lower() == wanted

        recommendations.append(
            Recommendation(entry, placement, score, area_score, snug_score, program_match)
        )

    recommendations.sort(key=lambda r: (not r.program_match, -r.score))
    return recommendations, rejected
