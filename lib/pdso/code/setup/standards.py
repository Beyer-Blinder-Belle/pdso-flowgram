# -*- coding: utf-8 -*-
"""Find and load the JSON standards that drive PDSO Quick Setup.

Standards are looked up in several folders. For each file (views.json,
schedules.json, ...) the first folder that has it wins, so a company or project
only needs to copy the files it wants to change. Search order, highest first:

1. Project:  a "PDSO Standards" folder next to the model (or its central model)
2. User:     the folder picked with Shift+Click on the Quick Setup button
3. Company:  the folder in the PDSO_STANDARDS_DIR environment variable
4. Default:  the standards shipped with this extension (lib/pdso/standards)
"""

# Python standard libraries
import io
import json
import os

# 3rd Party Modules
from pyrevit import DB, HOST_APP, script

USER_CONFIG_SECTION = "PDSO"
USER_CONFIG_OPTION = "standards_dir"
ENV_VAR = "PDSO_STANDARDS_DIR"
PROJECT_FOLDER_NAME = "PDSO Standards"

SOURCE_PROJECT = "Project"
SOURCE_USER = "User"
SOURCE_COMPANY = "Company"
SOURCE_DEFAULT = "Default"

_HERE = os.path.dirname(os.path.abspath(__file__))


class StandardsError(Exception):
    """A standards file is missing or can't be read."""


def default_standards_dir():
    """The standards folder shipped with the extension (lib/pdso/standards)."""
    return os.path.normpath(os.path.join(_HERE, "..", "..", "standards"))


def extension_dir():
    """The extension folder: the root of the cloned repository (it holds PDSO.tab)."""
    return os.path.normpath(os.path.join(_HERE, "..", "..", "..", ".."))


def repo_dir():
    """The cloned repository. The extension is the repository root, so this is
    the same folder as extension_dir(). Kept for the {repo_dir} token."""
    return extension_dir()


def get_user_standards_dir():
    """The standards folder this user picked, or None."""
    try:
        value = script.get_config(USER_CONFIG_SECTION).get_option(
            USER_CONFIG_OPTION, ""
        )
    except Exception:
        return None
    return value or None


def set_user_standards_dir(path):
    """Save (or clear, with None) this user's standards folder in pyRevit's config."""
    cfg = script.get_config(USER_CONFIG_SECTION)
    cfg.set_option(USER_CONFIG_OPTION, path or "")
    script.save_config()


def get_company_standards_dir():
    """The standards folder set in the PDSO_STANDARDS_DIR environment variable, or None."""
    return os.environ.get(ENV_VAR) or None


def get_project_standards_dir(doc):
    """The "PDSO Standards" folder next to the model (or its central model), or None."""
    if doc is None:
        return None

    model_path = None
    try:
        if doc.IsWorkshared:
            central = doc.GetWorksharingCentralModelPath()
            if central is not None and not central.ServerPath:
                model_path = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(
                    central
                )
    except Exception:
        model_path = None

    model_path = model_path or doc.PathName
    if not model_path or not os.path.isabs(model_path):
        return None

    return os.path.join(os.path.dirname(model_path), PROJECT_FOLDER_NAME)


def expand_tokens(value, tokens):
    """Replace {token} placeholders in strings (recursively in lists and dicts).

    Unknown tokens are left as they are.
    """
    if isinstance(value, dict):
        return dict((k, expand_tokens(v, tokens)) for k, v in value.items())
    if isinstance(value, list):
        return [expand_tokens(v, tokens) for v in value]
    if isinstance(value, basestring):  # noqa: F821 - IronPython 2.7 builtin
        for key, replacement in tokens.items():
            if replacement is not None:
                value = value.replace("{" + key + "}", u"{}".format(replacement))
        return value
    return value


class StandardsFile(object):
    """A loaded standards file and where it came from."""

    def __init__(self, name, path, source, data):
        self.name = name
        self.path = path
        self.source = source
        self.data = data

    @property
    def folder(self):
        return os.path.dirname(self.path)


class Standards(object):
    """Resolves standards files across the project, user, company and default folders."""

    def __init__(self, doc=None):
        self.doc = doc
        self.search_dirs = self._build_search_dirs()

    def _build_search_dirs(self):
        candidates = [
            (SOURCE_PROJECT, get_project_standards_dir(self.doc)),
            (SOURCE_USER, get_user_standards_dir()),
            (SOURCE_COMPANY, get_company_standards_dir()),
            (SOURCE_DEFAULT, default_standards_dir()),
        ]
        search_dirs = []
        for source, folder in candidates:
            if folder and os.path.isdir(folder):
                search_dirs.append((source, os.path.normpath(folder)))
        return search_dirs

    def describe_search_dirs(self):
        """[(source, folder, exists)] for every possible folder, for reporting."""
        rows = [
            (SOURCE_PROJECT, get_project_standards_dir(self.doc)),
            (SOURCE_USER, get_user_standards_dir()),
            (SOURCE_COMPANY, get_company_standards_dir()),
            (SOURCE_DEFAULT, default_standards_dir()),
        ]
        return [
            (source, folder, bool(folder) and os.path.isdir(folder))
            for source, folder in rows
        ]

    def find(self, name):
        """(source, path) of the highest-priority copy of a standards file, or (None, None)."""
        for source, folder in self.search_dirs:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return source, path
        return None, None

    def load(self, name):
        """Load a standards file.

        Returns:
            StandardsFile: The parsed file and where it came from.
        Raises:
            StandardsError: If no folder has the file, or it isn't valid JSON.
        """
        source, path = self.find(name)
        if path is None:
            raise StandardsError(
                "No '{}' found in any standards folder: {}".format(
                    name, ", ".join(folder for _, folder in self.search_dirs)
                )
            )

        try:
            with io.open(path, "r", encoding="utf-8-sig") as handle:
                data = json.loads(handle.read())
        except ValueError as error:
            raise StandardsError("'{}' is not valid JSON: {}".format(path, error))
        except (IOError, OSError) as error:
            raise StandardsError("Could not read '{}': {}".format(path, error))

        if not isinstance(data, dict):
            raise StandardsError(
                "'{}' must contain a JSON object at the top level".format(path)
            )

        return StandardsFile(name, path, source, data)


def base_tokens():
    """Tokens available in every standards file."""
    version = str(HOST_APP.version)
    return {
        "revit_year": version,
        "revit_yy": version[-2:],
        "extension_dir": extension_dir(),
        "repo_dir": repo_dir(),
        "default_standards_dir": default_standards_dir(),
    }
