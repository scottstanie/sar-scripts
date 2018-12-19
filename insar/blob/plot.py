import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from . import utils


def plot_blobs(image=None, blobs=None, cur_fig=None, cur_axes=None, color='blue', **kwargs):
    """Takes the blob results from find_blobs and overlays on image

    Can either make new figure of plot on top of existing axes.
    """
    if not cur_axes:
        if not cur_fig:
            cur_fig = plt.figure()
        cur_axes = cur_fig.gca()
        cur_axes.imshow(image)

    for blob in blobs:
        c = plt.Circle((blob[1], blob[0]),
                       blob[2],
                       color=color,
                       fill=False,
                       linewidth=2,
                       clip_on=False)
        cur_axes.add_patch(c)

    plt.draw()
    plt.show()
    return blobs, cur_axes


def plot_hist(H, row_edges, col_edges, ax=None):
    if not ax:
        fig, ax = plt.subplots(1, 1)
    else:
        fig = ax.get_figure()

    axes_image = ax.imshow(H, extent=[col_edges[0], col_edges[-1], row_edges[-1], row_edges[0]])
    fig.colorbar(axes_image)
    return fig, ax


def scatter_blobs(blobs, image=None, axes=None, color='b', label=None):
    if axes is None:
        fig, axes = plt.subplots(1, 3)
    else:
        fig = axes[0].get_figure()

    if blobs.shape[1] < 6:
        blobs = utils.append_stats(blobs, image)

    print('Taking abs value of blobs')
    blobs = np.abs(blobs)

    # Size vs amplitude
    sizes = blobs[:, 2]
    mags = blobs[:, 3]
    vars_ = blobs[:, 4]
    ptps = blobs[:, 5]

    axes[0].scatter(sizes, mags, c=color, label=label)
    axes[0].set_xlabel("Size")
    axes[0].set_ylabel("Magnitude")
    if label:
        axes[0].legend()

    axes[1].scatter(sizes, vars_, c=color, label=label)
    axes[1].set_xlabel("Size")
    axes[1].set_ylabel("variance")

    axes[2].scatter(sizes, ptps, c=color, label=label)
    axes[2].set_xlabel("Size")
    axes[2].set_ylabel("peak-to-peak")
    return fig, axes


def scatter_blobs_3d(blobs, image=None, ax=None, color='b', label=None, blob_img=None):
    if ax is None:
        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1, projection='3d')
    else:
        fig = ax.get_figure()

    if blobs.shape[1] < 6:
        blobs = utils.append_stats(blobs, image)

    if blob_img is not None:
        sizes = blob_img.blob_size(blobs[:, 2])
    else:
        sizes = blobs[:, 2]
    mags = blobs[:, 3]
    vars_ = blobs[:, 4]
    ax.scatter(sizes, mags, vars_, c=color, label=label)
    ax.set_title("Size, mag, var of blobs")
    ax.set_xlabel('size')
    ax.set_ylabel('magniture')
    ax.set_zlabel('variance')
    return fig, ax
