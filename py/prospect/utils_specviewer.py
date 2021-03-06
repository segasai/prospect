# -*- coding: utf-8 -*-

"""
Utility functions for prospect
"""

import os, glob
import numpy as np
import astropy.io.fits
from astropy.table import Table, vstack
import scipy.ndimage.filters

from desiutil.log import get_logger
import desispec.spectra
import desispec.frame
from desitarget.targetmask import desi_mask
from desitarget.cmx.cmx_targetmask import cmx_mask
from desitarget.sv1.sv1_targetmask import desi_mask as sv1_desi_mask
import redrock.results

from prospect import mycoaddcam
from prospect import myspecselect, myspecupdate

_vi_flags = [
    # Definition of VI flags
    # Replaces former list viflags = ["Yes","No","Maybe","LowSNR","Bad"]
    # shortlabels for "issue" flags must be a unique single-letter identifier
    {"label" : "4", "type" : "class", "description" : "Confident classification, two or more secure features"},
    {"label" : "3", "type" : "class", "description" : "Probable classification, at least one secure feature + continuum; or many weak features"},
    {"label" : "2", "type" : "class", "description" : "Possible classification, one strong emission feature, but not sure what it is"},
    {"label" : "1", "type" : "class", "description" : "Unlikely classification, one or some unidentified features"},
    {"label" : "0", "type" : "class", "description" : "Nothing there"},
    {"label" : "Bad redshift fit", "shortlabel" : "R", "type" : "issue", "description" : "Misestimation of redshift by pipeline fitter"},
    {"label" : "Bad spectype fit", "shortlabel" : "C", "type" : "issue", "description" : "Misidentification of spectral category by pipeline fitter, eg. star vs QSO..."},
    {"label" : "Bad spectrum", "shortlabel" : "S", "type" : "issue", "description" : "Bad spectrum, eg. cosmic / skyline subtraction residuals..."}
]

_vi_file_fields = [
    # Contents of VI files: [ 
    #      field name (in VI file header), 
    #      associated variable in cds_targetinfo, 
    #      dtype in VI file ]
    # Ordered list
    ["TargetID", "targetid", "i4"],
    ["ExpID", "expid", "i4"],
    ["Spec version", "spec_version", "i4"], # TODO define
    ["Redrock version", "redrock_version", "i4"], # TODO define
    ["Redrock spectype", "spectype", "S10"],
    ["Redrock z", "z", "f4"],
    ["VI scanner", "VI_scanner", "S10"],
    ["VI class", "VI_class_flag", "i2"],
    ["VI issue", "VI_issue_flag", "S6"],
    ["VI z", "VI_z", "f4"],
    ["VI spectype", "VI_spectype", "S10"],
    ["VI comment", "VI_comment", "S100"]
]

_vi_spectypes =[
    # List of spectral types to fill in VI categories
    # in principle, it should match somehow redrock spectypes...
    "STAR",
    "GALAXY",
    "QSO"
]

def read_vi(vifile) :
    '''
    Read visual inspection file (ASCII/CSV or FITS according to file extension)
    Return full VI catalog, in Table format
    '''
    vi_records = [x[0] for x in _vi_file_fields]
    vi_dtypes = [x[2] for x in _vi_file_fields]
    
    if (vifile[-5:] != ".fits" and vifile[-4:] not in [".fit",".fts",".csv"]) :
        raise RuntimeError("wrong file extension")
    if vifile[-4:] == ".csv" :
        vi_info = Table.read(vifile,format='ascii.csv', names=vi_records)
        for i,rec in enumerate(vi_records) :
            vi_info[rec] = vi_info[rec].astype(vi_dtypes[i])
    else :
        vi_info = astropy.io.fits.getdata(vifile,1)
        if [(x in vi_info.names) for x in vi_records]!=[1 for x in vi_records] :
            raise RuntimeError("wrong record names in VI fits file")
        vi_info = Table(vi_info)

    return vi_info


