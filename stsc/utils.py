#!/usr/bin/env python3

import re
import sys
import logging
import datetime
import os.path as osp
from typing import NoReturn, List, Tuple, Union, Collection

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import torch as t
from torch.utils.data import DataLoader

import stsc.datasets as D
import stsc.models as M

def generate_identifier():
    """Generate unique date and time based identifier"""
    return re.sub(' |:','',str(datetime.datetime.today()))


def make_joint_matrix(pths : List[str],
                     )->pd.DataFrame:
    """Generate joint count matrix

    Generates a joint count matrix from multiple
    count data sets. Union of genes found within
    all data sets is used. In sections were a gene
    has not been observed, all values are set to zero.

    Parameter:
    ---------
    pths : List[str]
        paths to matrix files

    Returns:
    -------

    Pandas DataFrame of a joint matrix. Data points
    from the same file will share the same
    rowname prefix k, in the
    joint matrix rowname given as
    "k&-original-rowname"

    """

    # Prepare variables
    mlist = []
    start_pos = [0]
    index = pd.Index([])
    genes = pd.Index([])

    # Iterate over all provided paths
    for k,pth in enumerate(pths):
        # read file
        cnt = read_file(pth)
        mlist.append(cnt)
        # add file identifier k&- to rownames
        index = index.append(pd.Index([str(k) + '&-' + x for \
                                       x in cnt.index ] ))

        # get union of all observed genes
        genes = genes.union(cnt.columns)
        # add length of matrix
        start_pos.append(cnt.shape[0])

    # get start position for entries of each file
    start_pos = np.cumsum(np.array(start_pos))
    # prepare joint matrix, rownames are numbers
    jmat = pd.DataFrame(np.zeros((start_pos[-1],
                                  genes.shape[0])
                                ),
                       columns = genes,
                       )
    # construct joint matrix
    for k in range(len(start_pos) - 1):
        # set start and end pos based on
        # numeric rownames
        start = start_pos[k]
        end = start_pos[k+1] - 1
        jmat.loc[start:end,mlist[k].columns] = mlist[k].values

    # set new indices
    jmat.index = index

    return jmat

def split_joint_matrix(jmat : pd.DataFrame,
                      ) -> List[pd.DataFrame]:

    """Split joint matrix

    Splits a joint matrix generated by
    make_joint_matrix into each of the
    contituents

    Parameter:
    ---------
    jmat : pd.DataFrame
        joint matrix

    Returns:
    -------
    List containing each individual
    matrix consituting the joint matrix

    """
    try:
        idx, name = zip(*[ idx.split('&-') for \
                          idx in jmat.index ])
    except:
        print("_".join([f"Matrix provided is not",
                        f"a joint matrix generated",
                        f"by make_joint_matrix",
                       ]
                      )
             )

    # convert names to pandas index
    name = pd.Index(name)
    # get identifers
    idx = np.array(idx).astype(int)
    # get unique identifiers
    uidx = np.unique(idx)
    # list to store matrices
    matlist = []
    # get all individual matrices
    for k in uidx:
        # get indices with same identifiers
        sel = (idx == k)
        # select indices with same identifiers
        tm = jmat.iloc[sel,:]
        # set index to original indices
        tm.index = pd.Index([x.replace('&-','_') for \
                             x in name[sel].values])
        # append single matrix to list
        matlist.append(tm)

    return matlist

def Logger(logname : str ,
          )->logging.Logger:

    """Logger for steroscope run

    Parameter:
    ----------
    logname : str
        full name of file to
        which log should be saved

    Returns:
    -------
    log : logging.Logger
        Logger object with
        identifier STereoSCope

    """
    log_level = logging.DEBUG

    log = logging.getLogger('stsc')

    log.setLevel(log_level)

    # filehandler to save log file
    fh = logging.FileHandler(logname)
    fh.setLevel(log_level)

    # streamhandler for stdout output
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    # format logger display message
    formatstr = '[%(asctime)s - %(name)s - %(levelname)s ] >> %(message)s'
    formatter = logging.Formatter(formatstr)
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    log.addHandler(fh)
    log.addHandler(ch)

    return log

