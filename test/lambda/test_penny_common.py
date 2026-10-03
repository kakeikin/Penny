import io

import pytest
from pypdf import PageObject, PdfReader, PdfWriter

from penny_common.chunking import (MAX_CHARS, MAX_SINGLE_CHUNK, chunk_document, chunk_page,
                                   detect_day_first, infer_year_month, vector_key)
from penny_common.masking import mask_identifiers
from penny_common.pdftext import SCANNED_MIN_CHARS, PdfTextError, extract_pdf_pages
from penny_common.textdoc import display_name, build_text_doc, doc_id_for, text_doc_key
from penny_common.textnorm import normalize, contains_normalized
from pdf_fixtures import make_pdf


def test_mask_card_keeps_last_four():
    assert mask_identifiers('Card 1234567890123456 ok') == 'Card ****3456 ok'


def test_mask_eight_digits_before_sentence_period():
    assert mask_identifiers('Acct 12345678.') == 'Acct ****5678.'


def test_mask_leaves_short_numbers_dates_and_amounts():
    for s in ['1234567', '2026-03-14', '03/14/2026', '1234.56', '1,234.56', '-120.00', '12345678.90']:
        assert mask_identifiers(s) == s, s


def test_normalize_collapses_whitespace_and_case():
    assert normalize('  03/14  ABC\n Utilities ') == '03/14 abc utilities'


def test_contains_normalized():
    assert contains_normalized('x\n03/14   ABC UTILITIES  -120.00\ny', '03/14 abc utilities -120.00')
    assert not contains_normalized('03/14 ABC -120.00', '03/14 ABC -121.00')
    assert not contains_normalized('anything', '   ')


def test_mask_exactly_eight_and_multiple_ids():
    assert mask_identifiers('12345678') == '****5678'
    assert mask_identifiers('a 1111222233334444 b 99998888') == 'a ****4444 b ****8888'


def test_mask_ids_next_to_letters_and_suffixes():
    assert mask_identifiers('ACCT12345678') == 'ACCT****5678'
    assert mask_identifiers('acct 12345678-9') == 'acct ****5678-9'   # check-digit suffix must not leak
    assert mask_identifiers('-12345678') == '-****5678'


def test_mask_compact_dates_are_masked_by_design():
    assert mask_identifiers('Ref 20260314') == 'Ref ****0314'


def test_mask_known_gap_spaced_and_dashed_cards_unmasked():
    for s in ['4111 1111 1111 1111', '4111-1111-1111-1111']:
        assert mask_identifiers(s) == s


def test_mask_idempotent_and_empty():
    once = mask_identifiers('Card 1234567890123456')
    assert mask_identifiers(once) == once
    assert mask_identifiers('') == '' and mask_identifiers(None) == ''


def test_contains_normalized_unicode():
    assert contains_normalized('ﬁnance charge', 'finance CHARGE')     # 'ﬁ' ligature
    assert contains_normalized('caf\u00e9 latte', 'cafe\u0301 latte')     # decomposed vs composed é
    assert contains_normalized('ABC\u200bUTILITIES', 'abcutilities')     # zero-width space
    assert normalize(None) == ''


def test_extract_pdf_pages_text_and_scanned():
    data = make_pdf([['03/14 ABC UTILITIES -120.00', '03/15 COFFEE SHOP -4.50'], []])
    pages = extract_pdf_pages(data)
    assert [p['page'] for p in pages] == [1, 2]
    assert pages[0]['extractor'] == 'pypdf'
    assert contains_normalized(pages[0]['text'], '03/14 ABC UTILITIES -120.00')
    assert pages[1] == {'page': 2, 'text': '', 'extractor': 'claude'}


def test_extract_pdf_pages_scanned_threshold_boundary():
    below = 'x' * (SCANNED_MIN_CHARS - 1)
    at = 'x' * SCANNED_MIN_CHARS
    pages = extract_pdf_pages(make_pdf([[below], [at], ['   ']]))
    assert [p['extractor'] for p in pages] == ['claude', 'pypdf', 'claude']


def test_extract_pdf_pages_bad_page_falls_back_to_claude(monkeypatch):
    original = PageObject.extract_text
    calls = {'n': 0}

    def flaky(self, *args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise KeyError('/Font')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, 'extract_text', flaky)
    line = '03/14 ABC UTILITIES -120.00'
    pages = extract_pdf_pages(make_pdf([[line], [line]]))
    assert [p['extractor'] for p in pages] == ['pypdf', 'claude']


@pytest.mark.parametrize('data', [b'', b'not a pdf at all', make_pdf([['x' * 40]])[:60]])
def test_extract_pdf_pages_malformed_raises_typed_error(data):
    with pytest.raises(PdfTextError) as exc:
        extract_pdf_pages(data)
    assert exc.value.reason == 'malformed'


