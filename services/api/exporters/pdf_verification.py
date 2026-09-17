"""Inspect final bytes before publication, rather than trusting renderer settings."""
from io import BytesIO

from pypdf import PdfReader
from pypdf.generic import ContentStream


def verify_review_pdf(data, scene, pages, bleed_mm, *, structure_snapshot=None):
    from .review_pdf import ExportValidationError
    from .production import _decode_barcodes
    import pypdfium2 as pdfium

    reader = PdfReader(BytesIO(data), strict=True)
    if len(reader.pages) != len(pages):
        raise ExportValidationError("PDF_PAGE_COUNT", "최종 PDF의 페이지 수가 일치하지 않습니다.")
    checks, fonts = [], set()
    for page, expected in zip(reader.pages, pages):
        is_face = expected["face_id"] != "net"
        bleed = bleed_mm if is_face else 0
        w, h = expected["width_mm"], expected["height_mm"]
        boxes = {"MediaBox": (0, 0, w+2*bleed, h+2*bleed), "TrimBox": (bleed, bleed, w+bleed, h+bleed),
                 "BleedBox": (0, 0, w+2*bleed, h+2*bleed)}
        measured = {}
        for name, target in boxes.items():
            actual = [float(v)*25.4/72 for v in page["/"+name]]
            if max(abs(a-b) for a, b in zip(actual, target)) > .01:
                raise ExportValidationError("PDF_PHYSICAL_BOX_MISMATCH", "최종 PDF의 재단·도련 치수가 0.01mm 허용 오차를 벗어났습니다.")
            measured[name] = actual
        resources = page["/Resources"]["/Font"]
        active_font = None
        for operands, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"Tf": active_font = operands[0]
            if operator not in {b"Tj", b"TJ", b"'", b'"'}: continue
            if active_font is None:
                raise ExportValidationError("PDF_FONT_UNVERIFIED", "최종 PDF 글꼴 사용 정보를 확인할 수 없습니다.")
            font = resources[active_font].get_object()
            descendant = font.get("/DescendantFonts")
            descriptor = (descendant[0].get_object() if descendant else font).get("/FontDescriptor")
            descriptor = descriptor.get_object() if descriptor else {}
            streams = [descriptor[key].get_object() for key in ("/FontFile", "/FontFile2", "/FontFile3") if key in descriptor]
            if not streams or not all(stream.get_data() for stream in streams):
                raise ExportValidationError("PDF_FONT_NOT_EMBEDDED", "최종 PDF에서 실제 사용 글꼴 임베드가 확인되지 않았습니다.")
            fonts.add(str(font["/BaseFont"]))
        checks.append({"face_id": expected["face_id"], "role": "face_review" if is_face else "assembly_reference",
                       "boxes_mm": measured, "tolerance_mm": .01, "passed": True})
    document = pdfium.PdfDocument(data)
    try: barcodes = _decode_barcodes(document, scene, bleed_mm=bleed_mm, structure_snapshot=structure_snapshot)
    finally: document.close()
    return {"page_boxes": checks, "used_fonts_embedded": sorted(fonts), "barcode_checks": barcodes,
            "pdf_version": reader.pdf_header, "manufacturer_approval": False}
