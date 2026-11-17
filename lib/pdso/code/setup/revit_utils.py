# -*- coding: utf-8 -*-
"""Revit API helpers shared by the Quick Setup steps (and the services)."""

# Python standard libraries
import fnmatch
import os
import tempfile

import System

# 3rd Party Modules
from pyrevit import DB
from rpw.utils.dotnet import List


class FamilyLoadOptions(DB.IFamilyLoadOptions):
    """Load options for a family file.

    By default the family in the project is overwritten, and its parameter
    values too if it has no instances yet or overwrite_parameters is True.
    With keep_existing, a family that is in the project is never changed
    (the catalogue loads only families a model doesn't have).
    """

    def __init__(self, overwrite_parameters=False, keep_existing=False):
        self.overwrite_parameters = overwrite_parameters
        self.keep_existing = keep_existing

    def OnFamilyFound(self, family_in_use, overwrite_parameter_values):
        if self.keep_existing:
            overwrite_parameter_values.Value = False
            return False
        overwrite_parameter_values.Value = (
            not family_in_use or self.overwrite_parameters
        )
        return True

    def OnSharedFamilyFound(
        self, shared_family, family_in_use, source, overwrite_parameter_values
    ):
        if self.keep_existing:
            source.Value = DB.FamilySource.Project
            overwrite_parameter_values.Value = False
            return False
        source.Value = DB.FamilySource.Family
        overwrite_parameter_values.Value = (
            not family_in_use or self.overwrite_parameters
        )
        return True


# The placeholder Data Silo family: a square marker, in feet
MARKER_HALF_SIZE = 1.0
MARKER_HEIGHT = 1.0


def _generic_model_template(app):
    path = os.path.join(app.FamilyTemplatePath, "English-Imperial", "Generic Model.rft")
    if os.path.exists(path):
        return path
    for root, _, files in os.walk(app.FamilyTemplatePath):
        if "Generic Model.rft" in files:
            return os.path.join(root, "Generic Model.rft")
    raise RuntimeError(
        "family template 'Generic Model.rft' not found in {}".format(
            app.FamilyTemplatePath
        )
    )


def build_marker_family(doc, family_name):
    """Build and load a Generic Model family with a small square marker.

    Used as the Data Silo family when no family file can be loaded (an older
    Revit than the shipped family). Must be called with no open transaction
    in doc. Raises RuntimeError if it can't be built.

    Returns:
        DB.Family: The loaded family.
    """
    app = doc.Application
    family_doc = app.NewFamilyDocument(_generic_model_template(app))
    if family_doc is None:
        raise RuntimeError("Revit could not make a new family document")
    try:
        transaction = DB.Transaction(family_doc, "PDSO - Build Data Silo Marker")
        transaction.Start()
        try:
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
            sketch_plane = DB.SketchPlane.Create(family_doc, plane)
            half = MARKER_HALF_SIZE
            corners = [
                DB.XYZ(-half, -half, 0),
                DB.XYZ(half, -half, 0),
                DB.XYZ(half, half, 0),
                DB.XYZ(-half, half, 0),
            ]
            loop = DB.CurveArray()
            for index in range(4):
                loop.Append(DB.Line.CreateBound(corners[index], corners[(index + 1) % 4]))
            profile = DB.CurveArrArray()
            profile.Append(loop)
            family_doc.FamilyCreate.NewExtrusion(True, profile, sketch_plane, MARKER_HEIGHT)
            transaction.Commit()
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            raise

        # The family takes the name of the file it is saved as
        folder = os.path.join(tempfile.gettempdir(), "pdso_families")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = os.path.join(folder, family_name + ".rfa")
        options = DB.SaveAsOptions()
        options.OverwriteExistingFile = True
        family_doc.SaveAs(path, options)
        family = family_doc.LoadFamily(doc, FamilyLoadOptions(True))
    finally:
        family_doc.Close(False)

    family = family or find_family(doc, family_name)
    if family is None:
        raise RuntimeError("Revit did not load the placeholder family")
    return family

# Filter rule operator (view_templates.json) -> ParameterFilterRuleFactory method.
# Rules compare text, so the value is always a string.
FILTER_RULE_OPERATORS = {
    "equals": "CreateEqualsRule",
    "not_equals": "CreateNotEqualsRule",
    "contains": "CreateContainsRule",
    "not_contains": "CreateNotContainsRule",
    "begins_with": "CreateBeginsWithRule",
    "not_begins_with": "CreateNotBeginsWithRule",
    "ends_with": "CreateEndsWithRule",
    "not_ends_with": "CreateNotEndsWithRule",
}

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


def level_name_matches(name, patterns):
    """Whether a level name matches any pattern (case-insensitive, * and ? wildcards)."""
    if isinstance(patterns, basestring):  # noqa: F821 - IronPython 2.7 builtin
        patterns = [patterns]
    lowered = name.lower()
    return any(
        fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns or []
    )


