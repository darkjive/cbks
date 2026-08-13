from backend.services.hashing import content_hash


def test_content_hash_known_value():
    assert content_hash("hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_content_hash_differs_for_different_content():
    assert content_hash("a") != content_hash("b")
