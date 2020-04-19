"""prepare.py

Preprocessing insar data for timeseries analysis

Forms stacks as .h5 files for easy access to depth-wise slices
"""
import h5py
import hdf5plugin
import os
import multiprocessing
import subprocess
import numpy as np
from scipy.ndimage.morphology import binary_opening
import rasterio as rio

from apertools import sario, utils, latlon
import apertools.gps
from apertools.log import get_log, log_runtime

from .constants import (
    # DATE_FMT,
    MASK_FILENAME,
    INT_FILENAME,
    UNW_FILENAME,
    CC_FILENAME,
    STACK_DSET,
    STACK_MEAN_DSET,
    STACK_FLAT_DSET,
    STACK_FLAT_SHIFTED_DSET,
    GEO_MASK_DSET,
    GEO_MASK_SUM_DSET,
    IGRAM_MASK_DSET,
    IGRAM_MASK_SUM_DSET,
    DEM_RSC_DSET,
    # GEOLIST_DSET,
    # INTLIST_DSET,
    # REFERENCE_ATTR,
    # REFERENCE_STATION_ATTR,
)

logger = get_log()


def create_dset(h5file, dset_name, shape, dtype, chunks=True, compress=True):
    comp_dict = hdf5plugin.Blosc() if compress else dict()
    with h5py.File(h5file, "a") as f:
        f.create_dataset(dset_name, shape=shape, dtype=dtype, chunks=chunks, **comp_dict)


def load_in_chunks(unw_stack_file="unw_stack.h5", flist=[], dset="stack_flat_dset", n=None):
    with h5py.File(unw_stack_file, "r+") as f:
        chunk_size = f[dset].chunks
        dshape = f[dset].shape
        dt = f[dset].dtype

    n = n or chunk_size[0]
    buf = np.empty((n, dshape[1], dshape[2]), dtype=dt)
    lastidx = 0
    for idx, fname in enumerate(flist):
        if idx % n == 0 and idx > 0:
            print(f"Writing {lastidx}:{lastidx+n}")
            with h5py.File("unw_test.h5", "r+") as f:
                f[dset][lastidx:lastidx + n, :, :] = buf
            lastidx = idx

        with rio.open(fname, driver="ROI_PAC") as src:
            curidx = idx % n
            # f["stack_flat_dset"][idx, :, :] = src.read(2)
            buf[curidx, :, :] = src.read(2)
            # print(src.shape)
    return buf


@log_runtime
def deramp_and_shift_unws(ref_row,
                          ref_col,
                          unw_stack_file=UNW_FILENAME,
                          dset_name="stack_flat_shifted",
                          directory=".",
                          order=1,
                          window=5,
                          overwrite=False):

    if not sario.check_dset(mask_file, dset_name, overwrite):
        return
    # First make the empty dataset and save aux info
    in_ext = ".unw"
    file_list = sario.find_files(directory=directory, search_term="*" + in_ext)
    band = 2

    with rio.open(file_list[0]) as src:
        rows, cols = src.shape
        # bshape = src.block_shapes[band-1]  # TODO: use?
        dtype = src.dtypes[band - 1]

    shape = (len(file_list), rows, cols)
    create_dset(unw_stack_file, dset_name, shape, dtype, chunks=True, compress=True)

    # Save the extra files too
    rsc_data = sario.load(os.path.join(directory, "dem.rsc"))
    sario.save_dem_to_h5(unw_stack_file, rsc_data, dset_name=DEM_RSC_DSET, overwrite=overwrite)
    sario.save_geolist_to_h5(directory, unw_stack_file, overwrite=overwrite)
    sario.save_intlist_to_h5(directory, unw_stack_file, overwrite=overwrite)

    with h5py.File(unw_stack_file, "r+") as f:
        chunk_shape = f[dset_name].chunks
        chunk_depth, chunk_rows, chunk_cols = chunk_shape
        # n = n or chunk_size[0]

    buf = np.empty((chunk_depth, rows, cols), dtype=dtype)
    win = window // 2
    lastidx = 0
    for idx, in_fname in enumerate(file_list):
        if idx % 100 == 0:
            print(f"Processing {in_fname} -> {idx+1} out of {len(file_list)}")

        if idx % chunk_depth == 0 and idx > 0:
            print(f"Writing {lastidx}:{lastidx+chunk_depth}")
            with h5py.File(unw_stack_file, "r+") as f:
                f[dset_name][lastidx:lastidx + chunk_depth, :, :] = buf

            lastidx = idx

        with rio.open(in_fname, driver="ROI_PAC") as inf:
            mask = _read_mask_by_idx(idx)
            # amp = inf.read(1)
            phase = inf.read(2)
            deramped_phase = remove_ramp(phase, order=order, mask=mask)

            # Now center it on the shift window
            patch = deramped_phase[ref_row - win:ref_row + win + 1, ref_col - win:ref_col + win + 1]

            deramped_phase -= np.mean(patch)

            # now store this in the bugger until emptied
            curidx = idx % chunk_depth
            buf[curidx, :, :] = phase


