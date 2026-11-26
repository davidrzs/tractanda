import atexit
import os
import pathlib
import shutil
import tempfile

# Tests never touch the real data directory or the real database: each run gets its own
# directory, set before the settings are first read.
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="opencommune-tests-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
# a local .env may set a disk reserve larger than the test filesystem has free
os.environ["RAW_MIN_FREE_BYTES"] = str(1024**3)

# A host .env may keep configuration apart from state (CONFIG_DIR), raw files (RAW_DATA_DIR) and
# rendered requests (SCRATCH_DIR) elsewhere; tests keep all of them in DATA_DIR.
from opencommune.config import settings  # noqa: E402

settings.config_dir = None
settings.raw_data_dir = None
settings.scratch_dir = None


# --- network guard ----------------------------------------------------------------------------
# Tests never go online. A mock patched into the wrong module would otherwise fall through to a
# real request and pass slowly or flakily; here it fails. Loopback and Unix sockets stay allowed.
# The guard raises RuntimeError (not OSError) so code that tolerates network errors cannot swallow
# it, and records every attempt so a test that catches broad exceptions still fails at teardown.
import ipaddress  # noqa: E402
import socket  # noqa: E402

import pytest  # noqa: E402

_network_attempts: list[str] = []


def _loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if not isinstance(host, str):
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _block(what: str) -> None:
    _network_attempts.append(what)
    raise RuntimeError(f"tests must not use the network: {what}")


def _guard_address(address: object) -> None:
    if isinstance(address, tuple) and not _loopback(address[0]):
        _block(f"connect to {address!r}")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex


def _connect(self, address):
    _guard_address(address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    _guard_address(address)
    return _real_connect_ex(self, address)


def _getaddrinfo(host, *args, **kwargs):
    if host is not None and not _loopback(host):
        _block(f"resolve {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _gethostbyname(host):
    if not _loopback(host):
        _block(f"resolve {host!r}")
    return _real_gethostbyname(host)


def _gethostbyname_ex(host):
    if not _loopback(host):
        _block(f"resolve {host!r}")
    return _real_gethostbyname_ex(host)


@pytest.fixture(scope="session", autouse=True)
def _no_network_session():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", _connect)
        mp.setattr(socket.socket, "connect_ex", _connect_ex)
        mp.setattr(socket, "getaddrinfo", _getaddrinfo)
        mp.setattr(socket, "gethostbyname", _gethostbyname)
        mp.setattr(socket, "gethostbyname_ex", _gethostbyname_ex)
        yield


@pytest.fixture(autouse=True)
def _no_network(_no_network_session):
    _network_attempts.clear()
    yield
    attempts = list(_network_attempts)
    _network_attempts.clear()
    assert not attempts, f"test attempted network access: {attempts}"
