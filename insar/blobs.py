"""blobs.py: Functions for finding blobs in deformation maps
"""
from __future__ import print_function
import numpy as np
import matplotlib.pyplot as plt
# Note: This is just a temp stopgap to not make skimage a hard requirement
# In the future, will port just the blob function, ski rest of skimage
try:
    import skimage.feature
except ImportError:
    print("Warning: scikit-image not installed. Blob function not available.")
    print("pip install scikit-image")
    pass
from insar.log import get_log
from insar import latlon

logger = get_log()


def find_blobs(image,
               blob_func='blob_log',
               sort_by_value=True,
               value_threshold=1.0,
               min_sigma=3,
               max_sigma=60,
               threshold=0.5,
               **kwargs):
    """Use skimage to find blobs in image

    Args:
        image (ndarray): image containing blobs
        blob_func (str): which of the functions to use to find blobs
            Options: 'blob_log', 'blob_dog', 'blob_doh'
        value_threshold (float): absolute value in the image that blob must surpass
        threshold (float): response threshold passed to the blob finding function
        min_sigma (int): minimum pixel size to check for blobs
        max_sigma (int): max pixel size to check for blobs

    Returns:
        ndarray: list of blobs: [(r, c, s)], r = row num of center,
        c is column, s is sigma (size of Gaussian that detected blob)

    Notes:
        kwargs are passed to the blob_func (such as overlap).
        See reference for full list

    Reference:
    [1] http://scikit-image.org/docs/dev/auto_examples/features_detection/plot_blob.html
    """
    blob_func = getattr(skimage.feature, blob_func)
    blobs = blob_func(
        image, threshold=threshold, min_sigma=min_sigma, max_sigma=max_sigma, **kwargs)
    blobs, values = sort_blobs_by_val(blobs, image)

    if value_threshold:
        blobs = [blob for blob, value in zip(blobs, values) if abs(value) >= value_threshold]
    return np.array(blobs)


def plot_blobs(image, blobs=None, cur_fig=None, cur_axes=None, color='blue', **kwargs):
    """Takes the blob results from find_blobs and overlays on image

    Can either make new figure of plot on top of existing axes.
    """
    if cur_fig:
        cur_axes = cur_fig.gca()
    elif not cur_axes:
        cur_fig = plt.figure()
        cur_axes = cur_fig.gca()
        cur_axes.imshow(image)

    if blobs is None:
        logger.info("Searching for blobs in image.")
        blobs = find_blobs(image, **kwargs)

    for blob in blobs:
        y, x, r = blob
        c = plt.Circle((x, y), np.sqrt(2) * r, color=color, fill=False, linewidth=2, clip_on=False)
        cur_axes.add_patch(c)

    plt.draw()
    plt.show()
    return blobs, cur_axes


def get_blob_values(blobs, image):
    """Finds the image's value of each blob center"""
    coords = blobs[:, :2].astype(int)
    return image[coords[:, 0], coords[:, 1]]


def sort_blobs_by_val(blobs, image):
    """Sort the blobs by their absolute value in the image

    Note: blobs must be in (row, col, sigma) form, not (lat, lon, sigma_ll)

    Returns:
        tuple[tuple[ndarrays], tuple[floats]]: The pair of (blobs, values)
    """
    blob_vals = get_blob_values(blobs, image)
    blob_val_tuples = sorted(zip(blobs, blob_vals), key=lambda tup: abs(tup[1]), reverse=True)
    # Now return as separated into (tuple of blobs, tuple of values)
    # zip is it's own inverse
    return tuple(zip(*blob_val_tuples))


def blobs_latlon(blobs, blob_info):
    """Converts (y, x, sigma) format to (lat, lon, sigma_latlon)

    Uses the dem x_step/y_step data to rescale blobs so that appear on an
    image using lat/lon as the `extent` argument of imshow.
    """
    blob_info = {k.lower(): v for k, v in blob_info.items()}
    blobs_latlon = []
    for blob in blobs:
        row, col, r = blob
        lat, lon = latlon.rowcol_to_latlon(row, col, blob_info)
        new_radius = r * blob_info['x_step']
        blobs_latlon.append((lat, lon, new_radius))

    return np.array(blobs_latlon)