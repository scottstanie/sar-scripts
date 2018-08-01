#! /usr/bin/env python
"""Author: Scott Staniewicz
Helper functions to prepare and process data
Email: scott.stanie@utexas.edu
"""
from __future__ import division
import glob
from math import floor, sin, cos, sqrt, atan2, radians
import errno
import os
import shutil
import numpy as np
from numpy import deg2rad
from scipy.ndimage.interpolation import shift
import multiprocessing as mp

import insar.sario
import insar.parsers
from insar.log import get_log, log_runtime

logger = get_log()


def get_file_ext(filename):
    """Extracts the file extension, including the '.' (e.g.: .slc)

    Examples:
        >>> print(get_file_ext('radarimage.slc'))
        .slc
        >>> print(get_file_ext('unwrapped.lowpass.unw'))
        .unw

    """
    return os.path.splitext(filename)[1]


def mkdir_p(path):
    """Emulates bash `mkdir -p`, in python style
    Used for igrams directory creation
    """
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def which(program):
    """Mimics UNIX which

    Used from https://stackoverflow.com/a/377028"""

    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None


def downsample_im(image, rate=10):
    """Takes a numpy matrix of an image and returns a smaller version

    Args:
        image (ndarray) 2D array of an image
        rate (int) the reduction rate to downsample
    """
    return image[::rate, ::rate]


def floor_float(num, ndigits):
    """Like rounding to ndigits, but flooring

    Used for .dem.rsc creation, because rounding to 12 sigfigs
    causes the fortran routines to overstep the matrix and fail,
    since 0.000277777778*3600 = 1.00000000079.. , but
    0.000277777777*3600 = 0.99999999719

    Example:
        >>> floor_float(1/3600, 12)
        0.000277777777
    """
    return floor((10**ndigits) * num) / (10**ndigits)


def clip(image):
    """Convert float image to only range 0 to 1 (clips)"""
    return np.clip(np.abs(image), 0, 1)


def log(image):
    """Converts magnitude amplitude image to log scale"""
    if np.iscomplexobj(image):
        image = np.abs(image)
    return 20 * np.log10(image)


# Alias: convert
db = log


def mag(db_image):
    """Reverse of log/db: decibel to magnitude"""
    return 10**(db_image / 20)


def mask_zeros(image):
    """Turn image into masked array, 0s masked"""
    return np.ma.masked_equal(image, 0)


def percent_zero(filepath=None, arr=None):
    """Function to give the percentage of a file that is exactly zero

    Used as a quality assessment check

    Args:
        filepath (str): path to file to check
        arr (ndarray): pre-loaded array to check

    Returns:
        float: decimal from 0 to 1, ratio of zeros to total entries

    Example:
        >>> a = np.array([[1 + 1j, 0.0], [1, 0.0001]])
        >>> print(percent_zero(arr=a))
        0.25
    """
    if filepath:
        arr = insar.sario.load(filepath)
    return (np.sum(arr == 0) / arr.size)


def _check_and_move(fp, zero_threshold, test, mv_dir):
    """Wrapper func for clean_files multiprocessing"""
    logger.debug("Checking {}".format(fp))
    pct = percent_zero(filepath=fp)
    if pct > zero_threshold:
        logger.info("Moving {} for having {:.2f}% zeros to {}".format(fp, 100 * pct, mv_dir))
        if not test:
            shutil.move(fp, mv_dir)


