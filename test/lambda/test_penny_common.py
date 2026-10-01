from penny_common.masking import mask_identifiers
from penny_common.textnorm import normalize, contains_normalized


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