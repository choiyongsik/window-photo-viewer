"""Rating/label persistence.

JPEG  → embedded XMP packet (Xmp.xmp.Rating / Xmp.xmp.Label) via pyexiv2. Lightroom Classic reads this.
Video → sidecar `<stem>.xmp` written by us (exiv2 cannot write MP4/MOV). Viewer-internal.
"""
from __future__ import annotations

import os
from pathlib import Path

import pyexiv2
from defusedxml import ElementTree as ET  # stdlib ET is vulnerable to XXE / entity expansion
from PIL import Image

from core.models import ExifSummary, Label, MediaItem, MediaKind

XMP_RATING = "Xmp.xmp.Rating"
XMP_LABEL = "Xmp.xmp.Label"
_XMP_NS = "http://ns.adobe.com/xap/1.0/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


class MetadataError(Exception):
    """Raised when a rating/label could not be written."""


def sidecar_path(path: Path) -> Path:
    return path.with_suffix(".xmp")


# ---------- read ----------

def read_rating_label(path: Path, kind: MediaKind) -> tuple[int, Label]:
    try:
        if kind is MediaKind.VIDEO:
            return _read_sidecar(sidecar_path(path))
        return _read_jpeg(path)
    except Exception:
        return 0, Label.NONE


def _parse_rating(value: str | None) -> int:
    if value is None:
        return 0
    try:
        r = int(float(str(value).strip()))
    except ValueError:
        return 0
    return max(-1, min(5, r))


def _read_jpeg(path: Path) -> tuple[int, Label]:
    with pyexiv2.ImageData(path.read_bytes()) as img:
        xmp = img.read_xmp()
    return _parse_rating(xmp.get(XMP_RATING)), Label.from_xmp(xmp.get(XMP_LABEL))


def _read_sidecar(sc: Path) -> tuple[int, Label]:
    if not sc.exists():
        return 0, Label.NONE
    root = ET.parse(sc).getroot()
    rating: str | None = None
    label: str | None = None
    for desc in root.iter(f"{{{_RDF_NS}}}Description"):
        rating = rating or desc.get(f"{{{_XMP_NS}}}Rating")
        label = label or desc.get(f"{{{_XMP_NS}}}Label")
        r_el = desc.find(f"{{{_XMP_NS}}}Rating")
        l_el = desc.find(f"{{{_XMP_NS}}}Label")
        if r_el is not None and r_el.text:
            rating = rating or r_el.text
        if l_el is not None and l_el.text:
            label = label or l_el.text
    return _parse_rating(rating), Label.from_xmp(label)


# ---------- write ----------

def write_rating_label(path: Path, kind: MediaKind, rating: int, label: Label) -> None:
    if not -1 <= rating <= 5:
        raise MetadataError(f"rating out of range: {rating}")
    try:
        if kind is MediaKind.VIDEO:
            _write_sidecar(sidecar_path(path), rating, label)
        else:
            _write_jpeg(path, rating, label)
    except MetadataError:
        raise
    except Exception as exc:  # pyexiv2 raises RuntimeError on bad data
        raise MetadataError(f"{path.name}: {exc}") from exc


def _write_jpeg(path: Path, rating: int, label: Label) -> None:
    updates = {
        XMP_RATING: str(rating) if rating != 0 else "",   # "" means: delete the tag
        XMP_LABEL: label.value,                          # "" means: delete the tag
    }
    to_delete = {k for k, v in updates.items() if v == ""}
    to_set = {k: v for k, v in updates.items() if v != ""}
    with pyexiv2.ImageData(path.read_bytes()) as img:
        if to_delete:
            # This pyexiv2 build does not treat modify_xmp({key: ""}) as a
            # delete (it just stores an empty string). Work around it by
            # rebuilding the XMP packet without the deleted keys.
            remaining = {k: v for k, v in img.read_xmp().items() if k not in to_delete}
            remaining.update(to_set)
            img.clear_xmp()
            if remaining:
                img.modify_xmp(remaining)
        else:
            img.modify_xmp(to_set)
        new_bytes = img.get_bytes()
    _atomic_write(path, new_bytes)


def _write_sidecar(sc: Path, rating: int, label: Label) -> None:
    attrs = ""
    if rating != 0:
        attrs += f' xmp:Rating="{rating}"'
    if label is not Label.NONE:
        attrs += f' xmp:Label="{label.value}"'
    if not attrs and not sc.exists():
        return  # nothing to record, do not litter the folder
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        f' <rdf:RDF xmlns:rdf="{_RDF_NS}">\n'
        f'  <rdf:Description rdf:about="" xmlns:xmp="{_XMP_NS}"{attrs}/>\n'
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )
    _atomic_write(sc, xml.encode("utf-8"))


def _atomic_write(target: Path, data: bytes) -> None:
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MetadataError(f"{target.name}: {exc}") from exc


# ---------- EXIF ----------

def _fmt_exposure(v) -> str | None:
    if v is None:
        return None
    f = float(v)
    if f <= 0:
        return None
    return f"{f:g}s" if f >= 1 else f"1/{round(1 / f)}"


def _fmt_fnumber(v) -> str | None:
    return None if v is None else f"f/{float(v):g}"


def _fmt_focal(v) -> str | None:
    return None if v is None else f"{float(v):g}mm"


def _to_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        v = v[0] if v else None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_exif_summary(path: Path) -> ExifSummary | None:
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            ifd = exif.get_ifd(0x8769)
            return ExifSummary(
                exposure_time=_fmt_exposure(ifd.get(0x829A)),
                f_number=_fmt_fnumber(ifd.get(0x829D)),
                iso=_to_int(ifd.get(0x8827)),
                focal_length=_fmt_focal(ifd.get(0x920A)),
                date_time_original=ifd.get(0x9003) or None,
                orientation=int(exif.get(0x0112) or 1),
            )
    except Exception:
        return None


def populate(item: MediaItem) -> None:
    item.rating, item.label = read_rating_label(item.path, item.kind)
    item.exif = read_exif_summary(item.path) if item.kind is MediaKind.IMAGE else None