@log_runtime
def prepare_stacks(
    igram_path,
    ref_row=None,
    ref_col=None,
    ref_station=None,
    order=1,
    window=5,
    overwrite=False,
):
    # int_stack_file = os.path.join(igram_path, INT_FILENAME)
    unw_stack_file = os.path.join(igram_path, UNW_FILENAME)
    cc_stack_file = os.path.join(igram_path, CC_FILENAME)
    mask_stack_file = os.path.join(igram_path, MASK_FILENAME)

    # create_igram_stacks(
    #     igram_path,
    #     int_stack_file=int_stack_file,
    #     unw_stack_file=unw_stack_file,
    #     cc_stack_file=cc_stack_file,
    #     overwrite=overwrite,
    # )

    create_mask_stacks(igram_path, overwrite=overwrite)

    if ref_station is not None:
        rsc_data = sario.load(os.path.join(igram_path, "dem.rsc"))
        ref_row, ref_col = apertools.gps.station_rowcol(
            station_name=ref_station,
            rsc_data=rsc_data,
        )
    if ref_row is None or ref_col is None:
        ref_row, ref_col, ref_station = find_reference_location(
            unw_stack_file=unw_stack_file,
            cc_stack_file=cc_stack_file,
            mask_stack_file=mask_stack_file,
        )

    deramp_and_shift_unws(
        ref_row,
        ref_col,
        unw_stack_file=unw_stack_file,
        dset_name="stack_flat_shifted",
        directory=igram_path,
        order=1,
        window=window,
        overwrite=overwrite,
    )


# TODO: this is for reading windows of a big ratser, not needed for now
def all_bands(file_list, band=2, col_off=0, row_off=0, height=20):
    from rasterio.windows import Window
    with rio.open(file_list[0]) as src:
        rows, cols = src.shape
        # bshape = src.block_shapes[band-1]  # TODO: use?
        dt = src.dtypes[band - 1]

    block = np.empty((len(file_list), height, cols), dtype=dt)
    for idx, f in enumerate(file_list):
        try:
            with rio.open(f, driver="ROI_PAC") as src:
                block[idx] = src.read(band, window=Window(col_off, row_off, cols, height))
        except Exception as e:
            print(idx, f, e)
    return block


def _run_stack(igram_path, d, overwrite):
    if d["filename"] is None:
        return
    logger.info("Creating hdf5 stack %s" % d["filename"])
    create_hdf5_stack(directory=igram_path, overwrite=overwrite, **d)
    sario.save_geolist_to_h5(igram_path, d["filename"], overwrite=overwrite)
    sario.save_intlist_to_h5(igram_path, d["filename"], overwrite=overwrite)


@log_runtime
def create_igram_stacks(
    igram_path,
    int_stack_file=INT_FILENAME,
    unw_stack_file=UNW_FILENAME,
    cc_stack_file=CC_FILENAME,
    overwrite=False,
):
    # TODO: make this just make a vrt of unw and .int
    sario.make_unw_vrt(directory=igram_path, output="unw_stack.vrt", ext=".unw")
    stack_dicts = (
        # dict(file_ext=".int", create_mean=False, filename=int_stack_file),
        dict(file_ext=".unw", create_mean=False, filename=unw_stack_file),
        # dict(file_ext=".cc", create_mean=True, filename=cc_stack_file),
    )
    for d in stack_dicts:
        if d["filename"] is None:
            continue
        logger.info("Creating hdf5 stack %s" % d["filename"])
        create_hdf5_stack(directory=igram_path, overwrite=overwrite, **d)
        sario.save_geolist_to_h5(igram_path, d["filename"], overwrite=overwrite)
        sario.save_intlist_to_h5(igram_path, d["filename"], overwrite=overwrite)

    pool = multiprocessing.Pool()
    results = [pool.apply_async(_run_stack, args=(igram_path, d, overwrite)) for d in stack_dicts]
    return [res.get() for res in results]


@log_runtime
def create_mask_stacks_gdal(igram_path, mask_filename=None, geo_path=None, overwrite=False):
    """Create mask stacks for areas in .geo and .int using `gdal_translate`

    Uses .geo dead areas
    """
    import gdal
    from osgeo import gdalconst  # gdal_array,
    if mask_filename is None:
        mask_file = os.path.join(igram_path, MASK_FILENAME)

    if geo_path is None:
        geo_path = utils.get_parent_dir(igram_path)

    # Used to shrink the .geo masks to save size as .int masks
    row_looks, col_looks = apertools.sario.find_looks_taken(igram_path, geo_path=geo_path)

    rsc_data = sario.load(sario.find_rsc_file(os.path.join(igram_path, "dem.rsc")))

    save_geo_masks_gdal(
        geo_path,
        mask_file,
        dem_rsc=rsc_data,
        overwrite=overwrite,
    )


