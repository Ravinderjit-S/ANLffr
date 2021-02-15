# -*- coding: utf-8 -*-
"""
Module: anlffr.spectral

A collection of spectral analysis functions for FFR or other kinds of M/EEG
data.

Includes functions to estimate frequency content / phase locking of
single-channel or individual data channels, as well as functions that produce
estimates by combining across channels (via the cPCA method described in [1]).
The accompanying bootsrap module provides support for bootstrapping any of the
analysis functions in this module.

Function Listing
================
Per-channel functions:
--------------------------------------

    mtplv

    mtspec

    mtphase

    mtppc

    mtspecraw

    mtpspec


Multichannel functions utilizing cPCA:
--------------------------------------

    mtcplv (alias for mtcpca)

    mtcspec

    mtcpca_timeDomain

    mtcpca_all

NOTE: Due to the poor SNR of individual trials typical in FFR datasets,
cPCA-based methods implemented in this module first compute the parameter
of interest on a per-channel basis, then computes the cross-spectral
densities over channels. We point out that this is different from what a
strict interpretation of the notation in the equations in [1] suggests.
Computation of the cross-spectral density on a per-trial basis will
emphasize features that are phase-locked across channels (e.g., noise). For
FFRs, the contributions of activity phase locked over channels but not over
trials will swamp peaks in the resulting output metric, particularly at low
frequencies.


References:
=======================================

[1] Bharadwaj, H and Shinn-Cunningham, BG (2014).
      "Rapid acquisition of auditory subcortical steady state responses using
      multichannel recordings".
      J Clin Neurophys 125 1878--1898.
      http://dx.doi.org/10.1016/j.clinph.2014.01.011

last updated: 2017-05-16 LV

@author: Hari Bharadwaj

"""

import numpy as np
from math import ceil
import scipy as sci
from scipy import linalg
from .utils import logger
from .utils import verbose
from multiprocessing import cpu_count
from .dpss import dpss_windows


