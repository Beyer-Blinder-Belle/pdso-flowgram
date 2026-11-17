# -*- coding: utf-8 -*-
"""The defaults of a new PDSO configuration, in one place.

Plain Python with no Revit imports, so the services, Quick Setup and the
catalogue can all use it. A model's Project Config overrides each of these;
use them only when a model has no configuration yet.
"""

# The shipped Data Silo family is "<prefix> - Rvt<yy>" (see families.json)
DATA_SILO_FAMILY_PREFIX = "PDSO - Program Data Silo Object"

PROGRAM_VOLUME_PREFIX = "PDSO_ProgramVolume-"

# {PDSO parameter: Revit area scheme}
AREA_SCHEME_MAPPINGS = {
    "PDSO - GSF": "PDSO - GSF",
    "PDSO - NSF": "PDSO - NSF",
    "PDSO - ZFA": "PDSO - ZFA",
}

UID_PATTERN = r"^[A-Za-z0-9]{2}-\d{2}-[A-Za-z0-9]{2}$"

PROGRAM_VOLUME_EXTRUSION_HEIGHT = 8.0  # feet


def data_silo_family_name(revit_version):
    """The shipped Data Silo family's name for a Revit version ("2026" -> "... - Rvt26")."""
    return "{} - Rvt{}".format(DATA_SILO_FAMILY_PREFIX, str(revit_version)[-2:])


def area_schemes():
    """The default area scheme names, sorted."""
    return sorted(set(AREA_SCHEME_MAPPINGS.values()))