@log_runtime
def clean_files(ext, path=".", zero_threshold=0.50, test=True):
    """Move files of type ext from path with a high pct of zeros

    Args:
        ext (str): file extension to open. Must be loadable by sario.load
        path (str): path of directory to search
        zero_threshold (float): between 0 and 1, threshold to delete files
            if they contain greater ratio of zeros
        test (bool): If true, doesn't delete files, just lists
    """

    file_glob = os.path.join(path, "*{}".format(ext))
    logger.info("Searching {} for files with zero threshold {}".format(file_glob, zero_threshold))

    # Make a folder to store the bad geos
    mv_dir = os.path.join(path, 'bad_{}'.format(ext.replace('.', '')))
    mkdir_p(mv_dir) if not test else logger.info("Test mode: not moving files.")

    max_procs = mp.cpu_count() // 2
    pool = mp.Pool(processes=max_procs)
    results = [
        pool.apply_async(_check_and_move, (fp, zero_threshold, test, mv_dir))
        for fp in glob.glob(file_glob)
    ]
    # Now ask for results so processes launch
    [res.get() for res in results]
    pool.close()


def rowcol_to_latlon(row, col, rsc_data=None):
    """ Takes the row, col of a pixel and finds its lat/lon

    Can also pass numpy arrays of row, col.
    row, col must match size

    Args:
        row (int or ndarray): row number
        col (int or ndarray): col number
        rsc_data (dict): data output from sario.load_dem_rsc

    Returns:
        tuple[float, float]: lat, lon for the pixel

    Example:
        >>> rsc_data = {"X_FIRST": 1.0, "Y_FIRST": 2.0, "X_STEP": 0.2, "Y_STEP": -0.1}
        >>> rowcol_to_latlon(7, 3, rsc_data)
        (1.4, 1.4)
    """
    start_lon = rsc_data["X_FIRST"]
    start_lat = rsc_data["Y_FIRST"]
    lon_step, lat_step = rsc_data["X_STEP"], rsc_data["Y_STEP"]
    lat = start_lat + (row - 1) * lat_step
    lon = start_lon + (col - 1) * lon_step
    return lat, lon


def split_array_into_blocks(data):
    """Takes a long rectangular array (like UAVSAR) and creates blocks

    Useful to look at small data pieces at a time in dismph

    Returns:
        blocks (list[np.ndarray])
    """
    rows, cols = data.shape
    blocks = np.array_split(data, np.ceil(rows / cols))
    return blocks


def split_and_save(filename):
    """Creates several files from one long data file

    Saves them with same filename with .1,.2,.3... at end before ext
    e.g. brazos_14937_17087-002_17088-003_0001d_s01_L090HH_01.int produces
        brazos_14937_17087-002_17088-003_0001d_s01_L090HH_01.1.int
        brazos_14937_17087-002_17088-003_0001d_s01_L090HH_01.2.int...

    Output:
        newpaths (list[str]): full paths to new files created
    """

    data = insar.sario.load_file(filename)
    blocks = split_array_into_blocks(data)

    ext = insar.sario.get_file_ext(filename)
    newpaths = []

    for ix_step, block in enumerate(blocks, start=1):
        fname = filename.replace(ext, ".{}{}".format(str(ix_step), ext))
        print("Saving {}".format(fname))
        insar.sario.save(fname, block)
        newpaths.append(fname)

    return newpaths


def combine_cor_amp(corfilename, save=True):
    """Takes a .cor file from UAVSAR (which doesn't contain amplitude),
    and creates a new file with amplitude data interleaved for dishgt

    dishgt brazos_14937_17087-002_17088-003_0001d_s01_L090HH_01_withamp.cor 3300 1 5000 1
      where 3300 is number of columns/samples, and we want the first 5000 rows. the final
      1 is needed for the contour interval to set a max of 1 for .cor data

    Inputs:
        corfilename (str): string filename of the .cor from UAVSAR
        save (bool): True if you want to save the combined array

    Returns:
        cor_with_amp (np.ndarray) combined correlation + amplitude (as complex64)
        outfilename (str): same name as corfilename, but _withamp.cor
            Saves a new file under outfilename
    Note: .ann and .int files must be in same directory as .cor
    """
    ext = insar.sario.get_file_ext(corfilename)
    assert ext == '.cor', 'corfilename must be a .cor file'

    intfilename = corfilename.replace('.cor', '.int')

    intdata = insar.sario.load_file(intfilename)
    amp = np.abs(intdata)

    cordata = insar.sario.load_file(corfilename)
    # For dishgt, it expects the two matrices stacked [[amp]; [cor]]
    cor_with_amp = np.vstack((amp, cordata))

    outfilename = corfilename.replace('.cor', '_withamp.cor')
    insar.sario.save(outfilename, cor_with_amp)
    return cor_with_amp, outfilename


