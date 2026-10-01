import io

import pytest
from pypdf import PageObject, PdfReader, PdfWriter

from penny_common.masking import mask_identifiers
from penny_common.pdftext import SCANNED_MIN_CHARS, PdfTextError, extract_pdf_pages
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
