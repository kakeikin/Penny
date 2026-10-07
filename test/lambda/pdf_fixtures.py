"""Build tiny valid PDFs in memory for tests (no extra dependencies)."""


def make_pdf(pages: list) -> bytes:
    """pages: list of lists of text lines; an empty list makes a page with no text (like a scan).

    Text must be Latin-1 (Helvetica, no embedded font); characters like '€' raise UnicodeEncodeError.
    """
    objects = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = ' '.join(f'{3 + 2 * i} 0 R' for i in range(n_pages))
    objects.append('<< /Type /Catalog /Pages 2 0 R >>')
    objects.append(f'<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>')
    for i, lines in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            f'/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>'
        )
        ops = ['BT', '/F1 10 Tf', '14 TL', '50 750 Td']
        for line in lines:
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            ops.append(f'({escaped}) Tj T*')
        ops.append('ET')
        stream = '\n'.join(ops)
        objects.append(f'<< /Length {len(stream)} >>\nstream\n{stream}\nendstream')
    objects.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')

    out = '%PDF-1.4\n'
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out.encode('latin-1')))
        out += f'{num} 0 obj\n{body}\nendobj\n'
    xref_at = len(out.encode('latin-1'))
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'
    out += ''.join(f'{o:010d} 00000 n \n' for o in offsets)
    out += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n'
    return out.encode('latin-1')