def test_extract_pdf_pages_password_protected_raises_encrypted():
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([['x' * 40]]))))
    writer.encrypt(user_password='secret', owner_password='owner', algorithm='RC4-128')
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(PdfTextError) as exc:
        extract_pdf_pages(buf.getvalue())
    assert exc.value.reason == 'encrypted'


def test_chunk_page_short_drops_blank_lines():
    assert chunk_page('a\n\nb\n') == ['a\nb']


def test_chunk_page_overlap_and_line_integrity():
    lines = [f'{i:02d} ' + 'x' * 96 for i in range(30)]
    chunks = chunk_page('\n'.join(lines), max_chars=1000, overlap=2)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 1000
        for line in c.split('\n'):
            assert line in lines          # a transaction line is never split
    for a, b in zip(chunks, chunks[1:]):
        assert a.split('\n')[-2:] == b.split('\n')[:2]   # 2-line overlap
    assert set(l for c in chunks for l in c.split('\n')) == set(lines)


def test_chunk_page_huge_lines_do_not_duplicate_via_overlap():
    lines = ['a' * 600, 'b' * 600, 'c' * 600]
    assert chunk_page('\n'.join(lines), max_chars=1000, overlap=2) == lines


def test_chunk_page_deterministic():
    t = '\n'.join(f'line {i}' for i in range(500))
    assert chunk_page(t) == chunk_page(t)


PERIOD = {'start': '2026-03-15', 'end': '2026-04-14'}


def test_year_month_majority_of_transaction_dates():
    assert infer_year_month('03/16 A\n03/20 B\n04/01 C', PERIOD, '2026-04-20T00:00:00Z') == '2026-03'


def test_year_month_tie_picks_earliest():
    assert infer_year_month('03/30 A\n04/02 B', PERIOD, '2026-04-20T00:00:00Z') == '2026-03'


def test_year_month_infers_year_across_december():
    period = {'start': '2025-12-15', 'end': '2026-01-14'}
    assert infer_year_month('12/20 X\n12/22 Y', period, '2026-01-20T00:00:00Z') == '2025-12'


def test_year_month_iso_and_full_us_dates():
    assert infer_year_month('2026-05-01 coffee', None, '2026-06-01T00:00:00Z') == '2026-05'
    assert infer_year_month('07/04/2025 fireworks', None, '2026-06-01T00:00:00Z') == '2025-07'


def test_year_month_falls_back_to_statement_period_end():
    assert infer_year_month('Summary page, totals only', PERIOD, '2026-04-20T00:00:00Z') == '2026-04'


def test_year_month_falls_back_to_upload_month():
    assert infer_year_month('no dates', None, '2026-03-02T10:00:00Z') == '2026-03'


def test_year_month_ignores_amounts():
    assert infer_year_month('Total 1,234.56 balance 120.00', None, '2026-03-02T10:00:00Z') == '2026-03'


def test_chunk_document_header_mask_and_key():
    doc = {'docId': 'abc', 'docType': 'bank_statement', 'fileName': 'mar.pdf',
           'uploadedAt': '2026-04-20T00:00:00Z', 'statementPeriod': PERIOD,
           'pages': [{'page': 1, 'text': 'Acct 1234567890\n03/16 A -1.00', 'extractor': 'pypdf'},
                     {'page': 2, 'text': '', 'extractor': 'claude'}]}
    recs = chunk_document(doc)
    assert len(recs) == 1                       # empty page produces no chunk
    r = recs[0]
    assert r['key'] == vector_key('abc', 1, 0) == 'abc#p1#c0'
    assert r['yearMonth'] == '2026-03'
    assert r['text'].startswith('[mar.pdf | p1 | 2026-03]\n')
    assert '****7890' in r['text'] and '1234567890' not in r['text']


def test_chunk_document_receipt_is_one_chunk():
    doc = {'docId': 'r1', 'docType': 'receipt', 'fileName': 'r.jpg', 'uploadedAt': '2026-04-20T00:00:00Z',
           'statementPeriod': None, 'pages': [{'page': 1, 'text': '\n'.join(['item'] * 1000), 'extractor': 'claude'}]}
    assert len(chunk_document(doc)) == 1


def test_year_month_ignores_page_footers_and_fractions():
    up = '2026-04-20T00:00:00Z'
    assert infer_year_month('1/2 off\n03/16 A', PERIOD, up) == '2026-03'
    assert infer_year_month('Page 1/3', PERIOD, up) == '2026-04'          # falls back to period end
    assert infer_year_month('Card Exp 10/28', None, up) == '2026-04'       # not at line start
    assert infer_year_month('02/31 invalid day', None, up) == '2026-04'    # not a real date


