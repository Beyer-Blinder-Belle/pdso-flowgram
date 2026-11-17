# -*- coding: utf-8 -*-
"""Quick Setup step: create the area schemes PDSO harvests from.

The schemes come from Project Config's area scheme mappings
({DSO parameter: Revit area scheme}), plus any names listed in area_schemes.json.
Schemes that are already in the model are left alone.

The Revit API can't create an area scheme from nothing, so a new scheme is a copy
of one already in the model (one that isn't Gross Building, if there is one),
renamed. The HTTP API's bootstrap runs this step too.
"""

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code import defaults
from pdso.code.setup import revit_utils, status


def run(ctx, data):
    names = []
    if data.get("from_project_config", True):
        mapped = [v for v in ctx.pdso_config["area_scheme_mappings"].values() if v]
        if not mapped:
            mapped = defaults.area_schemes()
            ctx.add(
                "Project Config",
                status.WARNING,
                "No area scheme mappings saved yet, so using the defaults ({}). Save "
                "Project Config first to choose your own.".format(", ".join(mapped)),
            )
        names.extend(sorted(mapped))
    for name in data.get("schemes", []):
        name = ctx.expand(name)
        if name and name not in names:
            names.append(name)

    for name in names:
        try:
            _ensure_area_scheme(ctx, name)
        except Exception as error:
            ctx.add(name, status.FAILED, str(error))


def _ensure_area_scheme(ctx, name):
    if revit_utils.find_area_scheme(ctx.doc, name) is not None:
        ctx.add(name, status.SKIPPED, "Already exists")
        return

    schemes = list(DB.FilteredElementCollector(ctx.doc).OfClass(DB.AreaScheme))
    if not schemes:
        raise ValueError(
            "The model has no area scheme to copy. Create one in Area and Volume "
            "Computations, then run setup again."
        )
    source = next(
        (scheme for scheme in schemes if not scheme.IsGrossBuildingArea), schemes[0]
    )

    with ctx.transaction("Create area scheme {}".format(name)):
        new_ids = DB.ElementTransformUtils.CopyElement(ctx.doc, source.Id, DB.XYZ.Zero)
        ctx.doc.GetElement(list(new_ids)[0]).Name = name

    ctx.add(name, status.CREATED, "Copied from '{}'".format(source.Name))
