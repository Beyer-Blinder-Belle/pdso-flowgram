# -*- coding: utf-8 -*-
"""Revit API helpers shared by the Model Setup steps."""

# 3rd Party Modules
from pyrevit import DB

# View types the setup steps can create: name -> (DB.ViewType, DB.ViewFamily)
VIEW_TYPES = {
    "AreaPlan": (DB.ViewType.AreaPlan, DB.ViewFamily.AreaPlan),
    "FloorPlan": (DB.ViewType.FloorPlan, DB.ViewFamily.FloorPlan),
    "CeilingPlan": (DB.ViewType.CeilingPlan, DB.ViewFamily.CeilingPlan),
    "ThreeD": (DB.ViewType.ThreeD, DB.ViewFamily.ThreeDimensional),
}


def enum_value(enum_type, name, what):
    """Look up a .NET enum member by name, with a readable error."""
    value = getattr(enum_type, str(name), None)
    if value is None:
        raise ValueError("'{}' is not a valid {}".format(name, what))
    return value


def view_type_info(name):
    """(DB.ViewType, DB.ViewFamily) for a view_type name from the standards."""
    if name not in VIEW_TYPES:
        raise ValueError(
            "Unsupported view_type '{}'. Use one of: {}".format(
                name, ", ".join(sorted(VIEW_TYPES.keys()))
            )
        )
    return VIEW_TYPES[name]


def resolve_category(doc, name):
    """Find a category by BuiltInCategory name ("OST_Walls") or by its name in Revit.

    "Parent|Sub" finds a subcategory by name, e.g. "Generic Models|PDSO - GSF Program Volume".

    Returns:
        DB.Category or None if the model doesn't have it.
    """
    if not name:
        return None

    if "|" in name:
        parent_name, sub_name = [part.strip() for part in name.split("|", 1)]
        parent = resolve_category(doc, parent_name)
        if parent is None:
            return None
        for sub in parent.SubCategories:
            if sub.Name == sub_name:
                return sub
        return None

    if name.startswith("OST_"):
        bic = getattr(DB.BuiltInCategory, name, None)
        if bic is None:
            return None
        try:
            return DB.Category.GetCategory(doc, bic)
        except Exception:
            return None

    for category in doc.Settings.Categories:
        if category.Name == name:
            return category
    return None


def parse_color(value):
    """DB.Color from "#RRGGBB" or [r, g, b]."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        red, green, blue = [int(v) for v in value]
    else:
        text = str(value).lstrip("#")
        if len(text) != 6:
            raise ValueError("Color '{}' must be '#RRGGBB' or [r, g, b]".format(value))
        red, green, blue = [int(text[i : i + 2], 16) for i in (0, 2, 4)]
    return DB.Color(red, green, blue)


def get_solid_fill_pattern_id(doc):
    """The ElementId of the model's solid fill pattern, or None."""
    for pattern_element in DB.FilteredElementCollector(doc).OfClass(
        DB.FillPatternElement
    ):
        pattern = pattern_element.GetFillPattern()
        if pattern.IsSolidFill and pattern.Target == DB.FillPatternTarget.Drafting:
            return pattern_element.Id
    return None


def get_line_pattern_id(doc, name):
    """The ElementId of a line pattern by name ("Solid" is the built-in solid line)."""
    if name.lower() == "solid":
        return DB.LinePatternElement.GetSolidPatternId()
    pattern = DB.LinePatternElement.GetLinePatternElementByName(doc, name)
    if pattern is None:
        raise ValueError("Line pattern '{}' is not in this model".format(name))
    return pattern.Id


def _apply_lines(doc, settings, spec, prefix):
    """Apply a {color, weight, pattern} spec to projection or cut lines."""
    if "color" in spec:
        getattr(settings, "Set{}LineColor".format(prefix))(parse_color(spec["color"]))
    if "weight" in spec:
        weight = int(spec["weight"])
        if not 1 <= weight <= 16:
            raise ValueError("Line weight must be 1-16, got {}".format(weight))
        getattr(settings, "Set{}LineWeight".format(prefix))(weight)
    if "pattern" in spec:
        getattr(settings, "Set{}LinePatternId".format(prefix))(
            get_line_pattern_id(doc, spec["pattern"])
        )


