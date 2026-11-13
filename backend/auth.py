"""
backend/auth.py
Login / register / logout for StockSense.
 * Users stored in SQLite with salted password hashes.
 * Registration requires Full Name, Email, Password, Confirm Password.
 * Registration does NOT automatically log in; redirects to Login page.
 * Login authenticates via Email and Password.
 * Every page and /api route needs a signed-in session, except /login, /register, /healthz, and auth endpoints.
 * 5 wrong passwords from one IP lock that IP for 10 minutes.
 * POST requests must be JSON (CSRF protection).
"""
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta

from flask import jsonify, redirect, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

OPEN_PATHS = {"/login", "/register", "/healthz", "/api/login", "/api/register", "/api/me"}
EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
MAX_FAILS, WINDOW = 5, 600
REMEMBER_DAYS = 30
_fails = {}   # ip -> [count, first_failure_time]


def _secret(data_dir):
    if os.getenv("SECRET_KEY"):
        return os.getenv("SECRET_KEY")
    path = os.path.join(data_dir, ".secret_key")
    if not os.path.exists(path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:      # another worker created it first
            pass
    with open(path) as f:
        return f.read().strip()


def _ip():
    return request.remote_addr or "?"   # real client IP via ProxyFix


def _locked(ip):
    rec = _fails.get(ip)
    if rec and time.time() - rec[1] > WINDOW:
        _fails.pop(ip, None)
        return False
    return bool(rec and rec[0] >= MAX_FAILS)


def _fail(ip):
    rec = _fails.get(ip)
    if not rec or time.time() - rec[1] > WINDOW:
        _fails[ip] = [1, time.time()]
    else:
        rec[0] += 1


def _init_db(db_path):
    """Initializes and migrates the database schema safely while preserving existing data."""
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                pw_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("PRAGMA table_info(users)")
        cols = {row["name"].lower(): row for row in cur.fetchall()}
        if "full_name" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        if "email" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT COLLATE NOCASE")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.commit()


def init_auth(app, base_dir):
    # Render (and most hosts) put a proxy in front: trust its forwarded IP / HTTPS headers
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    data_dir = os.getenv("DATA_DIR") or os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "users.db")
    frontend = os.path.join(base_dir, "frontend")

    _init_db(db_path)

    on_render = bool(os.getenv("RENDER"))
    secure_default = "1" if on_render else "0"
    app.secret_key = _secret(data_dir)
    app.config.update(
        SESSION_COOKIE_NAME="stocksense_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", secure_default) == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=REMEMBER_DAYS),
        SESSION_REFRESH_EACH_REQUEST=True,   # active users stay signed in
    )

    def query(sql, args=(), commit=False):
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, args)
            if commit:
                conn.commit()
                return cur.lastrowid
            return cur.fetchone()

    @app.before_request
    def gate():
        if request.method == "POST" and not request.is_json:
            return jsonify(error="JSON body required"), 400
        if request.path in OPEN_PATHS or session.get("uid"):
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="Not signed in"), 401
        return redirect("/login")

    @app.after_request
    def no_cache(resp):
        # Stop browser from showing stale login/app page when pressing Back
        if resp.mimetype == "text/html" or request.path.startswith("/api/") or request.path in ("/login", "/register"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    @app.get("/login")
    def login_page():
        if session.get("uid"):
            return redirect("/")
        return send_from_directory(frontend, "login.html")

    @app.get("/register")
    def register_page():
        if session.get("uid"):
            return redirect("/")
        return send_from_directory(frontend, "login.html")

    @app.get("/healthz")
    def healthz():
        return "ok"

    def sign_in(uid, name, email=None, remember=True):
        session.clear()
        session.permanent = bool(remember)   # True = 30-day cookie, False = until browser closes
        session["uid"] = uid
        session["user"] = name
        if email:
            session["email"] = email

    @app.post("/api/register")
    def register():
        if os.getenv("ALLOW_SIGNUP", "1") != "1":
            return jsonify(error="Sign-up is closed."), 403

        d = request.get_json(silent=True) or {}
        code = os.getenv("SIGNUP_CODE", "")
        if code and not secrets.compare_digest(str(d.get("code", "")), code):
            return jsonify(error="Wrong invite code."), 403

        full_name = str(d.get("full_name") or d.get("name") or "").strip()
        email = str(d.get("email") or "").strip().lower()
        pw = str(d.get("password") or "")
        confirm = str(d.get("confirm") or "")

        # Form Validation
        if not full_name or len(full_name) < 2 or len(full_name) > 100:
            return jsonify(error="Please enter your full name (2 to 100 characters)."), 400

        if not email or not EMAIL_RE.match(email) or len(email) > 120:
            return jsonify(error="Please enter a valid email address."), 400

        if not 8 <= len(pw) <= 128:
            return jsonify(error="Password must be between 8 and 128 characters."), 400

        if pw != confirm:
            return jsonify(error="Passwords do not match."), 400

        # Check duplicate email or username
        existing = query(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE OR username = ? COLLATE NOCASE",
            (email, email)
        )
        if existing:
            return jsonify(error="Email is already registered. Please login."), 409

        now_str = datetime.now().isoformat(timespec="seconds")
        try:
            query(
                "INSERT INTO users (username, full_name, email, pw_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (email, full_name, email, generate_password_hash(pw), now_str),
                commit=True
            )
        except sqlite3.IntegrityError:
            return jsonify(error="Email is already registered. Please login."), 409

        # Crucial requirement: Do NOT log in the user automatically.
        return jsonify(
            ok=True,
            message="Account created successfully! Please login.",
            email=email
        )

    @app.post("/api/login")
    def login():
        ip = _ip()
        if _locked(ip):
            return jsonify(error="Too many wrong attempts. Try again in 10 minutes."), 429

        d = request.get_json(silent=True) or {}
        email = str(d.get("email") or d.get("username") or "").strip().lower()
        pw = str(d.get("password") or "")

        if not email or not pw:
            return jsonify(error="Please enter your email and password."), 400

        # Look up user by registered email (or legacy username)
        row = query(
            "SELECT id, username, full_name, email, pw_hash FROM users "
            "WHERE email = ? COLLATE NOCASE OR (email IS NULL AND username = ? COLLATE NOCASE)",
            (email, email)
        )

        if not row:
            return jsonify(error="Account not found. Please create an account."), 404

        if not check_password_hash(row["pw_hash"], pw):
            _fail(ip)
            return jsonify(error="Invalid email or password."), 401

        _fails.pop(ip, None)
        display_name = row["full_name"] or row["username"] or row["email"]
        sign_in(row["id"], display_name, email=row["email"] or row["username"], remember=d.get("remember", True))
        return jsonify(ok=True, username=display_name, email=row["email"], created=False)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True, message="Logged out successfully")

    @app.get("/api/me")
    def me():
        if not session.get("uid"):
            return jsonify(error="Not signed in"), 401
        return jsonify(
            username=session.get("user"),
            email=session.get("email"),
            full_name=session.get("user")
        )