@log_runtime
def create_mask_stacks(igram_path, mask_filename=None, geo_path=None, overwrite=False):
    """Create mask stacks for areas in .geo and .int

    Uses .geo dead areas as well as correlation
    """
    if mask_filename is None:
        mask_file = os.path.join(igram_path, MASK_FILENAME)

    if geo_path is None:
        geo_path = utils.get_parent_dir(igram_path)

    # Used to shrink the .geo masks to save size as .int masks
    row_looks, col_looks = apertools.sario.find_looks_taken(igram_path, geo_path=geo_path)

    rsc_data = sario.load(sario.find_rsc_file(os.path.join(igram_path, "dem.rsc")))
    sario.save_dem_to_h5(mask_file, rsc_data, dset_name=DEM_RSC_DSET, overwrite=overwrite)
    sario.save_geolist_to_h5(igram_path, mask_file, overwrite=overwrite)
    sario.save_intlist_to_h5(igram_path, mask_file, overwrite=overwrite)

    save_geo_masks(
        geo_path,
        mask_file,
        dem_rsc=rsc_data,
        row_looks=row_looks,
        col_looks=col_looks,
        overwrite=overwrite,
    )

    compute_int_masks(
        mask_file=mask_file,
        igram_path=igram_path,
        geo_path=geo_path,
        row_looks=row_looks,
        col_looks=col_looks,
        dem_rsc=rsc_data,
        overwrite=overwrite,
    )
    # TODO: now add the correlation check
    return mask_file


def save_geo_masks(directory,
                   mask_file=MASK_FILENAME,
                   dem_rsc=None,
                   dset_name=GEO_MASK_DSET,
                   row_looks=1,
                   col_looks=1,
                   overwrite=False):
    """Creates .mask files for geos where zeros occur

    Makes look arguments are to create arrays the same size as the igrams
    Args:
        overwrite (bool): erase the dataset from the file if it exists and recreate
    """
    def _get_geo_mask(geo_arr):
        # Uses for removing single mask pixels from nearest neighbor resample
        m = binary_opening(np.abs(geo_arr) == 0, structure=np.ones((3, 3)))
        return np.ma.make_mask(m, shrink=False)

    geo_file_list = sario.find_files(directory=directory, search_term="*.geo")
    rsc_geo = sario.load(sario.find_rsc_file(filename=geo_file_list[0]))
    gshape = (rsc_geo["file_length"], rsc_geo["width"])
    # Make the empty stack, or delete if exists
    shape = _find_file_shape(dem_rsc=dem_rsc,
                             file_list=geo_file_list,
                             row_looks=row_looks,
                             col_looks=col_looks)

    if not sario.check_dset(mask_file, dset_name, overwrite):
        return
    if not sario.check_dset(mask_file, GEO_MASK_SUM_DSET, overwrite):
        return
    create_dset(mask_file, dset_name, shape=shape, dtype=bool)

    with h5py.File(mask_file, "a") as f:
        dset = f[dset_name]
        for idx, geo_fname in enumerate(geo_file_list):
            # save as an individual file too
            mask_name = os.path.split(geo_fname)[1] + ".mask"
            if os.path.exists(mask_name):
                logger.info(f"{mask_name} exists, skipping.")
                continue

            # g = sario.load(geo_fname, looks=(row_looks, col_looks))
            gmap = np.memmap(
                geo_fname,
                dtype="complex64",
                mode="r",
                shape=gshape,
            )
            g_subsample = gmap[(row_looks - 1)::row_looks, (col_looks - 1)::col_looks]
            # ipdb.set_trace()
            logger.info(f'Saving {geo_fname} to stack')
            cur_mask = _get_geo_mask(g_subsample)
            sario.save(mask_name, cur_mask)
            dset[idx] = cur_mask

        # Also add a composite mask depthwise
        f[GEO_MASK_SUM_DSET] = np.sum(dset, axis=0)


