# -*- coding: utf-8 -*-
"""Quick Setup step: load families listed in families.json.

The Data Silo family ships with only the required PDSO parameters (Program UID,
Program Stacking, Last Synced). After loading it, this step adds the parameters
the model tracks (chosen in the Parameter Manager, which runs before Quick Setup).

If no file of a family can be loaded (an older Revit than the shipped family)
and its spec has "placeholder_if_missing", a Generic Model with a small square
marker is built under the family's name instead.
"""

# Python standard libraries
import os
import re

# System Modules
import clr

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from pdso.code.setup import revit_utils, status


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
    # Revit can't load a family saved in a newer version: skip those files
    candidates = [p for p in candidates if not _newer_than_revit(ctx, p)]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    label = name or (os.path.basename(path) if path else "(unnamed family)")

    failure = None
    if family is not None and on_existing == "skip":
        ctx.add(label, status.SKIPPED, "Already loaded")
    elif path is None:
        failure = "No family file this Revit can load. Looked for: {}.".format(
            "; ".join(candidates) or "(only files for a newer Revit)"
        )
    else:
        family_ref = clr.Reference[DB.Family]()
        load_error = ""
        loaded = False
        try:
            with ctx.transaction("Load family {}".format(label)):
                loaded = ctx.doc.LoadFamily(
                    path,
                    revit_utils.FamilyLoadOptions(
                        spec.get("overwrite_parameter_values", False)
                    ),
                    family_ref,
                )
        except Exception as error:
            load_error = ": {}".format(error)
        file_name = os.path.splitext(os.path.basename(path))[0]
        found = family_ref.Value or revit_utils.find_family(ctx.doc, file_name)

        if found is None:
            failure = "Revit did not load '{}'{}.".format(path, load_error)
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

    if failure is not None:
        if family is None and name and spec.get("placeholder_if_missing"):
            family = _build_placeholder(ctx, name, failure, spec)
        else:
            ctx.add(label, status.FAILED, failure + _version_hint(spec))

    if family is not None and spec.get("use_as_data_silo_family"):
        _register_data_silo_family(ctx, family.Name)
        if (
            spec.get("add_tracked_parameters", True)
            and ctx.pdso_config["data_silo_family"] == family.Name
        ):
            _add_tracked_parameters(ctx, family.Name)


def _build_placeholder(ctx, name, failure, spec):
    """Build the placeholder family (see the module docstring), or report why not."""
    try:
        family = revit_utils.build_marker_family(ctx.doc, name)
    except Exception as error:
        ctx.add(
            name,
            status.FAILED,
            "{} Couldn't build a placeholder family either: {}.{}".format(
                failure, error, _version_hint(spec)
            ),
        )
        return None
    ctx.add(
        family.Name,
        status.CREATED,
        "{} Built a placeholder family instead: a small square marker. To use "
        "your own family, choose it in Project Config.".format(failure),
    )
    return family


def _version_hint(spec):
    if not spec.get("use_as_data_silo_family"):
        return ""
    return (
        " The shipped Data Silo family needs Revit 2026 or later. In an older "
        "Revit, choose a Data Silo family in Project Config."
    )


def _newer_than_revit(ctx, path):
    """True if a file name says it is for a newer Revit ("... - Rvt27.rfa")."""
    match = re.search(r"Rvt(\d{2})\.rfa$", os.path.basename(path), re.IGNORECASE)
    try:
        running = int(str(ctx.doc.Application.VersionNumber)[-2:])
    except Exception:
        return False
    return match is not None and int(match.group(1)) > running


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
