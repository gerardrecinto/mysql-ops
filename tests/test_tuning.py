"""Query tuning on real MySQL 8.4: find the slow query, read the plan, fix it, prove it. Needs docker-compose.async.yml."""
import pytest

from lab import mysqlops as m

ROWS = 300_000


@pytest.fixture(scope="module")
def db():
    m.wait_ready(33061)
    c = m.connect(33061)
    m.q(c, "DROP DATABASE IF EXISTS tune")
    m.q(c, "CREATE DATABASE tune")
    m.q(c, "USE tune")
    m.q(c, "CREATE TABLE orders (id INT PRIMARY KEY AUTO_INCREMENT, customer_id INT NOT NULL, "
           "status ENUM('open','paid','shipped','closed') NOT NULL, created_at DATETIME NOT NULL, "
           "amount DECIMAL(10,2) NOT NULL, note VARCHAR(100) NOT NULL DEFAULT 'x')")
    m.q(c, "SET SESSION cte_max_recursion_depth = %s", (ROWS + 10,))
    m.q(c, "INSERT INTO orders (customer_id, status, created_at, amount) "
           "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < %s) "
           "SELECT 1 + (i * 7919) %% 5000, ELT(1 + i %% 4, 'open','paid','shipped','closed'), "
           "TIMESTAMP('2026-01-01') + INTERVAL (i %% 200000) MINUTE, ROUND(10 + (i %% 9000) / 10, 2) FROM n", (ROWS,))
    m.q(c, "ANALYZE TABLE orders")
    return c


def rows_read(c, sql, args=None):
    """Rows the storage engine had to touch: scan reads plus index reads."""
    m.q(c, "FLUSH STATUS")
    m.q(c, sql, args)
    st = {r["Variable_name"]: int(r["Value"]) for r in m.q(c, "SHOW SESSION STATUS LIKE 'Handler_read_%'")}
    return st["Handler_read_rnd_next"] + st["Handler_read_next"] + st["Handler_read_key"]


def plan(c, sql):
    return m.one(c, "EXPLAIN " + sql)


def test_table_has_the_expected_rows(db):
    assert m.one(db, "SELECT COUNT(*) n FROM orders")["n"] == ROWS


def test_digest_view_finds_the_expensive_query_by_rows_examined(db):
    m.q(db, "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest")
    for cust in (42, 43, 44):
        m.q(db, "SELECT COUNT(*), SUM(amount) FROM orders WHERE customer_id = %s", (cust,))
    top = m.one(db, "SELECT DIGEST_TEXT t, SUM_ROWS_EXAMINED ex, SUM_ROWS_SENT sent "
                    "FROM performance_schema.events_statements_summary_by_digest "
                    "WHERE SCHEMA_NAME='tune' AND DIGEST_TEXT LIKE '%FROM `orders`%' ORDER BY SUM_ROWS_EXAMINED DESC LIMIT 1")
    assert "customer_id" in top["t"]
    assert int(top["ex"]) >= 3 * ROWS * 0.9, "full scans were not recorded"
    print(f"examined {top['ex']} rows to send {top['sent']}")


def test_missing_index_full_scan_then_index_lookup(db):
    sql = "SELECT COUNT(*), SUM(amount) FROM orders WHERE customer_id = 42"
    assert plan(db, sql)["type"] == "ALL"
    before = rows_read(db, sql)
    m.q(db, "ALTER TABLE orders ADD INDEX idx_customer (customer_id)")
    assert plan(db, sql)["type"] == "ref"
    after = rows_read(db, sql)
    print(f"rows read: {before} -> {after}")
    assert before > ROWS * 0.9 and after < 200 and before / after > 1000


def test_composite_index_removes_filesort(db):
    sql = "SELECT id, amount FROM orders WHERE status = 'open' AND created_at >= '2026-03-01' ORDER BY created_at LIMIT 20"
    m.q(db, "ALTER TABLE orders ADD INDEX idx_status (status)")
    assert "filesort" in (plan(db, sql)["Extra"] or "").lower()
    m.q(db, "ALTER TABLE orders ADD INDEX idx_status_created (status, created_at)")
    extra = plan(db, sql)["Extra"] or ""
    assert "filesort" not in extra.lower()
    assert rows_read(db, sql) < 100


def test_covering_index_answers_from_the_index_alone(db):
    sql = "SELECT customer_id, status, amount FROM orders WHERE customer_id = 7"
    assert "Using index" not in (plan(db, sql)["Extra"] or "")
    m.q(db, "ALTER TABLE orders ADD INDEX idx_cover (customer_id, status, amount)")
    assert "Using index" in (plan(db, sql)["Extra"] or "")


def test_redundant_index_report_and_buffer_pool_hit_ratio(db):
    for _ in range(3):
        m.q(db, "SELECT COUNT(*) FROM orders WHERE customer_id BETWEEN 1 AND 500")
    st = {r["Variable_name"]: int(r["Value"]) for r in m.q(
        db, "SHOW GLOBAL STATUS WHERE Variable_name IN ('Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads')")}
    ratio = 1 - st["Innodb_buffer_pool_reads"] / st["Innodb_buffer_pool_read_requests"]
    print(f"buffer pool hit ratio {ratio:.4f}")
    assert ratio > 0.95
    # Redundant index: idx_customer is a left prefix of idx_cover, so it can be dropped.
    dup = m.q(db, "SELECT redundant_index_name AS index_name FROM sys.schema_redundant_indexes WHERE table_schema='tune' AND table_name='orders'")
    names = {r["index_name"] for r in dup}
    assert {"idx_customer", "idx_status"} <= names
    m.q(db, "SET GLOBAL innodb_buffer_pool_size = 268435456")
    assert int(m.one(db, "SELECT @@innodb_buffer_pool_size v")["v"]) == 268435456
