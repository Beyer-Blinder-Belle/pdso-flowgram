# -*- coding: utf-8 -*-
"""Runs the PDSO Quick Setup steps and reports what they did.

Each step reads its own standards file (<step id>.json) and adds rows to the
report with SetupContext.add(). Steps run inside one TransactionGroup, so the
whole setup shows up as a single "PDSO Quick Setup" undo in Revit. Each item a
step creates gets its own transaction, so one bad item doesn't roll back the rest.
"""

# Python standard libraries
import os
import traceback
from contextlib import contextmanager

# 3rd Party Modules
from pyrevit import DB, script

# Custom Modules
from pdso.code.setup import revit_utils, status
from pdso.code.setup.standards import StandardsError, base_tokens, expand_tokens
from pdso.code.setup.steps import STEP_RUNNERS

# Used when the model has no PDSO Project Config yet (matches extensible_storage_service)
DEFAULT_PROGRAM_VOLUME_PREFIX = "PDSO_ProgramVolume-"

class SetupStep(object):
    """A step listed in setup.json."""

    def __init__(self, step_id, title, enabled):
        self.id = step_id
        self.title = title
        self.enabled = enabled

    @classmethod
    def from_standards(cls, setup_file):
        """Read the step list from a loaded setup.json."""
        steps = []
        for entry in setup_file.data.get("steps", []):
            step_id = entry.get("id")
            if not step_id:
                continue
            steps.append(
                cls(step_id, entry.get("title", step_id), entry.get("enabled", True))
            )
        return steps


class SetupContext(object):
    """Everything a step needs: the document, tokens, the PDSO config, and the report."""

    def __init__(self, doc, standards):
        self.doc = doc
        self.standards = standards
        self.logger = script.get_logger()
        self.results = []
        self.current_step = None
        self.current_file = None

        # None until first use; False if the extensible_storage library is missing
        self._storage = None
        self.storage_error = None
        self.pdso_config = self._read_pdso_config()
        self.tokens = base_tokens()

    # ----- Report -----

    def add(self, item, row_status, message=""):
        """Add a row to the report for the current step."""
        self.results.append((self.current_step, item, row_status, message))
        if row_status in (status.WARNING, status.FAILED):
            log = self.logger.warning
        else:
            log = self.logger.debug
        log(u"[{}] {} - {}: {}".format(self.current_step, item, row_status, message))

    # ----- Tokens and paths -----

    def expand(self, value, **extra_tokens):
        """Replace {tokens} in a value from a standards file."""
        tokens = dict(self.tokens)
        tokens["data_silo_family"] = self.pdso_config["data_silo_family"]
        tokens["program_volume_prefix"] = self.pdso_config["program_volume_prefix"]
        if self.current_file is not None:
            tokens["standards_dir"] = self.current_file.folder
        tokens.update(extra_tokens)
        return expand_tokens(value, tokens)

    def resolve_path(self, path):
        """Expand tokens, and resolve relative paths against the current standards file's folder."""
        path = self.expand(path)
        if not os.path.isabs(path) and self.current_file is not None:
            path = os.path.join(self.current_file.folder, path)
        return os.path.normpath(path)

    # ----- Transactions -----

    @contextmanager
    def transaction(self, name):
        """A transaction that hides Revit warnings and rolls back on errors."""
        transaction = DB.Transaction(self.doc, name)
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(revit_utils.SwallowWarnings())
        transaction.SetFailureHandlingOptions(options)
        transaction.Start()
        try:
            yield transaction
        except Exception:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
            raise
        else:
            result = transaction.Commit()
            if result != DB.TransactionStatus.Committed:
                raise RuntimeError(
                    "Revit did not commit '{}' ({})".format(name, result)
                )

    # ----- PDSO Project Config (Extensible Storage) -----

    def _storage_service(self):
        """The PDSO Extensible Storage service, or None if the library isn't installed.

        The reason it's unavailable is kept in storage_error for the report.
        """
        if self._storage is None:
            try:
                from pdso.code.business.extensible_storage_service import (
                    PDSOExtensibleStorageService,
                )

                self._storage = PDSOExtensibleStorageService(self.doc)
            except Exception as error:
                self._storage = False
                self.storage_error = (
                    "PDSO Project Config is unavailable. Is the extensible_storage "
                    "library installed? ({})".format(error)
                )
                self.logger.warning(self.storage_error)
        return self._storage or None

    def _read_pdso_config(self):
        """Read the values setup needs from the model's PDSO Project Config."""
        config = {
            "data_silo_family": None,
            "program_volume_prefix": DEFAULT_PROGRAM_VOLUME_PREFIX,
            "area_scheme_mappings": {},
        }
        service = self._storage_service()
        if service is None:
            return config

        try:
            stored = service.get_document_config()
            if stored is None:
                return config
            config["data_silo_family"] = stored.data_silo_family_name or None
            config["program_volume_prefix"] = (
                stored.program_volume_family_name_prefix
                or DEFAULT_PROGRAM_VOLUME_PREFIX
            )
            if stored.area_scheme_mappings:
                config["area_scheme_mappings"] = dict(stored.area_scheme_mappings)
        except Exception as error:
            self.logger.warning("Could not read PDSO Project Config: {}".format(error))
        return config

    def area_scheme_name(self, pdso_scheme_key):
        """The Revit area scheme mapped to a PDSO scheme key ("PDSO - GSF") in Project Config."""
        return self.pdso_config["area_scheme_mappings"].get(
            pdso_scheme_key, pdso_scheme_key
        )

    def set_data_silo_family(self, family_name):
        """Point the model's PDSO Project Config at a Data Silo family.

        Creates the Project Config with defaults first if the model doesn't have one.
        Must be called outside a transaction (Extensible Storage opens its own).
        Later steps in this run use the family even if it can't be saved.

        Returns:
            bool: True if the Project Config now uses this family.
        """
        if self.pdso_config["data_silo_family"] == family_name:
            return True

        service = self._storage_service()
        if service is None:
            self.pdso_config["data_silo_family"] = family_name
            return False

        if not service.check_document_pdso_config_schema_exists():
            service.ensure_schemas_exist()
            # Defaults may include area scheme mappings and a prefix
            self.pdso_config = self._read_pdso_config()

        saved = service.update_document_config(data_silo_family_name=family_name)
        self.pdso_config["data_silo_family"] = family_name
        return bool(saved)


