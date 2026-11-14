# -*- coding: utf-8 -*-
"""PDSO Parameter Presets - named sets of parameters to start a project from.

A preset only says which parameters *should* be ticked in the Parameter Manager.
Loading one changes nothing in the model until the user clicks Apply.

Two presets are built in and can't be overwritten:
- Default: every built-in parameter (what the PDSO tools have always used)
- Blank:   nothing optional; only the parameters the tools require

User presets are JSON files in a presets folder. The folder defaults to the
user's AppData, and can be pointed at a shared (network) folder so a team uses
the same presets. File format:

    {
      "format": "pdso-parameter-preset",
      "version": 1,
      "name": "Residential",
      "description": "Optional text",
      "parameters": [
        {"name": "PDSO - Use Group", "builtin": true},
        {"name": "PDSO - Balcony Area", "type": "area",
         "guid": "0f9c...", "builtin": false}
      ]
    }

Custom parameters keep their GUID in the preset, so every project that loads the
preset creates the *same* shared parameter (schedules and tags then match
across projects).

All file access here may hit a network drive: callers on the UI thread must use
call_with_timeout() (see common.code.background) or a worker thread.
"""

# Python standard libraries
import io
import json
import os
import re
from collections import OrderedDict

# 3rd Party Modules
from pyrevit import script

# Custom Modules
from pdso.code.data.extended_shared_parameter import PARAMETER_TYPES
from pdso.code.data.shared_parameter_constants import standard_pdso_parameters

logger = script.get_logger()

PRESET_FORMAT = "pdso-parameter-preset"
PRESET_VERSION = 1
PRESET_EXTENSION = ".json"

DEFAULT_PRESET_NAME = "Default"
BLANK_PRESET_NAME = "Blank"
RESERVED_PRESET_NAMES = (DEFAULT_PRESET_NAME.lower(), BLANK_PRESET_NAME.lower())

_USER_CONFIG_SECTION = "PDSO_ParameterManager"
_USER_CONFIG_FOLDER_OPTION = "preset_folder"


class PresetParameter(object):
    """One parameter in a preset."""

    def __init__(self, name, builtin, parameter_type=None, guid=None):
        self.name = name
        self.builtin = builtin
        self.parameter_type = parameter_type  # custom parameters only
        self.guid = guid  # custom parameters only; string or None

    def to_json(self):
        data = OrderedDict([("name", self.name), ("builtin", self.builtin)])
        if not self.builtin:
            data["type"] = self.parameter_type
            data["guid"] = self.guid
        return data


class ParameterPreset(object):
    """A named set of parameters.

    Attributes:
        name (str): Preset name shown in the dropdown
        description (str): Optional text
        parameters (list[PresetParameter]): Parameters to tick
        is_builtin (bool): True for Default and Blank
        path (str): File the preset was read from (None for built-in presets)
    """

    def __init__(self, name, parameters, description="", is_builtin=False, path=None):
        self.name = name
        self.parameters = parameters
        self.description = description
        self.is_builtin = is_builtin
        self.path = path

    @property
    def builtin_names(self):
        return set(p.name for p in self.parameters if p.builtin)

    @property
    def custom_parameters(self):
        return [p for p in self.parameters if not p.builtin]

    def to_json(self):
        # Ordered so saved files are stable and easy to read or diff
        return OrderedDict(
            [
                ("format", PRESET_FORMAT),
                ("version", PRESET_VERSION),
                ("name", self.name),
                ("description", self.description),
                ("parameters", [p.to_json() for p in self.parameters]),
            ]
        )

    @classmethod
    def from_json(cls, data, path=None):
        """Build a preset from parsed JSON. Raises ValueError if it isn't a valid preset."""
        if not isinstance(data, dict) or data.get("format") != PRESET_FORMAT:
            raise ValueError("not a PDSO parameter preset file")
        if data.get("version", 0) > PRESET_VERSION:
            raise ValueError(
                "made by a newer version of the PDSO tools (version {})".format(
                    data.get("version")
                )
            )
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("preset has no name")

        parameters = []
        for entry in data.get("parameters") or []:
            param_name = (entry.get("name") or "").strip()
            if not param_name:
                continue
            # A name that matches a built-in is always treated as the built-in
            if entry.get("builtin") or param_name in standard_pdso_parameters:
                parameters.append(PresetParameter(param_name, True))
                continue
            parameter_type = entry.get("type")
            if parameter_type not in PARAMETER_TYPES:
                logger.warning(
                    "Preset '{}': skipped '{}' (unsupported type '{}')".format(
                        name, param_name, parameter_type
                    )
                )
                continue
            parameters.append(
                PresetParameter(param_name, False, parameter_type, entry.get("guid") or None)
            )

        return cls(name, parameters, data.get("description") or "", False, path)


