"""Testes com dados sintéticos (não exigem SCT nem SUITPy)."""
import json

import numpy as np
import nibabel as nib
import pytest

from enigma_pipeline import labels, qc
from enigma_pipeline.suit import smooth_img


def _img(data, aff=None):
    return nib.Nifti1Image(data.astype(np.float32), np.eye(4) if aff is None else aff)


def test_disc_labels_roundtrip(tmp_path):
    t1 = tmp_path / "s_t1.nii.gz"
    aff = np.diag([0.9, 0.9, 1.1, 1.0])
    nib.save(_img(np.zeros((40, 50, 60)), aff), t1)
    js = tmp_path / "disc_labels.json"
    js.write_text(json.dumps({"format": "enigma-disc-labels-v1", "subjects": {
        "s": {"shape": [40, 50, 60], "labels": [{"value": 3, "ijk": [20, 25, 30]},
                                                {"value": 4, "ijk": [20, 24, 22]}]}}}))
    out = tmp_path / "lab.nii.gz"
    labels.write_disc_labels(js, "s", t1, out)
    lab = nib.load(out)
    d = np.asarray(lab.dataobj)
    assert d[20, 25, 30] == 3 and d[20, 24, 22] == 4 and d.sum() == 7
    assert np.allclose(lab.affine, aff)


def test_disc_labels_shape_mismatch(tmp_path):
    t1 = tmp_path / "s_t1.nii.gz"
    nib.save(_img(np.zeros((40, 50, 60))), t1)
    js = tmp_path / "d.json"
    js.write_text(json.dumps({"format": "enigma-disc-labels-v1", "subjects": {
        "s": {"shape": [41, 50, 60], "labels": [{"value": 3, "ijk": [1, 1, 1]},
                                                {"value": 4, "ijk": [1, 1, 2]}]}}}))
    with pytest.raises(ValueError):
        labels.write_disc_labels(js, "s", t1, tmp_path / "o.nii.gz")


def test_covariance_flags_outlier(tmp_path):
    rng = np.random.default_rng(0)
    base = rng.random((20, 20, 10)) * (rng.random((20, 20, 10)) > 0.3)
    files = []
    for i in range(8):
        d = base + rng.normal(0, 0.05, base.shape)
        if i == 5:  # imagem "corrompida"
            d = rng.random(base.shape)
        f = tmp_path / f"wd{i}.nii.gz"
        nib.save(_img(d), f)
        files.append(f)
    res = qc.check_covariance(files)
    assert res["flagged"][5] is True
    assert sum(res["flagged"]) == 1
    corr = np.array(res["corr"])
    assert np.allclose(np.diag(corr), 1)


def test_smoothing_preserves_sum():
    d = np.zeros((31, 31, 31))
    d[15, 15, 15] = 1000.0
    out = smooth_img(_img(d, np.diag([1, 1, 1, 1])), 3.0).get_fdata()
    assert abs(out.sum() - 1000) < 1
    # FWHM medido no perfil central ≈ 3 mm
    prof = out[15, 15, :]
    half = prof.max() / 2
    above = np.where(prof >= half)[0]
    assert 2 <= (above[-1] - above[0] + 1) <= 4
