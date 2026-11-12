import pytest


@pytest.mark.integration
def test_ci_probe() -> None:
    assert 1 == 2
