"""Group Replication, single primary, three nodes. Needs docker-compose.cluster.yml up."""
import time

from lab import mysqlops as m


def primary_of(conns):
    for c in conns:
        try:
            if m.one(c, "SELECT @@read_only r")["r"] == 0:
                return c
        except Exception:
            continue
    return None


def test_three_members_online_one_primary(group_lab):
    members = m.group_members(group_lab[0])
    assert [x["MEMBER_STATE"] for x in members] == ["ONLINE"] * 3
    assert sorted(x["MEMBER_ROLE"] for x in members) == ["PRIMARY", "SECONDARY", "SECONDARY"]


def test_write_on_primary_is_visible_everywhere_and_secondaries_refuse_writes(group_lab):
    p = primary_of(group_lab)
    m.q(p, "CREATE DATABASE IF NOT EXISTS gr")
    m.q(p, "CREATE TABLE IF NOT EXISTS gr.t (id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(40))")
    m.q(p, "INSERT INTO gr.t (v) VALUES ('one')")
    for c in group_lab:
        if c is p:
            continue
        deadline = time.time() + 20
        while time.time() < deadline and m.one(c, "SELECT COUNT(*) n FROM gr.t WHERE v='one'")["n"] == 0:
            time.sleep(0.5)
        assert m.one(c, "SELECT COUNT(*) n FROM gr.t WHERE v='one'")["n"] == 1
        try:
            m.q(c, "INSERT INTO gr.t (v) VALUES ('nope')")
        except Exception as e:
            assert "read-only" in str(e).lower()
        else:
            raise AssertionError("secondary accepted a write")


def test_primary_loss_elects_a_new_primary_and_old_one_rejoins(group_lab):
    old = primary_of(group_lab)
    old_port = old.port
    name = {33071: "gr1", 33072: "gr2", 33073: "gr3"}[old_port]
    m.compose("docker-compose.cluster.yml", "stop", name)
    survivors = [c for c in group_lab if c.port != old_port]
    deadline = time.time() + 60
    new = None
    while time.time() < deadline and new is None:
        time.sleep(1)
        new = primary_of(survivors)
    assert new is not None, "no new primary elected"
    m.q(new, "INSERT INTO gr.t (v) VALUES ('after-election')")
    assert len([x for x in m.group_members(new) if x["MEMBER_STATE"] == "ONLINE"]) == 2
    # Old primary comes back and rejoins as a secondary.
    m.compose("docker-compose.cluster.yml", "start", name)
    m.wait_ready(old_port)
    rejoined = m.connect(old_port)
    m.q(rejoined, "START GROUP_REPLICATION")
    m.wait_member_online(rejoined)
    roles = {x["MEMBER_HOST"]: x["MEMBER_ROLE"] for x in m.group_members(new)}
    assert roles[name] == "SECONDARY"
    deadline = time.time() + 30
    while time.time() < deadline and m.one(rejoined, "SELECT COUNT(*) n FROM gr.t WHERE v='after-election'")["n"] == 0:
        time.sleep(0.5)
    assert m.one(rejoined, "SELECT COUNT(*) n FROM gr.t WHERE v='after-election'")["n"] == 1
