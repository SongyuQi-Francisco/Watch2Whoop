"""Migration order (docs/ios-native-2.0.md section 6, decision 1): db.migrate sorts by the numeric prefix, so the 2.0.0
packages' migrations 1000+ run after 999, and the order of every existing migration is what the old string sort gave."""
from body_engine import db


def test_numeric_prefix_sorts_1000_after_999():
    names = ["1000_x.sql", "999_x.sql", "001_init.sql", "1010_y.sql", "010_scores.sql", "990_z.sql"]
    assert sorted(names, key=db.migration_key) == \
        ["001_init.sql", "010_scores.sql", "990_z.sql", "999_x.sql", "1000_x.sql", "1010_y.sql"]
    plain = sorted(names)   # the old string sort: wrong for 1000+
    assert plain.index("1000_x.sql") < plain.index("999_x.sql")


def test_same_number_keeps_the_name_order():
    assert sorted(["760_open_signup.sql", "760_exercise_media_licensed.sql"], key=db.migration_key) == \
        ["760_exercise_media_licensed.sql", "760_open_signup.sql"]


def test_existing_order_unchanged():
    names = [f.name for f in db.migration_files()]
    assert names, "no migrations found"
    below = [n for n in names if db.migration_key(n)[0] < 1000]
    assert below == sorted(below), "every migration below 1000 keeps the string order it always had"
    assert names[0] == "001_init.sql"
    assert names == sorted(names, key=db.migration_key)