def match_vi_targets(vifile, targetlist) :
    '''
    Returns list of VIs matching the list of targetids
    For a given target, several VI entries can be available
    '''
    vi_info = read_vi(vifile)
    vicatalog=[ [] for i in range(len(targetlist)) ]
    for itarget,targetnum in enumerate(targetlist) :
        w,=np.where( (vi_info['targetid'] == targetnum) )
        if len(w)>0 : vicatalog[itarget] = vi_info[w]
    return vicatalog


def convert_vi_tofits(vifile_in, overwrite=True) :
    log = get_logger()
    if vifile_in[-4:] != ".csv" : raise RuntimeError("wrong file extension")
    vi_info = read_vi(vifile_in)
    vifile_out=vifile_in.replace(".csv",".fits")
    vi_info.write(vifile_out, format='fits', overwrite=overwrite)
    log.info("Created fits file : "+vifile_out+" ("+str(len(vi_info))+" entries).")
    

def initialize_master_vi(mastervifile, overwrite=False) :
    '''
    Create "master" VI file with no entry
    '''
    log = get_logger()
    vi_records = [x[0] for x in _vi_file_fields]
    vi_dtypes = [x[2] for x in _vi_file_fields]
    vi_info = Table(names=vi_records, dtype=tuple(vi_dtypes))
    vi_info.write(mastervifile, format='fits', overwrite=overwrite)
    log.info("Initialized VI file : "+mastervifile+" (0 entry)")
    

def merge_vi(mastervifile, newvifile) :
    '''
    Merge a new VI file to the "master" VI file
    The master file is overwritten.
    '''
    log = get_logger()
    mastervi = read_vi(mastervifile)
    newvi = read_vi(newvifile)
    mergedvi = vstack([mastervi,newvi], join_type='exact')
    mergedvi.write(mastervifile, format='fits', overwrite=True)
    log.info("Updated master VI file : "+mastervifile+" (now "+str(len(mergedvi))+" entries).")



def match_zcat_to_spectra(zcat_in, spectra) :
    '''
    zcat_in : astropy Table from redshift fitter
    - creates a new astropy Table whose rows match the targetids of input spectra
    - also returns the corresponding list of indices
    - for each targetid, a unique row in zcat_in must exist.
    '''
    zcat_out = Table(dtype=zcat_in.dtype)
    index_list = list()
    for i_spec in range(spectra.num_spectra()) :
        ww, = np.where((zcat_in['TARGETID'] == spectra.fibermap['TARGETID'][i_spec]))
        if len(ww)<1 : 
                raise RuntimeError("No zcat entry for target "+str(spectra.fibermap['TARGETID'][i_spec]))
        if len(ww)>1 :
            raise RuntimeError("Several zcat entries for target "+str(spectra.fibermap['TARGETID'][i_spec]))
        zcat_out.add_row(zcat_in[ww[0]])
        index_list.append(ww[0])
    return (zcat_out, index_list)


def match_redrock_zfit_to_spectra(redrockfile, spectra, num_best_fit=3) :
    '''
    Read Redrock file, and return astropy Table of best fits matched to the targetids of input spectra
    - for each target, returns arrays chi2[N], coeff[N], z[N], spectype[N], subtype[N]
    - where N = num_best_fit
    '''
    matched_redrock_cat = Table(dtype=[('TARGETID', '<i8'), ('chi2', '<f8', (num_best_fit,)), ('coeff', '<f8', (num_best_fit,10,)), ('z', '<f8', (num_best_fit,)), ('spectype', '<U6', (num_best_fit,)), ('subtype', '<U2', (num_best_fit,))])
    dummy, rr_table = redrock.results.read_zscan(redrockfile)
    for i_spec in range(spectra.num_spectra()) :
        ww, = np.where((rr_table['targetid'] == spectra.fibermap['TARGETID'][i_spec]))
        if len(ww)<num_best_fit : 
            raise RuntimeError("redrock table cannot match spectra with "+str(num_best_fit)+" best fits")
        ind = np.argsort(rr_table[ww]['chi2'])[0:num_best_fit]
        sub_table = rr_table[ww][ind][0:num_best_fit]
        the_entry = [ spectra.fibermap['TARGETID'][i_spec] ]
        the_entry.append(sub_table['chi2'])
        the_entry.append(sub_table['coeff'])
        the_entry.append(sub_table['z'])
        the_entry.append(sub_table['spectype'])
        the_entry.append(sub_table['subtype'])
        matched_redrock_cat.add_row(the_entry)
        
    return matched_redrock_cat

