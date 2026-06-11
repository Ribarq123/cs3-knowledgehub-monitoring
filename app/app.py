import os
import sqlite3
from functools import wraps

import msal
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, jsonify, redirect, request, session, url_for


app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "dev-session-secret-change-me")

DB_PATH = os.getenv("DB_PATH", "/data/monitoring.db")
DATABASE_URL = os.getenv("DATABASE_URL")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/auth/callback")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else None
SCOPES = ["User.Read"]


# -------------------------
# Database helpers
# -------------------------

def using_postgres():
    return bool(DATABASE_URL)


def get_connection():
    if using_postgres():
        return psycopg2.connect(DATABASE_URL)
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def execute_query(query_sqlite, query_postgres=None, params=None, fetchone=False, fetchall=False):
    query = query_postgres if using_postgres() and query_postgres else query_sqlite
    params = params or ()

    conn = get_connection()
    try:
        if using_postgres():
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cursor = conn.cursor()

        cursor.execute(query, params)

        result = None
        if fetchone:
            result = cursor.fetchone()
        elif fetchall:
            result = cursor.fetchall()

        conn.commit()
        cursor.close()

        if result is None:
            return None

        if using_postgres():
            return result

        if fetchone:
            return dict(result) if result else None

        return [dict(row) for row in result]

    finally:
        conn.close()


def ensure_tables():
    if using_postgres():
        execute_query(
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

        execute_query(
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
        execute_query(
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

        execute_query(
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


def get_latest_metrics():
    ensure_tables()
    return execute_query(
        """
        SELECT timestamp, hostname, cpu_usage, memory_usage, disk_usage
        FROM system_metrics
        ORDER BY id DESC
        LIMIT 1
        """,
        fetchone=True
    )


def get_history():
    ensure_tables()
    return execute_query(
        """
        SELECT timestamp, hostname, cpu_usage, memory_usage, disk_usage
        FROM system_metrics
        ORDER BY id DESC
        LIMIT 10
        """,
        fetchall=True
    )


def get_container_metrics():
    ensure_tables()
    return execute_query(
        """
        SELECT timestamp, name, container_id, image, status
        FROM container_metrics
        ORDER BY id DESC
        LIMIT 10
        """,
        fetchall=True
    )


# -------------------------
# Authentication helpers
# -------------------------

def auth_configured():
    return bool(CLIENT_ID and CLIENT_SECRET and TENANT_ID and REDIRECT_URI)


def build_msal_app():
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )


def build_auth_url():
    msal_app = build_msal_app()
    return msal_app.get_authorization_request_url(
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if auth_configured() and "user" not in session:
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


# -------------------------
# Routes
# -------------------------

@app.route("/")
@login_required
def dashboard():
    latest = get_latest_metrics()
    history = get_history()
    containers = get_container_metrics()

    if latest:
        metrics = latest
        data_source = "Azure PostgreSQL database" if using_postgres() else "SQLite database"
    else:
        metrics = {
            "hostname": "Waiting for collector",
            "timestamp": "No data yet",
            "cpu_usage": 0,
            "memory_usage": 0,
            "disk_usage": 0
        }
        data_source = "Azure PostgreSQL database initialized, waiting for monitoring data" if using_postgres() else "SQLite database initialized, waiting for monitoring data"

    return render_template(
        "dashboard.html",
        metrics=metrics,
        history=history,
        containers=containers,
        data_source=data_source,
        user=session.get("user")
    )


@app.route("/login")
def login():
    if not auth_configured():
        session["user"] = {
            "name": "Local Development User",
            "preferred_username": "local"
        }
        return redirect(url_for("dashboard"))

    auth_url = build_auth_url()
    return render_template("login.html", auth_url=auth_url)


@app.route("/auth/callback")
def auth_callback():
    if not auth_configured():
        return redirect(url_for("dashboard"))

    code = request.args.get("code")
    if not code:
        return "Authentication failed: no authorization code returned.", 400

    msal_app = build_msal_app()
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    if "error" in result:
        return f"Authentication failed: {result.get('error_description')}", 400

    claims = result.get("id_token_claims", {})
    session["user"] = {
        "name": claims.get("name", "Unknown user"),
        "preferred_username": claims.get("preferred_username", claims.get("upn", "unknown"))
    }

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/metrics")
@login_required
def api_metrics():
    latest = get_latest_metrics()
    if not latest:
        return jsonify({"message": "No monitoring data yet"}), 404
    return jsonify(latest)


@app.route("/api/history")
@login_required
def api_history():
    return jsonify(get_history())


@app.route("/api/containers")
@login_required
def api_containers():
    return jsonify(get_container_metrics())


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "database": "postgresql" if using_postgres() else "sqlite",
        "db_path": DB_PATH if not using_postgres() else "DATABASE_URL configured",
        "auth_configured": auth_configured()
    })


if __name__ == "__main__":
    ensure_tables()
    app.run(host="0.0.0.0", port=5000)