class SimpleProgressBar:
    """
    Progress bar to display progress during estimation

    Attributes
    ----------
    max_value : int
        total number of epochs to be used
    length: int
        number of markers to use
    symbol : str
        symbol to use as indicator
    silent_mode : bool
        whether or not to use silent mode,
        default is False

    """

    def __init__(self,
                 max_value : int,
                 length : int = 20,
                 symbol : str = "=",
                 silent_mode : bool = False,
                 )->None:

        self.symbol = symbol
        self.mx = max_value
        self.len = length
        self.delta = self.mx / self.len
        self.ndigits = len(str(self.mx))

        print("\r\n")

        if silent_mode:
            self.call_func = self._silent
        else:
            self.call_func = self._verbose

    def _verbose(self,
                 epoch : int,
                 value : float,
                ) -> NoReturn:

        """Updates progressbar

            Parameters
            ----------
            epoch : int
                current epoch
            value : float
                value to display

            """

        progress = self.symbol*int((epoch / self.delta))
        print(f"\r"
              f"Epoch : {epoch +1:<{self.ndigits}}/{self.mx:<{self.ndigits}}"
              f" | Loss : {value:9E}"
              f" | \x1b[1;37m["
              f" \x1b[0;36m{progress:<{self.len}}"
              f"\x1b[1;37m]"
              f" \x1b[0m",
              end="")

    def _silent(self,
                *args,
                **kwargs,
               ) -> NoReturn:
        pass



    def __call__(self,
                 epoch : int,
                 value : float,
                 ) -> NoReturn:

        self.call_func(epoch, value)

class LossTracker:
    """Keep track of loss

    Class to save and keep track
    of loss progression thoroughout
    optimization.

    Attributes:
    ----------
    opth : str
        output path to save loss
        progression
    interval : int
        interval of epochs by which
        loss values should be written
        to file

    """

    def __init__(self,
                 opth : str,
                 interval : int  = 100,
                )->None:

        self.history = []
        self.interval = interval + 1
        self.opth = opth

    def write_history(self,
                      )->None:
        """Write loss history to file

        Will generate a file where each
        loss value is separated by a
        comma. First character is a comma
        as well.

        """

        # use comma separation
        with open(self.opth,"a") as fopen:
            fopen.writelines(',' + ','.join([str(x) for \
                                       x in self.history]
                                     )
                            )

        # erase loss history once written
        self.history = []

    def __call__(self,
                 loss : float,
                 epoch : int,
                )-> None:
        """Store and write loss history

        Paramers:
        --------
        loss : float
            loss of current epoch
        epoch : int
            current epoch

        """

        self.history.append(loss)

        if (epoch % self.interval == 0 and \
           epoch >= self.interval):
            self.write_history()

    def __len__(self,):
        """length of loss"""
        return len(self.history)
    def current(self,):
        """current loss value"""
        return self.history[-1]

def read_file(file_name : str,
             )-> pd.DataFrame :
    """Read file

    Control if file extension is supported
    and read file.

    Parameter:
    ---------
    file_name : str
        path to file

    Returns:
    -------
        DataFrame with content of file

    """
    supported = ['tsv','gz']
    extension = osp.splitext(file_name)[1][1::]

    if extension not in supported:
        print(' '.join([f"ERROR: File format {extension}",
                        f"is not yet supported. Please",
                        f"use any of {' '.join(supported)}",
                        f"formats instead",
                       ]
                      )
             )

        sys.exit(-1)

    elif extension == 'tsv' or extension == 'gz':
        try:
            compression = ('infer' if extension == 'tsv' else 'gzip')
            file = pd.read_csv(file_name,
                               header = 0,
                               index_col = 0,
                               compression = compression,
                               sep = '\t')

            return file
        except:
            print(' '.join([f"Something went wrong",
                            f"when trying to read",
                            f"file >> {file_name}",
                           ],
                          )
                 )


def write_file(file : pd.DataFrame,
               opth : str,
              )-> None:
    """Write file

    Parameter:
    ---------
    file : pd.DataFrame
        DataFrame to be written to file
    opth : str
        output path

    """

    try:
        file.to_csv(opth,
                    index = True,
                    header = True,
                    sep = '\t')
    except:
        print(' '.join([f"An error occured",
                        f"while trying to write",
                        f"file >> {opth}",
                       ],
                      )
             )


