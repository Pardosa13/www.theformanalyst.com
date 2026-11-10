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

Angle = namedtuple("Angle", "key name bet bets roi roi_first roi_second months_up note")

ANGLES = (
    Angle("top_pick_3_6", "Analyzer top pick at $3-$6", "place", 2592, -0.7, -0.8, -0.5, "4/9",
          "Price is the live Ladbrokes win price."),
    Angle("top_pick_fav", "Analyzer top pick and favourite", "place", 2464, -1.0, -1.7, -0.2, "3/9",
          "Favourite on the live Ladbrokes market."),
    Angle("ran_3_2", "Ran 3rd then 2nd at its last two starts", "place", 4037, 1.6, 2.5, 0.4, "3/9",
          "The live Best Bet rule is this angle plus no track+distance win."),
    Angle("backmarker_sprint", "Backmarker in a sprint", "win", 7439, 5.1, 5.2, 4.9, "6/9",
          "Win bet: big swings from month to month."),
    Angle("weight_below", "Carrying 2-3kg below the field average", "win", 6101, 22.5, 32.3, 11.0, "6/9",
          "Win bet: a few big-priced winners drive this; expect long losing runs."),
)
BY_KEY = {a.key: a for a in ANGLES}

COMPONENT_ANGLES = {
    "Ran places: 3rd 2nd": "ran_3_2",
    "Running Position - Backmarker Sprint": "backmarker_sprint",
    "Weight vs Field - Below (2-3kg)": "weight_below",
}


def matching_angles(components, analyzer_rank=None, price=None, is_favourite=False):
    """Angle keys a runner fits. components: set of analyzer component names."""
    keys = []
    if analyzer_rank == 1:
        if price is not None and 3.0 <= float(price) < 6.0:
            keys.append("top_pick_3_6")
        if is_favourite:
            keys.append("top_pick_fav")
    for name, key in COMPONENT_ANGLES.items():
        if name in (components or ()):
            keys.append(key)
    return keys


def angles_for_page():
    return [a._asdict() for a in ANGLES]
