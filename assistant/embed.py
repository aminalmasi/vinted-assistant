"""Stage 1: SigLIP2 similarity between a watch's photos and new listings.

SigLIP2 SO400M-384 is the model chosen on this exact domain: it led every
alternative (CLIP-L, CLIP-H, SigLIP1, DINOv2) on the verified luxury-shoe
retrieval benchmark. It runs on CPU here - about 20 s to load and 4 s per
image - which is plenty for a few new listings per tick and keeps this off the
GPU queue entirely.

Loaded lazily: a tick with no new listings never pays the 20 s.
"""

from __future__ import annotations

import os

import torch
from PIL import Image

from assistant.config import SIGLIP

_model = _proc = None


def _load():
    global _model, _proc
    if _model is None:
        from transformers import AutoModel, AutoProcessor
        torch.set_num_threads(int(os.environ.get("VA_THREADS", "4")))
        _model = AutoModel.from_pretrained(SIGLIP, dtype=torch.float32).eval()
        _proc = AutoProcessor.from_pretrained(SIGLIP)
    return _model, _proc


def embed(paths: list[str]) -> torch.Tensor:
    """L2-normalised image embeddings, one row per path."""
    model, proc = _load()
    ims = [Image.open(p).convert("RGB") for p in paths]
    with torch.no_grad():
        out = model.get_image_features(**proc(images=ims, return_tensors="pt"))
    # transformers 5.x returns a ModelOutput here rather than a bare tensor;
    # this exact difference has silently broken embedding scripts before.
    if not torch.is_tensor(out):
        out = out.pooler_output if getattr(out, "pooler_output", None) is not None else out[0]
    return torch.nn.functional.normalize(out.float(), dim=-1)


def scores(query: torch.Tensor, cands: torch.Tensor, mode: str,
           fav_index: int | None) -> list[float]:
    """One similarity per candidate against the watch's photos.

    exact:   the best match against any query photo - the shoe only has to
             look like one of your reference shots.
    similar: half the favourite photo, half the best of the rest, so the one
             you said you like most pulls hardest.
    """
    sim = cands @ query.T                      # [n_cand, n_query]
    best = sim.max(dim=1).values
    if mode == "similar" and fav_index is not None and query.shape[0] > 1:
        return (0.5 * sim[:, fav_index] + 0.5 * best).tolist()
    return best.tolist()
