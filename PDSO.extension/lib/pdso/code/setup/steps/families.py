# -*- coding: utf-8 -*-
"""Quick Setup step: load families listed in families.json."""

# Python standard libraries
import os

# System Modules
import clr

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code.setup import revit_utils, status


class _FamilyLoadOptions(DB.IFamilyLoadOptions):
    """Overwrite an existing family (and optionally its parameter values) when reloading."""

    def __init__(self, overwrite_parameters):
        self.overwrite_parameters = overwrite_parameters

    def OnFamilyFound(self, family_in_use, overwrite_parameter_values):
        overwrite_parameter_values.Value = self.overwrite_parameters
        return True

    def OnSharedFamilyFound(
        self, shared_family, family_in_use, source, overwrite_parameter_values
    ):
        source.Value = DB.FamilySource.Family
        overwrite_parameter_values.Value = self.overwrite_parameters
        return True


def run(ctx, data):
    for spec in data.get("families", []):
        if not spec.get("enabled", True):
            continue
        _load_family(ctx, spec)


def _load_family(ctx, spec):
    name = ctx.expand(spec.get("name", ""))
    on_existing = spec.get("on_existing", "skip")
    family = revit_utils.find_family(ctx.doc, name) if name else None

    candidates = [ctx.resolve_path(path) for path in spec.get("files", [])]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    label = name or (os.path.basename(path) if path else "(unnamed family)")

    if family is not None and on_existing == "skip":
        ctx.add(label, status.SKIPPED, "Already loaded")
    elif path is None:
        ctx.add(
            label,
            status.FAILED,
            "No family file found. Looked for: {}".format("; ".join(candidates)),
        )
    else:
        family_ref = clr.Reference[DB.Family]()
        with ctx.transaction("Load family {}".format(label)):
            loaded = ctx.doc.LoadFamily(
                path,
                _FamilyLoadOptions(spec.get("overwrite_parameter_values", False)),
                family_ref,
            )
        file_name = os.path.splitext(os.path.basename(path))[0]
        found = family_ref.Value or revit_utils.find_family(ctx.doc, file_name)

        if found is None:
            ctx.add(label, status.FAILED, "Revit did not load '{}'".format(path))
        else:
            existed = family is not None
            family = found
            if loaded or not existed:
                ctx.add(
                    family.Name,
                    status.UPDATED if existed else status.CREATED,
                    "Loaded from {}".format(path),
                )
            else:
                ctx.add(family.Name, status.SKIPPED, "Already up to date")

    if family is not None and spec.get("use_as_data_silo_family"):
        _register_data_silo_family(ctx, family.Name)


def _register_data_silo_family(ctx, family_name):
    """Point the model's PDSO Project Config at the loaded Data Silo family.

    Project Config runs before Quick Setup, so a family the user already chose
    there wins if it's loaded. Otherwise (a new model, or a name that isn't
    loaded) Project Config is pointed at the family this step loaded.
    """
    previous = ctx.pdso_config["data_silo_family"]
    if previous == family_name:
        return
    if previous and revit_utils.find_family(ctx.doc, previous) is not None:
        ctx.add(
            "Project Config",
            status.SKIPPED,
            "Keeping the Data Silo family chosen in Project Config ('{}')".format(
                previous
            ),
        )
        return

    if ctx.set_data_silo_family(family_name):
        ctx.add(
            "Project Config",
            status.UPDATED,
            "Data Silo family set to '{}' (was '{}')".format(family_name, previous),
        )
    else:
        ctx.add(
            "Project Config",
            status.WARNING,
            "Using '{}' as the Data Silo family for this run, but couldn't save it to "
            "Project Config. Set it there. {}".format(
                family_name, ctx.storage_error or ""
            ).strip(),
        )
