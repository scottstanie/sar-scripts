import os
import json
import glob
import subprocess
from insar import parsers, tile, utils
from insar.log import get_log

logger = get_log()


def unzip_sentinel_files(path="."):
    logger.info("Changing to %s to unzip files", path)
    cur_dir = os.getcwd()  # To return to after
    os.chdir(path)

    logger.info("Unzipping sentinel annotation/xml and VV .tiffs")

    # Unzip all .zip files by piping to xargs, using 10 processes
    # IMPORTANT: Only unzipping the VV .tiff files and annotation/*.xml !

    # Note: -n means "never overwrite existing files", so you can rerun this
    subprocess.check_call(
        "find . -maxdepth 1 -name '*.zip' -print0 | "
        'xargs -0 -I {} --max-procs 10 unzip -n {} "*/annotation/*.xml" "*/measurement/*slc-vv-*.tiff" ',
        shell=True)

    logger.info("Done unzipping, returning to %s", cur_dir)
    os.chdir(cur_dir)


def find_sentinels(data_path, path_num=None):
    sents = [
        parsers.Sentinel(f) for f in glob.glob(os.path.join(data_path, "*"))
        if f.endswith(".zip") or f.endswith(".SAFE")
    ]
    if path_num:
        sents = [s for s in sents if s.path == path_num]
    return list(set(sents))


def make_tile_geojsons(data_path, path_num=None, tile_size=0.5, overlap=0.1):
    """Find tiles over a sentinel area, form the tiles/geojsons"""
    sentinel_list = find_sentinels(data_path, path_num)
    total_extent = tile.total_swath_extent(sentinel_list)
    tile_list, (height, width) = tile.make_tiles(total_extent, tile_size=tile_size, overlap=overlap)
    gj_list = [t.to_geojson(height, width) for t in tile_list]
    tilename_list = [t.tilename for t in tile_list]
    return list(zip(tilename_list, gj_list))


def create_tile_directories(data_path, path_num=None, tile_size=0.5, overlap=0.1):
    """Use make_tile_geojsons to create a directory structure"""

    def _write_geojson(tilename, geojson):
        with open('{}.geojson'.format(tilename), 'w') as f:
            json.dump(geojson, f)

    for tilename, gj in make_tile_geojsons(data_path, path_num, tile_size, overlap):
        utils.mkdir_p(tilename)
        _write_geojson(tilename, gj)
