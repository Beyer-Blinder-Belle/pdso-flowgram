# -*- coding: utf-8 -*-
"""PDSO Bootstrap Service - make a model ready for PDSO.

A model needs these items before the sync can run:
    1. The PDSO configuration (Extensible Storage on ProjectInfo)
    2. The PDSO shared parameters, bound to the Generic Models category
    3. The Data Silo family (a Generic Model family)
    4. One area scheme for each configured area scheme mapping
    5. Area plans for the levels in use

This service creates the items that are missing. It does not change items that
are there.

Example Usage:
    report = bootstrap(doc, levels=["L1", "L2"])
"""

# Python standard libraries
import os
import tempfile

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from ..data.shared_parameter_constants import standard_pdso_parameters
from . import config_service
from .errors import PDSOError
from .family_manager_service import ProgramVolumeFamilyLoadOptions
from .revit_helpers import Transaction, all_levels, find_level, id_value

logger = script.get_logger()

SHARED_PARAMETER_GROUP = "PDSO"


def _spec_type(parameter_type):
    """Revit data type of a PDSO parameter type."""
    return {
        "string": DB.SpecTypeId.String.Text,
        "multilineText": DB.SpecTypeId.String.MultilineText,
        "number": DB.SpecTypeId.Number,
        "area": DB.SpecTypeId.Area,
    }[parameter_type]


# Half of the side of the Data Silo marker (a square extrusion), in feet
DATA_SILO_MARKER_HALF_SIZE = 1.0
DATA_SILO_MARKER_HEIGHT = 1.0


def bootstrap(doc, levels=None):
    """Create each PDSO item that the model does not have.

    Args:
        doc: Revit Document (a project, with no open transaction)
        levels (list): Level names or ids that get area plans. Default: all levels.

    Returns:
        dict: What was created and what was there already
    """
    report = {}
    report["config_created"] = config_service.initialize_config(doc)
    config = config_service.get_config(doc)

    report["shared_parameters"] = ensure_shared_parameters(doc)
    report["data_silo_family"] = ensure_data_silo_family(
        doc, config["data_silo_family_name"]
    )
    report["area_schemes"] = ensure_area_schemes(
        doc, sorted(set(config["area_scheme_mappings"].values()))
    )

    level_elements = (
        [find_level(doc, reference) for reference in levels]
        if levels
        else all_levels(doc)
    )
    plans = []
    for scheme_name in sorted(set(config["area_scheme_mappings"].values())):
        for level in level_elements:
            plans.append(ensure_area_plan(doc, scheme_name, level))
    report["area_plans"] = plans
    return report


# =============================================================================
# Shared parameters
# =============================================================================


def _required_parameters():
    return [
        definition
        for definition in standard_pdso_parameters.values()
        if definition.is_required
    ]


def _external_definitions(app, path):
    """Make a shared parameter file with the PDSO parameters.

    Returns:
        dict: Parameter name -> ExternalDefinition
    """
    with open(path, "w"):
        pass  # Revit fills an empty file
    app.SharedParametersFilename = path
    definition_file = app.OpenSharedParameterFile()
    if definition_file is None:
        raise PDSOError("Revit could not open the shared parameter file.", 500)
    group = definition_file.Groups.Create(SHARED_PARAMETER_GROUP)
    for definition in _required_parameters():
        options = DB.ExternalDefinitionCreationOptions(
            definition.parameter_name, _spec_type(definition.parameter_type)
        )
        options.GUID = definition.parameter_guid
        group.Definitions.Create(options)

    # Revit rejects a definition object that Create() returned ("The referenced
    # object is not valid"). Read the definitions again from the file.
    group = app.OpenSharedParameterFile().Groups.get_Item(SHARED_PARAMETER_GROUP)
    return dict(
        (
            definition.parameter_name,
            group.Definitions.get_Item(definition.parameter_name),
        )
        for definition in _required_parameters()
    )


def _is_bound_to_generic_models(doc, definition):
    binding = doc.ParameterBindings.get_Item(definition)
    if binding is None:
        return False
    generic_model = int(DB.BuiltInCategory.OST_GenericModel)
    return any(category.Id.Value == generic_model for category in binding.Categories)


