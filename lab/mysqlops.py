"""Helpers shared by the tests and the replication-watch service."""
import subprocess
import time

import pymysql

ROOT_PW = "rootpw"
REPL_PW = "replpw"


def connect(port, host="127.0.0.1", db=None, autocommit=True):
    return pymysql.connect(host=host, port=port, user="root", password=ROOT_PW, database=db,
                           autocommit=autocommit, cursorclass=pymysql.cursors.DictCursor)


def q(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def one(conn, sql, args=None):
    rows = q(conn, sql, args)
    return rows[0] if rows else None


def wait_ready(port, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        try:
            connect(port).close()
            return
        except Exception:
            time.sleep(2)
    raise TimeoutError(f"MySQL on port {port} not ready")


def compose(file, *args):
    return subprocess.run(["docker", "compose", "-f", file, *args], check=True, capture_output=True, text=True)


def gtid_gap(source_conn, replica_conn):
    """Transactions the source has that the replica has not applied yet."""
    src = one(source_conn, "SELECT @@GLOBAL.gtid_executed AS g")["g"].replace("\n", "")
    rep = one(replica_conn, "SELECT @@GLOBAL.gtid_executed AS g")["g"].replace("\n", "")
    gap = one(source_conn, "SELECT GTID_SUBTRACT(%s, %s) AS g", (src, rep))["g"]
    return gap


def replica_status(conn):
    row = one(conn, "SHOW REPLICA STATUS")
    return row


def wait_caught_up(source_conn, replica_conn, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if gtid_gap(source_conn, replica_conn) == "":
            return
        time.sleep(0.5)
    raise TimeoutError("replica did not catch up")


def setup_async(primary_port=33061, replica_ports=(33062, 33063)):
    for p in (primary_port, *replica_ports):
        wait_ready(p)
    p = connect(primary_port)
    q(p, "CREATE USER IF NOT EXISTS 'repl'@'%%' IDENTIFIED BY %s", (REPL_PW,))
    q(p, "GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%'")
    for port in replica_ports:
        r = connect(port)
        q(r, "SET GLOBAL super_read_only=ON")
        q(r, "SET GLOBAL read_only=ON")
        if replica_status(r) is None:
            q(r, "CHANGE REPLICATION SOURCE TO SOURCE_HOST='primary', SOURCE_USER='repl', "
                 "SOURCE_PASSWORD=%s, SOURCE_AUTO_POSITION=1, GET_SOURCE_PUBLIC_KEY=1", (REPL_PW,))
            q(r, "START REPLICA")
    for port in replica_ports:
        wait_caught_up(p, connect(port))


def setup_group(ports=(33071, 33072, 33073)):
    for p in ports:
        wait_ready(p)
    for i, port in enumerate(ports):
        c = connect(port)
        joined = one(c, "SELECT COUNT(*) AS n FROM performance_schema.replication_group_members "
                        "WHERE MEMBER_ID=@@server_uuid AND MEMBER_STATE='ONLINE'")["n"]
        if joined:
            continue
        q(c, "RESET BINARY LOGS AND GTIDS")  # drop the image init transactions
        q(c, "SET SQL_LOG_BIN=0")
        q(c, "CREATE USER IF NOT EXISTS 'repl'@'%%' IDENTIFIED BY %s", (REPL_PW,))
        q(c, "GRANT REPLICATION SLAVE, BACKUP_ADMIN, GROUP_REPLICATION_STREAM ON *.* TO 'repl'@'%'")
        q(c, "SET SQL_LOG_BIN=1")
        q(c, "CHANGE REPLICATION SOURCE TO SOURCE_USER='repl', SOURCE_PASSWORD=%s "
             "FOR CHANNEL 'group_replication_recovery'", (REPL_PW,))
        if i == 0:
            q(c, "SET GLOBAL group_replication_bootstrap_group=ON")
            q(c, "START GROUP_REPLICATION")
            q(c, "SET GLOBAL group_replication_bootstrap_group=OFF")
        else:
            q(c, "START GROUP_REPLICATION")
        wait_member_online(c)


def wait_member_online(conn, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        row = one(conn, "SELECT MEMBER_STATE AS s FROM performance_schema.replication_group_members "
                        "WHERE MEMBER_ID=@@server_uuid")
        if row and row["s"] == "ONLINE":
            return
        time.sleep(1)
    raise TimeoutError("member did not reach ONLINE")


def group_members(conn):
    return q(conn, "SELECT MEMBER_HOST, MEMBER_STATE, MEMBER_ROLE FROM performance_schema.replication_group_members "
                   "ORDER BY MEMBER_HOST")
