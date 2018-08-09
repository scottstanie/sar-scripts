from __future__ import print_function
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.animation as animation
# Note: This is just a temp stopgap to not make skimage a hard requirement
# In the future, will port just the blob function, ski rest of skimage
try:
    import skimage.feature
except ImportError:
    print("Warning: scikit-image not installed. Blob function not available.")
    print("pip install scikit-image")
    pass
from insar.log import get_log
from insar import utils

logger = get_log()


def shifted_color_map(cmap, start=0, midpoint=0.5, stop=1.0, name='shiftedcmap'):
    """Function to offset the "center" of a colormap. Useful for
    data with a negative min and positive max and you want the
    middle of the colormap's dynamic range to be at zero

    Attribution: https://stackoverflow.com/a/20528097, Paul H

    Args:
      cmap (str or matplotlib.cmap): The matplotlib colormap to be altered.
          Can be matplitlib.cm.seismic or 'seismic'
      start (float): Offset from lowest point in the colormap's range.
          Defaults to 0.0 (no lower ofset). Should be between
          0.0 and `midpoint`.
      midpoint (float): The new center of the colormap. Defaults to
          0.5 (no shift). Should be between 0.0 and 1.0. In
          general, this should be  1 - vmax/(vmax + abs(vmin))
          For example if your data range from -15.0 to +5.0 and
          you want the center of the colormap at 0.0, `midpoint`
          should be set to  1 - 5/(5 + 15)) or 0.75
      stop (float): Offset from highest point in the colormap's range.
          Defaults to 1.0 (no upper ofset). Should be between
          `midpoint` and 1.0.

    Returns:
        matplotlib.cmap
    """
    if isinstance(cmap, str):
        cmap = matplotlib.cm.get_cmap(cmap)

    cdict = {'red': [], 'green': [], 'blue': [], 'alpha': []}

    # regular index to compute the colors
    reg_index = np.linspace(start, stop, 257)

    # shifted index to match the data
    shift_index = np.hstack([
        np.linspace(0.0, midpoint, 128, endpoint=False),
        np.linspace(midpoint, 1.0, 129, endpoint=True)
    ])

    for ri, si in zip(reg_index, shift_index):
        r, g, b, a = cmap(ri)

        cdict['red'].append((si, r, r))
        cdict['green'].append((si, g, g))
        cdict['blue'].append((si, b, b))
        cdict['alpha'].append((si, a, a))

    newcmap = matplotlib.colors.LinearSegmentedColormap(name, cdict)
    plt.register_cmap(cmap=newcmap)

    return newcmap


def make_shifted_cmap(img, cmap_name='seismic'):
    """Scales the colorbar so that 0 is always centered (white)"""
    midpoint = 1 - np.max(img) / (abs(np.min(img)) + np.max(img))
    return shifted_color_map(cmap_name, midpoint=midpoint)


def plot_image_shifted(img,
                       fig=None,
                       cmap='seismic',
                       img_data=None,
                       title='',
                       label='',
                       xlabel='',
                       ylabel=''):
    """Plot an image with a zero-shifted colorbar

    Args:
        img (ndarray): 2D numpy array to imshow
        fig (matplotlib.Figure): Figure to plot image onto
        ax (matplotlib.AxesSubplot): Axes to plot image onto
            mutually exclusive with fig option
        cmap (str): name of colormap to shift
        img_data (dict): rsc_data from sario.load_dem_rsc containing lat/lon
            data about image, used to make axes into lat/lon instead of row/col
        title (str): Title for image
        label (str): label for colorbar
    """
    if img_data:
        extent = utils.latlon_grid_extent(**img_data)
    else:
        nrows, ncols = img.shape
        extent = (0, ncols, nrows, 0)

    if not fig:
        fig = plt.figure()
    ax = fig.gca()
    shifted_cmap = make_shifted_cmap(img, cmap)
    axes_image = ax.imshow(img, cmap=shifted_cmap, extent=extent)  # Type: AxesImage
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)

    cbar = fig.colorbar(axes_image)
    cbar.set_label(label)
    return fig, axes_image


