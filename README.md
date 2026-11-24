# mysql-ops-lab

MySQL 8.4 replication, Group Replication and query tuning, run against the real engine in Docker, plus a small replica-health service deployed to Kubernetes.

Site: https://gerardrecinto.github.io/mysql-ops-lab/

## What the tests prove

| Area | Test | Result |
|---|---|---|
| Replication | GTID replication to two replicas, replicas refuse writes | passes |
| Replication | Stop the SQL thread, write 20 rows, see the GTID gap, restart, gap clears | passes |
| Replication | A row written directly on a replica breaks the next insert (error 1062); fix it and resume | passes |
| Failover | Stop the primary, promote a replica, repoint the other with `SOURCE_AUTO_POSITION`, check no transaction was lost | passes |
| Cluster | 3-node Group Replication, one primary, secondaries read-only | passes |
| Cluster | Stop the primary, a new one is elected, writes continue, the old node rejoins as a secondary | passes |
| Tuning | Digest view finds the query that examined 900,000 rows to send 3 | passes |
| Tuning | Missing index: 300,002 rows read before, 61 after | passes |
| Tuning | Composite index removes the filesort; covering index answers from the index alone | passes |
| Tuning | Redundant index report, buffer pool hit ratio (0.9987 on the recorded run) | passes |
| Kubernetes | `replication-watch` deployed to kind answers 200 on a healthy replica and 503 after the SQL thread is stopped | CI |

Numbers above are from a local run. CI repeats every test on each push.

## Run it

```bash
pip install -r requirements.txt
docker compose -f docker-compose.async.yml up -d
python -m pytest tests/test_replication.py tests/test_tuning.py
docker compose -f docker-compose.async.yml down -v

docker compose -f docker-compose.cluster.yml up -d
python -m pytest tests/test_group_replication.py
docker compose -f docker-compose.cluster.yml down -v
```

Needs Docker and Python 3.10+. Each file takes under two minutes.

## replication-watch

A 70-line service: `GET /replication` returns 200 when both threads run and lag is under `MAX_LAG_SECONDS`, 503 with a reason otherwise. `Dockerfile` builds it (non-root, scanned with Trivy), `k8s/lab.yaml` deploys it with a primary and replica, and the `Jenkinsfile` runs test, build, scan, push and deploy with `rollout undo` on failure.

## What is not proven

- The `Jenkinsfile` has not run on a Jenkins server. The same steps run in GitHub Actions.
- Group Replication runs as three containers on one host, so network partitions and slow disks are not tested.
- The data set is 300,000 synthetic rows. Plans on large production tables will differ.
- The Kubernetes lab uses `emptyDir` storage and a plain password. It is not a production layout.

MIT licensed.