def run_setup(doc, standards, steps):
    """Run the given setup steps in order.

    Args:
        doc (DB.Document): The project document.
        standards (Standards): Where to read standards files from.
        steps (list[SetupStep]): The steps to run.
    Returns:
        SetupContext: The context, with the report in .results.
    """
    ctx = SetupContext(doc, standards)

    group = DB.TransactionGroup(doc, "PDSO Quick Setup")
    group.Start()
    try:
        for step in steps:
            ctx.current_step = step.id
            ctx.current_file = None

            runner = STEP_RUNNERS.get(step.id)
            if runner is None:
                ctx.add(step.id, status.FAILED, "Unknown setup step")
                continue

            try:
                ctx.current_file = standards.load(step.id + ".json")
            except StandardsError as error:
                ctx.add(step.id + ".json", status.FAILED, str(error))
                continue

            try:
                runner(ctx, ctx.current_file.data)
            except Exception as error:
                ctx.add(step.title, status.FAILED, str(error))
                ctx.logger.debug(traceback.format_exc())
        group.Assimilate()
    except Exception:
        group.RollBack()
        raise

    return ctx


def print_report(ctx, output):
    """Print the setup report to the pyRevit output window."""
    output.print_md("## Standards used")
    rows = []
    for step_id in sorted(set(row[0] for row in ctx.results)):
        source, path = ctx.standards.find(step_id + ".json")
        if path:
            rows.append([step_id + ".json", source, path])
    if rows:
        output.print_table(rows, columns=["File", "Source", "Path"])

    counts = {}
    for _, _, row_status, _ in ctx.results:
        counts[row_status] = counts.get(row_status, 0) + 1

    output.print_md("## Results")
    output.print_md(
        " | ".join(
            u"{} {} {}".format(status.ICONS.get(row_status, ""), count, row_status)
            for row_status, count in sorted(counts.items())
        )
        or "Nothing to do."
    )

    current_step = None
    table = []
    for step_id, item, row_status, message in ctx.results:
        if step_id != current_step:
            if table:
                output.print_table(table, columns=["Item", "Status", "Notes"])
            output.print_md("### {}".format(step_id))
            current_step = step_id
            table = []
        table.append(
            [item, u"{} {}".format(status.ICONS.get(row_status, ""), row_status), message]
        )
    if table:
        output.print_table(table, columns=["Item", "Status", "Notes"])