def animate_stack(stack,
                  pause_time=200,
                  display=True,
                  titles=None,
                  label=None,
                  save_title=None,
                  **savekwargs):
    """Runs a matplotlib loop to show each image in a 3D stack

    Args:
        stack (ndarray): 3D np.ndarray, 1st index is image number
            i.e. the idx image is stack[idx, :, :]
        pause_time (float): Optional- time between images in milliseconds (default=200)
        display (bool): True if you want the plot GUI to pop up and run
            False would be if you jsut want to save the movie with as save_title
        titles (list[str]): Optional- Names of images corresponding to stack.
            Length must match stack's 1st dimension length
        label (str): Optional- Label for the colorbar
        save_title (str): Optional- if provided, will save the animation to a file
            extension must be a valid extension for a animation writer:
        savekwargs: extra keyword args passed to animation.save
            See https://matplotlib.org/api/_as_gen/matplotlib.animation.Animation.html
            and https://matplotlib.org/api/animation_api.html#writer-classes

    Returns:
        None
    """
    num_images = stack.shape[0]
    if titles:
        assert len(titles) == num_images, "len(titles) must equal stack.shape[0]"
    else:
        titles = ['' for _ in range(num_images)]  # blank titles, same length

    # Use the same stack min and stack max for all colorbars/ color ranges
    minval, maxval = np.min(stack), np.max(stack)
    fig, ax = plt.subplots()
    axes_image = plt.imshow(stack[0, :, :], vmin=minval, vmax=maxval)  # Type: AxesImage

    cbar = fig.colorbar(axes_image)
    cbar_ticks = np.linspace(minval, maxval, num=6, endpoint=True)
    cbar.set_ticks(cbar_ticks)
    if label:
        cbar.set_label(label)

    def update_im(idx):
        axes_image.set_data(stack[idx, :, :])
        fig.suptitle(titles[idx])
        return axes_image,

    stack_ani = animation.FuncAnimation(
        fig, update_im, frames=range(num_images), interval=pause_time, blit=False, repeat=True)

    if save_title:
        logger.info("Saving to %s", save_title)
        stack_ani.save(save_title, **savekwargs)

    if display:
        plt.show()


def view_stack(stack,
               geolist=None,
               display_img=-1,
               label="Centimeters",
               cmap='seismic',
               title='',
               lat_lon=True,
               rsc_data=None):
    """Displays an image from a stack, allows you to click for timeseries

    Args:
        stack (ndarray): 3D np.ndarray, 1st index is image number
            i.e. the idx image is stack[idx, :, :]
        geolist (list[datetime]): Optional: times of acquisition for
            each stack layer. Used as xaxis if provided
        display_img (int, str): Optional- default = -1, the last image.
            Chooses which image in the stack you want as the display
            display_img = 'avg' will take the average across all images
        label (str): Optional- Label on colorbar/yaxis for plot
            Default = Centimeters
        cmap (str): Optional- colormap to display stack image (default='seismic')
        title (str): Optional- Title for plot
        lat_lon (bool): Optional- Use latitude and longitude in legend
            If False, displays row/col of pixel
        rsc_data (dict): Optional- if lat_lon=True, data to calc the lat/lon

    Returns:
        None

    Raises:
        ValueError: if display_img is not an int or the string 'mean'

    """
    # If we don't have dates, use indices as the x-axis
    if geolist is None:
        geolist = np.arange(stack.shape[0])

    if lat_lon and not rsc_data:
        raise ValueError("rsc_data is required for lat_lon=True")

    def get_timeseries(row, col):
        return stack[:, row, col]

    imagefig = plt.figure()

    if isinstance(display_img, int):
        img = stack[display_img, :, :]
    elif display_img == 'mean':
        img = np.mean(stack, axis=0)
    else:
        raise ValueError("display_img must be an int or 'mean'")

    title = title or "Deformation Time Series"  # Default title
    plot_image_shifted(img, fig=imagefig, title=title, cmap='seismic', label=label)

    timefig = plt.figure()

    plt.title(title)
    legend_entries = []

    def onclick(event):
        # Ignore right/middle click, clicks off image
        if event.button != 1 or not event.inaxes:
            return
        plt.figure(timefig.number)
        row, col = int(event.ydata), int(event.xdata)
        try:
            timeline = get_timeseries(row, col)
        except IndexError:  # Somehow clicked outside image, but in axis
            return

        if lat_lon:
            lat, lon = utils.rowcol_to_latlon(row, col, rsc_data)
            legend_entries.append('Lat {:.3f}, Lon {:.3f}'.format(lat, lon))
        else:
            legend_entries.append('Row %s, Col %s' % (row, col))

        plt.plot(geolist, timeline, marker='o', linestyle='dashed', linewidth=1, markersize=4)
        plt.legend(legend_entries, loc='upper left')
        x_axis_str = "SAR image date" if geolist is not None else "Image number"
        plt.xlabel(x_axis_str)
        plt.ylabel(label)
        plt.show()

    imagefig.canvas.mpl_connect('button_press_event', onclick)
    plt.show(block=True)


def equalize_and_mask(image, low=1e-6, high=2, fill_value=np.inf, db=True):
    """Clips an image to increase contrast"""
    # Mask the invalids, then mask zeros, then clip rest
    im = np.clip(utils.mask_zeros(np.ma.masked_invalid(image)), low, high)
    if fill_value:
        im.set_fill_value(fill_value)
    return utils.db(im) if db else im


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


def blobs_rowcol_to_latlon(blobs, blob_info):
    """Converts (y, x, sigma) format to (lat, lon, sigma_latlon)

    Uses the dem x_step/y_step data to rescale blobs so that appear on an
    image using lat/lon as the `extent` argument of imshow.
    """
    blob_info = {k.lower(): v for k, v in blob_info.items()}
    blobs_latlon = []
    for blob in blobs:
        row, col, r = blob
        lat, lon = utils.rowcol_to_latlon(row, col, blob_info)
        new_radius = r * blob_info['x_step']
        blobs_latlon.append((lat, lon, new_radius))

    return np.array(blobs_latlon)