def ensure_shared_parameters(doc):
    """Bind the required PDSO shared parameters to Generic Models (instance).

    Returns:
        dict: {"created": [names], "existing": [names]}
    """
    app = doc.Application
    created = []
    existing = []
    to_bind = []

    for definition in _required_parameters():
        element = DB.SharedParameterElement.Lookup(doc, definition.parameter_guid)
        if element is not None and _is_bound_to_generic_models(
            doc, element.GetDefinition()
        ):
            existing.append(definition.parameter_name)
        else:
            to_bind.append(definition)

    if not to_bind:
        return {"created": created, "existing": sorted(existing)}

    path = os.path.join(tempfile.gettempdir(), "pdso_shared_parameters.txt")
    original_path = app.SharedParametersFilename
    try:
        externals = _external_definitions(app, path)

        generic_models = doc.Settings.Categories.get_Item(
            DB.BuiltInCategory.OST_GenericModel
        )
        with Transaction(doc, "PDSO - Bind Shared Parameters"):
            for definition in to_bind:
                external = externals[definition.parameter_name]
                categories = app.Create.NewCategorySet()
                categories.Insert(generic_models)
                element = DB.SharedParameterElement.Lookup(
                    doc, definition.parameter_guid
                )
                bound = False
                if element is not None:
                    # The parameter is in the model but not on Generic Models:
                    # keep its categories and add Generic Models.
                    old_binding = doc.ParameterBindings.get_Item(
                        element.GetDefinition()
                    )
                    if old_binding is not None:
                        for category in old_binding.Categories:
                            categories.Insert(category)
                        binding = app.Create.NewInstanceBinding(categories)
                        bound = doc.ParameterBindings.ReInsert(
                            element.GetDefinition(), binding, DB.GroupTypeId.Data
                        )
                if not bound:
                    binding = app.Create.NewInstanceBinding(categories)
                    bound = doc.ParameterBindings.Insert(
                        external, binding, DB.GroupTypeId.Data
                    )
                if not bound:
                    raise PDSOError(
                        "Could not bind parameter '{}'.".format(
                            definition.parameter_name
                        ),
                        500,
                    )
                created.append(definition.parameter_name)
    finally:
        app.SharedParametersFilename = original_path or ""

    return {"created": sorted(created), "existing": sorted(existing)}


# =============================================================================
# Data Silo family
# =============================================================================


def find_family(doc, family_name):
    """Find a loaded Family by name, or None."""
    for family in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if family.Name == family_name:
            return family
    return None


def _generic_model_template(app):
    path = os.path.join(app.FamilyTemplatePath, "English-Imperial", "Generic Model.rft")
    if os.path.exists(path):
        return path
    for root, _, files in os.walk(app.FamilyTemplatePath):
        if "Generic Model.rft" in files:
            return os.path.join(root, "Generic Model.rft")
    raise PDSOError(
        "Family template 'Generic Model.rft' not found in {}".format(
            app.FamilyTemplatePath
        ),
        500,
    )


def ensure_data_silo_family(doc, family_name):
    """Load a Data Silo family with the configured name if there is none.

    The new family is a Generic Model with a small square marker. The PDSO
    parameters come from the project binding (see ensure_shared_parameters).

    Returns:
        dict: {"name", "id", "created"}
    """
    family = find_family(doc, family_name)
    if family is not None:
        return {"name": family_name, "id": id_value(family.Id), "created": False}

    app = doc.Application
    family_doc = app.NewFamilyDocument(_generic_model_template(app))
    if family_doc is None:
        raise PDSOError("Revit could not make a new family document.", 500)

    try:
        with Transaction(family_doc, "PDSO - Build Data Silo Marker"):
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
            sketch_plane = DB.SketchPlane.Create(family_doc, plane)
            half = DATA_SILO_MARKER_HALF_SIZE
            corners = [
                DB.XYZ(-half, -half, 0),
                DB.XYZ(half, -half, 0),
                DB.XYZ(half, half, 0),
                DB.XYZ(-half, half, 0),
            ]
            loop = DB.CurveArray()
            for index in range(4):
                loop.Append(
                    DB.Line.CreateBound(corners[index], corners[(index + 1) % 4])
                )
            profile = DB.CurveArrArray()
            profile.Append(loop)
            family_doc.FamilyCreate.NewExtrusion(
                True, profile, sketch_plane, DATA_SILO_MARKER_HEIGHT
            )

        folder = os.path.join(tempfile.gettempdir(), "pdso_families")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = os.path.join(folder, family_name + ".rfa")
        options = DB.SaveAsOptions()
        options.OverwriteExistingFile = True
        family_doc.SaveAs(path, options)
        family = family_doc.LoadFamily(doc, ProgramVolumeFamilyLoadOptions(True, True))
    finally:
        family_doc.Close(False)

    if family is None:
        family = find_family(doc, family_name)
    if family is None:
        raise PDSOError("Could not load the Data Silo family.", 500)
    return {"name": family_name, "id": id_value(family.Id), "created": True}


