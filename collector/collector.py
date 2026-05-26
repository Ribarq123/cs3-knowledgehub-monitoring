import psutil
import sqlite3
import socket
import time
from datetime import datetime
from pathlib import Path
import os
import docker

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "monitoring.db"
DB_PATH = os.getenv("DB_PATH", str(DEFAULT_DB_PATH))


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT,
            timestamp TEXT,
            cpu_percent REAL,
            memory_percent REAL,
            disk_percent REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS container_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            container_id TEXT,
            name TEXT,
            image TEXT,
            status TEXT,
            created TEXT
        )
    """)

    conn.commit()
    conn.close()


def collect_system_metrics():
    return {
        "hostname": socket.gethostname(),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_percent": psutil.cpu_percent(interval=1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent
    }


def save_system_metrics(metrics):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO system_metrics
        (hostname, timestamp, cpu_percent, memory_percent, disk_percent)
        VALUES (?, ?, ?, ?, ?)
    """, (
        metrics["hostname"],
        metrics["timestamp"],
        metrics["cpu_percent"],
        metrics["memory_percent"],
        metrics["disk_percent"]
    ))

    conn.commit()
    conn.close()


def collect_container_metrics():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    container_data = []

    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)

        for container in containers:
            image_tags = container.image.tags
            image_name = image_tags[0] if image_tags else container.image.short_id

            container_data.append({
                "timestamp": timestamp,
                "container_id": container.short_id,
                "name": container.name,
                "image": image_name,
                "status": container.status,
                "created": container.attrs.get("Created", "unknown")
            })

    except Exception as error:
        container_data.append({
            "timestamp": timestamp,
            "container_id": "error",
            "name": "Docker access error",
            "image": "unknown",
            "status": str(error),
            "created": "unknown"
        })

    return container_data


def save_container_metrics(container_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for item in container_data:
        cursor.execute("""
            INSERT INTO container_metrics
            (timestamp, container_id, name, image, status, created)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            item["timestamp"],
            item["container_id"],
            item["name"],
            item["image"],
            item["status"],
            item["created"]
        ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Monitoring collector started. Database: {DB_PATH}", flush=True)

    while True:
        system_metrics = collect_system_metrics()
        save_system_metrics(system_metrics)

        container_metrics = collect_container_metrics()
        save_container_metrics(container_metrics)

        print(f"Saved system metrics: {system_metrics}", flush=True)
        print(f"Saved container metrics: {len(container_metrics)} containers", flush=True)

        time.sleep(10)
