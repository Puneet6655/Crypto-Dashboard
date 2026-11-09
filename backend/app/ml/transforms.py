"""Small transforms saved inside model pipelines. They live in the app package so the API can load
the pickled models without importing the training code."""
import numpy as np


def signed_log(x):
    """log1p that keeps the sign; tames heavy-tailed BTC amounts for the linear baseline."""
    return np.sign(x) * np.log1p(np.abs(x))
