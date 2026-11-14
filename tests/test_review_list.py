import review_list

WB = "Weight vs Field - Below (2-3kg)"
BM = "Running Position - Backmarker Sprint"


def test_top_pick_price_band():
    assert "top_pick_3_6" in review_list.matching_angles(set(), flags={"a_top"}, price=4.2)
    assert "top_pick_3_6" not in review_list.matching_angles(set(), flags={"a_top"}, price=2.4)
    assert "top_pick_3_6" not in review_list.matching_angles(set(), flags={"a_top"}, price=6.0)
    assert "top_pick_3_6" not in review_list.matching_angles(set(), flags={"a_top"}, price=None)


def test_top_pick_follows_the_page():
    # On the ML Data page the top pick is the ML model's, not the analyzer's.
    assert "top_pick_3_6" not in review_list.matching_angles(set(), flags={"a_top"}, price=4.0, source="ml")
    assert "top_pick_3_6" in review_list.matching_angles(set(), flags={"ml_top"}, price=4.0, source="ml")
    assert review_list.angle_name(review_list.BY_KEY["top_pick_3_6"], "ml") == "ML top pick at $3-$6"
    assert review_list.angle_name(review_list.BY_KEY["top_pick_3_6"]) == "Analyzer top pick at $3-$6"


def test_component_angles():
    assert review_list.matching_angles(set(), price=4.0) == []
    keys = review_list.matching_angles({"Ran places: 3rd 2nd", WB, BM}, price=15.0)
    assert keys == ["ran_3_2", "weight_below", "weight_below_10_30", "backmarker_sprint",
                    "weight_below_backmarker", "two_long_flags"]


def test_favourite_and_field_size():
    assert review_list.matching_angles(set(), flags={"fav"}, starters=9) == ["fav_field_8_11"]
    assert review_list.matching_angles(set(), flags={"fav"}, starters=12) == []
    assert review_list.matching_angles(set(), flags={"fav"}) == []
    keys = review_list.matching_angles(set(), flags={"a_top", "ml_top", "fav"}, starters=14)
    assert keys == ["all_agree_fav", "ml_top_fav"]


def test_every_angle_can_be_matched():
    from notes_parsing import _NOTES_COMPONENT_PATTERNS
    known = {name for _pattern, name in _NOTES_COMPONENT_PATTERNS}
    keys = [a.key for a in review_list.ANGLES]
    assert len(keys) == len(set(keys))
    for a in review_list.ANGLES:
        assert a.group in ("steady", "long")
        assert a.comps or a.flags or a.at_least
        for name in tuple(a.comps) + (a.at_least[1] if a.at_least else ()):
            assert name in known, name
            assert name in review_list.COMPONENT_LIKE, name


def test_fast_matcher_agrees_with_the_notes_parser():
    from notes_parsing import parse_notes_components
    samples = ['+4.0 : Ran places: 3rd 2nd', '+10.0 : Weight 2.5kg below race avg', '+6.0 : Weight 1.5kg below race avg',
               '+8.0 : BACKMARKER in Sprint', '-10.0 : Trainer cold', '+1.0 : Low win rate (10%) at this track',
               '+15.0 : Last Start - Photo Win', '+0.0 : Quick backup', '+2.0: Last 200m (Rank 5) AVERAGE',
               '+2.0: Last 200m (Rank 1) ELITE', 'x', '']
    for notes in samples + ['\n'.join(samples)]:
        full = {k for k in parse_notes_components(notes) if k in review_list.COMPONENT_LIKE}
        assert review_list._angle_components(notes) == full, notes


def test_ran_3_2_label_says_placings_not_last_two_starts():
    # "Ran places" lists only the 1st-3rd finishes in the last 5 starts, so
    # 3-6-2 fits. The label must not say "last two starts".
    a = review_list.BY_KEY["ran_3_2"]
    assert "last two starts" not in a.name
    assert "placings" in a.name