def test_year_month_two_digit_year():
    assert infer_year_month('03/15/26 coffee', None, '2026-04-20T00:00:00Z') == '2026-03'


def test_year_month_ignores_implausible_full_dates():
    up = '2026-04-20T00:00:00Z'
    assert infer_year_month('Member since 2019-05-01\n12/31/1999 old', None, up) == '2026-04'
    assert infer_year_month('01/02/2027 future', None, up) == '2026-04'


def test_year_month_survives_malformed_period_and_upload():
    for period in ({'end': '2026-04'}, {'end': '2026/04/14'}, {'end': 'April 2026'}):
        assert infer_year_month('no dates', period, '2026-03-02T10:00:00Z') == '2026-03'
    assert len(infer_year_month('no dates', None, '')) == 7      # falls back to today


def test_day_first_detection_and_inference():
    uk = '01/03 Tesco\n05/03 Boots\n14/03 Pret'
    assert detect_day_first(uk) is True
    assert detect_day_first('03/01 A\n03/05 B') is False
    assert infer_year_month(uk, None, '2026-03-31T00:00:00Z', day_first=True) == '2026-03'


def test_chunk_document_applies_day_first_per_page():
    doc = {'docId': 'uk', 'docType': 'bank_statement', 'fileName': 'uk.pdf', 'uploadedAt': '2026-03-31T00:00:00Z',
           'statementPeriod': None, 'pages': [{'page': 1, 'text': '01/03 Tesco\n05/03 Boots\n14/03 Pret',
                                               'extractor': 'pypdf'}]}
    assert chunk_document(doc)[0]['yearMonth'] == '2026-03'


def test_chunk_page_wraps_a_single_oversized_line():
    chunks = chunk_page('word ' * 2000)          # ~10k chars, no newlines
    assert len(chunks) > 1 and all(len(c) <= MAX_CHARS for c in chunks)


def test_chunk_document_long_receipt_is_chunked():
    doc = {'docId': 'r2', 'docType': 'receipt', 'fileName': 'r.jpg', 'uploadedAt': '2026-04-20T00:00:00Z',
           'statementPeriod': None,
           'pages': [{'page': 1, 'text': '\n'.join(['item 1.00'] * (MAX_SINGLE_CHUNK // 5)), 'extractor': 'claude'}]}
    recs = chunk_document(doc)
    assert len(recs) > 1 and all(len(r['text']) <= MAX_CHARS + 100 for r in recs)
    assert recs[0]['key'] == 'r2#p1#c0' and recs[0]['text'].startswith('[r.jpg | p1 | 2026-04]\n')


def test_display_name_strips_uuid_and_demo_prefix():
    assert display_name('uploads/123e4567-e89b-12d3-a456-426614174000-bank-mar.pdf') == 'bank-mar.pdf'
    assert display_name('uploads/demo-abc/123e4567-e89b-12d3-a456-426614174000-r.jpg') == 'r.jpg'
    assert display_name('uploads/plain.pdf') == 'plain.pdf'
    assert display_name('uploads/q1-2026-bank-of-america-stmt.pdf') == 'q1-2026-bank-of-america-stmt.pdf'
    assert display_name('uploads/demo-a1-b2/123e4567-e89b-12d3-a456-426614174000-r.jpg') == 'r.jpg'
    assert display_name('uploads/123e4567-e89b-12d3-a456-426614174000-bank+mar.pdf') == 'bank+mar.pdf'  # no decoding here


def test_build_text_doc_shape():
    doc = build_text_doc('h1', 'uploads/123e4567-e89b-12d3-a456-426614174000-a.pdf', 'bank_statement',
                         [{'page': 1, 'text': 't', 'extractor': 'pypdf'}], [], None, '2026-03-01T00:00:00Z')
    assert text_doc_key('h1') == 'text/h1.json'
    assert doc == {'docId': 'h1', 'fileKey': 'uploads/123e4567-e89b-12d3-a456-426614174000-a.pdf',
                   'fileName': 'a.pdf', 'docType': 'bank_statement', 'uploadedAt': '2026-03-01T00:00:00Z',
                   'statementPeriod': None, 'sessionId': None,
                   'pages': [{'page': 1, 'text': 't', 'extractor': 'pypdf'}], 'entries': []}


def test_doc_id_for_namespaces_demo_sessions():
    assert doc_id_for('h1') == 'h1' and doc_id_for('h1', None) == 'h1'
    assert doc_id_for('h1', 'abc') == 'demo-abc-h1'
