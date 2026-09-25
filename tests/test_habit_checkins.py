from datetime import date, timedelta

MISSING = "00000000-0000-0000-0000-000000000000"


def _habit(client, name="Read"):
    r = client.post("/habits", json={"name": name, "target_per_week": 5, "unit": "pages"})
    assert r.status_code == 201, r.text
    return r.json()


def test_checkin_defaults_to_today(client):
    habit = _habit(client)
    r = client.post(f"/habits/{habit['id']}/checkins", json={})
    assert r.status_code == 201, r.text
    checkin = r.json()
    assert checkin["checkin_date"] == str(date.today())  # server-local, not the DB's UTC CURRENT_DATE
    assert checkin["done"] is True and checkin["value"] is None and checkin["habit_id"] == habit["id"]


def test_checkin_with_details_and_past_date(client):
    habit = _habit(client)
    yesterday = date.today() - timedelta(days=1)
    body = {"checkin_date": str(yesterday), "value": 20, "note": "before bed"}
    checkin = client.post(f"/habits/{habit['id']}/checkins", json=body).json()
    assert checkin["checkin_date"] == str(yesterday)
    assert checkin["value"] == 20 and checkin["note"] == "before bed"

    skipped = client.post(f"/habits/{habit['id']}/checkins", json={"checkin_date": str(yesterday - timedelta(days=1)), "done": False})
    assert skipped.status_code == 201 and skipped.json()["done"] is False


def test_duplicate_day_is_a_conflict(client):
    habit = _habit(client)
    assert client.post(f"/habits/{habit['id']}/checkins", json={}).status_code == 201
    r = client.post(f"/habits/{habit['id']}/checkins", json={"checkin_date": str(date.today())})
    assert r.status_code == 409
    assert str(date.today()) in r.json()["detail"]
    other = _habit(client, "Walk")  # same day on another habit is fine
    assert client.post(f"/habits/{other['id']}/checkins", json={}).status_code == 201


def test_validation_and_missing_habit(client):
    habit = _habit(client)
    tomorrow = str(date.today() + timedelta(days=1))
    r = client.post(f"/habits/{habit['id']}/checkins", json={"checkin_date": tomorrow})
    assert r.status_code == 422 and "future" in r.json()["detail"]
    assert client.post(f"/habits/{habit['id']}/checkins", json={"checkin_date": "not-a-date"}).status_code == 422
    assert client.post(f"/habits/{MISSING}/checkins", json={}).status_code == 404
    assert client.get(f"/habits/{MISSING}/checkins").status_code == 404
    assert client.get(f"/habits/{habit['id']}/checkins", params={"from": "2026-10-05", "to": "2026-10-01"}).status_code == 422


def test_list_newest_first_with_date_range(client):
    habit = _habit(client)
    today = date.today()
    for back in (0, 1, 2, 5):
        day = str(today - timedelta(days=back))
        assert client.post(f"/habits/{habit['id']}/checkins", json={"checkin_date": day}).status_code == 201
    other = _habit(client, "Walk")
    client.post(f"/habits/{other['id']}/checkins", json={})

    def days(**params):
        r = client.get(f"/habits/{habit['id']}/checkins", params=params)
        assert r.status_code == 200, r.text
        return [c["checkin_date"] for c in r.json()]

    assert days() == [str(today - timedelta(days=b)) for b in (0, 1, 2, 5)]
    assert days(**{"from": str(today - timedelta(days=2))}) == [str(today - timedelta(days=b)) for b in (0, 1, 2)]
    assert days(to=str(today - timedelta(days=1)), limit=2) == [str(today - timedelta(days=b)) for b in (1, 2)]


def test_delete_checkin(client):
    habit = _habit(client)
    other = _habit(client, "Walk")
    checkin = client.post(f"/habits/{habit['id']}/checkins", json={}).json()

    assert client.delete(f"/habits/{other['id']}/checkins/{checkin['id']}").status_code == 404  # wrong habit
    assert client.delete(f"/habits/{habit['id']}/checkins/{checkin['id']}").status_code == 204
    assert client.get(f"/habits/{habit['id']}/checkins").json() == []
    assert client.delete(f"/habits/{habit['id']}/checkins/{checkin['id']}").status_code == 404
    assert client.post(f"/habits/{habit['id']}/checkins", json={}).status_code == 201  # the day is free again


def test_deleting_a_habit_removes_its_checkins(client):
    habit = _habit(client)
    client.post(f"/habits/{habit['id']}/checkins", json={})
    assert client.delete(f"/habits/{habit['id']}").status_code == 204
    assert client.get(f"/habits/{habit['id']}/checkins").status_code == 404


def test_checkins_feed_me_context(client):
    read = _habit(client)
    walk = _habit(client, "Walk")
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    client.post(f"/habits/{read['id']}/checkins", json={})
    client.post(f"/habits/{walk['id']}/checkins", json={"done": False})  # skipped: not "done"
    client.post(f"/habits/{read['id']}/checkins", json={"checkin_date": str(monday - timedelta(days=1))})  # last week

    summary = client.get("/me/context").json()["summary"]
    assert summary["habits_active"] == 2
    assert summary["habits_done_today"] == 1
    assert summary["habits_done_this_week"] == 1
    habits = {h["name"]: h for h in client.get("/me/context", params={"include": "habits"}).json()["habits"]}
    assert habits["Read"]["done_today"] is True and habits["Walk"]["done_today"] is False
