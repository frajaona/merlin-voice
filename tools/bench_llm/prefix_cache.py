"""Snapshot correct du préfixe (système + outils) pour un modèle hybride
(couches linéaires non trimmables) : prefill une fois, copie profonde par tour."""
import copy
import mlx.core as mx


def _dup(x):
    if isinstance(x, mx.array):
        return mx.array(x) * 1  # vraie copie : KVCache écrit en place
    if isinstance(x, list):
        return [_dup(v) for v in x]
    if isinstance(x, tuple):
        return tuple(_dup(v) for v in x)
    return x


def clone_cache(cache):
    out = []
    for c in cache:
        n = copy.copy(c)
        for k, v in vars(c).items():
            setattr(n, k, _dup(v))
        out.append(n)
    mx.eval([a for n in out for a in vars(n).values() if isinstance(a, mx.array)])
    return out


def common_prefix(a, b):
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[:i]


def build_snapshot(model, prefix_ids):
    from mlx_vlm.models.cache import make_prompt_cache
    lm = model.language_model
    cache = make_prompt_cache(lm)
    lm(mx.array([prefix_ids]), cache=cache)
    mx.eval([a for c in cache for a in vars(c).values() if isinstance(a, mx.array)])
    return cache