def get_levels(doc, selector="all", exclude=None):
    """Levels sorted by elevation.

    Args:
        selector: "all", or a list of level names.
        exclude: Level name patterns to leave out, e.g. ["*Roof*", "Parapet"].
            Case-insensitive; * matches any text and ? one character.
    Returns:
        tuple[list[DB.Level], list[str]]: The levels found and any names that weren't.
    """
    levels = sorted(
        DB.FilteredElementCollector(doc).OfClass(DB.Level),
        key=lambda level: level.Elevation,
    )
    missing = []
    if selector not in (None, "all"):
        if isinstance(selector, basestring):  # noqa: F821 - IronPython 2.7 builtin
            selector = [selector]
        by_name = dict((level.Name, level) for level in levels)
        levels = [by_name[name] for name in selector if name in by_name]
        missing = [name for name in selector if name not in by_name]
        levels.sort(key=lambda level: level.Elevation)

    if exclude:
        levels = [
            level for level in levels if not level_name_matches(level.Name, exclude)
        ]
    return levels, missing


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


# ----- View filters -----


def find_parameter_filter(doc, name):
    """A rule-based view filter by name, or None."""
    for element in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement):
        if element.Name == name:
            return element
    return None


def _parameter_label(doc, parameter_id):
    """The name Revit shows for a filterable parameter id."""
    value = parameter_id.Value
    if value < 0:
        try:
            bip = System.Enum.ToObject(DB.BuiltInParameter, value)
            return DB.LabelUtils.GetLabelFor(bip)
        except Exception:
            return None
    element = doc.GetElement(parameter_id)
    return element.Name if element is not None else None


def resolve_filter_parameter(doc, category_ids, name):
    """The ElementId of a parameter a view filter on these categories can use.

    Args:
        name: A BuiltInParameter name ("ALL_MODEL_FAMILY_NAME", the same in every
            language) or the parameter name as Revit shows it ("PDSO - Program UID").
    Raises:
        ValueError: If no filterable parameter has the name.
    """
    if not name:
        raise ValueError("a rule has no parameter")
    filterable = list(
        DB.ParameterFilterUtilities.GetFilterableParametersInCommon(doc, category_ids)
    )

    bip = getattr(DB.BuiltInParameter, str(name), None)
    if bip is not None:
        parameter_id = DB.ElementId(bip)
        if any(item == parameter_id for item in filterable):
            return parameter_id
        raise ValueError(
            "'{}' can't be used in a filter on these categories".format(name)
        )

    for parameter_id in filterable:
        if _parameter_label(doc, parameter_id) == name:
            return parameter_id
    raise ValueError(
        "'{}' is not a parameter a filter on these categories can use".format(name)
    )


def build_filter_rule(doc, category_ids, spec):
    """A FilterRule from a {parameter, operator, value} spec."""
    operator = spec.get("operator", "equals")
    if operator not in FILTER_RULE_OPERATORS:
        raise ValueError(
            "unknown operator '{}'. Use one of: {}".format(
                operator, ", ".join(sorted(FILTER_RULE_OPERATORS))
            )
        )
    parameter_id = resolve_filter_parameter(doc, category_ids, spec.get("parameter"))
    value = u"{}".format(spec.get("value", ""))
    method = getattr(DB.ParameterFilterRuleFactory, FILTER_RULE_OPERATORS[operator])
    try:
        return method(parameter_id, value)
    except TypeError:
        # Revit 2022 and earlier also take a caseSensitive flag
        return method(parameter_id, value, False)


def create_or_update_parameter_filter(doc, spec):
    """Create a rule-based view filter, or update the one with the same name.

    Must be called inside a transaction. All rules must match (AND).

    Args:
        spec: {"name", "categories": [...],
            "rules": [{"parameter", "operator", "value"}]}
    Returns:
        DB.ParameterFilterElement
    """
    category_ids = List[DB.ElementId]()
    for category_name in spec.get("categories", []):
        category = resolve_category(doc, category_name)
        if category is None:
            raise ValueError("category '{}' is not in this model".format(category_name))
        category_ids.Add(category.Id)
    if category_ids.Count == 0:
        raise ValueError("list at least one category")

    rules = List[DB.FilterRule]()
    for rule_spec in spec.get("rules", []):
        rules.Add(build_filter_rule(doc, category_ids, rule_spec))
    if rules.Count == 0:
        raise ValueError("list at least one rule")
    element_filter = DB.ElementParameterFilter(rules)

    existing = find_parameter_filter(doc, spec["name"])
    if existing is None:
        return DB.ParameterFilterElement.Create(
            doc, spec["name"], category_ids, element_filter
        )
    existing.SetCategories(category_ids)
    existing.SetElementFilter(element_filter)
    return existing
