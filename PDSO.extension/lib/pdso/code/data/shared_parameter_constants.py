# -*- coding: utf-8 -*-
"""PDSO Parameter Constants - Standard parameter definitions and configuration.

This module contains all the standard PDSO parameter definitions used throughout
the system. These parameters define the schema for PDSO data exchange.
"""

# Custom Modules
from pdso.code.data.extended_shared_parameter import ExtendedSharedParameter


# Dictionary of all parameters required for the PDSO object to function correctly
standard_pdso_parameters = {
    # Core identification parameters
    "PDSO - Program UID": ExtendedSharedParameter(
        "PDSO - Program UID", "string", True, "d5695975-02f2-4f10-ac73-a803c8c3c6cd"
    ),
    "PDSO - Program Display Name": ExtendedSharedParameter(
        "PDSO - Program Display Name",
        "string",
        True,
        "25369a38-d392-48bb-bbf0-f590876d76ae",
    ),
    "PDSO - Unit Layout Type": ExtendedSharedParameter(
        "PDSO - Unit Layout Type",
        "string",
        True,
        "d32ac2e5-fae6-4994-b1da-9fde8e7becf5",
    ),
    # User-managed data parameters
    "PDSO - Program Type": ExtendedSharedParameter(
        "PDSO - Program Type", "string", True, "33b40dda-a725-437c-b6d1-a2d6e0409902"
    ),
    "PDSO - Use Group": ExtendedSharedParameter(
        "PDSO - Use Group", "string", True, "69d8f006-f868-4efe-b2f1-63e17c234437"
    ),
    "PDSO - Planning Dept. Misc": ExtendedSharedParameter(
        "PDSO - Planning Dept. Misc",
        "string",
        True,
        "b153335a-8d54-4205-8d98-8224c9bfb273",
    ),
    "PDSO - Accessibility": ExtendedSharedParameter(
        "PDSO - Accessibility", "string", True, "99e93e87-a073-4e93-9f86-36741b821fdc"
    ),
    "PDSO - Program Stacking": ExtendedSharedParameter(
        "PDSO - Program Stacking",
        "multilineText",
        True,
        "ae93fa4e-e0c7-4e70-85df-ce45176b083e",
    ),
    "PDSO - Program Market Segment": ExtendedSharedParameter(
        "PDSO - Program Market Segment",
        "string",
        True,
        "88a5fd0f-15ad-4193-bcd3-61e4cfb59278",
    ),
    "PDSO - Misc Modifier": ExtendedSharedParameter(
        "PDSO - Misc Modifier", "string", True, "c2c5d6bd-ec16-4799-bfea-5210accdfd3f"
    ),
    "PDSO - Load Factor": ExtendedSharedParameter(
        "PDSO - Load Factor", "number", True, "4c03906b-585f-4279-baba-84f8a04f861a"
    ),
    "PDSO - DU Bedroom Count": ExtendedSharedParameter(
        "PDSO - DU Bedroom Count",
        "number",
        True,
        "ed281228-82ec-4cc2-9930-637395897b90",
    ),
    "PDSO - Bathroom Count": ExtendedSharedParameter(
        "PDSO - Bathroom Count", "number", True, "d5fefa64-1e0b-4f67-a6bd-a982e645ba33"
    ),
    "PDSO - Bathroom Types": ExtendedSharedParameter(
        "PDSO - Bathroom Types",
        "multilineText",
        True,
        "c0ad87f3-e7ef-4cbf-9f63-558d35356614",
    ),
    "PDSO - Kitchen Count": ExtendedSharedParameter(
        "PDSO - Kitchen Count", "number", True, "57db7537-c40a-4049-b11a-c4f62203941a"
    ),
    "PDSO - Kitchen Types": ExtendedSharedParameter(
        "PDSO - Kitchen Types",
        "multilineText",
        True,
        "490db755-0f83-40d6-8002-9b5b5214facf",
    ),
    # Programmatically managed parameters - harvested from Areas
    "PDSO - ZFA": ExtendedSharedParameter(
        "PDSO - ZFA", "area", True, "fae706d3-8b81-4312-a68e-013dd54a22ae"
    ),
    "PDSO - NSF": ExtendedSharedParameter(
        "PDSO - NSF", "area", True, "1bea8991-17a7-4043-bf05-f11a876c9a02"
    ),
    "PDSO - GSF": ExtendedSharedParameter(
        "PDSO - GSF", "area", True, "a031daf5-5477-41c4-9d02-681c680c0023"
    ),
    # Programmatically managed parameters - system metadata (may be hidden or read-only)
    "PDSO - Last Synced": ExtendedSharedParameter(
        "PDSO - Last Synced", "string", True, "1d5966e2-9a8f-4855-97c2-9a5f952d2175"
    ),
    # Test parameter
    "Fake": ExtendedSharedParameter(
        "Fake", "string", False, "d5695975-02f2-4f10-ac73-a803c8c3c622"
    ),
}
