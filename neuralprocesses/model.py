import lab as B
import numpy as np
from matrix.util import indent
from plum import List, Tuple, Union

from . import _dispatch
from .augment import AugmentedInput
from .coding import code, code_track, recode_stochastic
from .dist import AbstractMultiOutputDistribution
from .mask import Masked
from .parallel import Parallel
from .util import register_module

__all__ = ["Model", "loglik", "elbo", "predict"]


@register_module
class Model:
    """Encoder-decoder model.

    Args:
        encoder (coder): Coder.
        decoder (coder): Coder.

    Attributes:
        encoder (coder): Coder.
        decoder (coder): Coder.
    """

    def __init__(self, encoder, decoder):
        self.encoder = encoder
        self.decoder = decoder

    @_dispatch
    def __call__(
        self,
        xc,
        yc,
        xt,
        *,
        num_samples=1,
        aux_t=None,
        dtype_enc_sample=None,
        **kw_args,
    ):
        """Run the model.

        Args:
            xc (input): Context inputs.
            yc (tensor): Context outputs.
            xt (input): Target inputs.
            num_samples (int, optional): Number of samples, if applicable. Defaults
                to 1.
            aux_t (tensor, optional): Target-specific auxiliary input, if applicable.
            dtype_enc_sample (dtype, optional): Data type to convert the sampled
                encoding to.

        Returns:
            tuple[input, tensor]: Target inputs and prediction for target outputs.
        """
        # Perform augmentation of `xt` with auxiliary target information.
        if aux_t is not None:
            xt = AugmentedInput(xt, aux_t)

        # If the keyword `noiseless` is set to `True`, then that only applies to the
        # decoder.
        enc_kw_args = dict(kw_args)
        if "noiseless" in enc_kw_args:
            del enc_kw_args["noiseless"]
        xz, pz = code(self.encoder, xc, yc, xt, **enc_kw_args)

        # Sample and convert sample to the right data type.
        z = _sample(pz, num=num_samples)
        if dtype_enc_sample:
            z = B.cast(dtype_enc_sample, z)

        _, d = code(self.decoder, xz, z, xt, **kw_args)

        return d

    @_dispatch
    def __call__(
        self,
        contexts: List[Tuple[Union[B.Numeric, tuple], Union[B.Numeric, Masked]]],
        xt,
        **kw_args,
    ):
        return self(
            Parallel(*(c[0] for c in contexts)),
            Parallel(*(c[1] for c in contexts)),
            xt,
            **kw_args,
        )

    def __str__(self):
        return (
            f"Model(\n"
            + indent(str(self.encoder), " " * 4)
            + ",\n"
            + indent(str(self.decoder), " " * 4)
            + "\n)"
        )

    def __repr__(self):
        return (
            f"Model(\n"
            + indent(repr(self.encoder), " " * 4)
            + ",\n"
            + indent(repr(self.decoder), " " * 4)
            + "\n)"
        )


@_dispatch
def _sample(x: AbstractMultiOutputDistribution, num: B.Int = 1):
    return x.sample(num=num)


@_dispatch
def _sample(x: Parallel, num: B.Int = 1):
    return Parallel(*[_sample(xi, num=num) for xi in x])


def loglik(
    model,
    xc,
    yc,
    xt,
    yt,
    num_samples=1,
    normalise=True,
    **kw_args,
):
    """Log-likelihood objective.

    Args:
        model (:class:`.Model`): Model.
        xc (tensor): Inputs of the context set.
        yc (tensor): Output of the context set.
        xt (tensor): Inputs of the target set.
        yt (tensor): Outputs of the target set.
        num_samples (int, optional): Number of samples. Defaults to 1.
        normalise (bool, optional): Normalise the objective by the number of targets.
            Defaults to `True`.

    Returns:
        tensor: Log-likelihoods.
    """
    float = B.dtype_float(yt)
    float64 = B.promote_dtypes(float, np.float64)

    pred = model(
        xc,
        yc,
        xt,
        num_samples=num_samples,
        dtype_enc_sample=float,
        dtype_lik=float64,
        **kw_args,
    )
    logpdfs = pred.logpdf(B.cast(float64, yt))

    if num_samples > 1:
        # Sample dimension should always be the first.
        logpdfs = B.logsumexp(logpdfs, axis=0) - B.log(num_samples)

    if normalise:
        # Normalise by the number of targets.
        logpdfs = logpdfs / B.shape(xt, -1)

    return logpdfs


