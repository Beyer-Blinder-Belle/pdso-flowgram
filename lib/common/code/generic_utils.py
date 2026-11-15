import os

from System import Uri, UriKind
from System.Windows.Controls import Image
from System.Windows.Media.Imaging import BitmapImage


def lib_path():
    """
    Returns the absolute path to the 'lib' directory of the current extension.
    """
    # Get the path to the current file (generic_utils.py)
    current_file = os.path.abspath(__file__)
    # Go up three directories: code -> common -> lib
    lib_directory = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
    return lib_directory


# I think this also exists in pyRevit...
def load_image(filepath):
    image = Image()
    bi = BitmapImage()
    bi.BeginInit()
    image_uri = filepath
    bi.UriSource = Uri(image_uri, UriKind.RelativeOrAbsolute)
    bi.EndInit()
    image.Source = bi

    return image


def load_image_source(filepath):
    bi = BitmapImage()
    bi.BeginInit()
    image_uri = filepath
    bi.UriSource = Uri(image_uri, UriKind.RelativeOrAbsolute)
    bi.EndInit()

    return bi
