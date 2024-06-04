import socket
from typing import Union

import lab as B
import pytest
from numpy.testing import assert_allclose
from plum import Dispatcher

import neuralprocesses
import tensorflow as tf
import torch

__all__ = ["approx", "nps", "generate_data", "remote_xfail", "remote_skip"]

_dispatch = Dispatcher()

# Stabilise numerics during tests.
B.epsilon = 1e-6
B.cholesky_retry_factor = 1e4


@_dispatch
def approx(a, b, **kw_args):
    assert_allclose(B.to_numpy(a), B.to_numpy(b), **kw_args)


@_dispatch
def approx(a: None, b: None, **kw_args):
    assert True


@_dispatch
def approx(
    a: Union[neuralprocesses.Parallel, tuple],
    b: Union[neuralprocesses.Parallel, tuple],
    **kw_args,
):
    assert len(a) == len(b)
    for ai, bi in zip(a, b):
        approx(ai, bi, **kw_args)


import neuralprocesses.tensorflow as nps_tf
import neuralprocesses.torch as nps_torch

nps_torch.dtype = torch.float32
nps_torch.dtype32 = torch.float32
nps_torch.dtype64 = torch.float64
nps_tf.dtype = tf.float32
nps_tf.dtype32 = tf.float32
nps_tf.dtype64 = tf.float64


@pytest.fixture(params=[nps_tf, nps_torch], scope="module")
def nps(request):
    return request.param


def generate_data(
    nps,
    batch_size=4,
    dim_x=1,
    dim_y=1,
    n_context=5,
    n_target=7,
    binary=False,
    dtype=None,
):
    if dtype is None:
        dtype = nps.dtype
    xc = B.randn(dtype, batch_size, dim_x, n_context)
    yc = B.randn(dtype, batch_size, dim_y, n_context)
    xt = B.randn(dtype, batch_size, dim_x, n_target)
    yt = B.randn(dtype, batch_size, dim_y, n_target)
    if binary:
        yc = B.cast(dtype, yc >= 0)
        yt = B.cast(dtype, yt >= 0)
    return xc, yc, xt, yt


if socket.gethostname().lower().startswith("wessel"):
    remote_xfail = lambda f: f  #: `xfail` only on CI.
    remote_skip = lambda f: f  #: `skip` only on CI.
else:
    remote_xfail = pytest.mark.xfail  #: `xfail` only on CI.
    remote_skip = pytest.mark.skip  #: `skip` only on CI.
