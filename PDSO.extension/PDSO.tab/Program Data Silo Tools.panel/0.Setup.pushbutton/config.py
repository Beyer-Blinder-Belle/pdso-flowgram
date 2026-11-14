# -*- coding: utf-8 -*-
"""PDSO Model Setup options (Shift+Click).

Choose the standards folder Model Setup reads from, or export the shipped
defaults to a folder as a starting point for company standards.
"""

# Python standard libraries
import os
import shutil

# 3rd Party Modules
from pyrevit import forms, revit, script

# Custom Modules
from pdso.code.setup.shared_parameter_file import (
    DEFAULT_FILE_NAME,
    write_shared_parameter_file,
)
from pdso.code.setup.standards import (
    ENV_VAR,
    PROJECT_FOLDER_NAME,
    Standards,
    StandardsError,
    default_standards_dir,
    get_user_standards_dir,
    set_user_standards_dir,
)

OPTION_USE = "Use a standards folder..."
OPTION_EXPORT = "Export default standards to a folder..."
OPTION_SHARED_PARAMETERS = "Export PDSO shared parameter file..."
OPTION_OPEN = "Open the standards folders"
OPTION_RESET = "Stop using my standards folder"


def _describe(standards):
    lines = ["Model Setup looks for each standards file in these folders, in order:", ""]
    for source, folder, exists in standards.describe_search_dirs():
        if not folder:
            state = "not set"
        elif exists:
            state = folder
        else:
            state = "{} (not found)".format(folder)
        lines.append("{}: {}".format(source, state))
    lines += [
        "",
        "Project: a '{}' folder next to the model.".format(PROJECT_FOLDER_NAME),
        "Company: the {} environment variable.".format(ENV_VAR),
    ]
    return "\n".join(lines)


def _use_folder():
    folder = forms.pick_folder(title="Choose a PDSO standards folder")
    if not folder:
        return
    set_user_standards_dir(folder)
    forms.alert(
        "Model Setup will now use standards from:\n{}\n\nFiles missing from this "
        "folder fall back to the shipped defaults.".format(folder),
        title="PDSO Model Setup",
    )


def _export_defaults():
    folder = forms.pick_folder(title="Export PDSO default standards to...")
    if not folder:
        return

    copied, kept = [], []
    source_dir = default_standards_dir()
    for file_name in sorted(os.listdir(source_dir)):
        if not file_name.lower().endswith((".json", ".md")):
            continue
        target = os.path.join(folder, file_name)
        if os.path.exists(target):
            kept.append(file_name)
        else:
            shutil.copy2(os.path.join(source_dir, file_name), target)
            copied.append(file_name)

    message = "Copied: {}".format(", ".join(copied) or "nothing")
    if kept:
        message += "\nAlready there, not overwritten: {}".format(", ".join(kept))

    if forms.alert(
        message + "\n\nUse this folder for Model Setup now?",
        title="PDSO Model Setup",
        yes=True,
        no=True,
    ):
        set_user_standards_dir(folder)


def _export_shared_parameter_file(standards):
    """Write a shared parameter file from the parameters.json Model Setup would use."""
    try:
        parameters = standards.load("parameters.json")
    except StandardsError as error:
        forms.alert(str(error), title="PDSO Model Setup")
        return

    path = forms.save_file(
        file_ext="txt",
        default_name=os.path.splitext(DEFAULT_FILE_NAME)[0],
        title="Save PDSO shared parameter file",
    )
    if not path:
        return

    try:
        write_shared_parameter_file(parameters.data, path)
    except (ValueError, KeyError, IOError, OSError) as error:
        forms.alert(
            "Could not write the shared parameter file: {}".format(error),
            title="PDSO Model Setup",
        )
        return

    forms.alert(
        "Saved {} parameters from:\n{}\n\nto:\n{}\n\nTo add them to the project: "
        "Manage > Project Parameters > New > Shared parameter > Select, then pick "
        "this file.".format(
            len(parameters.data.get("parameters", [])), parameters.path, path
        ),
        title="PDSO Model Setup",
    )


def _open_folders(standards):
    for _, folder in standards.search_dirs:
        os.startfile(folder)


if __name__ == "__main__":
    standards = Standards(revit.doc)
    options = [OPTION_USE, OPTION_EXPORT, OPTION_SHARED_PARAMETERS, OPTION_OPEN]
    if get_user_standards_dir():
        options.append(OPTION_RESET)

    choice = forms.CommandSwitchWindow.show(options, message=_describe(standards))

    if choice == OPTION_USE:
        _use_folder()
    elif choice == OPTION_EXPORT:
        _export_defaults()
    elif choice == OPTION_SHARED_PARAMETERS:
        _export_shared_parameter_file(standards)
    elif choice == OPTION_OPEN:
        _open_folders(standards)
    elif choice == OPTION_RESET:
        set_user_standards_dir(None)
        forms.alert("Model Setup will use the company or default standards.")
    else:
        script.exit()
