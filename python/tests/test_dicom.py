"""Teste da conversão DICOM -> padrão ENIGMA com séries DICOM sintéticas
(exige dcm2niix e pydicom; pula se não houver)."""
import csv
import shutil

import numpy as np
import nibabel as nib
import pytest

pydicom = pytest.importorskip("pydicom")
pytestmark = pytest.mark.skipif(shutil.which("dcm2niix") is None, reason="dcm2niix ausente")

from enigma_pipeline import dicom as D  # noqa: E402


def write_series(folder, desc, num, shape, vox, acq, tr, te, img_type="ORIGINAL\\PRIMARY\\M\\ND",
                 patient="ANON", study_uid=None):
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid, MRImageStorage
    folder.mkdir(parents=True, exist_ok=True)
    nx, ny, nz = shape
    rng = np.random.default_rng(num)
    data = (rng.random((nz, ny, nx)) * 1000).astype(np.uint16)
    series_uid = generate_uid()
    study_uid = study_uid or generate_uid()
    for k in range(nz):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = MRImageStorage
        sop = generate_uid()
        meta.MediaStorageSOPInstanceUID = sop
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = Dataset()
        ds.file_meta = meta
        ds.SOPClassUID, ds.SOPInstanceUID = MRImageStorage, sop
        ds.Modality = "MR"
        ds.Manufacturer = "SIEMENS"
        ds.MagneticFieldStrength = 3
        ds.PatientName, ds.PatientID = patient, patient
        ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid, series_uid
        ds.SeriesNumber, ds.InstanceNumber = num, k + 1
        ds.SeriesDescription = ds.ProtocolName = desc
        ds.ImageType = img_type
        ds.MRAcquisitionType = acq
        ds.RepetitionTime, ds.EchoTime = tr, te  # ms, como no DICOM
        ds.StudyDate = ds.SeriesDate = "20260101"
        ds.StudyTime = ds.SeriesTime = "120000"
        ds.AcquisitionTime = "120000"
        ds.ImagePositionPatient = [0.0, 0.0, float(k * vox[2])]
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.PixelSpacing = [vox[1], vox[0]]
        ds.SliceThickness = vox[2]
        ds.SpacingBetweenSlices = vox[2]
        ds.Rows, ds.Columns = ny, nx
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
        ds.PixelData = data[k].tobytes()
        ds.save_as(folder / f"IM{num:03d}_{k:04d}.dcm", enforce_file_format=True)


def make_patient(root, name):
    p = root / name
    write_series(p / "s1", "localizer", 1, (32, 32, 3), (4, 4, 8), "2D", 8.0, 4.0)
    write_series(p / "s2", "t1_mprage_sag_1mm", 2, (40, 48, 36), (1, 1, 1), "3D", 2300.0, 2.9)
    write_series(p / "s3", "t2_space_sag_1mm", 3, (40, 48, 36), (1, 1, 1), "3D", 3200.0, 408.0)
    write_series(p / "s4", "t2_flair_tra", 4, (40, 48, 10), (1, 1, 5), "2D", 9000.0, 90.0)
    write_series(p / "s5", "t1_mprage_sag_1mm_MPR_cor", 5, (40, 48, 36), (1, 1, 1), "3D", 2300.0, 2.9,
                 img_type="DERIVED\\SECONDARY\\MPR")


def test_scan_and_convert(tmp_path):
    dic = tmp_path / "dicoms"
    make_patient(dic, "Paciente 01")
    make_patient(dic, "Paciente-02")
    out = tmp_path / "proj"
    inv = D.scan(dic, out)
    rows = list(csv.DictReader(open(inv, encoding="utf-8")))
    roles = {(r["subject_id"], r["SeriesDescription"]): r["role"] for r in rows}
    assert roles[("Paciente01", "t1_mprage_sag_1mm")] == "t1"
    assert roles[("Paciente01", "t2_space_sag_1mm")] == "t2"
    assert roles[("Paciente01", "t2_flair_tra")] == ""
    assert roles[("Paciente01", "localizer")] == ""
    assert roles[("Paciente01", "t1_mprage_sag_1mm_MPR_cor")] == ""
    D.convert(out)
    for s in ("Paciente01", "Paciente02"):
        t1 = out / "input" / f"{s}_t1.nii.gz"
        assert t1.exists() and (out / "input" / f"{s}_t2.nii.gz").exists()
        assert nib.load(t1).shape[:3] == (40, 48, 36)
    assert (out / "deriv").is_dir()
    # o pipeline encontra os sujeitos pela convenção de nomes
    from enigma_pipeline.common import discover_subjects
    subs = discover_subjects(out / "input")
    assert [x.id for x in subs] == ["Paciente01", "Paciente02"] and subs[0].t2 is not None


def test_convert_rejects_two_t1(tmp_path):
    dic = tmp_path / "dicoms"
    make_patient(dic, "P1")
    out = tmp_path / "proj"
    inv = D.scan(dic, out)
    rows = list(csv.DictReader(open(inv, encoding="utf-8")))
    for r in rows:
        if r["SeriesDescription"] == "t2_space_sag_1mm":
            r["role"] = "t1"
    with open(inv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(SystemExit):
        D.convert(out)
