# -*- coding: utf-8 -*-
"""Unit Layout Catalogue: harvest -> store -> recommend -> create.

layout_geometry      unit frames and the eight rectangle orientations (no Revit)
layout_entry         the catalogue entry format (no Revit)
catalogue_store      JSON files on disk, plus an SVG plan sketch (no Revit)
layout_recommender   ranks entries for a target unit (no Revit)
catalogue_settings   which folder harvests are saved to (pyRevit config)
revit_layout_service harvest from and place into units: Areas with a data silo (Revit)
"""
