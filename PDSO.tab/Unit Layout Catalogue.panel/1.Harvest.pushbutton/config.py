# -*- coding: utf-8 -*-
"""Shift-click: choose the folder harvested layouts are saved to."""

from pyrevit import forms

from pdso.code.catalogue.catalogue_settings import (
    get_catalogue_folder,
    set_catalogue_folder,
)

if __name__ == "__main__":
    current = get_catalogue_folder()
    folder = forms.pick_folder(title="Catalogue folder (now: {})".format(current))
    if folder:
        set_catalogue_folder(folder)
        forms.alert("Harvested layouts will be saved to:\n{}".format(folder))
