"""Review list: today's runners that fit the angles closest to break even.

Not bets. Nothing in horse racing has made money here over time. These are
the angles that came closest, for a person to check against race replays
before deciding anything.

Found by audit/tasks/closest.py on 72,884 runners (Feb-Oct 2026), settled at
Betfair prices (8% commission). Each angle was close to break even in
Feb-Jun AND again in Jul-Oct. About 470 angles were tested in all, including
every jockey, trainer and jockey + trainer pairing; the people angles did
NOT hold up (those near break even in Feb-Jun did worse than average
after), so none are listed.

Backing every runner lost 7.7% to win and 8.0% for a place.
"""

from collections import namedtuple

Angle = namedtuple("Angle", "key name bet bets roi roi_first roi_second months_up without_top3 note")

# Checked 11 Nov 2026 (audit/tasks/closest.py verify): price bands use the
# price traded BEFORE the jump, not the final BSP. "without_top3" is the return
# with the angle's three biggest results taken out.
# Dropped after checking: "top pick and favourite" (-2.1%, no better than any
# favourite) and "backmarker in a sprint" (+5.1% was three big winners; -6.9%
# without them).
ANGLES = (
    Angle("top_pick_3_6", "Analyzer top pick at $3-$6", "place", 2664, -1.4, -0.3, -2.9, "4/9", -1.6,
          "Any favourite lost 2.1% for a place. Price is the live Ladbrokes win price."),
    Angle("ran_3_2", "Last 5 starts: placings go 3rd, then 2nd (e.g. 3-6-2)", "place", 4037, 1.6, 2.5, 0.4, "3/9", -3.8,
          "Only placings count: unplaced runs in between are skipped. One 119-unit place dividend makes it positive; -1.4% without it."),
    Angle("weight_below", "Carrying 2-3kg below the field average", "win", 6101, 22.5, 32.3, 11.0, "6/9", 8.1,
          "Win bet at long prices (median $23, 8% strike). -9.4% without its 10 biggest winners. Expect long losing runs."),
)
BY_KEY = {a.key: a for a in ANGLES}

COMPONENT_ANGLES = {
    "Ran places: 3rd 2nd": "ran_3_2",
    "Weight vs Field - Below (2-3kg)": "weight_below",
}


def matching_angles(components, analyzer_rank=None, price=None, is_favourite=False):
    """Angle keys a runner fits. components: set of analyzer component names."""
    keys = []
    if analyzer_rank == 1 and price is not None and 3.0 <= float(price) < 6.0:
        keys.append("top_pick_3_6")
    for name, key in COMPONENT_ANGLES.items():
        if name in (components or ()):
            keys.append(key)
    return keys


def angles_for_page():
    return [a._asdict() for a in ANGLES]
