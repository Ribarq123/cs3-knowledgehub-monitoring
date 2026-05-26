from flask import Flask, render_template, jsonify
import sqlite3
from pathlib import Path
import os

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "monitoring.db"
DB_PATH = os.getenv("DB_PATH", str(DEFAULT_DB_PATH))


def get_latest_metrics():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM system_metrics
            ORDER BY id DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        conn.close()

        if row:
            metrics = dict(row)
            metrics["source"] = "SQLite monitoring database"
            return metrics

    except Exception as error:
        return {
            "hostname": "Database not ready",
            "timestamp": "No data available",
            "cpu_percent": 0,
            "memory_percent": 0,
            "disk_percent": 0,
            "source": f"Database error: {error}"
        }

    return {
        "hostname": "No data yet",
        "timestamp": "No data yet",
        "cpu_percent": 0,
        "memory_percent": 0,
        "disk_percent": 0,
        "source": "Waiting for collector"
    }


def get_metric_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM system_metrics
            ORDER BY id DESC
            LIMIT 10
        """)

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception:
        return []


def get_latest_container_metrics():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM container_metrics
            WHERE timestamp = (
                SELECT MAX(timestamp)
                FROM container_metrics
            )
            ORDER BY name ASC
        """)

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception:
        return []


@app.route("/")
def dashboard():
    metrics = get_latest_metrics()
    history = get_metric_history()
    containers = get_latest_container_metrics()
    return render_template(
        "dashboard.html",
        metrics=metrics,
        history=history,
        containers=containers
    )


@app.route("/api/metrics")
def api_metrics():
    return jsonify(get_latest_metrics())


@app.route("/api/history")
def api_history():
    return jsonify(get_metric_history())


@app.route("/api/containers")
def api_containers():
    return jsonify(get_latest_container_metrics())


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "database": DB_PATH
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