def save_geo_masks_gdal(directory=".",
                        mask_file="masks.vrt",
                        dem_rsc=None,
                        dset_name=GEO_MASK_DSET,
                        overwrite=False):
    """Creates .mask files for geos where zeros occur

    Makes look arguments are to create arrays the same size as the igrams
    Args:
        overwrite (bool): erase the dataset from the file if it exists and recreate
    """
    import gdal
    from osgeo import gdalconst  # gdal_array,
    rsc_data = sario.load(dem_rsc)
    save_path = os.path.split(mask_file)[0]

    geo_file_list = sario.find_files(directory=directory, search_term="*.geo")
    for f in geo_file_list:
        logger.info("Processing mask for %s" % f)
        rsc_geo = sario.find_rsc_file(filename=f)
        apertools.sario.save_as_vrt(filename=f, rsc_file=rsc_geo)
        src = f + ".vrt"
        tmp_tif = os.path.join(save_path, "tmp.tif")
        # gdal_translate S1A_20171019.geo.vrt tmp.tif -outsize 792 624 -r average
        gdal.Translate(
            tmp_tif,
            src,
            # noData=0.0,
            width=rsc_data["width"],
            height=rsc_data["file_length"],
            # resampleAlg=gdalconst.GRA_Average,
            resampleAlg=gdalconst.GRA_NearestNeighbour,
        )
        # Now find zero pixels to get mask:
        ds = gdal.Open(tmp_tif)
        in_arr = ds.GetRasterBand(1).ReadAsArray()
        # Uses for removing single mask pixels from nearest neighbor resample
        mask_arr = binary_opening(np.abs(in_arr) == 0, structure=np.ones((3, 3)))

        outfile = os.path.join(save_path, os.path.split(f + ".mask.tif")[1])
        sario.save_as_geotiff(outfile=outfile, array=mask_arr, rsc_data=rsc_data)
        # cmd = gdal_calc.py -A {inp} --outfile {out} --type Byte --calc="abs(A)==0"
        # logger.info(cmd)
        # subprocess.run(cmd, shell=True, check=True)

    # Now stack all these together


def compute_int_masks(
    mask_file=None,
    igram_path=None,
    geo_path=None,
    row_looks=None,
    col_looks=None,
    dem_rsc=None,
    dset_name=IGRAM_MASK_DSET,
    overwrite=False,
):
    """Creates igram masks by taking the logical-or of the two .geo files

    Assumes save_geo_masks already run
    """
    if not sario.check_dset(mask_file, dset_name, overwrite):
        return
    if not sario.check_dset(mask_file, IGRAM_MASK_SUM_DSET, overwrite):
        return

    int_date_list = sario.find_igrams(directory=igram_path)
    int_file_list = sario.find_igrams(directory=igram_path, parse=False)

    geo_date_list = sario.find_geos(directory=geo_path)

    # Make the empty stack, or delete if exists
    shape = _find_file_shape(dem_rsc=dem_rsc,
                             file_list=int_file_list,
                             row_looks=row_looks,
                             col_looks=col_looks)
    create_dset(mask_file, dset_name, shape=shape, dtype=bool)

    with h5py.File(mask_file, "a") as f:
        geo_mask_stack = f[GEO_MASK_DSET]
        int_mask_dset = f[dset_name]
        for idx, (early, late) in enumerate(int_date_list):
            early_idx = geo_date_list.index(early)
            late_idx = geo_date_list.index(late)
            early_mask = geo_mask_stack[early_idx]
            late_mask = geo_mask_stack[late_idx]

            int_mask_dset[idx] = np.logical_or(early_mask, late_mask)

        # Also create one image of the total masks
        f[IGRAM_MASK_SUM_DSET] = np.sum(int_mask_dset, axis=0)


def create_hdf5_stack(filename=None,
                      directory=None,
                      dset=STACK_DSET,
                      file_ext=None,
                      create_mean=True,
                      save_rsc=True,
                      overwrite=False,
                      **kwargs):
    """Make stack as hdf5 file from a group of existing files

    Args:
        filename (str): if none provided, creates a file `[file_ext]_stack.h5`

    Returns:
        filename
    """
    def _create_mean(dset):
        """Used to create a mean without loading all into mem with np.mean"""
        mean_buf = np.zeros((dset.shape[1], dset.shape[2]), dset.dtype)
        for idx in range(len(dset)):
            mean_buf += dset[idx]
        return mean_buf / len(dset)

    if not filename:
        fname = "{fext}_stack.h5".format(fext=file_ext.strip("."))
        filename = os.path.abspath(os.path.join(directory, fname))
        logger.info("Creating stack file %s" % filename)

    if utils.get_file_ext(filename) not in (".h5", ".hdf5"):
        raise ValueError("filename must end in .h5 or .hdf5")

    # TODO: do we want to replace the .unw files with .h5 files, then make a Virtual dataset?
    # layout = h5py.VirtualLayout(shape=(len(file_list), nrows, ncols), dtype=dtype)
    if not sario.check_dset(filename, dset, overwrite):
        return

    file_list = sario.find_files(directory=directory, search_term="*" + file_ext)

    testf = sario.load(file_list[0])
    shape = (len(file_list), testf.shape[0], testf.shape[1])
    create_dset(filename, dset, shape, dtype=testf.dtype)
    with h5py.File(filename, "a") as hf:
        # First record the names in a dataset
        filename_dset = dset + "_filenames"
        hf[filename_dset] = np.array(file_list, dtype=np.string_)

        dset = hf[dset]
        for idx, f in enumerate(file_list):
            dset[idx] = sario.load(f)

    if save_rsc:
        dem_rsc = sario.load(os.path.join(directory, "dem.rsc"))
        sario.save_dem_to_h5(filename, dem_rsc, dset_name=DEM_RSC_DSET, overwrite=overwrite)

    if create_mean:
        if not sario.check_dset(filename, STACK_MEAN_DSET, overwrite):
            return
        with h5py.File(filename, "a") as hf:
            mean_data = _create_mean(hf[STACK_DSET])
            hf.create_dataset(
                STACK_MEAN_DSET,
                data=mean_data,
            )

    return filename


