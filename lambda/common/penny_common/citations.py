"""Deterministic citation checks for advisor answers (no LLM involved)."""
import re

REF = re.compile(r'\[([DST]\d+)\]')
# One bracket may hold several refs ("[T1, S2]") and the model may use lowercase ("[t1]").
_REF_GROUP = re.compile(r'[ \t]?\[\s*([DST]\d+(?:\s*,\s*[DST]\d+)*)\s*\]', re.I)
_EMPTY_PARENS = re.compile(r'[ \t]?\(\s*\)')

_SYMBOL = r'[$¥€£￥]'
_CODE = r'(?i:USD|CNY|RMB|EUR|GBP|JPY)'
_NUM = r'(?<![\d,.])-?\d[\d,]*(?:\.\d+)?'           # lookbehind keeps matching linear-time
# Money: a currency symbol/code/word next to a number, two decimal places, or accounting
# parentheses. Bare integers ("3 transactions", "2026") and comma-grouped integers
# ("1,234") are deliberately not money; neither are percentages or multipliers ("12.50%").
_MONEY = re.compile(
    rf'(?:{_SYMBOL}\s?-?\d[\d,]*(?:\.\d+)?)'
    rf'|(?:\b{_CODE}\s?-?\d[\d,]*(?:\.\d+)?)'
    rf'|(?:{_NUM}\s?(?:{_CODE}\b|元|(?i:yuan|dollars?)\b))'
    rf'|(?:\(\s?(?:{_SYMBOL}\d[\d,]*(?:\.\d{{2}})?|\d[\d,]*\.\d{{2}})\s?\))'
    r'|(?<![\d,.])-?\d[\d,]*\.\d{2}(?![\d%x.])'
)


def contains_money(text: str) -> bool:
    return bool(_MONEY.search(text or ''))


def validate_citations(answer: str, results: dict) -> dict:
    """Keep only refs that a tool actually returned in this request.

    results: {ref: item} for every tool result item. Returns
    {answer, citations, invalidCitations, evidenceStatus}. Kept refs are normalized to
    "[T1][S2]"; an all-invalid bracket is removed together with one leading space or tab.
    """
    invalid = 0

    def keep(match):
        nonlocal invalid
        refs = [r.strip().upper() for r in match.group(1).split(',')]
        valid = [r for r in refs if r in results]
        invalid += len(refs) - len(valid)
        lead = match.group(0)[0] if match.group(0)[0] in ' \t' else ''
        if valid:
            return lead + ''.join(f'[{r}]' for r in valid)
        after = match.string[match.end():match.end() + 1]
        return ' ' if lead and after[:1].isalnum() else ''    # don't glue "word [T9]word"

    cleaned = _REF_GROUP.sub(keep, answer or '')
    if invalid:
        cleaned = _EMPTY_PARENS.sub('', cleaned)              # "([T9])" leaves "()"
    cited, citations = set(), []
    for ref in REF.findall(cleaned):
        if ref not in cited:
            cited.add(ref)
            citations.append(results[ref])
    status = 'unsupported' if contains_money(cleaned) and not citations else 'supported'
    return {'answer': cleaned.strip(), 'citations': citations,
            'invalidCitations': invalid, 'evidenceStatus': status}