def sliding_window_view(x, shape, step=None):
    """
    Create sliding window views of the N dimensions array with the given window
    shape. Window slides across each dimension of `x` and provides subsets of `x`
    at any window position.

    Adapted from https://github.com/numpy/numpy/pull/10771

    Args:
        x (ndarray): Array to create sliding window views.
        shape (sequence of int): The shape of the window.
            Must have same length as number of input array dimensions.
        step: (sequence of int), optional
            The steps of window shifts for each dimension on input array at a time.
            If given, must have same length as number of input array dimensions.
            Defaults to 1 on all dimensions.
    Returns:
        ndarray: Sliding window views (or copies) of `x`.
            view.shape = (x.shape - shape) // step + 1

    Notes
    -----
    ``sliding_window_view`` create sliding window views of the N dimensions array
    with the given window shape and its implementation based on ``as_strided``.
    The returned views are *readonly* due to the numpy sliding tricks.
    Examples
    --------
    >>> i, j = np.ogrid[:3,:4]
    >>> x = 10*i + j
    >>> shape = (2,2)
    >>> sliding_window_view(x, shape)[0, 0]
    array([[ 0,  1],
           [10, 11]])
    >>> sliding_window_view(x, shape)[1, 2]
    array([[12, 13],
           [22, 23]])
    """
    # first convert input to array, possibly keeping subclass
    x = np.array(x, copy=False)

    try:
        shape = np.array(shape, np.int)
    except ValueError:
        raise TypeError('`shape` must be a sequence of integer')
    else:
        if shape.ndim > 1:
            raise ValueError('`shape` must be one-dimensional sequence of integer')
        if len(x.shape) != len(shape):
            raise ValueError("`shape` length doesn't match with input array dimensions")
        if np.any(shape <= 0):
            raise ValueError('`shape` cannot contain non-positive value')

    if step is None:
        step = np.ones(len(x.shape), np.intp)
    else:
        try:
            step = np.array(step, np.intp)
        except ValueError:
            raise TypeError('`step` must be a sequence of integer')
        else:
            if step.ndim > 1:
                raise ValueError('`step` must be one-dimensional sequence of integer')
            if len(x.shape) != len(step):
                raise ValueError("`step` length doesn't match with input array dimensions")
            if np.any(step <= 0):
                raise ValueError('`step` cannot contain non-positive value')

    o = (np.array(x.shape) - shape) // step + 1  # output shape
    if np.any(o <= 0):
        raise ValueError('window shape cannot larger than input array shape')

    strides = x.strides
    view_strides = strides * step

    view_shape = np.concatenate((o, shape), axis=0)
    view_strides = np.concatenate((view_strides, strides), axis=0)
    view = np.lib.stride_tricks.as_strided(x, view_shape, view_strides, writeable=False)

    return view