# TODO: Process the correlation, mask very bad corr pixels in the igrams


def _find_file_shape(dem_rsc=None, file_list=None, row_looks=None, col_looks=None):
    if not dem_rsc:
        try:
            g = sario.load(file_list[0], looks=(row_looks, col_looks))
        except IndexError:
            raise ValueError("No .geo files found in s")
        except TypeError:
            raise ValueError("Need file_list if no dem_rsc")

        return (len(file_list), g.shape[0], g.shape[1])
    else:
        return (len(file_list), dem_rsc["file_length"], dem_rsc["width"])


def shift_unw_file(
    unw_stack_file,
    ref_row,
    ref_col,
    out_dset=STACK_FLAT_SHIFTED_DSET,
    window=3,
    ref_station=None,
    overwrite=False,
):
    """Runs a reference point shift on flattened stack of unw files stored in .h5"""
    logger.info("Starting shift_stack: using %s, %s as ref_row, ref_col", ref_row, ref_col)
    if not sario.check_dset(unw_stack_file, out_dset, overwrite):
        return

    in_files = sario.find_files(".", "*.unwflat")
    rows, cols = sario.load(in_files[0]).shape
    with h5py.File(unw_stack_file, "a") as f:
        # f STACK_FLAT_DSET not in f:
        #    raise ValueError("Need %s to be created in %s before"
        #                     " shift stack can be run" % (STACK_FLAT_DSET, unw_stack_file))

        # stack_in = f[STACK_FLAT_DSET]
        # f.create_dataset(
        #     STACK_FLAT_SHIFTED_DSET,
        #     shape=f[STACK_FLAT_DSET].shape,
        #     dtype=f[STACK_FLAT_DSET].dtype,
        # )
        # stack_out = f[STACK_FLAT_SHIFTED_DSET]
        # shift_stack(stack_in, stack_out, ref_row, ref_col, window=window)
        # f[STACK_FLAT_SHIFTED_DSET].attrs[REFERENCE_ATTR] = (ref_row, ref_col)
        # f[STACK_FLAT_SHIFTED_DSET].attrs[REFERENCE_STATION_ATTR] = (ref_station or "")
        f.create_dataset(
            out_dset,
            shape=(len(in_files), rows, cols),
            dtype="float32",
        )
        win = window // 2
        stack_out = f[out_dset]
        for idx, inf in enumerate(in_files):
            layer = sario.load(inf)
            patch = layer[ref_row - win:ref_row + win + 1, ref_col - win:ref_col + win + 1]
            stack_out[idx] = layer - np.mean(patch)

    dem_rsc = sario.load("dem.rsc")
    sario.save_dem_to_h5(unw_stack_file, dem_rsc, dset_name=DEM_RSC_DSET, overwrite=overwrite)
    logger.info("Shifting stack complete")


def shift_stack(stack_in, stack_out, ref_row, ref_col, window=3):
    """Subtracts reference pixel group from each layer

    Args:
        stack_in (ndarray-like): 3D array of images, stacked along axis=0
        stack_out (ndarray-like): empty 3D array, will hold output
            Both can be hdf5 datasets
        ref_row (int): row index of the reference pixel to subtract
        ref_col (int): col index of the reference pixel to subtract
        window (int): size of the group around ref pixel to avg for reference.
            if window=1 or None, only the single pixel used to shift the group.

    Raises:
        ValueError: if window is not a positive int, or if ref pixel out of bounds
    """
    win = window // 2
    for idx, layer in enumerate(stack_in):
        patch = layer[ref_row - win:ref_row + win + 1, ref_col - win:ref_col + win + 1]
        stack_out[idx] = layer - np.mean(patch)


def load_reference(unw_stack_file=UNW_FILENAME):
    with h5py.File(unw_stack_file, "r") as f:
        try:
            return f[STACK_FLAT_SHIFTED_DSET].attrs["reference"]
        except KeyError:
            return None, None


