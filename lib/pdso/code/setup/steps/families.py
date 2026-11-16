# -*- coding: utf-8 -*-
"""Quick Setup step: load families listed in families.json.

The Data Silo family ships with only the required PDSO parameters (Program UID,
Program Stacking, Last Synced). After loading it, this step adds the parameters
the model tracks (chosen in the Parameter Manager, which runs before Quick Setup).
"""

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
    # "name" may be a list: the first name that is loaded counts (a newer
    # Revit loads and upgrades the newest shipped family, whose name is kept)
    names = ctx.expand(spec.get("name", ""))
    if not isinstance(names, list):
        names = [names]
    names = [name for name in names if name]
    name = names[0] if names else ""
    on_existing = spec.get("on_existing", "skip")
    family = next(
        (
            found
            for found in (revit_utils.find_family(ctx.doc, n) for n in names)
            if found is not None
        ),
        None,
    )

    # Project Config runs before Quick Setup. If it names a Data Silo family
    # that is loaded and is not this one, that family is the Data Silo family:
    # the shipped family is not loaded, and the tracked parameters go into the
    # user's family.
    if spec.get("use_as_data_silo_family"):
        chosen = ctx.pdso_config["data_silo_family"]
        if (
            chosen
            and chosen not in names
            and revit_utils.find_family(ctx.doc, chosen) is not None
        ):
            ctx.add(
                "Project Config",
                status.SKIPPED,
                "Keeping the Data Silo family chosen in Project Config ('{}'); "
                "'{}' was not loaded".format(chosen, name),
            )
            if spec.get("add_tracked_parameters", True):
                _add_tracked_parameters(ctx, chosen)
            return

    candidates = [ctx.resolve_path(path) for path in spec.get("files", [])]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    label = name or (os.path.basename(path) if path else "(unnamed family)")

    if family is not None and on_existing == "skip":
        ctx.add(label, status.SKIPPED, "Already loaded")
    elif path is None:
        hint = ""
        if spec.get("use_as_data_silo_family"):
            hint = (
                " The shipped Data Silo family needs Revit 2026 or later. In an "
                "older Revit, choose a Data Silo family in Project Config."
            )
        ctx.add(
            label,
            status.FAILED,
            "No family file found. Looked for: {}.{}".format(
                "; ".join(candidates), hint
            ),
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
        if (
            spec.get("add_tracked_parameters", True)
            and ctx.pdso_config["data_silo_family"] == family.Name
        ):
            _add_tracked_parameters(ctx, family.Name)


def _add_tracked_parameters(ctx, family_name):
    """Embed the model's tracked parameters in the Data Silo family.

    Data Silos get the tracked parameters through the project binding either way,
    so a problem here is a warning, not a failure.
    """
    # Imported here: these services read Project Config, which Quick Setup can
    # run without (see SetupContext._storage_service)
    from pdso.code.business import parameter_registry_service, parameter_service

    item = "{} parameters".format(family_name)
    try:
        service = parameter_registry_service.PDSOParameterSetupService(ctx.doc)
        report = service.add_tracked_parameters_to_family(
            family_name, parameter_service.shared_parameter_file_for_writes(ctx.doc)
        )
    except Exception as error:
        ctx.add(
            item, status.WARNING, "Couldn't add tracked parameters: {}".format(error)
        )
        return

    added = [
        text
        for level, text in report.messages
        if level == "ok" and text.endswith("added to the Data Silo family.")
    ]
    problems = [
        text for level, text in report.messages if level in ("warning", "error")
    ]
    if problems:
        ctx.add(item, status.WARNING, " ".join(problems))
    elif added:
        ctx.add(
            item,
            status.UPDATED,
            "Added {} tracked parameter{} to the family".format(
                len(added), "s" if len(added) != 1 else ""
            ),
        )
    else:
        notes = [text for level, text in report.messages if level == "info"]
        ctx.add(
            item,
            status.SKIPPED,
            notes[-1] if notes else "The family has every tracked parameter",
        )


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
