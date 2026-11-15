# -*- coding: utf-8 -*-
"""Where harvested layouts are saved, remembered in the pyRevit user config.

Unset, harvests go to catalogue/local/ in the repository, which is git-ignored.
Shift-click the Harvest button to choose another folder, e.g. a shared drive
the whole office reads from.
"""

from pyrevit import script

from pdso.code.catalogue.catalogue_store import default_local_folder, sample_folder

CONFIG_SECTION = "PDSOUnitLayoutCatalogue"
FOLDER_OPTION = "catalogue_folder"


def get_catalogue_folder():
    config = script.get_config(CONFIG_SECTION)
    folder = config.get_option(FOLDER_OPTION, "")
    return folder or default_local_folder()


def set_catalogue_folder(folder):
    config = script.get_config(CONFIG_SECTION)
    setattr(config, FOLDER_OPTION, folder)
    script.save_config()


def catalogue_folders():
    """Folders the recommender reads, in order. Later folders win on duplicate ids."""
    folders = [sample_folder(), default_local_folder()]
    chosen = get_catalogue_folder()
    if chosen not in folders:
        folders.append(chosen)
    return folders