def matrix_indices(shape, flatten=True):
    """Returns a pair of vectors for all indices of a 2D array

    Convenience function to help remembed mgrid syntax

    Example:
        >>> a = np.arange(12).reshape((4, 3))
        >>> print(a)
        [[ 0  1  2]
         [ 3  4  5]
         [ 6  7  8]
         [ 9 10 11]]
        >>> rs, cs = matrix_indices(a.shape)
        >>> rs
        array([0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3])
        >>> cs
        array([0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2])
        >>> print(a[rs[1], cs[1]] == a[0, 1])
        True
    """
    nrows, ncols = shape
    row_block, col_block = np.mgrid[0:nrows, 0:ncols]
    if flatten:
        return row_block.flatten(), col_block.flatten()
    else:
        return row_block, col_block


def _read_mask_by_idx(idx, fname="masks.h5", dset=IGRAM_MASK_DSET):
    with h5py.File(fname, "r") as f:
        return f[dset][idx, :, :]


@log_runtime
def deramp_stack(
    unw_stack_file=UNW_FILENAME,
    order=1,
    overwrite=False,
):
    """Handles removing linear ramps for all files in a stack

    Saves the files to a new dataset in the same unw stack .h5 file

    Args:
        unw_stack_file (str): Filename for the .h5 stack of .unw
            These layers will be deramped and saved do a new dset
        order (int): order of polynomial surface to use to deramp
            1 is linear (default), 2 is quadratic
    """
    logger.info("Removing any ramp from each stack layer")
    # Get file names to save results/ check if we deramped already

    # Make sure the normal .unw stack file has been created
    with h5py.File(unw_stack_file, "r") as f:
        if STACK_DSET not in f:
            raise ValueError("unw stack dataset doesn't exist at %s" % unw_stack_file)

    if not sario.check_dset(unw_stack_file, STACK_FLAT_DSET, overwrite):
        return

    with h5py.File(MASK_FILENAME) as fmask:
        mask_dset = fmask[IGRAM_MASK_DSET]
        with h5py.File(unw_stack_file, "a") as f:
            logger.info("Creating dataset %s in %s" % (STACK_FLAT_DSET, unw_stack_file))

            f.create_dataset(
                STACK_FLAT_DSET,
                shape=f[STACK_DSET].shape,
                dtype=f[STACK_DSET].dtype,
            )
            # Shape of sario.load_stack with return_amp is (nlayers, 2, nrows, ncols)
            for idx, layer in enumerate(f[STACK_DSET]):
                with mask_dset.astype(bool):
                    mask = mask_dset[idx]
                try:
                    f[STACK_FLAT_DSET][idx] = remove_ramp(layer, order=order, mask=mask)
                except np.linalg.linalg.LinAlgError:
                    logger.info("Failed to estimate ramp on layer %s: setting to 0" % idx)
                    f[STACK_FLAT_DSET][idx] = np.zeros_like(layer)


def remove_ramp(z, order=1, mask=np.ma.nomask):
    """Estimates a linear plane through data and subtracts to flatten

    Used to remove noise artifacts from unwrapped interferograms

    Args:
        z (ndarray): 2D array, interpreted as heights
        order (int): degree of surface estimation
            order = 1 removes linear ramp, order = 2 fits quadratic surface

    Returns:
        ndarray: flattened 2D array with estimated surface removed
    """
    # Make a version of the image with nans in masked places
    z_masked = z.copy()
    z_masked[mask] = np.nan
    # Use this constrained version to find the plane fit
    z_fit = estimate_ramp(z_masked, order)
    # Then use the non-masked as return value
    return z - z_fit


def estimate_ramp(z, order):
    """Takes a 2D array an fits a linear plane to the data

    Ignores pixels that have nan values

    Args:
        z (ndarray): 2D array, interpreted as heights
        order (int): degree of surface estimation
            order = 1 removes linear ramp, order = 2 fits quadratic surface
        order (int)

    Returns:
        ndarray: the estimated coefficients of the surface
            For order = 1, it will be 3 numbers, a, b, c from
                 ax + by + c = z
            For order = 2, it will be 6:
                f + ax + by + cxy + dx^2 + ey^2
    """
    if order > 2:
        raise ValueError("Order only implemented for 1 and 2")
    # Note: rows == ys, cols are xs
    yidxs, xidxs = matrix_indices(z.shape, flatten=True)
    # c_ stacks 1D arrays as columns into a 2D array
    zflat = z.flatten()
    good_idxs = ~np.isnan(zflat)
    if order == 1:
        A = np.c_[np.ones(xidxs.shape), xidxs, yidxs]
        coeffs, _, _, _ = np.linalg.lstsq(A[good_idxs], zflat[good_idxs], rcond=None)
        # coeffs will be a, b, c in the equation z = ax + by + c
        c, a, b = coeffs
        # We want full blocks, as opposed to matrix_index flattened
        y_block, x_block = matrix_indices(z.shape, flatten=False)
        z_fit = (a * x_block + b * y_block + c)

    elif order == 2:
        A = np.c_[np.ones(xidxs.shape), xidxs, yidxs, xidxs * yidxs, xidxs**2, yidxs**2]
        # coeffs will be 6 elements for the quadratic
        coeffs, _, _, _ = np.linalg.lstsq(A[good_idxs], zflat[good_idxs], rcond=None)
        yy, xx = matrix_indices(z.shape, flatten=True)
        idx_matrix = np.c_[np.ones(xx.shape), xx, yy, xx * yy, xx**2, yy**2]
        z_fit = np.dot(idx_matrix, coeffs).reshape(z.shape)

    return z_fit


