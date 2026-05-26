from flask import Flask, render_template, jsonify, redirect, url_for, session, request
import sqlite3
from pathlib import Path
import os
import msal
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "dev-session-secret")

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "monitoring.db"
DB_PATH = os.getenv("DB_PATH", str(DEFAULT_DB_PATH))

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/auth/callback")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = ["User.Read"]


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


def build_msal_app():
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )


def build_auth_url():
    return build_msal_app().get_authorization_request_url(
        scopes=SCOPE,
        redirect_uri=REDIRECT_URI
    )


def is_logged_in():
    return "user" in session


def get_latest_metrics():
    try:
        init_db()

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
            "hostname": "Database error",
            "timestamp": "No data available",
            "cpu_percent": 0,
            "memory_percent": 0,
            "disk_percent": 0,
            "source": f"Database error: {error}"
        }

    return {
        "hostname": "Waiting for collector",
        "timestamp": "No data yet",
        "cpu_percent": 0,
        "memory_percent": 0,
        "disk_percent": 0,
        "source": "SQLite database initialized, waiting for monitoring data"
    }


def get_metric_history():
    try:
        init_db()

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
        init_db()

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
    if not is_logged_in():
        return redirect(url_for("login"))

    init_db()
    metrics = get_latest_metrics()
    history = get_metric_history()
    containers = get_latest_container_metrics()

    return render_template(
        "dashboard.html",
        metrics=metrics,
        history=history,
        containers=containers,
        user=session.get("user")
    )


@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET or not TENANT_ID:
        return "Authentication is not configured. Check CLIENT_ID, CLIENT_SECRET, and TENANT_ID.", 500

    auth_url = build_auth_url()
    return render_template("login.html", auth_url=auth_url)


@app.route("/auth/callback")
def auth_callback():
    if "error" in request.args:
        return f"Login error: {request.args.get('error_description')}", 400

    if "code" not in request.args:
        return "Login failed: no authorization code returned.", 400

    result = build_msal_app().acquire_token_by_authorization_code(
        request.args["code"],
        scopes=SCOPE,
        redirect_uri=REDIRECT_URI
    )

    if "id_token_claims" not in result:
        return f"Login failed: {result.get('error_description', 'Unknown error')}", 400

    claims = result["id_token_claims"]

    session["user"] = {
        "name": claims.get("name", "Unknown user"),
        "email": claims.get("preferred_username", "unknown")
    }

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/metrics")
def api_metrics():
    if not is_logged_in():
        return jsonify({"error": "authentication required"}), 401

    return jsonify(get_latest_metrics())


@app.route("/api/history")
def api_history():
    if not is_logged_in():
        return jsonify({"error": "authentication required"}), 401

    return jsonify(get_metric_history())


@app.route("/api/containers")
def api_containers():
    if not is_logged_in():
        return jsonify({"error": "authentication required"}), 401

    return jsonify(get_latest_container_metrics())


@app.route("/health")
def health():
    init_db()

    return jsonify({
        "status": "healthy",
        "database": DB_PATH,
        "auth_configured": bool(CLIENT_ID and CLIENT_SECRET and TENANT_ID)
    })


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
