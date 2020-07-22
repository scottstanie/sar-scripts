# coding: utf-8
from datetime import date
import numpy as np
import apertools.sario as sario
from insar.prepare import remove_ramp


def stack_igrams(event_date=date(2020, 3, 26), rate=False, outname=None, verbose=True):

    geolist, intlist = sario.load_geolist_intlist('.')
    insert_idx = np.searchsorted(geolist, event_date)
    num_igrams = len(geolist) - insert_idx

    geo_subset = geolist[-(2 * num_igrams):]
    stack_igrams = list(zip(geo_subset[:num_igrams], geo_subset[num_igrams:]))
    stack_fnames = sario.intlist_to_filenames(stack_igrams, '.unw')
    if verbose:
        print("Using the following igrams in stack:")
        for f in stack_fnames:
            print(f)

    dts = [(pair[1] - pair[0]).days for pair in stack_igrams]
    stack = np.zeros(sario.load(stack_fnames[0]).shape).astype(float)
    for f, dt in zip(stack_fnames, dts):
        deramped_phase = remove_ramp(sario.load(f), deramp_order=1, mask=np.ma.nomask)
        stack += deramped_phase
        if rate:
            stack /= dt

    if outname:
        import h5py
        with h5py.File(outname, 'w') as f:
            f['stackavg'] = stack
        sario.save_dem_to_h5(outname, sario.load("dem.rsc"))
    return stack