def make_targetdict(tiledir, petals=[str(i) for i in range(10)], tiles=None) :
    '''
    Small homemade/hack utility (based on Anand's hack)
    Makes "mini-db" of targetids. It basically reads zbest, so it's reasonably fast
    Adapted to the directory structure tiledir/{tile}/{night}/{cframe/zbest/...}
    '''
    target_dict = {}
    if tiles is None :
        tiles = os.listdir(tiledir)
    else :
        alltiles = os.listdir(tiledir)
        assert all(x in alltiles for x in tiles)
            
    for tile in tiles :
        nights = os.listdir(os.path.join(tiledir,tile))
        for night in nights :
            target_dict[tile+"-"+night] = {}
            # exposures
            fns = np.sort(glob.glob(os.path.join(tiledir,tile,night,'cframe-b'+petals[0]+'-????????.fits')))
            target_dict[tile+"-"+night]['exps'] = np.array([fn.replace('.','-').split('-')[-2] for fn in fns])
            # targetid, fibres
            targetid,fiber,petal_list = [],[],[]
            for petal in petals:
                pp = glob.glob(os.path.join(tiledir,tile,night,'zbest-'+petal+'-'+tile+'-????????.fits'))
                if len(pp)>0 :
                    fn        = pp[0]
                    data      = astropy.io.fits.open(fn)['fibermap'].data
                    targetid += data['targetid'].tolist()
                    fiber    += data['fiber'].tolist()
                    petal_list    += [petal for i in range(len(data['targetid']))]
            target_dict[tile+"-"+night]['targetid'] = np.array(targetid)
            target_dict[tile+"-"+night]['fiber']    = np.array(fiber)
            target_dict[tile+"-"+night]['petal']    = np.array(petal_list)
            
    return target_dict

def load_spectra_zcat_from_targets(targets, tiledir, obs_db) :
    '''
    Creates (spectra,zcat) = (Spectra object, zcatalog Table) from a list of targetids
    - targets must be a list of int64
    - obs_db : "mini-db" produced by make_targetdict()
    '''
    
    spectra = None
    ztables = []
    
    for target in targets :
        for tile_night in obs_db.keys() :
            w, = np.where( obs_db[tile_night]['targetid'] == target )
            if len(w) > 1 : 
                print("Warning ! Several entries in tile/night "+tile_night+" available for target "+str(target))
            if len(w) == 0 : continue
            the_path = tile_night.replace("-","/")
            the_petal = obs_db[tile_night]['petal'][w[0]]
            the_spec = desispec.io.read_spectra(os.path.join(tiledir,the_path,"coadd-"+the_petal+"-"+tile_night+".fits"))
            the_spec = myspecselect.myspecselect(the_spec, targets=[target], remove_scores=True)
            if the_spec.num_spectra() != 1 : 
                print("Warning ! Several spectra for "+tile_night+" and target "+str(target))
            the_zcat = Table.read(os.path.join(tiledir,the_path,"zbest-"+the_petal+"-"+tile_night+".fits"),'ZBEST')
            the_zcat, dummy = match_zcat_to_spectra(the_zcat, the_spec)
            ztables.append(the_zcat)
            if spectra is None : spectra = the_spec
            else : spectra = myspecupdate.myspecupdate(spectra,the_spec)
    zcat = vstack(ztables)
    
    return (spectra,zcat)