def elbo(
    model,
    xc,
    yc,
    xt,
    yt,
    num_samples=1,
    normalise=True,
    subsume_context=True,
    **kw_args,
):
    """ELBO objective.

    Args:
        model (:class:`.Model`): Model.
        xc (tensor): Inputs of the context set.
        yc (tensor): Output of the context set.
        xt (tensor): Inputs of the target set.
        yt (tensor): Outputs of the target set.
        num_samples (int, optional): Number of samples. Defaults to 1.
        normalise (bool, optional): Normalise the objective by the number of targets.
            Defaults to `True`.
        subsume_context (bool, optional): Subsume the context set into the target set.
            Defaults to `True`.

    Returns:
        tensor: ELBOs.
    """
    float = B.dtype_float(yt)
    float64 = B.promote_dtypes(float, np.float64)

    if subsume_context:
        # Subsume the context set into the target set.
        xt = B.concat(xc, xt, axis=-1)
        yt = B.concat(yc, yt, axis=-1)

    # Construct prior.
    xz, pz, h = code_track(model.encoder, xc, yc, xt, dtype_lik=float64, **kw_args)

    # Construct posterior.
    qz = recode_stochastic(model.encoder, pz, xt, yt, h, dtype_lik=float64, **kw_args)

    # Sample from poster.
    z = B.cast(float, _sample(qz, num=num_samples))

    # Run sample through decoder.
    _, d = code(model.decoder, xz, z, xt, dtype_lik=float64, **kw_args)

    # Compute the ELBO.
    elbos = B.mean(d.logpdf(B.cast(float64, yt)), axis=0) - _kl(qz, pz)

    if normalise:
        # Normalise by the number of targets.
        elbos = elbos / B.shape(xt, -1)

    return elbos


@_dispatch
def _kl(q: AbstractMultiOutputDistribution, p: AbstractMultiOutputDistribution):
    return q.kl(p)


@_dispatch
def _kl(q: Parallel, p: Parallel):
    return sum([_kl(qi, pi) for qi, pi in zip(q, p)], 0)


def predict(model, xc, yc, xt, pred_num_samples=50, num_samples=5):
    """Use a model to predict.

    Args:
        model (:class:`.Model`): Model.
        xc (tensor): Inputs of the context set.
        yc (tensor): Output of the context set.
        xt (tensor): Inputs of the target set.
        pred_num_samples (int, optional): Number of samples to use for prediction.
            Defaults to 50.
        num_samples (int, optional): Number of noiseless samples to produce. Defaults
            to 5.

    Returns:
        tensor: Marignal mean.
        tensor: Marginal variance.
        tensor: `num_samples` noiseless samples.
    """
    # Predict marginal statistics.
    pred = model(xc, yc, xt, num_samples=pred_num_samples)
    m1 = B.mean(pred.mean, axis=0)
    m2 = B.mean(pred.var + pred.mean**2, axis=0)
    mean, var = m1, m2 - m1**2

    # Produce noiseless samples.
    pred_noiseless = model(
        batch["xc"],
        batch["yc"],
        x,
        dtype_enc_sample=torch.float32,
        dtype_lik=torch.float64,
        noiseless=True,
        num_samples=num_samples,
    )
    # Try sampling with increasingly higher regularisation.
    epsilon_before = B.epsilon
    while True:
        try:
            samples = pred_noiseless.sample()
            break
        except Exception as e:
            B.epsilon *= 10
            if B.epsilon > 1e-3:
                # Reset regularisation before failing.
                B.epsilon = epsilon_before
                raise e
    B.epsilon = epsilon_before  # Reset regularisation after success.

    return mean, var, samples
