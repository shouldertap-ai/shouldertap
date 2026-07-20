import shouldertap


def test_version_is_a_string() -> None:
    assert isinstance(shouldertap.__version__, str)
