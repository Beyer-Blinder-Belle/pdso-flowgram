# -*- coding: utf-8 -*-
"""Harvest Layout: save one unit's layout into the Unit Layout Catalogue.

A unit is an Area with a PDSO data silo inside. Select it (its Area, its data
silo or something in it), or type its Program UID.
"""

import sys

from pyrevit import revit, script

# pyRevit keeps imported library modules in memory. Drop the catalogue modules,
# so that this button uses the code that is on disk now (no pyRevit reload).
for _name in [name for name in sys.modules if name.startswith("pdso.code.catalogue")]:
    del sys.modules[_name]

from pdso.code.business.errors import PDSOError  # noqa: E402
from pdso.code.catalogue import catalogue_api_service as catalogue  # noqa: E402
from pdso.code.catalogue import catalogue_commands as commands  # noqa: E402
from pdso.code.catalogue import catalogue_windows as windows  # noqa: E402

if __name__ == "__main__":
    units = commands.UnitIndex()
    answer = windows.HarvestWindow(
        units.describe, units.uid_of_selection(), commands.owner()
    ).show()
    if answer is None:
        script.exit()
    try:
        harvest = catalogue.harvest_layout(
            revit.doc,
            answer["unit"],
            name=answer["name"],
            program=answer["program"],
            project_type=answer["project_type"],
        )
    except PDSOError as error:
        windows.show_message("The layout was not saved", [(str(error), "bad")], commands.owner())
        script.exit()
    windows.show_layout_result(harvest, commands.owner())
