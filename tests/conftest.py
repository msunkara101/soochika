import pytest


@pytest.fixture(scope="session")
def httpserver_listen_address() -> tuple[str, int]:
    # 127.0.0.1 rather than "localhost": name lookup for localhost is slow on Windows.
    return ("127.0.0.1", 0)
