import math

import pytest

from backend.storage.faiss_index import FaissIndex


@pytest.fixture
def index(tmp_path):
    return FaissIndex(dim=4, index_path=tmp_path / "index.faiss")


def test_add_and_search_returns_closest_first(index):
    index.add(1, [1.0, 0.0, 0.0, 0.0])
    index.add(2, [0.0, 1.0, 0.0, 0.0])
    index.add(3, [0.9, 0.1, 0.0, 0.0])

    results = index.search([1.0, 0.0, 0.0, 0.0], k=3)

    assert [faiss_id for faiss_id, _ in results][0] == 1
    assert len(results) == 3


def test_search_scores_are_cosine_like(index):
    index.add(1, [1.0, 0.0, 0.0, 0.0])
    results = index.search([1.0, 0.0, 0.0, 0.0], k=1)
    faiss_id, score = results[0]
    assert faiss_id == 1
    assert math.isclose(score, 1.0, abs_tol=1e-5)


def test_save_and_reload_preserves_vectors(tmp_path):
    path = tmp_path / "index.faiss"
    index = FaissIndex(dim=4, index_path=path)
    index.add(1, [1.0, 0.0, 0.0, 0.0])
    index.save()

    reloaded = FaissIndex(dim=4, index_path=path)
    results = reloaded.search([1.0, 0.0, 0.0, 0.0], k=1)
    assert results[0][0] == 1


def test_clear_empties_index(index):
    index.add(1, [1.0, 0.0, 0.0, 0.0])
    index.clear()
    results = index.search([1.0, 0.0, 0.0, 0.0], k=1)
    assert results == []