@verbose
def mtplv(x, params, verbose=None):
    """Multitaper Phase-Locking Value

    Parameters
    ----------
    x - NumPy Array
        Input Data (channel x trial x time) or (trials x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

      params['itc'] - 1 for ITC, 0 for PLV

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
        (plvtap, f): Tuple
           plvtap - Multitapered phase-locking estimate (channel x frequency)

    In bootstrap mode:
        Dictionary with the following keys:
         mtplv - Multitapered phase-locking estimate (channel x frequency)

         f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper PLV Estimation')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        nchans = x.shape[0]
        ntrials = x.shape[trialdim]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    elif(len(x.shape) == 2):
        timedim = 1
        trialdim = 0
        ntrials = x.shape[trialdim]
        nchans = 1
        logger.info('The data is of format %d trials x time (single channel)',
                    ntrials)
    else:
        logger.error('Sorry, The data should be a 2 or 3 dimensional array')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    # Make space for the PLV result

    plvtap = np.zeros((ntaps, nchans, len(fInd)))

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)

        if(params['itc'] == 0):
            plvtap[k, :, :] = abs((xw/abs(xw)).mean(axis=trialdim))**2
        else:
            plvtap[k, :, :] = ((abs(xw.mean(axis=trialdim))**2) /
                               ((abs(xw) ** 2).mean(axis=trialdim)))

    plvtap = plvtap.mean(axis=0)

    plvtap = plvtap[:, fInd].squeeze()

    if bootstrapMode:
        out = {}
        out['mtplv'] = plvtap
        out['f'] = f
    else:
        return (plvtap, f)

    return out


@verbose
def mtspec(x, params, verbose=None):
    """Multitaper Spectrum and SNR estimate

    Noise floor estimate obtained by substituting random phases in fft

    Parameters
    ----------
    x - NumPy Array
        Input data (channel x trial x time) or (trials x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:

        (S, N ,f): Tuple
          S - Multitapered spectrum (channel x frequency)

          N - Noise floor estimate

          f - Frequency vector matching S and N

    In bootstrap mode:
        Dictionary with the following keys:
         mtspec - Multitapered spectrum (channel x frequency)

         mtspec_noise - Noise floor estimate

         f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Spectrum and Noise-floor Estimation')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    elif(len(x.shape) == 2):
        timedim = 1
        trialdim = 0
        ntrials = x.shape[trialdim]
        nchans = 1
        logger.info('The data is of format %d trials x time (single channel)',
                    ntrials)
    else:
        logger.error('Sorry! The data should be a 2 or 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    S = np.zeros((ntaps, nchans, len(fInd)))
    N = np.zeros((ntaps, nchans, len(fInd)))
    
    logger.warning('''using random phases for noise floor estimate...
                   this is probably fine for a single shot estimate,
                   but the noise floor returned when using
                   this function in bootstrap resampling may be 
                   inaccurate''')

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)

        S[k, :, :] = abs(xw.mean(axis=trialdim))

        randph = np.random.random_sample(xw.shape) * 2 * sci.pi
        N[k, :, :] = abs((xw*sci.exp(1j*randph)).mean(axis=trialdim))


    # Average over tapers and squeeze to pretty shapes
    S = S.mean(axis=0)
    N = N.mean(axis=0)
    S = S[:, fInd].squeeze()
    N = N[:, fInd].squeeze()

    if bootstrapMode:
        out = {}
        out['mtspec'] = S
        out['mtspec_noise'] = N
        out['f'] = f

        return out
    else:
        return (S, N, f)


@verbose
def mtphase(x, params, verbose=None):
    """Multitaper phase estimation

    Parameters
    ----------
    x - NumPy Array
        Input data (channel x trial x time) or (trials x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns:
    -------
    In normal mode:
        (Ph, f): Tuple
          Ph - Multitapered phase spectrum (channel x frequency)

          f - Frequency vector matching S and N

    In bootstrap mode:
        Dictionary with the following keys:

          Ph - Multitapered phase spectrum (channel x frequency)

          f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Spectrum and Noise-floor Estimation')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    elif(len(x.shape) == 2):
        timedim = 1
        trialdim = 0
        ntrials = x.shape[trialdim]
        nchans = 1
        logger.info('The data is of format %d trials x time (single channel)',
                    ntrials)
    else:
        logger.error('Sorry! The data should be a 2 or 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    Ph = np.zeros((ntaps, nchans, len(fInd)))

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)
        Ph[k, :, :] = np.angle(xw.mean(axis=trialdim))

    # Average over tapers and squeeze to pretty shapes
    Ph = Ph[:, :, fInd].mean(axis=0).squeeze()

    if bootstrapMode:
        out = {}
        out['mtphase'] = Ph
        out['f'] = f

        return out
    else:
        return (Ph, f)


@verbose
def mtcpca(x, params, verbose=None):
    """Multitaper complex PCA and PLV

    Parameters
    ----------
    x - NumPy Array
        Input data (channel x trial x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

      params['itc'] - 1 for ITC, 0 for PLV

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
        Tuple (plv, f):
          plv - Multitapered PLV estimate using cPCA

          f - Frequency vector matching plv

    In bootstrap mode:
        Dictionary with the following keys:
          mtcplv - Multitapered PLV estimate using cPCA

          f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Complex PCA based PLV Estimation')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    else:
        logger.error('Sorry! The data should be a 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    plv = np.zeros((ntaps, len(fInd)))

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)

        if params['itc']:
            C = (xw.mean(axis=trialdim) /
                 (abs(xw).mean(axis=trialdim))).squeeze()
        else:
            C = (xw / abs(xw)).mean(axis=trialdim).squeeze()

        for fi in np.arange(0, C.shape[1]):
            Csd = np.outer(C[:, fi], C[:, fi].conj())
            vals = linalg.eigh(Csd, eigvals_only=True)
            plv[k, fi] = vals[-1] / nchans

    # Average over tapers and squeeze to pretty shapes
    plv = (plv.mean(axis=0)).squeeze()
    plv = plv[fInd]
    if bootstrapMode:
        out = {}
        out['mtcplv'] = plv
        out['f'] = f

        return out
    else:
        return (plv, f)


mtcplv = mtcpca


@verbose
def mtcspec(x, params, verbose=None):
    """Multitaper complex PCA and power spectral estimate

    Parameters
    ----------
    x - NumPy Array
        Input data (channel x trial x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

      params['itc'] - 1 for ITC, 0 for PLV

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
        Tuple (cspec, f):

          cspec - Multitapered PLV estimate using cPCA

          f - Frequency vector matching plv

    In bootstrap mode:
        Dictionary with the following keys:

          cspec - Multitapered PLV estimate using cPCA

          f - Frequency vector matching plv
    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Complex PCA based power estimation!')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    else:
        logger.error('Sorry! The data should be a 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    # Make space for the PLV result

    cspec = np.zeros((ntaps, len(fInd)))

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)
        C = (xw.mean(axis=trialdim)).squeeze()
        for fi in np.arange(0, C.shape[1]):
            Csd = np.outer(C[:, fi], C[:, fi].conj())
            vals = linalg.eigh(Csd, eigvals_only=True)
            cspec[k, fi] = vals[-1] / nchans

    # Average over tapers and squeeze to pretty shapes
    cspec = (cspec.mean(axis=0)).squeeze()
    cspec = cspec[fInd]

    if bootstrapMode:
        out = {}
        out['mtcspec'] = cspec
        out['f'] = f

        return out
    else:
        return (cspec, f)


@verbose
def mtcpca_timeDomain(x, params, verbose=None):
    """Multitaper complex PCA and regular time-domain PCA and return time
    domain waveforms.

    Note of caution
    ---------------
    The cPCA method is not really suited to extract fast transient features of
    the time domain waveform. This is because, the frequency domain
    representation of any signal (when you think of it as random process) is
    interpretable only when the signal is stationary, i.e., in steady-state.
    Practically speaking, the process of transforming short epochs to the
    frequency domain necessarily involves smoothing in frequency. This
    leakage is minimized by tapering the original signal using DPSS windows,
    also known as Slepian sequences. The effect of this tapering would be
    present when going back to the time domain. Note that only a single taper
    is used here as combining tapers with different symmetries in the time-
    domain leads to funny cancellations.

    Also, for transient features, simple time-domain PCA is likely
    to perform better as the cPCA smoothes out transient features. Thus
    both regular time-domain PCA and cPCA outputs are returned.

    Note that for sign of the output is indeterminate (you may need to flip
    the output to match the polarity of signal channel responses)

    Parameters
    ----------
    x - NumPy Array
        Input data (channel x trial x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
        Tuple (y_cpc, y_pc):
          'y_cpc' - Multitapered cPCA estimate of time-domain waveform

          'y_pc' - Regular time-domain PCA

    In bootstrap mode:
        Dictionary with the following keys:
          'y_cpc' - Multitapered cPCA estimate of time-domain waveform

          'y_pc' - Regular time-domain PCA

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Complex PCA to extract time waveform!')
    logger.info('ignoring params["tapers"]: see docstring for details')

    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    else:
        logger.error('Sorry! The data should be a 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    w, conc = dpss_windows(x.shape[timedim], 1, 1)
    w = w.squeeze() / w.max()

    cpc_freq = np.zeros(len(fInd), dtype=np.complex)
    cspec = np.zeros(len(fInd))
    xw = np.fft.rfft(w * x, n=nfft, axis=timedim)
    C = (xw.mean(axis=trialdim)).squeeze()
    Cnorm = C / ((abs(xw).mean(axis=trialdim)).squeeze())
    for fi in np.arange(0, Cnorm.shape[1]):
        Csd = np.outer(Cnorm[:, fi], Cnorm[:, fi].conj())
        vals, vecs = linalg.eigh(Csd, eigvals_only=False)
        cspec[fi] = vals[-1]
        cwts = vecs[:, -1] / (np.abs(vecs[:, -1]).sum())
        cpc_freq[fi] = (cwts.conjugate() * C[:, fi]).sum()

    # Filter through spectrum, do ifft.
    cscale = cspec ** 0.5
    cscale = cscale / cscale.max()  # Maxgain of filter = 1
    y_cpc = np.fft.irfft(cpc_freq * cscale)[:x.shape[timedim]]

    # Do time domain PCA
    x_ave = x.mean(axis=trialdim)
    C_td = np.cov(x_ave)
    vals, vecs = linalg.eigh(C_td, eigvals_only=False)
    y_pc = np.dot(vecs[:, -1].T, x_ave) / (vecs[:, -1].sum())

    if bootstrapMode:
        out = {}
        out['y_cpc'] = y_cpc
        out['y_pc'] = y_pc

        return out
    else:
        return (y_cpc, y_pc)


@verbose
def mtppc(x, params, verbose=None, bootstrapMode=False):
    """Multitaper Pairwise Phase Consistency

    Parameters
    ----------
    x - Numpy array
        Input data (channel x trial x time) or (trials x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

      params['Npairs'] - Number of pairs for PPC analysis

      params['itc'] - If True, normalize after mean like ITC instead of PLV

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
        Tuple (ppc, f):
          ppc - Multitapered PPC estimate (channel x frequency)

          f - Frequency vector matching plv

    In bootstrap mode:
        Dictionary with the following keys:
          mtppc - Multitapered PPC estimate (channel x frequency)

          f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Pairwise Phase Consistency Estimate')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    elif(len(x.shape) == 2):
        timedim = 1
        trialdim = 0
        ntrials = x.shape[trialdim]
        nchans = 1
        logger.info('The data is of format %d trials x time (single channel)',
                    timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)
    nfft, f, fInd = _get_freq_vector(x, params, timedim)

    # Make space for the result

    ppc = np.zeros((ntaps, nchans, len(fInd)))

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)

        npairs = params['Npairs']
        trial_pairs = np.random.randint(0, ntrials, (npairs, 2))

        if(nchans == 1):
            if(not params['itc']):
                xw_1 = xw[trial_pairs[:, 0], :]/abs(xw[trial_pairs[:, 0], :])
                xw_2 = xw[trial_pairs[:, 1], :]/abs(xw[trial_pairs[:, 1], :])
                ppc[k, :, :] = np.real((xw_1*xw_2.conj()).mean(axis=trialdim))
            else:
                xw_1 = xw[trial_pairs[:, 0]]
                xw_2 = xw[trial_pairs[:, 1]]
                ppc_unnorm = np.real((xw_1 * xw_2.conj()).mean(axis=trialdim))
                ppc[k, :, :] = (ppc_unnorm /
                                (abs(xw_1).mean(trialdim) *
                                 abs(xw_2).mean(trialdim)))

        else:
            if(not params['itc']):
                xw_1 = (xw[:, trial_pairs[:, 0], :] /
                        abs(xw[:, trial_pairs[:, 0], :]))
                xw_2 = (xw[:, trial_pairs[:, 1], :] /
                        abs(xw[:, trial_pairs[:, 1], :]))
                ppc[k, :, :] = np.real((xw_1*xw_2.conj()).
                                       mean(axis=trialdim))
            else:
                xw_1 = xw[:, trial_pairs[:, 0], :]
                xw_2 = xw[:, trial_pairs[:, 1], :]
                ppc_unnorm = np.real((xw_1 * xw_2.conj()).mean(axis=trialdim))
                ppc[k, :, :] = (ppc_unnorm /
                                (abs(xw_1).mean(trialdim) *
                                 abs(xw_2).mean(trialdim)))

    ppc = ppc.mean(axis=0)
    ppc = ppc[:, fInd].squeeze()

    if bootstrapMode:
        out = {}
        out['mtppc'] = ppc
        out['f'] = f

        return out
    else:
        return (ppc, f)


@verbose
def mtspecraw(x, params, verbose=None, bootstrapMode=False):
    """Multitaper Spectrum (of raw signal)

    Parameters
    ----------
    x - Numpy array
        Input data numpy array (channel x trial x time) or (trials x time)

    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    Normal mode:
        Tuple (mtspecraw, f)
          mtspecraw - multitapered spectrum

          f - Frequency vector matching plv

    In bootstrap mode:
        Dictionary with the following keys:
          mtspecraw - Multitapered spectrum (channel x frequency)

          f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Raw Spectrum Estimation')
    x = x.squeeze()  # comment out for single-trial computation - R.S.
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        nchans = x.shape[0]
        ntrials = x.shape[trialdim]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    elif(len(x.shape) == 2):
        timedim = 1
        trialdim = 0
        nchans = 1
        ntrials = x.shape[trialdim]
        logger.info('The data is of format %d trials x time (single channel)',
                    ntrials)
    else:
        logger.error('Sorry! The data should be a 2 or 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    # Make space for the results

    Sraw = np.zeros((ntaps, nchans, len(fInd)))

    for k, tap in enumerate(w):
        logger.info('Doing Taper #%d', k)
        xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)
        Sraw[k, :, :] = (abs(xw)**2).mean(axis=trialdim)

    # Average over tapers and squeeze to pretty shapes
    Sraw = Sraw.mean(axis=0)
    Sraw = Sraw[:, fInd].squeeze()

    if bootstrapMode:
        out = {}
        out['mtspecraw'] = Sraw
        out['f'] = f

        return out
    else:
        return (Sraw, f)


@verbose
def mtpspec(x, params, verbose=None, bootstrapMode=False):
    """Multitaper Pairwise Power Spectral estimate

    Parameters
    ----------
    x - Numpy Array
        Input data numpy array (channel x trial x time) or (trials x time)
    params - Dictionary of parameter settings
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

      params['Npairs'] - Number of pairs for pairwise analysis
    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
      Tuple (pspec, f):
          pspec -  Multitapered Pairwise Power estimate (channel x frequency)

          f - Frequency vector matching plv

    In bootstrap mode:

      Dictionary with following keys:
          pspec -  Multitapered Pairwise Power estimate (channel x frequency)

          f - Frequency vector matching plv

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    logger.info('Running Multitaper Pairwise Power Estimate')
    x = x.squeeze()
    if(len(x.shape) == 3):
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    elif(len(x.shape) == 2):
        timedim = 1
        trialdim = 0
        ntrials = x.shape[trialdim]
        nchans = 1
        logger.info('The data is of format %d trials x time (single channel)',
                    ntrials)
    else:
        logger.error('Sorry! The data should be a 2 or 3 dimensional array!')

    # Calculate the tapers
    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]
    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)

    # Make space for the PLV result

    pspec = np.zeros((ntaps, nchans, len(fInd)))

    for ch in np.arange(0, nchans):
        for k, tap in enumerate(w):
            logger.debug('Running Channel # %d, taper #%d', ch, k)
            xw = np.fft.rfft(tap * x, n=nfft, axis=timedim)
            npairs = params['Npairs']
            trial_pairs = np.random.randint(0, ntrials, (npairs, 2))

            # For unbiasedness, pairs should be made of independent trials!
            trial_pairs = trial_pairs[np.not_equal(trial_pairs[:, 0],
                                                   trial_pairs[:, 1])]
            if(nchans == 1):
                xw_1 = xw[trial_pairs[:, 0]]
                xw_2 = xw[trial_pairs[:, 1]]
                pspec[k, ch, :] = np.real((xw_1*xw_2.conj()).mean(axis=0))
            else:
                xw_1 = xw[ch, trial_pairs[:, 0], :]
                xw_2 = xw[ch, trial_pairs[:, 1], :]
                pspec[k, ch, :] = np.real((xw_1*xw_2.conj()).mean(axis=0))

    pspec = pspec.mean(axis=0)
    pspec = pspec[:, fInd].squeeze()

    if bootstrapMode:
        out = {}
        out['pspec'] = pspec
        out['f'] = f

        return out
    else:
        return (pspec, f)


@verbose
def mtcpca_all(x, params, verbose=None, bootstrapMode=False):
    """
    Convenience function to obtain plv, itc, and spectrum with cpca and
    multitaper.  Equivalent to calling:

    spectral.mtcpca(data, params, ...) with ITC = 0
    spectral.mtcpca(data, params, ...) with ITC = 1
    spectral.mtcspec(data, params, ...)

    Gets power spectra and plv on the same set of data using multitaper and
    complex PCA. 

    Parameters
    ----------
    x - NumPy Array
        Input data (channel x trial x time)

    params - dictionary. Must contain the following fields:
      params['Fs'] - sampling rate

      params['tapers'] - [TW, Number of tapers]

      params['fpass'] - Freqency range of interest, e.g. [5, 1000]

      params['returnEigenvectors']: returns the eigenvectors associated with
      the complex PCA operation
      
      params['pcaComponentNumber']: if for some reason you want to look at the
      multi-taper complex PCA for something other than the largest eigenvalue,
      set this array. Defaults to [1] (to look at the plv/itc/spectrum for main
      eigenvalue/eigenvector pair only). 

    verbose : bool, str, int, or None
        The verbosity of messages to print. If a str, it can be either DEBUG,
        INFO, WARNING, ERROR, or CRITICAL.

    Returns
    -------
    In normal mode:
        Tuple (X, f)

        Where X is a dictionary with the following keys:

          spectrum - Multitapered power spectral estimate using cPCA

          plv - Multitapered PLV using cPCA
          
          itc - Multitapered ITC using cPCA

          spectrumV - (optional) eigenvector associated with spectrum

          plvV - (optional) eigenvector associated with spectrum
          
          itcV - (optional) eigenvector associated with spectrum

        f - frequency vector

    In bootstrap mode:
        dictionary with keys:
          spectrum - Multitapered power spectral estimate using cPCA

          plv- Multitapered PLV using cPCA
          
          itc - Multitapered power spectral estimate using cPCA

          f - frequency vector

    """
    try:
        bootstrapMode = params['bootstrapMode']
    except KeyError:
        bootstrapMode = False

    out = {}

    logger.info('Running Multitaper Complex PCA based ' +
                'plv and power estimation.')
    x = x.squeeze()
    if len(x.shape) == 3:
        timedim = 2
        trialdim = 1
        ntrials = x.shape[trialdim]
        nchans = x.shape[0]
        logger.info('The data is of format %d channels x %d trials x time',
                    nchans, ntrials)
    else:
        logger.error('Sorry! The data should be a 3 dimensional array!')

    # Calculate the tapers

    nfft, f, fInd = _get_freq_vector(x, params, timedim)
    ntaps = params['tapers'][1]
    TW = params['tapers'][0]

    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)
    
    if 'pcaComponentNumber' in params.keys() and params['pcaComponentNumber']:
        pc = -1*np.array([params['pcaComponentNumber']]).squeeze()
        pc = np.atleast_1d(pc)
    else:
        pc = np.atleast_1d(-1*np.array([1]))
    
    nPC = len(pc)

    plv = np.zeros((ntaps, nPC, len(f)))
    itc = np.zeros((ntaps, nPC, len(f)))
    cspec = np.zeros((ntaps, nPC, len(f)))

    if 'returnEigenvectors' in params.keys() and params['returnEigenvectors']:
        cspecV = np.zeros((ntaps, nPC, nchans, len(f)), dtype=complex)
        plvV = np.zeros((ntaps, nPC, nchans, len(f)), dtype=complex)
        itcV = np.zeros((ntaps, nPC, nchans, len(f)), dtype=complex)

    useData = x

    for k, tap in enumerate(w):

        xw = np.fft.rfft((tap * useData), n=nfft, axis=timedim)

        # no point keeping everything if fpass was already set
        # this is OK, because time must be last dimension for this function
        xw = xw[:, :, fInd]

        C = xw.mean(axis=trialdim).squeeze()

        itcC = (xw.mean(axis=trialdim) /
                (abs(xw).mean(axis=trialdim))).squeeze()

        plvC = (xw / abs(xw)).mean(axis=trialdim).squeeze()
        
        for fi in np.arange(0, len(f)):
            powerCsd = np.outer(C[:, fi], C[:, fi].conj())
            powerEigenvals, powEigenvec = linalg.eigh(powerCsd)
            cspec[k, :, fi] = powerEigenvals[pc] / nchans

            plvCsd = np.outer(plvC[:, fi], plvC[:, fi].conj())
            plvEigenvals, plvEigenvec = linalg.eigh(plvCsd)
            plv[k, :, fi] = plvEigenvals[pc] / nchans

            itcCsd = np.outer(itcC[:, fi], itcC[:, fi].conj())
            itcEigenvals, itcEigenvec = linalg.eigh(itcCsd)
            itc[k, :, fi] = itcEigenvals[pc] / nchans
    
            if params['returnEigenvectors']:
                cspecV[k, :, :, fi] = powEigenvec[:, pc].T
                plvV[k, :, :, fi] = plvEigenvec[:, pc].T
                itcV[k, :, :, fi] = itcEigenvec[:, pc].T

    # Average over tapers and squeeze to pretty shapes
    out['spectrum'] = (cspec.mean(axis=0)).squeeze()
    out['plv'] = (plv.mean(axis=0)).squeeze()
    out['itc'] = (itc.mean(axis=0)).squeeze()
    
    if 'returnEigenvectors' in params.keys() and params['returnEigenvectors']:
        out['spectrumV'] = cspecV.squeeze()
        out['plvV'] = plvV.squeeze()
        out['itcV'] = itcV.squeeze()

    if bootstrapMode:
        out['f'] = f
        return out
    else:
        return (out, f)


#'''untested and possibly not useful
#@verbose
#def mtcpca_autocorr(x, params, verbose=None, bootstrapMode=False):
#    """
#    This function aligns the phases of a multi-channel response using the
#    frequency-domain PCA ("complex PCA"), and then performs autocorrelation on
#    the resulting time series
#    """
#
#    try:
#        bootstrapMode = params['bootstrapMode']
#    except KeyError:
#        bootstrapMode = bootstrapMode
#
#    out = {}
#
#    logger.info('Running Multitaper Complex PCA based ' +
#                'plv and power estimation.')
#    x = x.squeeze()
#    if len(x.shape) == 3:
#        timedim = 2
#        trialdim = 1
#        ntrials = x.shape[trialdim]
#        nchans = x.shape[0]
#        logger.info('The data is of format %d channels x %d trials x time',
#                    nchans, ntrials)
#    else:
#        logger.error('Sorry! The data should be a 3 dimensional array!')
#
#    nfft, f, _ = _get_freq_vector(x, params, timedim)
#
#    # Calculate the tapers
#    ntaps = params['tapers'][1]
#    TW = params['tapers'][0]
#    w, conc = dpss_windows(x.shape[timedim], TW, ntaps)
#    cspec = np.zeros((ntaps, len(f)))
#    cspecV = np.zeros((ntaps, nchans, len(f)), dtype=complex)
#    
#    for k, tap in enumerate(w):
#        xw = np.fft.rfft((tap * x), n=nfft, axis=timedim)
#        C = xw.mean(axis=trialdim).squeeze()
#        
#        for fi in np.arange(0, len(f)):
#            powerCsd = np.outer(C[:, fi], C[:, fi].conj())
#            powerEigenvals, powEigenvec = linalg.eigh(powerCsd)
#            cspec[k, fi] = powerEigenvals[-1] / nchans
#            cspecV[k, :, fi] = powEigenvec[:, -1].squeeze()
#    
#    cspec = cspec.mean(axis=0).squeeze()
#    cspecV = cspecV.mean(axis=0).squeeze()
#    xx = np.zeros(f.shape, dtype=complex)
#    xmean = x.mean(axis=timedim, keepdims=True)
#    X = np.fft.rfft(x - xmean, axis=timedim, n=nfft).mean(axis=1)
#
#    for fi in range(len(f)):
#        xx[fi] = np.dot(X[:, fi], cspecV[:, fi])
#
#    recovered = np.fft.irfft(xx, n=nfft)
#    a = np.fft.irfft(xx * np.conj(xx), n=nfft)[:x.shape[timedim]]
#    t = np.arange(0, x.shape[timedim] / params['Fs'], 1/params['Fs'])
#
#    if bootstrapMode:
#        out['ac'] = a / a[0]
#        out['t'] = t
#        out['mtcspec'] = cspec
#        out['f'] = f
#        out['recovered'] = recovered
#        return out
#    else:
#        return (a / a[0], t, cspec, f, recovered)


@verbose
def _get_freq_vector(x, params, timeDim=2, verbose=None):
    '''
    internal function, not really meant to be called/viewed by the end user
    (unless end user is curious).

    computes nfft based on x.shape.
    '''
    badNfft = False
    if 'nfft' in params:
        if params['nfft'] < x.shape[timeDim]:
            badNfft = True
            logger.warning(
                'nfft should be >= than number of time points. Reverting' +
                'to default setting of nfft = 2**ceil(log2(nTimePts))\n')

    if 'nfft' not in params or badNfft:
        nfft = int(2.0 ** ceil(np.log2(x.shape[timeDim])))
    else:
        nfft = int(params['nfft'])

    f = np.fft.fftfreq(nfft, 1/params['Fs'])
    # since np.fft.rfft in use:
    if nfft % 2: 
        maxIdx = int((nfft+1)/2)
    else:
        maxIdx = int((nfft/2) + 1)

    # when even nfft, but nfft/2+1 point is a neg frequency using fftfreq
    # symmetric so I think this is OK:
    f = np.abs(f[0:maxIdx])

    fInd = ((f >= params['fpass'][0]) & (f <= params['fpass'][1]))
    f = f[fInd]

    return (nfft, f, fInd)


@verbose
def generate_parameters(verbose=True, **kwArgs):

    """
    Generates some default parameter values using keyword arguments!

    See documentation for each individual function to see which keywords are
    required. Samplerate (Fs= ) is always required.

    Without keyword arguments, the following parameter structure is generated:

    params['tapers'] = [2, 3]
    params['Npairs'] = 0
    params['itc'] = False
    params['threads'] = 4
    params['nDraws'] = 100
    params['indivDraw'] = False
    params['debugMode'] = False

    Change any key value by using it as an keyword argument; e.g.,

    generate_params(Fs = 16384, threads = 8)

    would result in the parameter values associated with Fs and threads only,
    without changing the other default values.

    Returns
    ---------
    Dictionary of parameters.

    """

    params = {}
    params['tapers'] = [2, 3]
    params['Npairs'] = 0
    params['itc'] = False
    params['threads'] = cpu_count()
    params['nDraws'] = 1000
    params['indivDraw'] = False
    params['debugMode'] = False
    params['bootstrapMode'] = False
    params['returnEigenvectors'] = False
    params['pcaComponentNumber'] = [1] 

    userKeys = kwArgs.keys()

    for kw in userKeys:
        if (kw.lower() == 'fs' or
            kw.lower() == 'samplerate' or
            kw.lower == 'sfreq' or
            kw.lower == 'srate'):
            params['Fs'] = int(kwArgs[kw])

        elif kw.lower() == 'nfft':
            params['nfft'] = int(kwArgs[kw])

        elif kw.lower() == 'tapers':
            params['tapers'] = list(kwArgs[kw])
            params['tapers'][1] = int(params['tapers'][1])

        elif kw.lower() == 'fpass':
            params['fpass'] = list(kwArgs[kw])

        elif kw.lower() == 'npairs':
            params['Npairs'] = int(kwArgs[kw])

        elif kw.lower() == 'itc':
            params['itc'] = bool(kwArgs[kw])

        elif kw.lower() == 'threads':
            params['threads'] = int(kwArgs[kw])

        elif kw.lower() == 'ndraws':
            params['nDraws'] = int(kwArgs[kw])

        elif kw.lower() == 'debugmode':
            params['debugMode'] = bool(kwArgs[kw])

        elif (kw.lower() == 'returnindividualbootstrapresults' or
              kw.lower() == 'indivdraw'):
            params['indivDraw'] = bool(kwArgs[kw])

        elif kw.lower() == 'bootstrapmode':
            params['bootstrapMode'] = bool(kwArgs[kw])

        elif kw.lower() == 'pcacomponentnumber':
            params['pcaComponentNumber'] = np.array(np.atleast_1d(kwArgs[kw]),
                                                    dtype=int).flatten()

        else:
            params[kw] = kwArgs[kw]
            logger.info((kw + ' = {}').format(kwArgs[kw]))

    _validate_parameters(params)

    logger.info('Current parameters:')
    logger.info('sampleRate (Fs) = {} Hz'.format(params['Fs']))
    if 'nfft' in params:
        logger.info('nfft = {}'.format(params['nfft']))
    else:
        logger.info('nfft = 2**ceil(log2(data.shape[timeDimension]))')
    logger.info('Number of tapers = {} '.format(params['tapers'][1]))
    logger.info('Taper TW = {} '.format(params['tapers'][0]))
    logger.info('fpass = [{}, {}] (inclusive)'.format(params['fpass'][0],
                                                      params['fpass'][1]))
    logger.info('itc = {}'.format(params['itc']))
    logger.info('NPairs = {}'.format(params['Npairs']))
    logger.info('debugMode = {}'.format(params['debugMode']))
    logger.info('bootstrapMode: {}'.format(params['bootstrapMode']))
    if params['bootstrapMode']:
        logger.info('\nBootstrap specific:\n')
        logger.info('threads = {}'.format(params['threads']))
        logger.info('nDraws = {}'.format(params['nDraws']))
        logger.info('indivDraw = {}'.format(params['indivDraw']))

    return params


@verbose
def _validate_parameters(params, verbose=True):
    '''
    internal function, not really meant to be called/viewed by the end user
    (unless end user is curious).

    validates parameters
    '''

    if 'Fs' not in params:
        logger.error('sample rate (params["Fs"]) must be specified')

    if 'nfft' not in params:
        logger.info('params["nfft"] defaulting to ' +
                    '2**ceil(log2(data.shape[timeDimension]))')

    # check/fix taper input
    if len(params['tapers']) != 2:
        logger.error('params["tapers"] must be a list/tuple of '
                     'form [TW,taps]')
    if params['tapers'][0] <= 0:
        logger.error('params["tapers"][0] (TW) must be positive')

    if params['tapers'][1] <= 0:
        logger.error('params["tapers"][1] (ntaps) must be a ' +
                     'positive integer')

    # check/fix fpass
    if 'fpass' in params:
        if 2 != len(params['fpass']):
            logger.error('fpass must have two values')

        if params['fpass'][0] < 0:
            logger.error('params[''fpass[0]''] should be >= 0')

        if params['fpass'][1] < 0:
            logger.error('params[''fpass[1]''] should be >= 0')

        if params['fpass'][0] > params['Fs'] / 2.0:
            logger.error('params[''fpass''][0] should be <= ' +
                         'params[''Fs'']/2')

        if params['fpass'][1] > params['Fs'] / 2.0:
            logger.error('params[''fpass''][1] should be <= ' +
                         'params[''Fs'']/2')

        if params['fpass'][0] >= params['fpass'][1]:
            logger.error('params[''fpass''][0] should be < ' +
                         'params[''fpass''][1]')
    else:
        params['fpass'] = [0.0, params['Fs'] / 2.0]
        logger.info('params[''fpass''] defaulting to ' +
                    '[0, (params[''Fs'']/2.0)]')

    return params
