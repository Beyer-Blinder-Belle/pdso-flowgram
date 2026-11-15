# -*- coding: utf-8 -*-
"""Catalogue entry: one harvested unit layout, as plain data.

An entry is what one JSON file in the catalogue holds. No Revit imports.
All lengths are feet and all angles radians, in the room's own frame
(see layout_geometry.RoomFrame).
"""

import re
from datetime import datetime

SCHEMA_VERSION = 1


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
        name (str): Human name, e.g. "Classroom - 28 seats, teaching wall north".
        program (str): Program the layout serves, e.g. "Classroom". Used to filter.
        project_type (str): e.g. "K-12", "Residential". Used to filter.
        width, depth (float): Room frame size (ft).
        area (float): Room area (ft2).
        boundary (list): Room outline in the room frame, [(x, y), ...].
        items (list[LayoutItem]): Placed families.
        source (dict): Where it was harvested from (project name, element id, date).
        notes (str): Free text.
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
        self.id = entry_id or make_entry_id(program, name)

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
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
        )


def slugify(text):
    """Lower-case, file-safe text."""
    text = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return text or "layout"


def make_entry_id(program, name):
    """program-name-timestamp, unique enough for a hand-built catalogue."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return "{}--{}--{}".format(slugify(program), slugify(name), stamp)
