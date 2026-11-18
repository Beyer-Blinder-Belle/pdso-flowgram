# -*- coding: utf-8 -*-
"""Audit Report (Shift+Click): delete orphaned Program Volumes.

An orphaned Program Volume family belongs to no Data Silo: its Data Silo was
deleted, or an old UID or prefix change left it behind. The Audit Report lists
them; this deletes them, with their instances, after a confirmation.
"""

# 3rd Party Modules
from pyrevit import forms, revit

# Custom Modules
from pdso.code.business import program_service
from pdso.code.business.errors import PDSOError

TITLE = "Orphaned Program Volumes"


if __name__ == "__main__":
    try:
        orphans = program_service.orphaned_program_volumes(revit.doc)
    except PDSOError as error:
        forms.alert(str(error), title=TITLE, exitscript=True)

    if not orphans:
        forms.alert("No orphaned Program Volume families were found.", title=TITLE, exitscript=True)

    names = [
        "{} ({} instance{})".format(
            orphan["name"], len(orphan["instances"]), "" if len(orphan["instances"]) == 1 else "s"
        )
        for orphan in orphans
    ]
    chosen = forms.SelectFromList.show(
        names,
        title=TITLE,
        button_name="Delete the selected families",
        multiselect=True,
    )
    if not chosen:
        forms.alert("Nothing was deleted.", title=TITLE, exitscript=True)

    family_ids = [orphan["id"] for orphan, name in zip(orphans, names) if name in chosen]
    count = sum(len(orphan["instances"]) for orphan in orphans if orphan["id"] in family_ids)
    if not forms.alert(
        "Delete {} Program Volume famil{} and their {} instance{}? Tags on those "
        "instances are deleted too. You can undo it.".format(
            len(family_ids),
            "y" if len(family_ids) == 1 else "ies",
            count,
            "" if count == 1 else "s",
        ),
        title=TITLE,
        yes=True,
        no=True,
    ):
        forms.alert("Nothing was deleted.", title=TITLE, exitscript=True)

    try:
        result = program_service.purge_orphaned_program_volumes(revit.doc, family_ids)
    except PDSOError as error:
        forms.alert(str(error), title=TITLE, exitscript=True)
    forms.alert(
        "Deleted {} Program Volume famil{}.".format(
            len(result["deleted"]), "y" if len(result["deleted"]) == 1 else "ies"
        ),
        title=TITLE,
    )