def get_y_minmax(pmin, pmax, data, ispec) :
    '''
    Utility, from plotframe
    '''
    dx = np.sort(data[np.isfinite(data)])
    if len(dx)==0 : return (0,0)
    imin = int(np.floor(pmin*len(dx)))
    imax = int(np.floor(pmax*len(dx)))
    if (imax >= len(dx)) : imax = len(dx)-1
    return (dx[imin],dx[imax])



def frames2spectra(frames, nspec=None, startspec=None, with_scores=False, with_resolution_data=False):
    '''Convert input list of Frames into Spectra object
    with_score : if true, propagate scores
    with_resolution_data: if true, propagate resolution
    '''
    bands = list()
    wave = dict()
    flux = dict()
    ivar = dict()
    mask = dict()
    res = dict()
        
    for fr in frames:
        fibermap = fr.fibermap
        band = fr.meta['CAMERA'][0]
        bands.append(band)
        wave[band] = fr.wave
        flux[band] = fr.flux
        ivar[band] = fr.ivar
        mask[band] = fr.mask
        res[band] = fr.resolution_data
        if nspec is not None :
            if startspec is None : startspec = 0
            flux[band] = flux[band][startspec:nspec+startspec]
            ivar[band] = ivar[band][startspec:nspec+startspec]
            mask[band] = mask[band][startspec:nspec+startspec]
            res[band] = res[band][startspec:nspec+startspec,:,:]
            fibermap = fr.fibermap[startspec:nspec+startspec]
    
    merged_scores = None
    if with_scores :
        scores_columns = frames[0].scores.columns
        for i in range(1,len(frames)) : 
            scores_columns += frames[i].scores.columns
        merged_scores = astropy.io.fits.FITS_rec.from_columns(scores_columns)

    if not with_resolution_data : res = None
    
    spectra = desispec.spectra.Spectra(
        bands, wave, flux, ivar, mask, fibermap=fibermap, meta=fr.meta, scores=merged_scores, resolution_data=res
    )
    return spectra


