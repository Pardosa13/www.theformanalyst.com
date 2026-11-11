import review_list


def test_top_pick_price_band_and_favourite():
    assert review_list.matching_angles(set(), analyzer_rank=1, price=4.2, is_favourite=False) == ["top_pick_3_6"]
    assert review_list.matching_angles(set(), analyzer_rank=1, price=2.4, is_favourite=True) == ["top_pick_fav"]
    assert review_list.matching_angles(set(), analyzer_rank=1, price=6.0) == []


def test_not_top_pick_needs_a_component():
    assert review_list.matching_angles(set(), analyzer_rank=2, price=4.0, is_favourite=True) == []
    keys = review_list.matching_angles({"Ran places: 3rd 2nd", "Running Position - Backmarker Sprint"})
    assert keys == ["ran_3_2", "backmarker_sprint"]


def test_no_price_means_no_price_angle():
    assert review_list.matching_angles(set(), analyzer_rank=1, price=None) == []


def test_every_angle_is_described():
    keys = {a["key"] for a in review_list.angles_for_page()}
    assert set(review_list.COMPONENT_ANGLES.values()) <= keys
    assert {"top_pick_3_6", "top_pick_fav"} <= keys


def test_review_list_endpoint_lists_matching_runners(monkeypatch):
    import os
    from datetime import datetime
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    import app as appmod
    from models import db, User, Meeting, Race, Horse, Prediction

    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='rv', email='rv@example.com', password_hash='x', is_admin=True)
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Testville', uploaded_at=datetime.utcnow())
            db.session.add(meeting)
            db.session.flush()
            race = Race(meeting_id=meeting.id, race_number=3)
            db.session.add(race)
            db.session.flush()
            ids = {}
            for name, score, notes in (('Top', 90, 'x'), ('Other', 50, '+4.0 : Ran places: 3rd 2nd'), ('Plain', 40, 'x')):
                h = Horse(race_id=race.id, horse_name=name, csv_data={})
                db.session.add(h)
                db.session.flush()
                db.session.add(Prediction(horse_id=h.id, score=score, notes=notes))
                ids[name] = h.id
            db.session.commit()

            monkeypatch.setattr(appmod, 'race_has_started', lambda *a, **k: (False, None))
            monkeypatch.setattr(appmod, 'match_race_info', lambda *a, **k: {'uuid': 'u'})
            monkeypatch.setattr(appmod, 'fetch_race_odds', lambda *a, **k: {})
            monkeypatch.setattr(appmod, '_track_from_meeting', lambda m: 'testville')
            meeting.date = datetime.utcnow().date()
            prices = {ids['Top']: {'ladbrokes_fixed_win_price': 4.5}, ids['Other']: {'ladbrokes_fixed_win_price': 9.0}}
            monkeypatch.setattr(appmod, 'evaluate_ladbrokes_best_bet_signals', lambda *a, **k: prices)

            client = appmod.app.test_client()
            with client.session_transaction() as sess:
                sess['_user_id'] = str(user.id)
                sess['_fresh'] = True
            r = client.get('/api/best-bets/review-list?hours=12')
            assert r.status_code == 200
            data = r.get_json()
            got = {row['horse_name']: row['angles'] for row in data['runners']}
            assert got == {'Top': ['top_pick_3_6'], 'Other': ['ran_3_2']}
            assert len(data['angles']) == len(review_list.ANGLES)
        finally:
            db.session.remove()
            db.drop_all()
