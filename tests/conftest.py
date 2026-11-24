import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab import mysqlops as m  # noqa: E402


@pytest.fixture(scope="session")
def async_lab():
    m.setup_async()
    p = m.connect(33061)
    m.q(p, "CREATE DATABASE IF NOT EXISTS lab")
    m.q(p, "CREATE TABLE IF NOT EXISTS lab.t (id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(40))")
    return {"p": p, "r1": m.connect(33062), "r2": m.connect(33063)}


@pytest.fixture(scope="session")
def group_lab():
    m.setup_group()
    return [m.connect(p) for p in (33071, 33072, 33073)]
