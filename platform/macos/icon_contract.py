#!/usr/bin/env python3
"""Pure PNG/ICNS structural validation shared by the macOS tooling."""

import hashlib
import struct
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CANONICAL_PNG_SHA256 = (
    "0492cd1a9fca0f6e658910c85a21ea854f6a8494dc67b6f95998cd91f953f3a5"
)
FOCUS_ICNS_SHA256 = (
    "326ded57eec25c32ba405d3a9246fd80c1ab5cfc5a71735afc542df3594f4948"
)
FOCUS_ICNS_DIMENSIONS = {
    (32, 32),
    (64, 64),
    (128, 128),
    (256, 256),
    (512, 512),
    (1024, 1024),
}
FOCUS_ICNS_REQUIRED_CHUNKS = {
    "ic04",
    "ic05",
    "ic07",
    "ic08",
    "ic09",
    "ic10",
    "ic11",
    "ic12",
    "ic13",
    "ic14",
}


class IconContractError(ValueError):
    """Raised when an icon container does not satisfy its binary contract."""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_png(path):
    """Validate a PNG header and return dimensions/colour metadata."""
    data = Path(path).read_bytes()
    if len(data) < 33 or data[:8] != PNG_SIGNATURE:
        raise IconContractError("not a valid PNG: {}".format(path))
    ihdr_length = struct.unpack(">I", data[8:12])[0]
    if ihdr_length != 13 or data[12:16] != b"IHDR":
        raise IconContractError("PNG must begin with a 13-byte IHDR: {}".format(path))
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    if width <= 0 or height <= 0:
        raise IconContractError("PNG dimensions must be positive: {}".format(path))
    return {
        "path": str(Path(path).resolve()),
        "sha256": sha256_file(path),
        "bytes": len(data),
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
    }


def inspect_icns(path):
    """Parse an ICNS container without invoking system image tools."""
    icon_path = Path(path)
    data = icon_path.read_bytes()
    if len(data) < 8 or data[:4] != b"icns":
        raise IconContractError("not a valid ICNS container: {}".format(icon_path))
    declared_length = struct.unpack(">I", data[4:8])[0]
    if declared_length != len(data):
        raise IconContractError(
            "ICNS length mismatch for {}: header {}, file {}".format(
                icon_path, declared_length, len(data)
            )
        )

    offset = 8
    chunks = []
    png_dimensions = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise IconContractError("truncated ICNS chunk header: {}".format(icon_path))
        chunk_type_bytes = data[offset : offset + 4]
        try:
            chunk_type = chunk_type_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise IconContractError("non-ASCII ICNS chunk type") from exc
        chunk_length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        if chunk_length < 8 or offset + chunk_length > len(data):
            raise IconContractError(
                "invalid ICNS chunk length for {}: {}".format(chunk_type, chunk_length)
            )
        payload = data[offset + 8 : offset + chunk_length]
        dimensions = None
        if payload.startswith(PNG_SIGNATURE):
            if len(payload) < 26 or payload[12:16] != b"IHDR":
                raise IconContractError("malformed PNG payload in {}".format(chunk_type))
            dimensions = struct.unpack(">II", payload[16:24])
            png_dimensions.append(dimensions)
        chunks.append(
            {
                "type": chunk_type,
                "length": chunk_length,
                "png_dimensions": list(dimensions) if dimensions else None,
            }
        )
        offset += chunk_length

    if offset != len(data) or not chunks:
        raise IconContractError("ICNS has no complete image chunks: {}".format(icon_path))
    return {
        "path": str(icon_path.resolve()),
        "sha256": sha256_file(icon_path),
        "bytes": len(data),
        "chunk_types": [chunk["type"] for chunk in chunks],
        "chunks": chunks,
        "png_dimensions": sorted({tuple(value) for value in png_dimensions}),
    }


def validate_focus_icns(path):
    """Validate the exact generated Focus Browser ICNS contract."""
    report = inspect_icns(path)
    if report["sha256"] != FOCUS_ICNS_SHA256:
        raise IconContractError(
            "Focus Browser ICNS SHA-256 changed: expected {}, got {}".format(
                FOCUS_ICNS_SHA256, report["sha256"]
            )
        )
    if set(report["png_dimensions"]) != FOCUS_ICNS_DIMENSIONS:
        raise IconContractError("Focus Browser ICNS embedded dimensions changed")
    if not FOCUS_ICNS_REQUIRED_CHUNKS.issubset(report["chunk_types"]):
        raise IconContractError("Focus Browser ICNS is missing required icon chunks")
    return report
