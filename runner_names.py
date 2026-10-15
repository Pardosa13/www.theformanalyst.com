"""One way to match a runner from an outside feed to our horses.

Used by the results job (PuntingForm), the odds poller (Ladbrokes) and the
Betfair BSP import, so a horse that matches in one matches in all of them.

Rules, in order:
  1. TAB number, when the feed has one and the names agree (same cleaned name,
     one a prefix of the other, or very similar). A TAB match with a clearly
     different name is refused: TAB numbers can be reassigned in fields.
  2. Cleaned name.
Anything else is a miss, and callers log it.

Cleaning: lower case, a trailing country suffix such as "(NZ)" removed,
apostrophes dropped (Betfair writes "Millys", others "Milly's"), every other
non-alphanumeric run turned into one space.
"""
import difflib
import re

COUNTRY_SUFFIX = re.compile(r"\((nz|aus|ire|gb|usa|fr|ger|jpn|saf|arg|chi|brz|ity|hk)\)\s*$")
FUZZY_NAME_RATIO = 0.8


def clean_name(name):
    """'Milly's Clever Too (NZ)' -> 'millys clever too'."""
    s = str(name or '').lower().strip()
    s = COUNTRY_SUFFIX.sub('', s)
    s = re.sub(r"[’'`]", '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return s.strip()


def same_horse(a, b):
    """Two cleaned names that are clearly the same horse."""
    if not a or not b:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_NAME_RATIO


def to_tab(value):
    try:
        tab = int(float(value))
    except (TypeError, ValueError):
        return None
    return tab if tab > 0 else None


class FieldMatcher:
    """Our runners in one race, ready to match feed runners against.

    `runners` is an iterable of objects or dicts with horse_id, name and tab.
    match() returns (runner, method) with method 'tab' or 'name', or (None, None).
    """

    def __init__(self, runners):
        self.runners = list(runners)
        self.by_tab = {}
        self.by_name = {}
        for runner in self.runners:
            tab = to_tab(_get(runner, 'tab'))
            if tab is not None:
                self.by_tab.setdefault(tab, runner)
            self.by_name.setdefault(clean_name(_get(runner, 'name')), runner)

    def match(self, name, tab=None):
        cleaned = clean_name(name)
        tab = to_tab(tab)
        if tab is not None and tab in self.by_tab:
            candidate = self.by_tab[tab]
            if not cleaned or same_horse(cleaned, clean_name(_get(candidate, 'name'))):
                return candidate, 'tab'
        candidate = self.by_name.get(cleaned) if cleaned else None
        if candidate is not None:
            return candidate, 'name'
        return None, None


def _get(runner, key):
    return runner.get(key) if isinstance(runner, dict) else getattr(runner, key, None)