def latlon_to_dist(lat_lon_start, lat_lon_end, R=6378):
    """Find the distance between two lat/lon points on Earth

    Uses the haversine formula: https://en.wikipedia.org/wiki/Haversine_formula
    so it does not account for the ellopsoidal Earth shape. Will be with about
    0.5-1% of the correct value.

    Notes: lats and lons are in degrees, and the values used for R Earth
    (6373 km) are optimized for locations around 39 degrees from the equator

    Reference: https://andrew.hedges.name/experiments/haversine/

    Args:
        lat_lon_start (tuple[int, int]): (lat, lon) in degrees of start
        lat_lon_end (tuple[int, int]): (lat, lon) in degrees of end
        R (float): Radius of earth

    Returns:
        float: distance between two points in km

    Examples:
        >>> round(latlon_to_dist((38.8, -77.0), (38.9, -77.1)), 1)
        14.1
    """
    lat1, lon1 = lat_lon_start
    lat2, lon2 = lat_lon_end
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1 = radians(lat1)
    lat2 = radians(lat2)
    a = (sin(dlat / 2)**2) + (cos(lat1) * cos(lat2) * sin(dlon / 2)**2)
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def offset(img_info1, img_info2, axis=None):
    """Calculates how many pixels two images are offset

    Finds offset FROM img_info2 TO img_info1

    If image2 is 3 pixels down and 2 left of image1, the returns would
    be offset(im1, im2) = (3, 2), offset(im1, im2, axis=1) = 2

    To align image2 with image1, you can do:
    offsets = offset(img_info1, img_info2)
    Examples:
    >>> fake_info1 = {'x_first': -155.0, 'x_step': 0.1, 'y_first': 19.5, 'y_step': -0.2}
    >>> fake_info1 = {'x_first': -155.0, 'x_step': 0.1, 'y_first': 19.5, 'y_step': -0.2}

    """
    if img_info1['y_step'] != img_info2['y_step']:
        raise ValueError("Step sizes must be the same for the two images")

    row_offset = (img_info2['y_first'] - img_info1['y_first']) / img_info1['y_step']
    col_offset = (img_info2['x_first'] - img_info1['x_first']) / img_info1['x_step']
    output_tuple = (row_offset, col_offset)
    if axis is None:
        return output_tuple
    else:
        if not isinstance(axis, int):
            raise ValueError("axis must be an int less than 2")
        return output_tuple[axis]


def align_image_pair(image_pair, info_list, verbose=True):
    """Takes two images, shifts the second to align with the first

    Args:
        image_pair (tuple[ndarray, ndarray]): two images to align
        info_list (tuple[dict, dict]): the associated rsc_data/ann_info
            for the two images

    Returns:
        ndarray: shifted version of the 2nd image of image_pair
    """

    cropped_images = crop_to_smallest(image_pair)
    img1, img2 = cropped_images
    img1_ann, img2_ann = info_list

    offset_tup = offset(img1_ann, img2_ann)
    if verbose:
        logger.info("Offset (rows, cols): {}".format(offset_tup))
    # Note: we use order=1 since default order=3 spline was giving
    # negative values for images (leading to invalid nonsense)
    return shift(img2, offset_tup, order=1)


def crop_to_smallest(image_list):
    """Makes all images the smallest dimension so they are alignable

    Args:
        image_list (iterable[ndarray]): list of images, or 3D array
            with 1st axis as the image number
    Returns:
        list[ndarray]: images of all same size

    Example:
    >>> a = np.arange(10).reshape((5, 2))
    >>> b = np.arange(9).reshape((3, 3))
    >>> cropped = crop_to_smallest((a, b))
    >>> print(all(img.shape == (3, 2) for img in cropped))
    True
    """
    shapes = np.array([i.shape for i in image_list])
    min_rows, min_cols = np.min(shapes, axis=0)
    return [img[:min_rows, :min_cols] for img in image_list]


def latlon_grid(rows=None,
                cols=None,
                y_step=None,
                x_step=None,
                y_first=None,
                x_first=None,
                sparse=False):
    """Takes sizes and spacing info, creates a grid of values

    Args:
        rows (int): number of rows
        cols (int): number of cols
        y_step (float): spacing between rows
        x_step (float): spacing between cols
        y_first (float): starting location of first row at top
        x_first (float): starting location of first col on left
        sparse (bool): Optional (default False). Passed through to
            np.meshgrid to optionally conserve memory

    Returns:
        tuple[ndarray, ndarray]: the XX, YY grids of longitudes and lats

    Examples:
    >>> test_grid_data = {'cols': 2, 'rows': 3, 'x_first': -155.0, 'x_step': 0.01, 'y_first': 19.5, 'y_step': -0.2}
    >>> lons, lats = latlon_grid(**test_grid_data)
    >>> lons
    array([[-155.  , -154.99],
           [-155.  , -154.99],
           [-155.  , -154.99]])
    >>> lats
    array([[19.5, 19.5],
           [19.3, 19.3],
           [19.1, 19.1]])
    """
    x = np.linspace(x_first, x_first + (cols - 1) * x_step, cols).reshape((1, cols))
    y = np.linspace(y_first, y_first + (rows - 1) * y_step, rows).reshape((rows, 1))
    return np.meshgrid(x, y, sparse=sparse)


