#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Line-of-sight (LOS) fitting utilities.

"""

from __future__ import (print_function, division)
import six
from six.moves import range

import sys
import os
import warnings
import math
import numpy as np
import warnings
from scipy.stats import truncnorm

try:
    from scipy.special import logsumexp
except ImportError:
    from scipy.misc import logsumexp

__all__ = ["LOS_clouds_priortransform", "LOS_clouds_loglike_bin",
           "LOS_clouds_loglike_samples",
           "kernel_tophat", "kernel_gauss", "kernel_lorentz"]


def LOS_clouds_priortransform(u, rlims=(0., 6.), dlims=(4., 19.),
                              pb_params=(-3., 0.7, -np.inf, 0.),
                              s_params=(-3., 0.3, -np.inf, 0.)):
    """
    The "prior transform" for the LOS fit that converts from draws on the
    N-dimensional unit cube to samples from the prior. Used in nested sampling
    methods. Assumes uniform priors for distance and reddening
    and a (truncated) log-normal in outlier fraction.

    Parameters
    ----------
    u : `~numpy.ndarray` of shape `(Nparams)`
        The `2 + 2 * Nclouds` values drawn from the unit cube.
        Contains the portion of outliers `P_b`, followed by the smoothing `s`,
        followed by the foreground reddening `fred`, followed by a series of
        `(dist, red)` pairs for each "cloud" along the LOS.

    rlims : 2-tuple, optional
        The reddening bounds within which we'd like to sample. Default is
        `(0., 6.)`, which also assumes reddening is in units of Av.

    dlims : 2-tuple, optional
        The distance bounds within which we'd like to sample. Default is
        `(4., 19.)`, which also assumes distance is in units of distance
        modulus.

    pb_params : 4-tuple, optional
        Mean, standard deviation, lower bound, and upper bound for a
        truncated log-normal distribution used as a prior for the outlier
        model. The default is `(-3., 0.7, -np.inf, 0.)`, which corresponds
        to a mean of 0.05, a standard deviation of a factor of 2, a lower
        bound of 0, and an upper bound of 1.

    s_params : 4-tuple, optional
        Mean, standard deviation, lower bound, and upper bound for a
        truncated log-normal distribution used as a prior for the
        smoothing along the reddening axis (in %). The default is
        `(-3.5, 0.7, -np.inf, 0.)`, which corresponds to a mean of 0.05, a
        standard deviation of a factor of 1.35, a lower bound of 0, and an
        upper bound of 1.

    Returns
    -------
    x : `~numpy.ndarray` of shape `(Nparams)`
        The transformed parameters.

    """

    # Initialize values.
    x = np.array(u)

    # pb (outlier fraction)
    pb_mean, pb_std, pb_low, pb_high = pb_params
    a = (pb_low - pb_mean) / pb_std  # set normalized lower bound
    b = (pb_high - pb_mean) / pb_std  # set normalized upper bound
    x[0] = np.exp(truncnorm.ppf(u[0], a, b, loc=pb_mean, scale=pb_std))

    # s (fractional smoothing)
    s_mean, s_std, s_low, s_high = s_params
    a = (s_low - s_mean) / s_std  # set normalized lower bound
    b = (s_high - s_mean) / s_std  # set normalized upper bound
    x[1] = np.exp(truncnorm.ppf(u[1], a, b, loc=s_mean, scale=s_std))

    # reddening
    x[2::2] = np.sort(u[2::2]) * (rlims[1] - rlims[0]) + rlims[0]

    # distances
    x[3::2] = np.sort(u[3::2]) * (dlims[1] - dlims[0]) + dlims[0]

    return x


def LOS_clouds_loglike_bin(theta, cdfs, xedges, yedges, interpolate=True):
    """
    Compute the log-likelihood for the cumulative reddening along the
    line of sight (LOS) parameterized by `theta`, given input binned
    stellar posteriors/bounds generated by `.pdf.bin_pdfs_distred`. Assumes
    a uniform outlier model in distance and reddening across our binned
    posteriors.

    Parameters
    ----------
    theta : `~numpy.ndarray` of shape `(Nparams,)`
        A collection of parameters that characterizes the cumulative
        reddening along the LOS. Contains the fraction of outliers `P_b`
        followed by fractional reddening smoothing `s`,
        the foreground reddening `fred`, and a series of
        `(dist, red)` pairs for each "cloud" along the LOS.

    cdfs : `~numpy.ndarray` of shape `(Nobj, Nxbin, Nybin)`
        Binned versions of the CDFs.

    xedges : `~numpy.ndarray` of shape `(Nxbin+1,)`
        The edges defining the bins in distance.

    yedges : `~numpy.ndarray` of shape `(Nybin+1,)`
        The edges defining the bins in reddening.

    interpolate : bool, optional
        Whether to linearly interpolate between bins (defined by their central
        positions). Default is `True`.

    Returns
    -------
    loglike : float
        The computed log-likelihood.

    """

    # Grab parameters.
    pb, s = theta[0], theta[1]
    reds, dists = np.atleast_1d(theta[2::2]), np.atleast_1d(theta[3::2])

    # Convert to bin coordinates.
    dx, dy = xedges[1] - xedges[0], yedges[1] - yedges[0]
    Nxedges, Nyedges = len(xedges), len(yedges)
    Nxbin, Nybin = Nxedges - 1, Nyedges - 1
    x_ctrs = np.arange(0.5, Nxbin, 1.)
    y_ctrs = np.arange(0.5, Nybin, 1.)
    x = np.concatenate(([0], (dists - xedges[0]) / dx, [Nxbin]))
    y = (reds - yedges[0]) / dy

    # Find (x,y) neighbors in bin space.
    x1 = np.array(np.floor(x - 0.5), dtype='int')
    x2 = np.array(np.ceil(x - 0.5), dtype='int')
    y1 = np.array(np.floor(y - 0.5), dtype='int')
    y2 = np.array(np.ceil(y - 0.5), dtype='int')

    # Threshold values to bin edges.
    x1[np.where(x1 < 0)] = 0
    x1[np.where(x1 >= Nxbin)] = Nxbin - 1
    x2[np.where(x2 < 0)] = 0
    x2[np.where(x2 >= Nxbin)] = Nxbin - 1
    y1[np.where(y1 < 0)] = 0
    y1[np.where(y1 >= Nybin)] = Nybin - 1
    y2[np.where(y2 < 0)] = 0
    y2[np.where(y2 >= Nybin)] = Nybin - 1

    # Threshold (x,y) to edges (and shift to deal with centers).
    x[np.where(x < 0.5)] = 0.5
    x[np.where(x > Nxbin - 0.5)] = Nxbin - 0.5
    y[np.where(y < 0.5)] = 0.5
    y[np.where(y > Nybin - 0.5)] = Nybin - 0.5

    # Define "left" and "right" versions for xs.
    x1l, x1r = x1[:-1], x1[1:]
    x2l, x2r = x2[:-1], x2[1:]
    xl, xr = x[:-1], x[1:]

    # Compute integral along LOS using the provided CDFs.
    if interpolate:
        # Interpolate between bins (left side).
        # Define values q(x_i, y_i).
        q11, q12, q21, q22 = (cdfs[:, x1l, y1], cdfs[:, x1l, y2],
                              cdfs[:, x2l, y1], cdfs[:, x2l, y2])
        # Compute areas.
        dx1, dx2 = (xl - x_ctrs[x1l]), (x_ctrs[x2l] - xl)
        dy1, dy2 = (y - y_ctrs[y1]), (y_ctrs[y2] - y)
        # Interpolate in x.
        qp1, qp2 = (q11 * dx2 + q21 * dx1), (q12 * dx2 + q22 * dx1)
        xsel = np.where(~((dx1 > 0.) & (dx2 > 0.)))  # deal with edges
        qp1[:, xsel], qp2[:, xsel] = q11[:, xsel], q12[:, xsel]  # replace
        # Interpolate in y.
        cdf_left = qp1 * dy2 + qp2 * dy1
        ysel = np.where(~((dy1 > 0.) & (dy2 > 0.)))  # deal with edges
        cdf_left[ysel] = qp1[ysel]  # replace edges

        # Interpolate between the bins (right side).
        # Define values q(x_i, y_i).
        q11, q12, q21, q22 = (cdfs[:, x1r, y1], cdfs[:, x1r, y2],
                              cdfs[:, x2r, y1], cdfs[:, x2r, y2])
        # Compute areas.
        dx1, dx2 = (xr - x_ctrs[x1r]), (x_ctrs[x2r] - xr)
        dy1, dy2 = (y - y_ctrs[y1]), (y_ctrs[y2] - y)
        # Interpolate in x.
        qp1, qp2 = (q11 * dx2 + q21 * dx1), (q12 * dx2 + q22 * dx1)
        xsel = np.where(~((dx1 > 0.) & (dx2 > 0.)))  # deal with edges
        qp1[:, xsel], qp2[:, xsel] = q11[:, xsel], q12[:, xsel]  # replace
        # Interpolate in y.
        cdf_right = qp1 * dy2 + qp2 * dy1
        ysel = np.where(~((dy1 > 0.) & (dy2 > 0.)))  # deal with edges
        cdf_right[ysel] = qp1[ysel]  # replace edges
    else:
        # Just use the values from the bin we're currently in.
        cdf_left, cdf_right = cdfs[:, x1l, y1], cdfs[:, x1r, y1]

    # Compute likelihood.
    likes = np.sum(cdf_right - cdf_left, axis=1)

    # Add in outlier mixture model. Assume uniform in (x, y) with `pb` weight.
    likes = pb * (1. / (Nybin * Nxbin)) + (1. - pb) * likes

    # Compute total log-likelihood.
    loglike = np.sum(np.log(likes))

    return loglike


def LOS_clouds_loglike_samples(theta, dsamps, rsamps, kernel='gauss',
                               rlims=(0., 6.), Ndraws=25):
    """
    Compute the log-likelihood for the cumulative reddening along the
    line of sight (LOS) parameterized by `theta`, given a set of input
    reddening and distance draws. Assumes a uniform outlier model in distance
    and reddening across our binned posteriors.

    Parameters
    ----------
    theta : `~numpy.ndarray` of shape `(Nparams,)`
        A collection of parameters that characterizes the cumulative
        reddening along the LOS. Contains the fraction of outliers `P_b`
        followed by the foreground reddening `fred` followed by a series of
        `(dist, red)` pairs for each "cloud" along the LOS.

    dsamps : `~numpy.ndarray` of shape `(Nobj, Nsamps)`
        Distance samples for each object. Follows the units used in `theta`.

    rsamps : `~numpy.ndarray` of shape `(Nobj, Nsamps)`
        Reddening samples for each object. Follows the units in `theta`.

    kernel : str or function, optional
        The kernel used to weight the samples along the LOS. If a string is
        passed, a pre-specified kernel will be used. Options include
        `'lorentz'`, `'gauss'`, and `'tophat'`. Default is `'gauss'`.

    rlims : 2-tuple, optional
        The reddening bounds within which we'd like to sample. Default is
        `(0., 6.)`, which also assumes reddening is in units of Av.

    Ndraws : int, optional
        The number of draws to use for each star. Default is `25`.

    Returns
    -------
    loglike : float
        The computed log-likelihood.

    """

    # Check kernel
    KERNELS = {'tophat': kernel_tophat, 'gauss': kernel_gauss,
               'lorentz': kernel_lorentz}
    if kernel in KERNELS:
        kern = KERNELS[kernel]
    elif callable(kernel):
        kern = kernel
    else:
        raise ValueError("The kernel provided is not a valid function nor "
                         "one of the pre-defined options. Please provide a "
                         "valid kernel.")

    # Grab parameters.
    pb, s = theta[0], theta[1]
    reds, dists = np.atleast_1d(theta[2::2]), np.atleast_1d(theta[3::2])
    area = (rlims[1] - rlims[0])
    rsmooth = s * area

    # Define cloud edges ("distance bounds").
    xedges = np.concatenate(([0], dists, [1e10]))

    # Define kernel parameters (mean, sigma) per LOS chunk.
    kparams = np.array([(r, rsmooth) for r in reds])

    # Sub-sample distance and reddening samples.
    ds, rs = dsamps[:, :Ndraws], rsamps[:, :Ndraws]
    Nobj, Nsamps = ds.shape

    # Compute log-weights for samples along the LOS by evaluating reddening
    # samples within each segment against the associated centered kernel.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        logw = np.array([kern(rs, kp) + np.log((ds >= xl) & (ds < xh))
                         for xl, xh, kp in zip(xedges[:-1], xedges[1:],
                                               kparams)])

    # Compute log-likelihoods across all samples and clouds.
    logls = logsumexp(logw, axis=(0, 2)) - np.log(Nsamps)

    # Add in outlier mixture model.
    logls = logsumexp(a=np.c_[logls, np.full_like(logls, -np.log(area))],
                      b=[(1. - pb), pb], axis=1)

    # Compute total log-likeihood.
    loglike = np.sum(logls)

    return loglike


def kernel_tophat(reds, kp):
    """
    Compute a weighted sum of the provided reddening draws using a Top-Hat
    kernel.

    Parameters
    ----------
    reds : `~numpy.ndarray` of shape `(Nsamps)`
        Distance samples for each object.

    kp : 2-tuple
        The kernel parameters `(mean, half-bin-width)`.

    Returns
    -------
    logw : `~numpy.ndarray` of shape `(Nsamps)`
        Log(weights).

    """

    # Extract kernel parameters.
    kmean, kwidth = kp[0], kp[1]
    klow, khigh = kmean - kwidth, kmean + kwidth  # tophat low/high edges
    norm = 2. * kwidth

    # Compute weights.
    inbounds = (reds >= klow) & (reds < khigh)

    # Compute log-sum.
    logw = np.log(inbounds) - np.log(norm)

    return logw


def kernel_gauss(reds, kp):
    """
    Compute a weighted sum of the provided reddening draws using a Gaussian
    kernel.

    Parameters
    ----------
    reds : `~numpy.ndarray` of shape `(Nsamps)`
        Distance samples for each object.

    kp : 2-tuple
        The kernel parameters `(mean, standard deviation)`.

    Returns
    -------
    logw : `~numpy.ndarray` of shape `(Nsamps)`
        Log(weights).

    """

    # Extract kernel parameters.
    kmean, kstd = kp[0], kp[1]
    norm = np.sqrt(2 * np.pi) * kstd

    # Compute log-weights.
    logw = -0.5 * ((reds - kmean) / kstd)**2 - np.log(norm)

    return logw


def kernel_lorentz(reds, kp):
    """
    Compute a weighted sum of the provided reddening draws using a Lorentzian
    kernel.

    Parameters
    ----------
    reds : `~numpy.ndarray` of shape `(Nsamps)`
        Distance samples for each object.

    kp : 2-tuple
        The kernel parameters `(mean, HWHM)`.

    Returns
    -------
    logw : `~numpy.ndarray` of shape `(Nsamps)`
        Log(weights).

    """

    # Extract kernel parameters.
    kmean, khwhm = kp[0], kp[1]
    norm = np.pi * khwhm

    # Compute log-weights.
    logw = -np.log(1. + ((reds - kmean) / khwhm)**2) - np.log(norm)

    return logw
