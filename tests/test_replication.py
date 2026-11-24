"""Asynchronous GTID replication against real MySQL 8.4. Needs docker-compose.async.yml up."""
import time

from lab import mysqlops as m


def test_rows_reach_both_replicas(async_lab):
    p, r1, r2 = async_lab["p"], async_lab["r1"], async_lab["r2"]
    m.q(p, "CREATE DATABASE IF NOT EXISTS lab")
    m.q(p, "CREATE TABLE IF NOT EXISTS lab.t (id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(40))")
    m.q(p, "INSERT INTO lab.t (v) VALUES ('hello')")
    m.wait_caught_up(p, r1)
    m.wait_caught_up(p, r2)
    assert m.one(r1, "SELECT COUNT(*) n FROM lab.t WHERE v='hello'")["n"] >= 1
    assert m.one(r2, "SELECT COUNT(*) n FROM lab.t WHERE v='hello'")["n"] >= 1


def test_replicas_reject_writes(async_lab):
    try:
        m.q(async_lab["r1"], "INSERT INTO lab.t (v) VALUES ('nope')")
    except Exception as e:
        assert "read-only" in str(e).lower()
    else:
        raise AssertionError("replica accepted a write")


def test_lag_is_visible_in_gtid_gap_and_clears(async_lab):
    p, r1 = async_lab["p"], async_lab["r1"]
    m.q(r1, "STOP REPLICA SQL_THREAD")
    for _ in range(20):
        m.q(p, "INSERT INTO lab.t (v) VALUES ('lagging')")
    gap = m.gtid_gap(p, r1)
    assert gap != "", "expected unapplied transactions while the SQL thread is stopped"
    st = m.replica_status(r1)
    assert st["Replica_IO_Running"] == "Yes" and st["Replica_SQL_Running"] == "No"
    started = time.time()
    m.q(r1, "START REPLICA SQL_THREAD")
    m.wait_caught_up(p, r1)
    assert m.gtid_gap(p, r1) == ""
    print(f"caught up in {time.time() - started:.2f}s")


def test_duplicate_key_error_is_found_and_skipped_by_gtid(async_lab):
    p, r2 = async_lab["p"], async_lab["r2"]
    m.q(p, "CREATE TABLE IF NOT EXISTS lab.u (id INT PRIMARY KEY, v VARCHAR(10))")
    m.wait_caught_up(p, r2)
    # Someone writes directly to the replica, so the same key later collides.
    m.q(r2, "SET GLOBAL super_read_only=OFF")
    m.q(r2, "SET GLOBAL read_only=OFF")
    m.q(r2, "SET SESSION sql_log_bin=0")
    m.q(r2, "INSERT INTO lab.u VALUES (1, 'drift')")
    m.q(r2, "SET SESSION sql_log_bin=1")
    m.q(r2, "SET GLOBAL read_only=ON")
    m.q(r2, "SET GLOBAL super_read_only=ON")
    m.q(p, "INSERT INTO lab.u VALUES (1, 'real')")
    deadline = time.time() + 30
    st = m.replica_status(r2)
    while (st["Last_SQL_Errno"] == 0 or st["Replica_SQL_Running"] != "No") and time.time() < deadline:
        time.sleep(0.5)
        st = m.replica_status(r2)
    assert st["Last_SQL_Errno"] == 1062, st["Last_SQL_Error"]
    assert st["Replica_SQL_Running"] == "No"
    # Fix: remove the drifted row, then let the same transaction apply.
    m.q(r2, "SET GLOBAL super_read_only=OFF")
    m.q(r2, "SET GLOBAL read_only=OFF")
    m.q(r2, "SET SESSION sql_log_bin=0")
    m.q(r2, "DELETE FROM lab.u WHERE id=1")
    m.q(r2, "SET SESSION sql_log_bin=1")
    m.q(r2, "SET GLOBAL read_only=ON")
    m.q(r2, "SET GLOBAL super_read_only=ON")
    m.q(r2, "START REPLICA SQL_THREAD")
    m.wait_caught_up(p, r2)
    assert m.one(r2, "SELECT v FROM lab.u WHERE id=1")["v"] == "real"


def test_failover_promotes_a_replica_without_losing_transactions(async_lab):
    p, r1, r2 = async_lab["p"], async_lab["r1"], async_lab["r2"]
    m.q(p, "INSERT INTO lab.t (v) VALUES ('before-failover')")
    m.wait_caught_up(p, r1)
    m.wait_caught_up(p, r2)
    last = m.one(p, "SELECT @@GLOBAL.gtid_executed g")["g"].replace("\n", "")
    m.compose("docker-compose.async.yml", "stop", "primary")
    # Promote replica1: stop replication, open for writes.
    m.q(r1, "STOP REPLICA")
    m.q(r1, "RESET REPLICA ALL")
    m.q(r1, "SET GLOBAL super_read_only=OFF")
    m.q(r1, "SET GLOBAL read_only=OFF")
    lost = m.one(r1, "SELECT GTID_SUBTRACT(%s, @@GLOBAL.gtid_executed) g", (last,))["g"]
    assert lost == "", f"lost transactions: {lost}"
    # Point replica2 at the new primary using GTID auto-position, no binlog coordinates needed.
    m.q(r2, "STOP REPLICA")
    m.q(r2, "CHANGE REPLICATION SOURCE TO SOURCE_HOST='replica1', SOURCE_USER='repl', "
            "SOURCE_PASSWORD=%s, SOURCE_AUTO_POSITION=1, GET_SOURCE_PUBLIC_KEY=1", (m.REPL_PW,))
    m.q(r1, "CREATE USER IF NOT EXISTS 'repl'@'%%' IDENTIFIED BY %s", (m.REPL_PW,))
    m.q(r1, "GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%'")
    m.q(r2, "START REPLICA")
    m.q(r1, "INSERT INTO lab.t (v) VALUES ('after-failover')")
    m.wait_caught_up(r1, r2)
    assert m.one(r2, "SELECT COUNT(*) n FROM lab.t WHERE v='after-failover'")["n"] == 1
