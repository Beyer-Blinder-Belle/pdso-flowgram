# -*- coding: utf-8 -*-
"""Catalogue entry: one harvested unit layout, as plain data.

An entry is what one JSON file in the catalogue holds. No Revit imports.
All lengths are feet and all angles radians, in the room's own frame
(see layout_geometry.RoomFrame).
"""

import re
from datetime import datetime

# 2 added "features": what surrounds the layout (walls, doors, windows, shape).
# Version 1 files still load; they simply have no features.
SCHEMA_VERSION = 2

# Every number in an entry is in these units (Revit's internal ones). They are
# written into each file so a reader never has to guess feet or metres.
UNITS = {"length": "ft", "area": "ft2", "angle": "rad"}


class LayoutItem(object):
    """One placed family in a layout.

    Attributes:
        category (str): Revit category name, e.g. "Furniture".
        family (str): Family name.
        type (str): Type name.
        x, y (float): Location in the room frame (ft).
        z_offset (float): Height of the location point above the level (ft).
        rotation (float): Rotation in the room frame (radians).
        hand_flipped, facing_flipped (bool): Flip state of the instance.
        mirrored (bool): True if the instance was mirrored in the source model.
    """

    def __init__(
        self,
        category,
        family,
        type_name,
        x,
        y,
        z_offset=0.0,
        rotation=0.0,
        hand_flipped=False,
        facing_flipped=False,
        mirrored=False,
    ):
        self.category = category
        self.family = family
        self.type = type_name
        self.x = x
        self.y = y
        self.z_offset = z_offset
        self.rotation = rotation
        self.hand_flipped = hand_flipped
        self.facing_flipped = facing_flipped
        self.mirrored = mirrored

    @property
    def symbol_key(self):
        """(family, type), the key used to find the type in a target model."""
        return (self.family, self.type)

    def to_dict(self):
        return {
            "category": self.category,
            "family": self.family,
            "type": self.type,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "z_offset": round(self.z_offset, 4),
            "rotation": round(self.rotation, 6),
            "hand_flipped": self.hand_flipped,
            "facing_flipped": self.facing_flipped,
            "mirrored": self.mirrored,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("category", ""),
            data["family"],
            data["type"],
            float(data["x"]),
            float(data["y"]),
            float(data.get("z_offset", 0.0)),
            float(data.get("rotation", 0.0)),
            bool(data.get("hand_flipped", False)),
            bool(data.get("facing_flipped", False)),
            bool(data.get("mirrored", False)),
        )


class LayoutEntry(object):
    """One catalogue entry.

    Attributes:
        id (str): File-safe identifier, also the JSON file name.
        name (str): Human name, e.g. "1 Bedroom - 650 sf, corner".
        program (str): Program the layout serves, e.g. "1 Bedroom". Used to filter.
        project_type (str): e.g. "Multifamily", "K-12". Used to filter.
        width, depth (float): Room frame size (ft).
        area (float): Room area (ft2).
        boundary (list): Room outline in the room frame, [(x, y), ...].
        items (list[LayoutItem]): Placed families.
        source (dict): Where it was harvested from (project name, element id, date).
        notes (str): Free text.
        features (dict): What the room is like around the layout, used to match
            it to a target room. Empty for entries harvested before schema 2.
            All points are in the room frame.
                perimeter (float)
                edges (list): one per boundary segment, in boundary order:
                    start, end ([x, y]), length,
                    kind: "exterior" | "corridor" | "party" | "unroomed",
                    beyond: name of the room on the other side, if any,
                    wall_type, glazed (True for a curtain wall)
                edge_lengths (dict): total length per kind
                window_wall_length (float): length of exterior boundary
                glazed_length (float): window widths plus curtain wall length
                openings (list): doors and windows in the boundary walls:
                    kind: "door" | "window", family, type, x, y, u, v,
                    width, sill_height, edge (index into edges)
                entry (dict or None): the entrance door: x, y, u, v, edge,
                    direction ([dx, dy], pointing into the room)
                shape_matrix (list): see layout_geometry.shape_matrix
                shape (dict): category ("rectangle", "L", "T", "Z", "U",
                    "other"), corners, reflex_corners, turns, orthogonal.
                    The same for a unit however it is turned, mirrored or
                    stretched; see layout_geometry.shape_category
                unit (dict): bedrooms, bathrooms, kitchens, room_count, and
                    pdso: every "PDSO - ..." parameter of the unit's data silo,
                    as a list of {key, value, display}; value is None when the
                    parameter is empty, display is the value with its unit
                rooms (list): rooms inside the unit: name, area, x, y, boundary
                internal_walls (list): partitions: start, end, width, wall_type
                internal_doors (list): family, type, x, y, width, rotation,
                    hand_flipped, facing_flipped, wall (index into internal_walls)
                hosted_items (list): wall- and face-hosted families, recorded
                    but not placed by Create: category, family, type, x, y,
                    z_offset, rotation, host, edge, edge_distance
    """

    def __init__(
        self,
        name,
        program,
        project_type,
        width,
        depth,
        area,
        boundary,
        items,
        source=None,
        notes="",
        entry_id=None,
        features=None,
    ):
        self.name = name
        self.program = program
        self.project_type = project_type
        self.width = width
        self.depth = depth
        self.area = area
        self.boundary = boundary
        self.items = items
        self.source = source or {}
        self.notes = notes
        self.features = features or {}
        self.id = entry_id or make_entry_id(program, name)

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "units": UNITS,
            "id": self.id,
            "name": self.name,
            "program": self.program,
            "project_type": self.project_type,
            "width": round(self.width, 4),
            "depth": round(self.depth, 4),
            "area": round(self.area, 2),
            "boundary": [[round(x, 4), round(y, 4)] for x, y in self.boundary],
            "items": [item.to_dict() for item in self.items],
            "source": self.source,
            "notes": self.notes,
            "features": self.features,
        }

    @classmethod
    def from_dict(cls, data):
        version = data.get("schema_version", 1)
        if version > SCHEMA_VERSION:
            raise ValueError(
                "entry {} uses schema {}, this tool reads up to {}".format(
                    data.get("id"), version, SCHEMA_VERSION
                )
            )
        return cls(
            data["name"],
            data.get("program", ""),
            data.get("project_type", ""),
            float(data["width"]),
            float(data["depth"]),
            float(data.get("area", 0.0)),
            [(float(x), float(y)) for x, y in data.get("boundary", [])],
            [LayoutItem.from_dict(item) for item in data.get("items", [])],
            data.get("source", {}),
            data.get("notes", ""),
            data.get("id"),
            data.get("features", {}),
        )


def slugify(text):
    """Lower-case, file-safe text."""
    text = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return text or "layout"


def make_entry_id(program, name):
    """program-name-timestamp, unique enough for a hand-built catalogue."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return "{}--{}--{}".format(slugify(program), slugify(name), stamp)
