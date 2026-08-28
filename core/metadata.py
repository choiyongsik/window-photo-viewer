"""Rating/label persistence.

JPEG  → embedded XMP packet (Xmp.xmp.Rating / Xmp.xmp.Label). Lightroom Classic reads this.
        Written via pyexiv2; READ by parsing the APP1 segment straight out of the file
        header (tens of KB, never the whole file) so root-wide collections stay cheap.
PNG   → XMP in an iTXt chunk, read and written via pyexiv2 (whole file).
Video → sidecar `<stem>.xmp` written by us (exiv2 cannot write MP4/MOV). Viewer-internal.
"""
from __future__ import annotations

import os
from pathlib import Path

import pyexiv2
from defusedxml import ElementTree as ET  # stdlib ET is vulnerable to XXE / entity expansion
from PIL import Image

from core.models import ExifSummary, Label, MediaItem, MediaKind
from core.rating_cache import RatingCache

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


_JPEG_EXTENSIONS = frozenset({".jpg", ".jpeg"})
_XMP_APP1_SIGNATURE = b"http://ns.adobe.com/xap/1.0/\x00"
_SOI, _APP1, _SOS = 0xD8, 0xE1, 0xDA


def _read_jpeg(path: Path) -> tuple[int, Label]:
    """JPEG: parse the XMP packet straight out of the file header (reads only the
    metadata segments before the pixel data — tens of KB, not the whole file). A
    JPEG with no XMP packet is simply unrated; that verdict is final and never
    re-checked with pyexiv2, because the whole-file read that would take is exactly
    what this path exists to avoid. Anything that isn't a JPEG (PNG), or a header
    that can't be parsed, falls back to pyexiv2 over the whole file."""
    if path.suffix.lower() in _JPEG_EXTENSIONS:
        try:
            packet = _read_jpeg_xmp_packet(path)
            if packet is None:
                return 0, Label.NONE
            return _parse_xmp_packet(packet)
        except Exception:
            pass   # odd header or XML: let pyexiv2 have a go at the whole file
    return _read_with_pyexiv2(path)


def _read_jpeg_xmp_packet(path: Path) -> bytes | None:
    """The raw XMP packet from the JPEG's APP1 segment, or None if there is none
    before the image data starts. Raises on a malformed header."""
    with open(path, "rb") as f:
        if f.read(2) != bytes([0xFF, _SOI]):
            raise ValueError("not a JPEG")
        while True:
            marker = f.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                raise ValueError("bad JPEG marker")
            kind = marker[1]
            if kind == _SOS:
                return None   # pixel data from here on; XMP would have come first
            if kind == 0xFF or 0xD0 <= kind <= 0xD9:
                continue      # fill byte / standalone marker: no length field
            length_bytes = f.read(2)
            if len(length_bytes) < 2:
                raise ValueError("truncated segment length")
            length = int.from_bytes(length_bytes, "big") - 2
            if length < 0:
                raise ValueError("bad segment length")
            if kind == _APP1:
                payload = f.read(length)
                if len(payload) < length:
                    raise ValueError("truncated APP1 segment")
                if payload.startswith(_XMP_APP1_SIGNATURE):
                    return payload[len(_XMP_APP1_SIGNATURE):]
            else:
                f.seek(length, os.SEEK_CUR)


def _read_with_pyexiv2(path: Path) -> tuple[int, Label]:
    with pyexiv2.ImageData(path.read_bytes()) as img:
        xmp = img.read_xmp()
    return _parse_rating(xmp.get(XMP_RATING)), Label.from_xmp(xmp.get(XMP_LABEL))


def _read_sidecar(sc: Path) -> tuple[int, Label]:
    if not sc.exists():
        return 0, Label.NONE
    return _parse_xmp_packet(sc.read_bytes())


def _parse_xmp_packet(data: bytes) -> tuple[int, Label]:
    """Rating/label out of an XMP packet (embedded or sidecar). Both the attribute
    form Lightroom writes (xmp:Rating="3") and the element form are accepted."""
    root = ET.fromstring(data)
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
    with pyexiv2.ImageData(path.read_bytes()) as img:
        current = img.read_xmp()
        # Only a key that is both marked "" AND actually present needs deleting.
        # ("rate 3, no label" on a photo with no existing color label is the
        # overwhelmingly common case — there is nothing to delete there.)
        to_delete = {k for k, v in updates.items() if v == "" and k in current}
        to_set = {k: v for k, v in updates.items() if v != ""}
        if to_delete:
            # This pyexiv2 build does not treat modify_xmp({key: ""}) as a
            # delete (it just stores an empty string). Work around it by
            # rebuilding the XMP packet without the deleted keys. This is
            # destructive (clear_xmp() wipes every XMP key, not just ours)
            # so it is only taken when a tag we own must actually be removed
            # — the common no-op-delete write stays on the safe merge path
            # below and never touches unrelated tags (keywords, develop
            # history, custom namespaces, ...).
            remaining = {k: v for k, v in current.items() if k not in to_delete}
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


def read_rating_label_cached(
    path: Path, kind: MediaKind, mtime: float, size: int, cache: RatingCache | None, *, refresh: bool = False
) -> tuple[int, Label]:
    """read_rating_label through *cache*: a hit for this (path, mtime, size) is
    trusted without opening the file unless *refresh*; a miss (or a refresh) reads
    the file and records the answer -- unrated files included."""
    if cache is not None and not refresh:
        hit = cache.lookup(path, mtime, size)
        if hit is not None:
            return hit
    rating, label = read_rating_label(path, kind)
    if cache is not None:
        cache.store(path, mtime, size, rating, label)
    return rating, label


def populate(item: MediaItem, cache: RatingCache | None = None, *, refresh: bool = False) -> None:
    item.rating, item.label = read_rating_label_cached(item.path, item.kind, item.mtime, item.size, cache, refresh=refresh)
    item.exif = read_exif_summary(item.path) if item.kind is MediaKind.IMAGE else None
