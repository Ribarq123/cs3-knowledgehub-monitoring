import os
import time
import socket
import sqlite3
from datetime import datetime

import psutil
import psycopg2
import docker


DB_PATH = os.getenv("DB_PATH", "/data/monitoring.db")
DATABASE_URL = os.getenv("DATABASE_URL")
INTERVAL_SECONDS = int(os.getenv("COLLECTOR_INTERVAL", "30"))


def using_postgres():
    return bool(DATABASE_URL)


def get_connection():
    if using_postgres():
        return psycopg2.connect(DATABASE_URL)
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        return sqlite3.connect(DB_PATH)


def execute(query_sqlite, query_postgres=None, params=None):
    query = query_postgres if using_postgres() and query_postgres else query_sqlite
    params = params or ()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def ensure_tables():
    if using_postgres():
        execute(
            "",
            """
            CREATE TABLE IF NOT EXISTS system_metrics (
                id SERIAL PRIMARY KEY,
                timestamp TEXT,
                hostname TEXT,
                cpu_usage REAL,
                memory_usage REAL,
                disk_usage REAL
            )
            """
        )

        execute(
            "",
            """
            CREATE TABLE IF NOT EXISTS container_metrics (
                id SERIAL PRIMARY KEY,
                timestamp TEXT,
                name TEXT,
                container_id TEXT,
                image TEXT,
                status TEXT,
                created TEXT
            )
            """
        )

    else:
        execute(
            """
            CREATE TABLE IF NOT EXISTS system_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                hostname TEXT,
                cpu_usage REAL,
                memory_usage REAL,
                disk_usage REAL
            )
            """
        )

        execute(
            """
            CREATE TABLE IF NOT EXISTS container_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                name TEXT,
                container_id TEXT,
                image TEXT,
                status TEXT,
                created TEXT
            )
            """
        )


def collect_system_metrics():
    timestamp = datetime.utcnow().isoformat()
    hostname = socket.gethostname()
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage("/").percent

    if using_postgres():
        execute(
            "",
            """
            INSERT INTO system_metrics (
                timestamp,
                hostname,
                cpu_usage,
                memory_usage,
                disk_usage
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (timestamp, hostname, cpu_usage, memory_usage, disk_usage)
        )
        print(f"[PostgreSQL] Saved system metrics: CPU={cpu_usage}%, Memory={memory_usage}%, Disk={disk_usage}%")
    else:
        execute(
            """
            INSERT INTO system_metrics (
                timestamp,
                hostname,
                cpu_usage,
                memory_usage,
                disk_usage
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            params=(timestamp, hostname, cpu_usage, memory_usage, disk_usage)
        )
        print(f"[SQLite] Saved system metrics: CPU={cpu_usage}%, Memory={memory_usage}%, Disk={disk_usage}%")


def clear_old_container_metrics():
    if using_postgres():
        execute("", "DELETE FROM container_metrics")
    else:
        execute("DELETE FROM container_metrics")


def insert_container_metric(timestamp, name, container_id, image, status, created):
    if using_postgres():
        execute(
            "",
            """
            INSERT INTO container_metrics (
                timestamp,
                name,
                container_id,
                image,
                status,
                created
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (timestamp, name, container_id, image, status, created)
        )
    else:
        execute(
            """
            INSERT INTO container_metrics (
                timestamp,
                name,
                container_id,
                image,
                status,
                created
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            params=(timestamp, name, container_id, image, status, created)
        )


def collect_container_metrics():
    timestamp = datetime.utcnow().isoformat()

    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)

        clear_old_container_metrics()

        for container in containers:
            image_name = container.image.tags[0] if container.image.tags else container.image.short_id
            insert_container_metric(
                timestamp=timestamp,
                name=container.name,
                container_id=container.short_id,
                image=image_name,
                status=container.status,
                created=container.attrs.get("Created", "")
            )

        print(f"[Docker] Saved {len(containers)} container status rows")

    except Exception as e:
        print(f"[Docker] Could not collect container metrics: {e}")


def main():
    print("Knowledge Hub monitoring collector started")
    print(f"Database mode: {'PostgreSQL' if using_postgres() else 'SQLite'}")

    ensure_tables()

    while True:
        try:
            collect_system_metrics()
            collect_container_metrics()
        except Exception as e:
            print(f"[Collector error] {e}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
