from crosspost.matching import FileInfo, Post, match

W, TOL = 30 * 60, 1.0


def test_matches_on_time_and_duration():
    posts = [Post("p1", 1000, 15)]
    files = [FileInfo("f1", 1100, 15.4)]
    assert match(posts, files, W, TOL) == {"p1": "f1"}


def test_rejects_when_duration_off():
    assert match([Post("p1", 1000, 15)], [FileInfo("f1", 1000, 16.5)], W, TOL) == {}


def test_rejects_when_outside_window():
    assert match([Post("p1", 1000, 15)], [FileInfo("f1", 1000 + W + 1, 15)], W, TOL) == {}


def test_duration_tolerance_is_inclusive():
    assert match([Post("p1", 1000, 15)], [FileInfo("f1", 1000, 16.0)], W, TOL) == {"p1": "f1"}


def test_closest_in_time_wins_and_files_used_once():
    # Two posts a few minutes apart with the same duration must not swap files.
    posts = [Post("a", 1000, 10), Post("b", 1300, 10)]
    files = [FileInfo("fa", 1010, 10), FileInfo("fb", 1290, 10)]
    assert match(posts, files, W, TOL) == {"a": "fa", "b": "fb"}


def test_one_file_two_candidate_posts():
    posts = [Post("a", 1000, 10), Post("b", 1100, 10)]
    files = [FileInfo("f", 1090, 10)]
    assert match(posts, files, W, TOL) == {"b": "f"}


def test_unknown_metadata_never_matches():
    posts = [Post("a", 1000, None), Post("b", 1000, 10)]
    files = [FileInfo("f1", None, 10), FileInfo("f2", 1000, None)]
    assert match(posts, files, W, TOL) == {}


def test_extra_files_are_ignored():
    posts = [Post("a", 1000, 10)]
    files = [FileInfo("x", 50_000, 10), FileInfo("f", 1005, 10), FileInfo("y", 1000, 99)]
    assert match(posts, files, W, TOL) == {"a": "f"}