def find_reference_location(
    # unw_stack_file=UNW_FILENAME,
    mask_stack_file=MASK_FILENAME,
    cc_stack_file=CC_FILENAME,
    ref_station=None,
    rsc_data=None,
):
    """Find reference pixel on based on GPS availability and mean correlation
    """
    rsc_data = sario.load_dem_from_h5(h5file=unw_stack_file, dset="dem_rsc")

    # CHAGNE
    # Make a latlon image to check for gps data containment
    with h5py.File(unw_stack_file, "r") as f:
        latlon_image = latlon.LatlonImage(data=f[STACK_DSET][0], rsc_data=rsc_data)

    logger.info("Searching for gps station within area")
    # Don't make the invalid GPS here in case the random image chosed above is bad:
    # We'll use the mask ll image to decide which pixels are bad
    stations = apertools.gps.stations_within_image(latlon_image, mask_invalid=False)
    # Make a latlon image From the total masks
    with h5py.File(mask_stack_file, "r") as f:
        mask_ll_image = latlon.LatlonImage(data=f[GEO_MASK_SUM_DSET], rsc_data=rsc_data)

    with h5py.File(cc_stack_file, "r") as f:
        mean_cor = f[STACK_MEAN_DSET][:]
        mean_cor_ll_image = latlon.LatlonImage(data=mean_cor, rsc_data=rsc_data)

    if len(stations) > 0:
        logger.info("Station options:")
        logger.info(stations)
        num_masks = [mask_ll_image[lat, lon] for _, lon, lat in stations]
        pixel_correlations = [mean_cor_ll_image[lat, lon] for _, lon, lat in stations]

        logger.info("Sorting by fewer masked dates and highest correlation")
        # Note: make cor negative to sort large numbers to the front
        sorted_stations = sorted(
            zip(num_masks, pixel_correlations, stations),
            key=lambda tup: (tup[0], -tup[1]),
        )
        logger.info(sorted_stations)

        name, lon, lat = sorted_stations[0][-1]
        logger.info("Using station %s at (lon, lat) (%s, %s)", name, lon, lat)
        ref_row, ref_col = latlon_image.nearest_pixel(lon=lon, lat=lat)
        ref_station = name

    if ref_row is None:
        raise ValueError("GPS station search failed, need reference row/col")
        # logger.warning("GPS station search failed, reverting to coherence")
        # logger.info("Finding most coherent patch in stack.")
        # ref_row, ref_col = find_coherent_patch(mean_cor)
        # ref_station = None

    logger.info("Using %s as .unw reference point", (ref_row, ref_col))
    return ref_row, ref_col, ref_station


# # TODO: change this to the Rowena paper for auto find
# from scipy.ndimage.filters import uniform_filter
# def find_coherent_patch(correlations, window=11):
#     """Looks through 3d stack of correlation layers and finds strongest correlation patch
#
#     Also accepts a 2D array of the pre-compute means of the 3D stack.
#     Uses a window of size (window x window), finds the largest average patch
#
#     Args:
#         correlations (ndarray, possibly masked): 3D array of correlations:
#             correlations = sario.load_stack('path/to/correlations', '.cc')
#
#         window (int): size of the patch to consider
#
#     Returns:
#         tuple[int, int]: the row, column of center of the max patch
#
#     Example:
#         >>> corrs = np.arange(25).reshape((5, 5))
#         >>> print(find_coherent_patch(corrs, window=3))
#         (3, 3)
#         >>> corrs = np.stack((corrs, corrs), axis=0)
#         >>> print(find_coherent_patch(corrs, window=3))
#         (3, 3)
#     """
#     correlations = correlations.view(np.ma.MaskedArray)  # Force to be type np.ma
#     if correlations.ndim == 2:
#         mean_stack = correlations
#     elif correlations.ndim == 3:
#         mean_stack = np.ma.mean(correlations, axis=0)
#     else:
#         raise ValueError("correlations must be a 2D mean array, or 3D correlations")
#
#     # Run a 2d average over the image, then convert to masked array
#     conv = uniform_filter(mean_stack, size=window, mode='constant')
#     conv = np.ma.array(conv, mask=correlations.mask.any(axis=0))
#     # Now find row, column of the max value
#     max_idx = conv.argmax()
#     return np.unravel_index(max_idx, mean_stack.shape)