def specviewer_selection(spectra, log=None, mask=None, mask_type=None, gmag_cut=None, rmag_cut=None, chi2cut=None, zbest=None, snr_cut=None, with_dirty_mask_merge=False, remove_scores=False) :
    '''
    Simple sub-selection on spectra based on meta-data.
        Implemented cuts based on : target mask ; photo mag (g, r) ; chi2 from fit ; SNR (in spectra.scores, BRZ)
        - if chi2cut : a catalog zbest must be provided, with entries matching exactly those of spectra
    '''

    # SNR selection
    if snr_cut is not None :
        assert ( (len(snr_cut)==2) and (spectra.scores is not None) )
        for band in ['B','R','Z'] :
            w, = np.where( (spectra.scores['MEDIAN_CALIB_SNR_'+band]>snr_cut[0]) & (spectra.scores['MEDIAN_CALIB_SNR_'+band]<snr_cut[1]) )
            if len(w) == 0 :
                if log is not None : log.info(" * No spectra with MEDIAN_CALIB_SNR_"+band+" in requested range")
                return 0
            else :
                targetids = spectra.fibermap['TARGETID'][w]
                spectra = myspecselect.myspecselect(spectra, targets=targetids, remove_scores=remove_scores)
    
    # Target mask selection
    if mask is not None :
        assert mask_type in ['SV1_DESI_TARGET', 'DESI_TARGET', 'CMX_TARGET']
        if mask_type == 'SV1_DESI_TARGET' :
            assert ( mask in sv1_desi_mask.names() )            
            w, = np.where( (spectra.fibermap['SV1_DESI_TARGET'] & sv1_desi_mask[mask]) )
        elif mask_type == 'DESI_TARGET' :
            assert ( mask in desi_mask.names() ) 
            w, = np.where( (spectra.fibermap['DESI_TARGET'] & desi_mask[mask]) )
        elif mask_type == 'CMX_TARGET' :
            assert ( mask in cmx_mask.names() )
            mask2 = None
            if with_dirty_mask_merge : # Self-explanatory... only for fast VI of minisv
                if mask in ['SV0_QSO', 'SV0_ELG', 'SV0_LRG'] : mask2 = mask.replace('SV0','MINI_SV')
                if mask == 'SV0_BGS' : mask2 = 'MINI_SV_BGS_BRIGHT'
                if mask in ['SV0_STD_FAINT', 'SV0_STD_BRIGHT'] : mask2 = mask.replace('SV0_','')
            if mask2 is None :
                w, = np.where( (spectra.fibermap['CMX_TARGET'] & cmx_mask[mask]) )                
            else :
                w, = np.where( (spectra.fibermap['CMX_TARGET'] & cmx_mask[mask]) | 
                             (spectra.fibermap['CMX_TARGET'] & cmx_mask[mask2]) )
        if len(w) == 0 :
            if log is not None : log.info(" * No spectra with mask "+mask)
            return 0
        else :
            targetids = spectra.fibermap['TARGETID'][w]
            spectra = myspecselect.myspecselect(spectra, targets=targetids, remove_scores=remove_scores)

    # Photometry selection
    if gmag_cut is not None :
        assert len(gmag_cut)==2 # Require range [gmin, gmax]
        gmag = np.zeros(spectra.num_spectra())
        w, = np.where( (spectra.fibermap['FLUX_G']>0) & (spectra.fibermap['MW_TRANSMISSION_G']>0) )
        gmag[w] = -2.5*np.log10(spectra.fibermap['FLUX_G'][w]/spectra.fibermap['MW_TRANSMISSION_G'][w])+22.5
        w, = np.where( (gmag>gmag_cut[0]) & (gmag<gmag_cut[1]) )
        if len(w) == 0 :
            if log is not None : log.info(" * No spectra with g_mag in requested range")
            return 0
        else :
            targetids = spectra.fibermap['TARGETID'][w]
            spectra = myspecselect.myspecselect(spectra, targets=targetids)
    if rmag_cut is not None :
        assert len(rmag_cut)==2 # Require range [rmin, rmax]
        rmag = np.zeros(spectra.num_spectra())
        w, = np.where( (spectra.fibermap['FLUX_R']>0) & (spectra.fibermap['MW_TRANSMISSION_R']>0) )
        rmag[w] = -2.5*np.log10(spectra.fibermap['FLUX_R'][w]/spectra.fibermap['MW_TRANSMISSION_R'][w])+22.5
        w, = np.where( (rmag>rmag_cut[0]) & (rmag<rmag_cut[1]) )
        if len(w) == 0 :
            if log is not None : log.info(" * No spectra with r_mag in requested range")
            return 0
        else :
            targetids = spectra.fibermap['TARGETID'][w]
            spectra = myspecselect.myspecselect(spectra, targets=targetids, remove_scores=remove_scores)
    
    # Chi2 selection
    if chi2cut is not None :
        assert len(chi2cut)==2 # Require range [chi2min, chi2max]
        if np.any(zbest['TARGETID'] != spectra.fibermap['TARGETID']) :
            raise RunTimeError('specviewer_selection : zbest and spectra do not match (different targetids)') 

        w, = np.where( (zbest['DELTACHI2']>chi2cut[0]) & (zbest['DELTACHI2']<chi2cut[1]) )
        if len(w) == 0 :
            if log is not None : log.info(" * No target in this pixel with DeltaChi2 in requested range")
            return 0
        else :
            targetids = spectra.fibermap['TARGETID'][w]
            spectra = myspecselect.myspecselect(spectra, targets=targetids, remove_scores=remove_scores)

    return spectra


