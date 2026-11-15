# -*- coding: utf-8 -*-
"""PDSO Parameter Catalog - built-in parameter definitions.

These are the parameters the PDSO tools ship with. They are *options*, not a
guarantee: none of them exist in a model until the Parameter Manager has been
run at least once, and only the ones a user chose to track are created.

What a model actually tracks is stored in the document config
(PdsoConfigDocumentSchema.tethered_parameters) and read through
pdso.code.business.parameter_registry_service.

The GUIDs below must never change: models created with earlier versions of the
PDSO tools already contain shared parameters with these GUIDs.
"""

# Python standard libraries
from collections import OrderedDict

# Custom Modules
from pdso.code.data.extended_shared_parameter import (
    ExtendedSharedParameter,
    ROLE_AREA,
    ROLE_CORE,
    ROLE_STANDARD,
)

# Names the PDSO tools read or write directly in code
PROGRAM_UID = "PDSO - Program UID"
PROGRAM_STACKING = "PDSO - Program Stacking"
LAST_SYNCED = "PDSO - Last Synced"
ZFA = "PDSO - ZFA"
NSF = "PDSO - NSF"
GSF = "PDSO - GSF"

# Shared parameter file group used for parameters the PDSO tools create
SHARED_PARAMETER_GROUP_NAME = "PDSO"


def _param(name, parameter_type, guid, role=ROLE_STANDARD, description="", user_modifiable=True):
    return ExtendedSharedParameter(
        name,
        parameter_type,
        role == ROLE_CORE,
        guid,
        role=role,
        description=description,
        user_modifiable=user_modifiable,
    )


# Built-in parameter catalog, in display order
standard_pdso_parameters = OrderedDict(
    (p.parameter_name, p)
    for p in [
        # Core identification parameters
        _param(
            PROGRAM_UID,
            "string",
            "d5695975-02f2-4f10-ac73-a803c8c3c6cd",
            role=ROLE_CORE,
            description="Unique ID that ties a Data Silo to its Program Volumes.",
        ),
        _param(
            "PDSO - Program Display Name",
            "string",
            "25369a38-d392-48bb-bbf0-f590876d76ae",
        ),
        _param(
            "PDSO - Unit Layout Type",
            "string",
            "d32ac2e5-fae6-4994-b1da-9fde8e7becf5",
        ),
        # User-managed data parameters
        _param("PDSO - Program Type", "string", "33b40dda-a725-437c-b6d1-a2d6e0409902"),
        _param("PDSO - Use Group", "string", "69d8f006-f868-4efe-b2f1-63e17c234437"),
        _param(
            "PDSO - Planning Dept. Misc",
            "string",
            "b153335a-8d54-4205-8d98-8224c9bfb273",
        ),
        _param("PDSO - Accessibility", "string", "99e93e87-a073-4e93-9f86-36741b821fdc"),
        _param(
            PROGRAM_STACKING,
            "multilineText",
            "ae93fa4e-e0c7-4e70-85df-ce45176b083e",
            role=ROLE_CORE,
            description="Levels this program occupies, e.g. '03-05, 10'.",
        ),
        _param(
            "PDSO - Program Market Segment",
            "string",
            "88a5fd0f-15ad-4193-bcd3-61e4cfb59278",
        ),
        _param("PDSO - Misc Modifier", "string", "c2c5d6bd-ec16-4799-bfea-5210accdfd3f"),
        _param("PDSO - Load Factor", "number", "4c03906b-585f-4279-baba-84f8a04f861a"),
        _param(
            "PDSO - DU Bedroom Count",
            "number",
            "ed281228-82ec-4cc2-9930-637395897b90",
        ),
        _param("PDSO - Bathroom Count", "number", "d5fefa64-1e0b-4f67-a6bd-a982e645ba33"),
        _param(
            "PDSO - Bathroom Types",
            "multilineText",
            "c0ad87f3-e7ef-4cbf-9f63-558d35356614",
        ),
        _param("PDSO - Kitchen Count", "number", "57db7537-c40a-4049-b11a-c4f62203941a"),
        _param(
            "PDSO - Kitchen Types",
            "multilineText",
            "490db755-0f83-40d6-8002-9b5b5214facf",
        ),
        # Programmatically managed parameters - harvested from Areas
        _param(
            ZFA,
            "area",
            "fae706d3-8b81-4312-a68e-013dd54a22ae",
            role=ROLE_AREA,
            description="Written by PDSO Area Harvest. Do not edit.",
            user_modifiable=False,
        ),
        _param(
            NSF,
            "area",
            "1bea8991-17a7-4043-bf05-f11a876c9a02",
            role=ROLE_AREA,
            description="Written by PDSO Area Harvest. Do not edit.",
            user_modifiable=False,
        ),
        _param(
            GSF,
            "area",
            "a031daf5-5477-41c4-9d02-681c680c0023",
            role=ROLE_AREA,
            description="Written by PDSO Area Harvest. Do not edit.",
            user_modifiable=False,
        ),
        # Programmatically managed parameters - system metadata
        _param(
            LAST_SYNCED,
            "string",
            "1d5966e2-9a8f-4855-97c2-9a5f952d2175",
            role=ROLE_CORE,
            description="Written by PDSO sync. Do not edit.",
            user_modifiable=False,
        ),
    ]
)

# Parameters that must always be tracked, whatever the user selects
CORE_PARAMETER_NAMES = [
    name for name, p in standard_pdso_parameters.items() if p.role == ROLE_CORE
]
