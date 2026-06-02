"""APK upload: drag-and-drop an .apk, parse its package/version/label."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Apk, User
from ..schemas import ApkOut

router = APIRouter(prefix="/api/apks", tags=["apks"])

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")


def _parse_apk(path: str) -> dict:
    """Extract package / version / label using pyaxmlparser (no Android SDK needed)."""
    import contextlib
    import io

    info = {"package": None, "version": None, "label": None}
    try:
        from pyaxmlparser import APK as AxmlAPK

        # pyaxmlparser is chatty on stdout ("res1 is not zero!"); silence it.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            a = AxmlAPK(path)
            info["package"] = a.package
            info["version"] = getattr(a, "version_name", None)
            info["label"] = getattr(a, "application", None)
    except Exception as err:  # parsing is best-effort; the file still installs at run time
        print(f"[apk] could not parse {path}: {err}", flush=True)
    return info


@router.post("", response_model=ApkOut, status_code=201)
async def upload_apk(
    file: UploadFile = File(...), db: Session = Depends(get_db), _: User = Depends(current_user)
):
    if not (file.filename or "").lower().endswith(".apk"):
        raise HTTPException(400, "Expected a .apk file")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_DIR, file.filename)
    size = 0
    with open(dest, "wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            fh.write(chunk)
    if size == 0:
        raise HTTPException(400, "Uploaded file was empty")

    info = _parse_apk(dest)
    apk = Apk(
        filename=file.filename,
        path=os.path.abspath(dest),
        package=info["package"],
        version=info["version"],
        label=info["label"],
    )
    db.add(apk)
    db.commit()
    db.refresh(apk)
    return apk


@router.get("", response_model=list[ApkOut])
def list_apks(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.query(Apk).order_by(Apk.id.desc()).all()