def _apply_fill(doc, settings, spec, prefix):
    """Apply a {color} spec as a solid foreground fill to surfaces or cut faces."""
    if "color" not in spec:
        return
    solid_id = get_solid_fill_pattern_id(doc)
    if solid_id is None:
        raise ValueError("This model has no solid fill pattern")
    getattr(settings, "Set{}ForegroundPatternId".format(prefix))(solid_id)
    getattr(settings, "Set{}ForegroundPatternColor".format(prefix))(
        parse_color(spec["color"])
    )


def build_overrides(doc, spec):
    """Build OverrideGraphicSettings from a category spec.

    Supported keys: halftone, projection_lines, cut_lines, surface, cut_pattern.

    Returns:
        DB.OverrideGraphicSettings, or None if the spec has no graphic overrides.
    """
    keys = ("halftone", "projection_lines", "cut_lines", "surface", "cut_pattern")
    if not any(key in spec for key in keys):
        return None

    settings = DB.OverrideGraphicSettings()

    if "halftone" in spec:
        settings.SetHalftone(bool(spec["halftone"]))
    if "projection_lines" in spec:
        _apply_lines(doc, settings, spec["projection_lines"], "Projection")
    if "cut_lines" in spec:
        _apply_lines(doc, settings, spec["cut_lines"], "Cut")
    if "surface" in spec:
        surface = spec["surface"]
        _apply_fill(doc, settings, surface, "Surface")
        if "transparency" in surface:
            settings.SetSurfaceTransparency(int(surface["transparency"]))
    if "cut_pattern" in spec:
        _apply_fill(doc, settings, spec["cut_pattern"], "Cut")

    return settings


def find_view_family_type(doc, view_family):
    """The first ViewFamilyType of a ViewFamily, or None."""
    for vft in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if vft.ViewFamily == view_family:
            return vft
    return None


def find_view_template(doc, name):
    """A view template by name, or None."""
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate and view.Name == name:
            return view
    return None


def find_view(doc, name, view_type=None, view_class=DB.View):
    """A (non-template) view by name, optionally of one DB.ViewType, or None.

    Revit only requires view names to be unique within a view type, so pass
    view_type to avoid matching, say, a floor plan with an area plan's name.
    """
    for view in DB.FilteredElementCollector(doc).OfClass(view_class):
        if view.IsTemplate or view.Name != name:
            continue
        if view_type is None or view.ViewType == view_type:
            return view
    return None


def find_family(doc, name):
    """A loaded family by name, or None."""
    for family in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if family.Name == name:
            return family
    return None


def find_area_scheme(doc, name):
    """An area scheme by name, or None."""
    for scheme in DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme):
        if scheme.Name == name:
            return scheme
    return None


def get_levels(doc, selector="all"):
    """Levels sorted by elevation.

    Args:
        selector: "all", or a list of level names.
    Returns:
        tuple[list[DB.Level], list[str]]: The levels found and any names that weren't.
    """
    levels = sorted(
        DB.FilteredElementCollector(doc).OfClass(DB.Level),
        key=lambda level: level.Elevation,
    )
    if selector in (None, "all"):
        return levels, []
    if isinstance(selector, basestring):  # noqa: F821 - IronPython 2.7 builtin
        selector = [selector]

    by_name = dict((level.Name, level) for level in levels)
    found = [by_name[name] for name in selector if name in by_name]
    missing = [name for name in selector if name not in by_name]
    found.sort(key=lambda level: level.Elevation)
    return found, missing


def set_parameter_value(element, name, value):
    """Set a parameter by name, converting the value to the parameter's storage type.

    Raises:
        ValueError: If the parameter is missing or read-only.
    """
    parameter = element.LookupParameter(name)
    if parameter is None:
        raise ValueError("Parameter '{}' not found".format(name))
    if parameter.IsReadOnly:
        raise ValueError(
            "Parameter '{}' is read-only (is it controlled by the view template?)".format(
                name
            )
        )

    storage = parameter.StorageType
    if storage == DB.StorageType.String:
        parameter.Set(u"{}".format(value))
    elif storage == DB.StorageType.Integer:
        parameter.Set(int(value))
    elif storage == DB.StorageType.Double:
        parameter.Set(float(value))
    else:
        raise ValueError(
            "Parameter '{}' can't be set from a standards file".format(name)
        )


class SwallowWarnings(DB.IFailuresPreprocessor):
    """Deletes Revit warnings during setup transactions so no dialogs pop up."""

    def PreprocessFailures(self, failures_accessor):
        for failure in failures_accessor.GetFailureMessages():
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                failures_accessor.DeleteWarning(failure)
        return DB.FailureProcessingResult.Continue
