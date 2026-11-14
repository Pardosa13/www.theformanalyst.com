import review_list


def test_top_pick_price_band_and_favourite():
    assert review_list.matching_angles(set(), analyzer_rank=1, price=4.2, is_favourite=False) == ["top_pick_3_6"]
    assert review_list.matching_angles(set(), analyzer_rank=1, price=2.4, is_favourite=True) == []
    assert review_list.matching_angles(set(), analyzer_rank=1, price=6.0) == []


def test_not_top_pick_needs_a_component():
    assert review_list.matching_angles(set(), analyzer_rank=2, price=4.0, is_favourite=True) == []
    keys = review_list.matching_angles({"Ran places: 3rd 2nd", "Weight vs Field - Below (2-3kg)",
                                        "Running Position - Backmarker Sprint"})
    assert keys == ["ran_3_2", "weight_below"]


def test_no_price_means_no_price_angle():
    assert review_list.matching_angles(set(), analyzer_rank=1, price=None) == []


def test_every_angle_is_described():
    keys = {a.key for a in review_list.ANGLES}
    assert set(review_list.COMPONENT_ANGLES.values()) <= keys
    assert "top_pick_3_6" in keys


def test_ran_3_2_label_says_placings_not_last_two_starts():
    # "Ran places" lists only the 1st-3rd finishes in the last 5 starts, so
    # 3-6-2 fits. The label must not say "last two starts".
    a = review_list.BY_KEY["ran_3_2"]
    assert "last two starts" not in a.name
    assert "placings" in a.name


def test_review_angles_history_counts_win_and_place_bets():
    import os
    from datetime import datetime
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    import app as appmod
    from models import db, User, Meeting, Race, Horse, Prediction, Result

    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='ra', email='ra@example.com', password_hash='x', is_admin=True)
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Testville', uploaded_at=datetime.utcnow())
            db.session.add(meeting)
            db.session.flush()
            race = Race(meeting_id=meeting.id, race_number=1)
            db.session.add(race)
            db.session.flush()
            # 8 runners: 3 places paid. Top pick wins at $4. The 3rd-2nd horse
            # runs 3rd. The light-weight horse is unplaced.
            field = [('Top', 90, 'x', 1, 4.0), ('Ran', 50, '+4.0 : Ran places: 3rd 2nd', 3, 9.0),
                     ('Light', 40, '+10.0 : Weight 2.5kg below race avg', 5, 21.0)]
            field += [(f'F{i}', 10, 'x', 2 if i == 0 else 5, 15.0) for i in range(5)]
            for name, score, notes, fin, sp in field:
                h = Horse(race_id=race.id, horse_name=name, csv_data={})
                db.session.add(h)
                db.session.flush()
                db.session.add(Prediction(horse_id=h.id, score=score, notes=notes))
                db.session.add(Result(horse_id=h.id, finish_position=fin, sp=sp))
            db.session.commit()

            client = appmod.app.test_client()
            with client.session_transaction() as sess:
                sess['_user_id'] = str(user.id)
                sess['_fresh'] = True
            for limit in ('200', 'all'):
                r = client.get(f'/api/data/review-angles?limit={limit}')
                assert r.status_code == 200
                data = r.get_json()
                assert data['races'] == 1
                got = {a['key']: a for a in data['angles']}
                top, ran, light = got['top_pick_3_6'], got['ran_3_2'], got['weight_below']
                assert (top['bets'], top['wins'], top['win_profit'], top['win_roi']) == (1, 1, 30.0, 300.0)
                assert (top['placed'], top['place_waiting']) == (1, 1)   # placed, no place price yet
                assert (ran['bets'], ran['wins'], ran['win_roi'], ran['placed']) == (1, 0, -100.0, 1)
                assert (light['bets'], light['placed'], light['place_bets'], light['place_roi']) == (1, 0, 1, -100.0)
        finally:
            db.session.remove()
            db.drop_all()