def get_builtin_presets():
    """Default (every built-in parameter) and Blank (required parameters only)."""
    default = ParameterPreset(
        DEFAULT_PRESET_NAME,
        [PresetParameter(name, True) for name in standard_pdso_parameters.keys()],
        "Every built-in PDSO parameter.",
        is_builtin=True,
    )
    blank = ParameterPreset(
        BLANK_PRESET_NAME,
        [],
        "Only the parameters the PDSO tools require.",
        is_builtin=True,
    )
    return [default, blank]


# =============================================================================
# Presets folder
# =============================================================================


def default_preset_folder():
    return os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "PDSO", "ParameterPresets"
    )


def get_preset_folder():
    """The presets folder this user has chosen (or the default). No file access."""
    try:
        config = script.get_config(_USER_CONFIG_SECTION)
        folder = config.get_option(_USER_CONFIG_FOLDER_OPTION, "") or ""
    except Exception:
        folder = ""
    return folder or default_preset_folder()


def set_preset_folder(folder):
    """Remember the presets folder for this user (pyRevit config)."""
    try:
        config = script.get_config(_USER_CONFIG_SECTION)
        config.set_option(_USER_CONFIG_FOLDER_OPTION, folder or "")
        script.save_config()
    except Exception as e:
        logger.warning("Could not remember presets folder: {}".format(e))


# =============================================================================
# Reading and writing (file access: call off the UI thread or with a timeout)
# =============================================================================


def list_user_presets(folder):
    """Read every preset file in a folder.

    Returns:
        tuple(list[ParameterPreset], list[str]): Presets sorted by name, and
            one message per file that couldn't be read.
    """
    presets = []
    problems = []
    if not folder or not os.path.isdir(folder):
        return presets, problems

    for file_name in sorted(os.listdir(folder)):
        if not file_name.lower().endswith(PRESET_EXTENSION):
            continue
        path = os.path.join(folder, file_name)
        try:
            with io.open(path, "r", encoding="utf-8") as preset_file:
                preset = ParameterPreset.from_json(json.load(preset_file), path)
        except Exception as e:
            problems.append("{}: {}".format(file_name, e))
            continue
        if preset.name.lower() in RESERVED_PRESET_NAMES:
            problems.append(
                "{}: the name '{}' is reserved for a built-in preset".format(
                    file_name, preset.name
                )
            )
            continue
        presets.append(preset)

    presets.sort(key=lambda p: p.name.lower())
    return presets, problems


def validate_preset_name(name):
    """Return an error message for an unusable preset name, or None."""
    if not name or not name.strip():
        return "Enter a name for the preset."
    if name.strip().lower() in RESERVED_PRESET_NAMES:
        return "'{}' is a built-in preset; choose another name.".format(name.strip())
    if len(name.strip()) > 80:
        return "Keep the name under 80 characters."
    return None


def preset_file_path(folder, name):
    """File path for a preset name (unsafe filename characters replaced)."""
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name.strip()).strip(". ")
    return os.path.join(folder, (safe or "preset") + PRESET_EXTENSION)


def find_existing_preset_file(folder, name):
    """Path of the preset in `folder` with this name (case-insensitive), or None."""
    presets, _ = list_user_presets(folder)
    for preset in presets:
        if preset.name.lower() == name.strip().lower():
            return preset.path
    return None


def save_preset(folder, preset, path=None):
    """Write a preset to the folder (creating the folder if needed).

    Args:
        folder (str): Presets folder
        preset (ParameterPreset): Preset to save
        path (str): File to overwrite (e.g. an existing preset with the same name);
            defaults to a file named after the preset

    Returns:
        str: Path written
    """
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = path or preset_file_path(folder, preset.name)
    text = json.dumps(
        preset.to_json(), indent=2, separators=(",", ": "), ensure_ascii=False
    )
    if not isinstance(text, type(u"")):
        text = text.decode("utf-8")
    with io.open(path, "w", encoding="utf-8") as preset_file:
        preset_file.write(text)
    return path