def latlon_grid_extent(rows=None, cols=None, y_step=None, x_step=None, y_first=None, x_first=None):
    """Takes sizes and spacing info, finds boundaries

    Used for `matplotlib.pyplot.imshow` keyword arg `extent`:
    extent : scalars (left, right, bottom, top)

    Args:
        rows (int): number of rows
        cols (int): number of cols
        y_step (float): spacing between rows
        x_step (float): spacing between cols
        y_first (float): starting location of first row at top
        x_first (float): starting location of first col on left

    Returns:
        tuple[float]: the boundaries of the latlon grid in order:
        (lon_left,lon_right,lat_bottom,lat_top)

    Examples:
    >>> test_grid_data = {'cols': 2, 'rows': 3, 'x_first': -155.0, 'x_step': 0.01, 'y_first': 19.5, 'y_step': -0.2}
    >>> print(latlon_grid_extent(**test_grid_data))
    (-155.0, -154.99, 19.1, 19.5)
    """
    return (x_first, x_first + x_step * (cols - 1), y_first + y_step * (rows - 1), y_first)


def rotate_xyz_to_enu(xyz, lat, lon):
    """Rotates a vector in XYZ coords to ENU

    Args:
        xyz (list[float], ndarray[float]): length 3 x, y, z coordinates
        lat (float): latitude of point to rotate into
        lon (float): longitude of point to rotate into
    """
    # Rotate about axis 3 with longitude, then axis 1 with latitude
    R3 = rot(90 + lon, 3)
    R1 = rot(90 - lat, 1)
    R = np.matmul(R3, R1)
    return np.matmul(R, xyz)


def rot(angle, axis):
    """
    Find a 3x3 euler rotation matrix given an angle and axis.

    Rotation matrix used for rotating a vector about a single axis.

    Args:
        angle (float): angle in degrees to rotate
        axis (int): 1, 2 or 3
    """
    R = np.eye(3)
    cang = cos(deg2rad(angle))
    sang = sin(deg2rad(angle))
    if (axis == 1):
        R[1, 1] = cang
        R[2, 2] = cang
        R[1, 2] = sang
        R[2, 1] = -sang
    elif (axis == 2):
        R[0, 0] = cang
        R[2, 2] = cang
        R[0, 2] = -sang
        R[2, 0] = sang
    elif (axis == 3):
        R[0, 0] = cang
        R[1, 1] = cang
        R[1, 0] = -sang
        R[0, 1] = sang
    else:
        raise ValueError("axis must be 1, 2 or 2")
    return R


def read_los_output(los_file):
    """Reads file of x,y,z positions, parses for lat/lon and vectors

    Example line:
     19.0  -155.0
        0.94451263868681301      -0.30776088245682498      -0.11480032487005554
         35999       35999
    """

    def _line_to_floats(line, split_char=None):
        return tuple(map(float, line.split(split_char)))

    with open(los_file) as f:
        los_lines = f.read().splitlines()

    lat_lon_list = [_line_to_floats(line) for line in los_lines[::3]]
    xyz_list = [_line_to_floats(line) for line in los_lines[1::3]]
    return lat_lon_list, xyz_list


def convert_xyz_latlon_to_enu(lat_lons, xyz_array):
    return [rotate_xyz_to_enu(xyz, lat, lon) for (lat, lon), xyz in zip(lat_lons, xyz_array)]