def _client_with_race(field):
    """An app client logged in as an admin, with one 8-runner race saved."""
    import os
    from datetime import datetime
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    import app as appmod
    from models import db, User, Meeting, Race, Horse, Prediction, Result

    db.create_all()
    user = User(username='ra', email='ra@example.com', password_hash='x', is_admin=True)
    db.session.add(user)
    db.session.flush()
    meeting = Meeting(user_id=user.id, meeting_name='Testville', uploaded_at=datetime.utcnow(),
                      date=datetime.utcnow().date())
    db.session.add(meeting)
    db.session.flush()
    race = Race(meeting_id=meeting.id, race_number=1)
    db.session.add(race)
    db.session.flush()
    for name, score, notes, fin, sp, jockey, trainer in field:
        h = Horse(race_id=race.id, horse_name=name, csv_data={}, jockey=jockey, trainer=trainer)
        db.session.add(h)
        db.session.flush()
        db.session.add(Prediction(horse_id=h.id, score=score, notes=notes))
        db.session.add(Result(horse_id=h.id, finish_position=fin, sp=sp, bsp=sp + 1))
    db.session.commit()
    client = appmod.app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
    return appmod, db, client


# 8 runners: 3 places paid. Top pick wins at $4 and is the favourite. The
# 3rd-2nd horse runs 3rd. The light-weight horse is unplaced.
FIELD = [('Top', 90, 'x', 1, 4.0, 'J Smith', 'T Jones'),
         ('Ran', 50, '+4.0 : Ran places: 3rd 2nd', 3, 9.0, 'J Smith (a2)', 'T Jones'),
         ('Light', 40, '+10.0 : Weight 2.5kg below race avg', 5, 21.0, 'A Other', 'T Jones')] + \
        [(f'F{i}', 10, 'x', 2 if i == 0 else 5, 15.0, 'B Rider', 'C Stable') for i in range(5)]


def test_review_angles_history_counts_win_and_place_bets():
    import app as appmod
    with appmod.app.app_context():
        appmod, db, client = _client_with_race(FIELD)
        try:
            for limit in ('200', 'all'):
                r = client.get(f'/api/data/review-angles?limit={limit}')
                assert r.status_code == 200
                data = r.get_json()
                assert data['races'] == 1
                got = {a['key']: a for a in data['angles']}
                assert set(got) == set(review_list.BY_KEY)
                top, ran, light = got['top_pick_3_6'], got['ran_3_2'], got['weight_below']
                assert (top['bets'], top['wins'], top['win_profit'], top['win_roi']) == (1, 1, 30.0, 300.0)
                assert top['betfair_roi'] == 368.0                        # (5 - 1) * 0.92
                assert (top['placed'], top['place_waiting']) == (1, 1)   # placed, no place price yet
                assert (ran['bets'], ran['wins'], ran['win_roi'], ran['placed']) == (1, 0, -100.0, 1)
                assert (light['bets'], light['placed'], light['place_bets'], light['place_roi']) == (1, 0, 1, -100.0)
                assert got['fav_field_8_11']['bets'] == 1 and got['fav_field_8_11']['wins'] == 1
                assert got['weight_below_10_30']['bets'] == 1
                assert got['ran_3_2_3_8']['bets'] == 0                   # $9 is outside $3-$8
            # No ML scores saved: the ML page has no races to count.
            ml = client.get('/api/data/review-angles?source=ml').get_json()
            assert ml['races'] == 0 and ml['source'] == 'ml'
            assert ml['angles'][0]['name'] == 'ML top pick at $3-$6'
        finally:
            db.session.remove()
            db.drop_all()


def test_people_form_lists_jockeys_trainers_and_pairs(monkeypatch):
    import app as appmod
    import people_form
    monkeypatch.setattr(people_form, 'MIN_RUNS', {'jockeys': 1, 'trainers': 1, 'combos': 1})
    with appmod.app.app_context():
        appmod, db, client = _client_with_race(FIELD)
        try:
            r = client.get('/api/data/people-form?days=30')
            assert r.status_code == 200
            data = r.get_json()
            jockeys = {j['name']: j for j in data['jockeys']}
            # 'J Smith (a2)' and 'J Smith' are the same rider: 2 rides, 1 win at $4.
            smith = jockeys['J Smith']
            assert (smith['runs'], smith['wins'], smith['strike_rate'], smith['places']) == (2, 1, 50.0, 2)
            assert (smith['profit'], smith['roi']) == (20.0, 100.0)
            trainers = {t['name']: t for t in data['trainers']}
            assert (trainers['T Jones']['runs'], trainers['T Jones']['wins']) == (3, 1)
            combos = {c['name']: c for c in data['combos']}
            assert combos['J Smith + T Jones']['runs'] == 2
            assert combos['B Rider + C Stable']['roi'] == -100.0
        finally:
            db.session.remove()
            db.drop_all()