def _coadd(wave, flux, ivar, rdat):
    '''
    Return weighted coadd of spectra

    Parameters
    ----------
    wave : 1D[nwave] array of wavelengths
    flux : 2D[nspec, nwave] array of flux densities
    ivar : 2D[nspec, nwave] array of inverse variances of `flux`
    rdat : 3D[nspec, ndiag, nwave] sparse diagonals of resolution matrix

    Returns
    -------
        coadded spectrum (wave, outflux, outivar, outrdat)
    '''
    nspec, nwave = flux.shape
    unweightedflux = np.zeros(nwave, dtype=flux.dtype)
    weightedflux = np.zeros(nwave, dtype=flux.dtype)
    weights = np.zeros(nwave, dtype=flux.dtype)
    outrdat = np.zeros(rdat[0].shape, dtype=rdat.dtype)
    for i in range(nspec):
        unweightedflux += flux[i]
        weightedflux += flux[i] * ivar[i]
        weights += ivar[i]
        outrdat += rdat[i] * ivar[i]

    isbad = (weights == 0)
    outflux = weightedflux / (weights + isbad)
    outflux[isbad] = unweightedflux[isbad] / nspec

    outrdat /= (weights + isbad)
    outivar = weights

    return wave, outflux, outivar, outrdat

def coadd_targets(spectra, targetids=None):
    '''
    Coadds individual exposures of the same targets; returns new Spectra object

    Parameters
    ----------
    spectra : :class:`desispec.spectra.Spectra`
    targetids : (optional) array-like subset of target IDs to keep

    Returns
    -------
    coadded_spectra : :class:`desispec.spectra.Spectra` where individual
        spectra of each target have been combined into a single spectrum
        per camera.

    Note: coadds per camera but not across cameras.
    '''
    if targetids is None:
        targetids = spectra.target_ids()

    #- Create output arrays to fill
    ntargets = spectra.num_targets()
    wave = dict()
    flux = dict()
    ivar = dict()
    rdat = dict()
    if spectra.mask is None:
        mask = None
    else:
        mask = dict()
    for channel in spectra.bands:
        wave[channel] = spectra.wave[channel].copy()
        nwave = len(wave[channel])
        flux[channel] = np.zeros((ntargets, nwave))
        ivar[channel] = np.zeros((ntargets, nwave))
        ndiag = spectra.resolution_data[channel].shape[1]
        rdat[channel] = np.zeros((ntargets, ndiag, nwave))
        if mask is not None:
            mask[channel] = np.zeros((ntargets, nwave), dtype=spectra.mask[channel].dtype)

    #- Loop over targets, coadding all spectra for each target
    fibermap = Table(dtype=spectra.fibermap.dtype)
    for i, targetid in enumerate(targetids):
        ii = np.where(spectra.fibermap['TARGETID'] == targetid)[0]
        fibermap.add_row(spectra.fibermap[ii[0]])
        for channel in spectra.bands:
            if len(ii) > 1:
                outwave, outflux, outivar, outrdat = _coadd(
                    spectra.wave[channel],
                    spectra.flux[channel][ii],
                    spectra.ivar[channel][ii],
                    spectra.resolution_data[channel][ii]
                    )
                if mask is not None:
                    outmask = spectra.mask[channel][ii[0]]
                    for j in range(1, len(ii)):
                        outmask |= spectra.mask[channel][ii[j]]
            else:
                outwave, outflux, outivar, outrdat = (
                    spectra.wave[channel],
                    spectra.flux[channel][ii[0]],
                    spectra.ivar[channel][ii[0]],
                    spectra.resolution_data[channel][ii[0]]
                    )
                if mask is not None:
                    outmask = spectra.mask[channel][ii[0]]

            flux[channel][i] = outflux
            ivar[channel][i] = outivar
            rdat[channel][i] = outrdat
            if mask is not None:
                mask[channel][i] = outmask

    return desispec.spectra.Spectra(spectra.bands, wave, flux, ivar,
            mask=mask, resolution_data=rdat, fibermap=fibermap,
            meta=spectra.meta)