# TODO: do this with gdal calc
# @log_runtime
# def zero_masked_areas(igram_path=".", mask_filename=None, verbose=True):
#     logger.info("Zeroing out masked area in .cc and .int files")
#
#     if mask_filename is None:
#         mask_filename = os.path.join(igram_path, MASK_FILENAME)
#
#     int_date_list = sario.load_intlist_from_h5(mask_filename)
#
#     with h5py.File(mask_filename, "r") as f:
#         igram_mask_dset = f[IGRAM_MASK_DSET]
#         for idx, (early, late) in enumerate(int_date_list):
#             cur_mask = igram_mask_dset[idx]
#             base_str = "%s_%s" % (early.strftime(DATE_FMT), late.strftime(DATE_FMT))
#
#             if verbose:
#                 logger.info("Zeroing {0}.cc and {0}.int".format(base_str))
#
#             int_filename = base_str + ".int"
#             zero_file(int_filename, cur_mask, is_stacked=False)
#
#             cc_filename = base_str + ".cc"
#             zero_file(cc_filename, cur_mask, is_stacked=True)

# TODO: do this with gdal_calc.py
# def zero_file(filename, mask, is_stacked=False):
#     if is_stacked:
#         amp, img = sario.load(filename, return_amp=True)
#         img[mask] = 0
#         sario.save(filename, np.stack((amp, img), axis=0))
#     else:
#         img = sario.load(filename)
#         img[mask] = 0
#         sario.save(filename, img)

# TODO: decide if there's a fesible way to add a file to the repacked HDF5...
# @log_runtime
# def merge_files(filename1, filename2, new_filename, overwrite=False):
#     """Merge together 2 (currently mask) hdf5 files into a new file"""
#     def _merge_lists(list1, list2, merged_list, dset_name, dset1, dset2):
#         logger.info("%s: %s from %s and %s from %s into %s in file %s" % (
#             dset_name,
#             len(list1),
#             filename1,
#             len(list2),
#             filename2,
#             len(merged_list),
#             new_filename,
#         ))
#         for idx in range(len(merged_list)):
#             cur_item = merged_list[idx]
#             if cur_item in list1:
#                 jdx = list1.index(cur_item)
#                 fnew[dset_name][idx] = dset1[jdx]
#             else:
#                 jdx = list2.index(cur_item)
#                 fnew[dset_name][idx] = dset2[jdx]
#
#     if overwrite:
#         sario.check_dset(new_filename, IGRAM_MASK_DSET, overwrite)
#         sario.check_dset(new_filename, GEO_MASK_DSET, overwrite)
#
#     f1 = h5py.File(filename1)
#     f2 = h5py.File(filename2)
#     igram_dset1 = f1[IGRAM_MASK_DSET]
#     igram_dset2 = f2[IGRAM_MASK_DSET]
#     geo_dset1 = f1[GEO_MASK_DSET]
#     geo_dset2 = f2[GEO_MASK_DSET]
#
#     intlist1 = sario.load_intlist_from_h5(filename1)
#     intlist2 = sario.load_intlist_from_h5(filename2)
#     geolist1 = sario.load_geolist_from_h5(filename1)
#     geolist2 = sario.load_geolist_from_h5(filename2)
#     merged_intlist = sorted(set(intlist1) | set(intlist2))
#     merged_geolist = sorted(set(geolist1) | set(geolist2))
#
#     sario.save_intlist_to_h5(out_file=new_filename, overwrite=True, int_date_list=merged_intlist)
#     sario.save_geolist_to_h5(out_file=new_filename, overwrite=True, geo_date_list=merged_geolist)
#
#     new_geo_shape = (len(merged_geolist), geo_dset1.shape[1], geo_dset1.shape[2])
#     create_dset(new_filename, GEO_MASK_DSET, new_geo_shape, dtype=igram_dset1.dtype)
#     new_igram_shape = (len(merged_intlist), igram_dset1.shape[1], igram_dset1.shape[2])
#     create_dset(new_filename, IGRAM_MASK_DSET, new_igram_shape, dtype=igram_dset1.dtype)
#
#     fnew = h5py.File(new_filename, "a")
#     try:
#         _merge_lists(geolist1, geolist2, merged_geolist, GEO_MASK_DSET, geo_dset1, geo_dset2)
#        _merge_lists(intlist1, intlist2, merged_intlist, IGRAM_MASK_DSET, igram_dset1, igram_dset2)
#
#     finally:
#         f1.close()
#         f2.close()
#         fnew.close()
