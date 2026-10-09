from services import images


def test_slide_prompt_contains_no_text():
    p = images.slide_prompt("Sunʼiy intellekt", ["taʼlim", "texnologiya"])
    assert "no text" in p
    assert "Sunʼiy intellekt" in p


def test_commons_query_prefers_hint():
    qs = images._commons_queries("Muqova", "artificial intelligence robot")
    assert qs[0].startswith("artificial intelligence robot")
    assert qs[0].endswith("filetype:bitmap")
    assert len(qs) >= 3


def test_commons_query_falls_back_to_title():
    qs = images._commons_queries("Geografiya darslari", "")
    assert "Geografiya darslari filetype:bitmap" in qs
    assert any(q.startswith("Geografiya") for q in qs)


def test_fetch_failure_returns_none(monkeypatch):
    monkeypatch.setattr(images, "_POLLINATION_GAP", 0)
    monkeypatch.setattr(images, "_commons_one", lambda i, t, h, deadline=None: (i, None))
    monkeypatch.setattr(images, "_pollinations_one", lambda i, t, b, s: (i, None))
    out = images.fetch_for_slides([{"title": "a", "bullets": ["b"]}], seed=1)
    assert out == [None]


def test_commons_success_skips_pollinations(monkeypatch):
    monkeypatch.setattr(images, "_POLLINATION_GAP", 0)
    monkeypatch.setattr(
        images, "_commons_one", lambda i, t, h, deadline=None: (i, b"\xff\xd8" + b"0" * 4000)
    )

    def boom(*a, **k):
        raise AssertionError("pollinations chaqrilmasligi kerak")

    monkeypatch.setattr(images, "_pollinations_one", boom)
    out = images.fetch_for_slides([{"title": "a", "bullets": ["b"], "image_hint": "cat"}], seed=1)
    assert out == [b"\xff\xd8" + b"0" * 4000]


def test_pollinations_fills_missed_slides(monkeypatch):
    monkeypatch.setattr(images, "_POLLINATION_GAP", 0)
    monkeypatch.setattr(images, "_commons_one", lambda i, t, h, deadline=None: (i, None))
    monkeypatch.setattr(
        images, "_pollinations_one", lambda i, t, b, s: (i, b"\x89PNG" + b"1" * 4000)
    )
    out = images.fetch_for_slides(
        [{"title": "a", "bullets": ["b"]}, {"title": "c", "bullets": ["d"]}], seed=7
    )
    assert out == [b"\x89PNG" + b"1" * 4000, b"\x89PNG" + b"1" * 4000]


def test_fetch_empty_slides():
    assert images.fetch_for_slides([], seed=1) == []


def test_add_ppt_images_sets_bytes(monkeypatch):
    monkeypatch.setattr(
        images, "fetch_for_slides", lambda slides, seed: [b"\xff\xd8" + b"0" * 4000, None]
    )
    data = {"slides": [{"title": "a"}, {"title": "b"}]}
    out = images.add_ppt_images(data)
    assert out["slides"][0]["image_bytes"]
    assert "image_bytes" not in out["slides"][1]
    assert out["image_failures"] == 1


def test_fetch_falls_back_to_image_prompt(monkeypatch):
    monkeypatch.setattr(images, "_POLLINATION_GAP", 0)
    seen = {}

    def fake_commons(i, title, hint, deadline=None):
        seen["hint"] = hint
        return i, None

    monkeypatch.setattr(images, "_commons_one", fake_commons)
    monkeypatch.setattr(images, "_pollinations_one", lambda i, t, b, s: (i, None))
    images.fetch_for_slides([{"title": "x", "image_prompt": "old style description"}], seed=1)
    assert seen["hint"] == "old style description"
