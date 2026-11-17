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
import re
import math
import os
import time

from pdso.code.catalogue.layout_entry import LayoutEntry

ENTRY_EXTENSION = ".json"
SKETCH_EXTENSION = ".svg"
IMAGE_EXTENSION = ".png"  # plan image of the unit, when Harvest made one


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


_NON_ASCII = re.compile(u"[^\\x00-\\x7f]")
_ESCAPE_TOKEN = re.compile(r"@@u([0-9a-f]{4})@@")


def _mark_non_ascii(value):
    """Replace each non-ASCII character in the strings of a JSON value by a token."""
    if isinstance(value, dict):
        return dict((_mark_non_ascii(k), _mark_non_ascii(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_mark_non_ascii(item) for item in value]
    if isinstance(value, basestring):  # noqa: F821 (IronPython 2.7)
        return _NON_ASCII.sub(lambda m: "@@u{:04x}@@".format(ord(m.group(0))), value)
    return value


def to_json(data):
    """Serialize to JSON text that is pure ASCII.

    json.dumps of IronPython 2.7 fails on a string with a non-ASCII character
    (for example an Area with the name "Caf\u00e9"): it tries to decode the text
    as UTF-8 bytes. Thus each such character is written as a \\uXXXX escape.
    """
    text = json.dumps(_mark_non_ascii(data), indent=2, sort_keys=True)
    return _ESCAPE_TOKEN.sub(lambda m: "\\u" + m.group(1), text)


def save_entry(entry, folder):
    """Write an entry (and its plan sketch) into a folder.

    Returns:
        str: Path of the JSON file written.
    """
    if not os.path.isdir(folder):
        os.makedirs(folder)

    json_path = os.path.join(folder, entry.id + ENTRY_EXTENSION)
    _write_text(json_path, to_json(entry.to_dict()))

    sketch_path = os.path.join(folder, entry.id + SKETCH_EXTENSION)
    _write_text(sketch_path, entry_to_svg(entry))

    return json_path


def _write_text(path, text):
    """Write a UTF-8 file so that a reader never sees it half written.

    The text goes to a temporary file in the same folder, which then takes
    the file's place in one step (os.replace in Python 3, File.Replace in
    IronPython). If that fails, the old file is still there and the
    temporary file is removed.
    """
    temporary = "{}.{}.tmp".format(path, os.getpid())
    with codecs.open(temporary, "w", encoding="utf-8") as stream:
        stream.write(text)
    try:
        _replace_file(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def _replace_file(source, target):
    """Move source to target in one step, over a file that is there."""
    replace = getattr(os, "replace", None)
    if replace is not None:  # Python 3
        replace(source, target)
        return
    # IronPython 2.7: no os.replace, and os.rename can't overwrite
    from System.IO import File

    if File.Exists(target):
        File.Replace(source, target, None)
    else:
        File.Move(source, target)


LOCK_FILE_NAME = ".catalogue.lock"
# A lock not touched for this long (s) was left by a run that stopped; it is
# removed. The run that holds a lock touches it for each unit (see touch).
STALE_LOCK_AGE = 600


class catalogue_lock(object):
    """Lock a catalogue folder while catalogue numbers are given out.

    Two people who harvest into one shared folder at the same time would
    otherwise give one number to two shapes. The lock is a file made with
    O_EXCL, so only one run can hold it.

    Example Usage:
        with catalogue_lock(folder):
            numbers = catalogue_numbers(folder)
            ...  # assign_catalogue and save_entry
    """

    def __init__(self, folder, timeout=30.0):
        self.path = os.path.join(folder, LOCK_FILE_NAME)
        self.folder = folder
        self.timeout = timeout
        # Written into the lock, so a run only ever removes its own lock
        self.token = "{}-{}-{}".format(os.getpid(), id(self), time.time())
        self._held = False

    def _read_token(self):
        try:
            with codecs.open(self.path, "r", encoding="ascii") as stream:
                return stream.read().strip()
        except (IOError, OSError):
            return None

    def __enter__(self):
        if not os.path.isdir(self.folder):
            os.makedirs(self.folder)
        deadline = time.time() + self.timeout
        while True:
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                if not os.path.exists(self.path):
                    # Not a lock held by someone: the folder can't be written
                    raise RuntimeError(
                        "Can't write to the catalogue folder {}. Check that it "
                        "exists and that you can save files there.".format(self.folder)
                    )
                self._remove_if_stale()
                if time.time() > deadline:
                    raise RuntimeError(
                        "Someone else is saving to the catalogue folder {}. Try "
                        "again in a minute. If nobody is, delete the file {}.".format(
                            self.folder, LOCK_FILE_NAME
                        )
                    )
                time.sleep(0.5)
                continue
            try:
                os.write(handle, self.token.encode("ascii"))
            finally:
                os.close(handle)
            self._held = True
            return self

    def _remove_if_stale(self):
        """Remove a lock that its run stopped touching long ago.

        The age is measured against a file this run writes in the same
        folder, so the clocks of two computers don't matter.
        """
        probe = os.path.join(self.folder, ".catalogue.{}.probe".format(os.getpid()))
        try:
            with open(probe, "w"):
                pass
            now = os.path.getmtime(probe)
            os.remove(probe)
            token = self._read_token()
            if now - os.path.getmtime(self.path) > STALE_LOCK_AGE:
                # Remove it only if it is still the same lock
                if token is not None and self._read_token() == token:
                    os.remove(self.path)
        except (IOError, OSError):
            pass

    def touch(self):
        """Show that this run is still working, so the lock isn't taken as stale."""
        if self._held:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if self._held:
            # Only remove the lock if it is still this run's lock
            if self._read_token() == self.token:
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            self._held = False
        return False


# Text of each entry file read, by path: (modified time, size, text). A file
# that hasn't changed is not read again, which matters on a network folder
# that Harvest All Units and Pull read many times in one run.
_TEXT_CACHE = {}


def _read_entry_text(path):
    """The text of an entry file, from the cache if the file hasn't changed."""
    stat = os.stat(path)
    stamp = (stat.st_mtime, stat.st_size)
    cached = _TEXT_CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    with codecs.open(path, "r", encoding="utf-8") as stream:
        text = stream.read()
    _TEXT_CACHE[path] = (stamp, text)
    return text


def _read_entry_json(path):
    return json.loads(_read_entry_text(path))


def remove_previous(folder, source, keep=None):
    """Delete earlier harvests of the same unit, so a re-run replaces them.

    The same unit is the same element id in the same model: both models were
    saved to files with the same name, and their model_id (when both have one)
    is the same. An unsaved model ("Project1") is never matched, since many
    projects share that name.

    Call it after the new entry is saved, with keep= its path, so a failed
    save never leaves the unit with no entry.

    Returns:
        int: How many earlier entries were removed.
    """
    element_id = (source or {}).get("element_id")
    if not element_id or not folder or not os.path.isdir(folder):
        return 0

    removed = 0
    keep = os.path.normcase(os.path.abspath(keep)) if keep else None
    for file_name in sorted(os.listdir(folder)):
        if not file_name.lower().endswith(ENTRY_EXTENSION):
            continue
        path = os.path.join(folder, file_name)
        if keep and os.path.normcase(os.path.abspath(path)) == keep:
            continue
        try:
            other = _read_entry_json(path).get("source") or {}
        except Exception:
            continue
        if str(other.get("element_id")) != str(element_id):
            continue
        # Both models must be saved files with the same name. Projects made
        # from one template can share a model_id, so it only narrows the
        # match (a different model_id is a different model); it never makes
        # one on its own.
        same = bool(source.get("model_path")) and source.get(
            "model_path"
        ) == other.get("model_path")
        if same and source.get("model_id") and other.get("model_id"):
            same = source["model_id"] == other["model_id"]
        if not same:
            continue
        os.remove(path)
        for extension in (SKETCH_EXTENSION, IMAGE_EXTENSION):
            beside = path[: -len(ENTRY_EXTENSION)] + extension
            if os.path.isfile(beside):
                os.remove(beside)
        removed += 1
    return removed


def catalogue_numbers(folder):
    """The catalogue numbers already given out in a folder, as {key: number}.

    A catalogue is a group of layouts with the same shape. Its key is the
    shape's corner sequence, which is the same however the unit is turned or
    mirrored and whatever its proportions (see layout_geometry.shape_category).
    """
    numbers = {}
    if not folder or not os.path.isdir(folder):
        return numbers
    for file_name in sorted(os.listdir(folder)):
        if not file_name.lower().endswith(ENTRY_EXTENSION):
            continue
        try:
            data = _read_entry_json(os.path.join(folder, file_name))
            group = (data.get("features") or {}).get("catalogue") or {}
        except Exception:
            continue
        if group.get("key") and group.get("number"):
            numbers.setdefault(group["key"], int(group["number"]))
    return numbers


def assign_catalogue(entry, numbers):
    """Give an entry its catalogue number; a new shape gets the next number.

    Args:
        entry (LayoutEntry): Its features must hold "shape".
        numbers (dict): From catalogue_numbers; a new key is added to it.
    Returns:
        int: The catalogue number.
    """
    shape = entry.features.get("shape") or {}
    key = shape.get("turns") or shape.get("category") or "unknown"
    if key not in numbers:
        numbers[key] = max(list(numbers.values()) + [0]) + 1
    entry.features["catalogue"] = {
        "number": numbers[key],
        "key": key,
        "shape": shape.get("category", ""),
    }
    return numbers[key]


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
            entries.append(LayoutEntry.from_dict(_read_entry_json(path)))
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


_EDGE_COLORS = {
    "exterior": "#1f77b4",
    "corridor": "#d62728",
    "party": "#333333",
    "no_area": "#aaaaaa",
    "unroomed": "#aaaaaa",  # the same kind, in entries harvested before Areas
}


def entry_to_svg(entry, scale=12.0, margin=24.0):
    """A small top-down sketch: unit outline, one marker per item.

    Each marker is a dot with a tick showing the item's rotation, colored by
    category. Where the entry has features, walls are colored by what is
    beyond them (blue exterior, red corridor, black a neighbour), partitions
    are grey, Rooms of older entries are named, windows
    are light blue bars, doors are white gaps, and an arrow marks the entry.
    Scale is pixels per foot. Y is flipped so the frame's Y axis points up.
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

    features = entry.features or {}
    edges = features.get("edges", [])
    for edge in edges:
        x1, y1 = px(*edge["start"])
        x2, y2 = px(*edge["end"])
        parts.append(
            '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="{}" '
            'stroke-width="4" stroke-linecap="round"><title>{} {}</title></line>'.format(
                x1, y1, x2, y2,
                _EDGE_COLORS.get(edge.get("kind"), "#333333"),
                _xml(edge.get("kind", "")),
                _xml(edge.get("beyond", "")),
            )
        )

    for wall in features.get("internal_walls", []):
        x1, y1 = px(*wall["start"])
        x2, y2 = px(*wall["end"])
        parts.append(
            '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="#777777" '
            'stroke-width="3"/>'.format(x1, y1, x2, y2)
        )
    for door in features.get("internal_doors", []):
        cx, cy = px(door["x"], door["y"])
        parts.append(
            '<circle cx="{:.1f}" cy="{:.1f}" r="5" fill="#ffffff" stroke="#777777">'
            "<title>door: {} : {}</title></circle>".format(
                cx, cy, _xml(door.get("family", "")), _xml(door.get("type", ""))
            )
        )
    # Entries harvested before Areas only also listed the Revit Rooms inside
    for space in features.get("rooms", []):
        cx, cy = px(space["x"], space["y"])
        parts.append(
            '<text x="{:.1f}" y="{:.1f}" font-size="10" fill="#555" '
            'text-anchor="middle">{}</text>'.format(cx, cy, _xml(space.get("name", "")))
        )

    for opening in features.get("openings", []):
        if opening.get("edge", -1) < 0 or opening["edge"] >= len(edges):
            continue
        edge = edges[opening["edge"]]
        dx = edge["end"][0] - edge["start"][0]
        dy = edge["end"][1] - edge["start"][1]
        length = math.hypot(dx, dy) or 1.0
        half = (opening.get("width") or 3.0) / 2.0
        x1, y1 = px(opening["x"] - dx / length * half, opening["y"] - dy / length * half)
        x2, y2 = px(opening["x"] + dx / length * half, opening["y"] + dy / length * half)
        is_door = opening.get("kind") == "door"
        parts.append(
            '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="{}" '
            'stroke-width="{}"><title>{}: {} : {}</title></line>'.format(
                x1, y1, x2, y2,
                "#ffffff" if is_door else "#9ecae1",
                5 if is_door else 7,
                _xml(opening.get("kind", "")),
                _xml(opening.get("family", "")),
                _xml(opening.get("type", "")),
            )
        )

    entrance = features.get("entry")
    if entrance:
        ex, ey = px(entrance["x"], entrance["y"])
        tx, ty = px(
            entrance["x"] + 2.5 * entrance["direction"][0],
            entrance["y"] + 2.5 * entrance["direction"][1],
        )
        parts.append(
            '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" stroke="#d62728" '
            'stroke-width="2"/><circle cx="{:.1f}" cy="{:.1f}" r="4" fill="#d62728">'
            "<title>entry</title></circle>".format(ex, ey, tx, ty, tx, ty)
        )

    for hosted in features.get("hosted_items", []):
        cx, cy = px(hosted["x"], hosted["y"])
        parts.append(
            '<rect x="{:.1f}" y="{:.1f}" width="7" height="7" fill="none" stroke="#666">'
            "<title>{} : {} (hosted)</title></rect>".format(
                cx - 3.5, cy - 3.5, _xml(hosted.get("family", "")), _xml(hosted.get("type", ""))
            )
        )

    for nested in features.get("nested_items", []):
        cx, cy = px(nested["x"], nested["y"])
        parts.append(
            '<circle cx="{:.1f}" cy="{:.1f}" r="3" fill="none" stroke="{}">'
            "<title>{} : {} (inside {})</title></circle>".format(
                cx,
                cy,
                _CATEGORY_COLORS.get(nested.get("category"), "#888888"),
                _xml(nested.get("family", "")),
                _xml(nested.get("type", "")),
                _xml(nested.get("parent_family", "")),
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
