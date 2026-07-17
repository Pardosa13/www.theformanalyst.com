"""
mma_name_utils.py
==================
Single canonical fighter-name normalisation/matching implementation, shared by
mma_sync.py (ESPN scraping + prediction pipeline) and mma_data.py (Odds API
ingestion + edge-finder).

Both modules used to keep independent copies of this logic with slightly
different alias tables. Drift between the two copies silently breaks
odds-to-fighter matching (a fighter's odds simply fail to match their fight
card row, with no error raised), so this is the one place it should live.
"""

from __future__ import annotations

import re
import unicodedata

# Known cases where ESPN/Odds-API display names for the same fighter diverge
# beyond what normalisation + suffix-stripping can reconcile automatically.
_NAME_ALIASES = {
    'king green': 'bobby green',
    'robert green': 'bobby green',
    'zach reese': 'zachary reese',
    'zachary reese': 'zachary reese',
}
_SUFFIX_TOKENS = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}


def normalize_name(name) -> str:
    """Lowercase, strip accents/punctuation/suffixes and collapse known aliases."""
    if not name:
        return ''
    name = str(name).replace('’', "'").replace('`', "'")
    nfkd = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    name = name.lower().replace('-', ' ')
    name = re.sub(r"[^a-zA-Z0-9\s]", '', name)
    name = name.replace(' saint ', ' st ').replace(' saint', ' st').replace('saint ', 'st ')
    norm = ' '.join(name.split())
    return _NAME_ALIASES.get(norm, norm)


# Backwards-compatible alias used by mma_data.py callers.
normalise_name = normalize_name


def normalized_name_aliases(name) -> set[str]:
    """Safe aliases for matching ESPN/Odds API display names to DB rows."""
    norm = normalize_name(name)
    aliases = {norm} if norm else set()
    parts = norm.split()
    if parts and parts[-1] in _SUFFIX_TOKENS:
        aliases.add(' '.join(parts[:-1]))
    if norm.replace(' ', '') == 'loneerkavanagh':
        aliases.update({'loneer kavanagh', 'lone er kavanagh'})
    if norm == 'benoit st denis':
        aliases.update({'benoit saint denis', 'benoit saintdenis', 'benoit st denis'})
    return {a for a in aliases if a}


# Backwards-compatible alias used by mma_data.py callers.
name_aliases = normalized_name_aliases


def names_match(a, b) -> bool:
    """True when two fighter names refer to the same person using safe aliases."""
    aa = normalized_name_aliases(a)
    bb = normalized_name_aliases(b)
    if aa & bb:
        return True
    for x in aa:
        for y in bb:
            if x and y and (x in y or y in x):
                return True
    return False


def unordered_pair_key(a, b) -> str:
    ca = next(iter(sorted(normalized_name_aliases(a))), normalize_name(a))
    cb = next(iter(sorted(normalized_name_aliases(b))), normalize_name(b))
    return '|'.join(sorted([ca, cb]))


def pairs_match(a1, a2, b1, b2) -> bool:
    """True when (a1,a2) and (b1,b2) name the same fight, in either order."""
    return ((names_match(a1, b1) and names_match(a2, b2)) or
            (names_match(a1, b2) and names_match(a2, b1)))
