from watch.app import evaluate

OK = {"Replica_IO_Running": "Yes", "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 0}


def test_healthy():
    assert evaluate(OK)["healthy"] is True


def test_not_configured():
    assert evaluate(None)["reason"] == "replication is not configured"


def test_io_thread_down():
    assert evaluate({**OK, "Replica_IO_Running": "No"})["reason"] == "IO thread is not running"


def test_sql_thread_down_reports_the_error():
    r = evaluate({**OK, "Replica_SQL_Running": "No", "Last_SQL_Error": "Duplicate entry"})
    assert r["healthy"] is False and r["last_error"] == "Duplicate entry"


def test_lag_over_limit_and_unknown_lag():
    assert evaluate({**OK, "Seconds_Behind_Source": 99}, max_lag=30)["healthy"] is False
    assert evaluate({**OK, "Seconds_Behind_Source": None})["reason"] == "lag is unknown"
