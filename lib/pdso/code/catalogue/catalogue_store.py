# -*- coding: utf-8 -*-
"""Store step: read and write catalogue entries as JSON files.

Each entry is one <id>.json, with a <id>.svg plan sketch beside it so the
catalogue can be browsed on GitHub or in a file explorer. No Revit imports.

Two folders are read:
    catalogue/sample/  committed to the repo; made-up entries for trying the tool
    catalogue/local/   git-ignored; where harvests go unless another folder is set

Harvests from real projects go to the local folder (or a folder the user
chooses) so that project data is not committed to a public repository.
"""

import codecs
import json
import math
import os

from pdso.code.catalogue.layout_entry import LayoutEntry

ENTRY_EXTENSION = ".json"
SKETCH_EXTENSION = ".svg"


def repo_root():
    """The repository root, which is also the extension folder."""
    here = os.path.abspath(__file__)
    # catalogue_store.py -> catalogue -> code -> pdso -> lib -> repo root
    path = here
    for _ in range(5):
        path = os.path.dirname(path)
    return path


def sample_folder():
    return os.path.join(repo_root(), "catalogue", "sample")


def default_local_folder():
    return os.path.join(repo_root(), "catalogue", "local")


def save_entry(entry, folder):
    """Write an entry (and its plan sketch) into a folder.

    Returns:
        str: Path of the JSON file written.
    """
    if not os.path.isdir(folder):
        os.makedirs(folder)

    json_path = os.path.join(folder, entry.id + ENTRY_EXTENSION)
    with codecs.open(json_path, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(entry.to_dict(), indent=2, sort_keys=True))

    sketch_path = os.path.join(folder, entry.id + SKETCH_EXTENSION)
    with codecs.open(sketch_path, "w", encoding="utf-8") as stream:
        stream.write(entry_to_svg(entry))

    return json_path


def load_folder(folder):
    """Load every entry in one folder.

    Returns:
        tuple: (entries, errors). A file that can't be read is reported in
            errors, never fatal, so one bad file doesn't hide the catalogue.
    """
    entries, errors = [], []
    if not folder or not os.path.isdir(folder):
        return entries, errors

    for file_name in sorted(os.listdir(folder)):
        if not file_name.lower().endswith(ENTRY_EXTENSION):
            continue
        path = os.path.join(folder, file_name)
        try:
            with codecs.open(path, "r", encoding="utf-8") as stream:
                entries.append(LayoutEntry.from_dict(json.loads(stream.read())))
        except Exception as error:
            errors.append("{}: {}".format(file_name, error))
    return entries, errors


def load_catalogue(folders):
    """Load entries from several folders. Later folders win on duplicate ids."""
    by_id, errors = {}, []
    for folder in folders:
        entries, folder_errors = load_folder(folder)
        errors.extend(folder_errors)
        for entry in entries:
            by_id[entry.id] = entry
    return list(by_id.values()), errors


# ---------------------------------------------------------------------------
# Plan sketch
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "Furniture": "#4C78A8",
    "Furniture Systems": "#4C78A8",
    "Casework": "#F58518",
    "Specialty Equipment": "#54A24B",
    "Plumbing Fixtures": "#72B7B2",
    "Electrical Equipment": "#B279A2",
    "Generic Models": "#9D755D",
}


def entry_to_svg(entry, scale=12.0, margin=24.0):
    """A small top-down sketch: room outline, one marker per item.

    Each marker is a dot with a tick showing the item's rotation, colored by
    category. Scale is pixels per foot. Y is flipped so north stays up.
    """
    width_px = entry.width * scale + 2 * margin
    height_px = entry.depth * scale + 2 * margin + 20

    def px(x, y):
        return (margin + x * scale, margin + (entry.depth - y) * scale)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="{:.0f}" height="{:.0f}" '
        'viewBox="0 0 {:.0f} {:.0f}" font-family="sans-serif">'.format(
            width_px, height_px, width_px, height_px
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]

    if entry.boundary:
        points = " ".join("{:.1f},{:.1f}".format(*px(x, y)) for x, y in entry.boundary)
        parts.append(
            '<polygon points="{}" fill="#f4f4f4" stroke="#333" stroke-width="2"/>'.format(
                points
            )
        )

    for item in entry.items:
        cx, cy = px(item.x, item.y)
        color = _CATEGORY_COLORS.get(item.category, "#888888")
        tick = 0.9 * scale
        tx = cx + tick * math.cos(item.rotation)
        ty = cy - tick * math.sin(item.rotation)
        parts.append(
            '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="{}" '
            'stroke-width="2"/>'.format(cx, cy, tx, ty, color)
        )
        parts.append(
            '<circle cx="{:.1f}" cy="{:.1f}" r="{:.1f}" fill="{}">'
            "<title>{} : {}</title></circle>".format(
                cx, cy, 0.35 * scale, color, _xml(item.family), _xml(item.type)
            )
        )

    parts.append(
        '<text x="{:.1f}" y="{:.1f}" font-size="12" fill="#333">{} — {:.0f} sf, '
        "{} items</text>".format(
            margin,
            height_px - 8,
            _xml(entry.name),
            entry.area,
            len(entry.items),
        )
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _xml(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
