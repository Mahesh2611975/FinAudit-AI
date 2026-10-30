"""Minimal dependency-free text PDF writer for audit report exports."""
from __future__ import annotations

import textwrap


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _ascii(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def build_text_pdf(title: str, lines: list[str]) -> bytes:
    wrapped: list[str] = []
    for line in [title, ""] + list(lines):
        parts = textwrap.wrap(_ascii(str(line)), width=95, replace_whitespace=False) or [""]
        wrapped.extend(parts)
    pages = [wrapped[index : index + 50] for index in range(0, len(wrapped), 50)] or [[""]]

    objects: dict[int, bytes] = {}
    page_ids: list[int] = []
    next_id = 4
    for _ in pages:
        page_ids.append(next_id)
        next_id += 2

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for page_id, page_lines in zip(page_ids, pages):
        content_id = page_id + 1
        commands = ["BT", "/F1 11 Tf", "50 792 Td", "14 TL"]
        for line in page_lines:
            commands.append(f"({_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", "replace")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("latin-1")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("latin-1"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    max_id = max(objects)
    output.extend(f"xref\n0 {max_id + 1}\n".encode("latin-1"))
    output.extend(b"0000000000 65535 f \n")
    for object_id in range(1, max_id + 1):
        output.extend(f"{offsets.get(object_id, 0):010d} 00000 n \n".encode("latin-1"))
    output.extend(
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    )
    return bytes(output)