# =============================================================================
# Area schemes and area plans
# =============================================================================


def find_area_scheme(doc, name):
    """Find an AreaScheme by name, or None."""
    for scheme in DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme):
        if scheme.Name == name:
            return scheme
    return None


def ensure_area_schemes(doc, scheme_names):
    """Create each area scheme that the model does not have.

    The Revit API cannot make an area scheme from nothing. A new scheme is a
    copy of a scheme in the model (a scheme that is not Gross Building, if
    there is one).

    Returns:
        dict: {"created": [names], "existing": [names]}
    """
    created = []
    existing = []
    missing = []
    for name in scheme_names:
        if find_area_scheme(doc, name) is not None:
            existing.append(name)
        else:
            missing.append(name)

    if missing:
        schemes = list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme))
        if not schemes:
            raise PDSOError("The model has no area scheme to copy.", 409)
        source = next(
            (scheme for scheme in schemes if not scheme.IsGrossBuildingArea),
            schemes[0],
        )
        with Transaction(doc, "PDSO - Create Area Schemes"):
            for name in missing:
                new_ids = DB.ElementTransformUtils.CopyElement(
                    doc, source.Id, DB.XYZ.Zero
                )
                doc.GetElement(new_ids[0]).Name = name
                created.append(name)

    return {"created": created, "existing": existing}


def find_area_plan(doc, scheme, level):
    """Find the area plan of a scheme on a level, or None."""
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if view.IsTemplate or view.ViewType != DB.ViewType.AreaPlan:
            continue
        if view.AreaScheme is None or view.GenLevel is None:
            continue
        if view.AreaScheme.Id == scheme.Id and view.GenLevel.Id == level.Id:
            return view
    return None


def ensure_area_plan(doc, scheme_name, level):
    """Get the area plan of a scheme on a level. Create it if there is none.

    Returns:
        dict: {"id", "name", "scheme", "level", "created"}
    """
    scheme = find_area_scheme(doc, scheme_name)
    if scheme is None:
        raise PDSOError("Area scheme '{}' not found.".format(scheme_name), 404)

    view = find_area_plan(doc, scheme, level)
    created = False
    if view is None:
        with Transaction(doc, "PDSO - Create Area Plan"):
            view = DB.ViewPlan.CreateAreaPlan(doc, scheme.Id, level.Id)
            try:
                view.Name = "{} - {}".format(scheme_name, level.Name)
            except Exception:
                pass  # The name is in use. Keep the name that Revit gave.
            for category in (
                DB.BuiltInCategory.OST_Elev,
                DB.BuiltInCategory.OST_Sections,
                DB.BuiltInCategory.OST_Grids,
                DB.BuiltInCategory.OST_CLines,
                DB.BuiltInCategory.OST_Dimensions,
            ):
                try:
                    view.SetCategoryHidden(DB.ElementId(category), True)
                except Exception:
                    pass
            # Bold area boundary lines, so that the outlines show on a real plan
            try:
                settings = DB.OverrideGraphicSettings()
                settings.SetProjectionLineWeight(7)
                settings.SetProjectionLineColor(DB.Color(196, 0, 122))
                view.SetCategoryOverrides(
                    DB.ElementId(DB.BuiltInCategory.OST_AreaSchemeLines), settings
                )
            except Exception:
                pass
        created = True

    return {
        "id": id_value(view.Id),
        "name": view.Name,
        "scheme": scheme_name,
        "level": level.Name,
        "created": created,
    }
