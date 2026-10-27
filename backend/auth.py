"""
backend/auth.py
Login / register / logout for StockSense.
 * Users stored in SQLite with salted password hashes.
 * Every page and /api route needs a signed-in session, except /login, /healthz and the auth routes.
 * 5 wrong passwords from one address lock that address for 10 minutes.
 * POST requests must be JSON (simple CSRF protection).

.env / Render environment variables:
   SECRET_KEY    REQUIRED on Render - fixed session signing key (else everyone is logged out on each restart)
   DATA_DIR      folder for users.db. On Render, attach a Persistent Disk (e.g. mount /var/data)
                 and set DATA_DIR=/var/data. Without it the database is wiped on every deploy/restart.
   ALLOW_SIGNUP  1 (default) lets people register, 0 closes registration
   SIGNUP_CODE   if set, registration also needs this invite code
   COOKIE_SECURE 1/0 - defaults to 1 automatically on Render (HTTPS)
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

OPEN_PATHS = {"/login", "/healthz", "/api/login", "/api/register", "/api/me"}
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
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


def init_auth(app, base_dir):
    # Render (and most hosts) put a proxy in front: trust its forwarded IP / HTTPS headers
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    data_dir = os.getenv("DATA_DIR") or os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "users.db")
    frontend = os.path.join(base_dir, "frontend")

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
    dummy_hash = generate_password_hash("not-a-real-password")   # equal timing for unknown users

    def query(sql, args=(), commit=False):
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, args)
            if commit:
                conn.commit()
                return cur.lastrowid
            return cur.fetchone()

    query("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, "
          "username TEXT UNIQUE NOT NULL COLLATE NOCASE, pw_hash TEXT NOT NULL, created_at TEXT NOT NULL)",
          commit=True)

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
        # stop the browser showing a stale login/app page when the user presses Back
        if resp.mimetype == "text/html" or request.path.startswith("/api/") or request.path == "/login":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/login")
    def login_page():
        if session.get("uid"):
            return redirect("/")
        return send_from_directory(frontend, "login.html")

    @app.get("/healthz")
    def healthz():
        return "ok"

    def sign_in(uid, username, remember):
        session.clear()
        session.permanent = bool(remember)   # True = 30-day cookie, False = until browser closes
        session["uid"], session["user"] = uid, username

    @app.post("/api/register")
    def register():
        if os.getenv("ALLOW_SIGNUP", "1") != "1":
            return jsonify(error="Sign-up is closed."), 403
        d = request.get_json(silent=True) or {}
        code = os.getenv("SIGNUP_CODE", "")
        if code and not secrets.compare_digest(str(d.get("code", "")), code):
            return jsonify(error="Wrong invite code."), 403
        user, pw = str(d.get("username", "")).strip(), str(d.get("password", ""))
        if not USER_RE.match(user):
            return jsonify(error="Username: 3-30 letters, numbers, dot, dash or underscore."), 400
        if not 8 <= len(pw) <= 128:
            return jsonify(error="Password must be 8 to 128 characters."), 400
        if "confirm" in d and str(d.get("confirm")) != pw:
            return jsonify(error="Passwords do not match."), 400
        try:
            uid = query("INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
                        (user, generate_password_hash(pw), datetime.now().isoformat(timespec="seconds")),
                        commit=True)
        except sqlite3.IntegrityError:
            return jsonify(error="That username is already taken. Use Sign in instead."), 409
        sign_in(uid, user, d.get("remember", True))
        return jsonify(ok=True, username=user, created=True)

    @app.post("/api/login")
    def login():
        ip = _ip()
        if _locked(ip):
            return jsonify(error="Too many wrong attempts. Try again in 10 minutes."), 429
        d = request.get_json(silent=True) or {}
        user, pw = str(d.get("username", "")).strip(), str(d.get("password", ""))
        row = query("SELECT id, username, pw_hash FROM users WHERE username = ?", (user,))
        ok = check_password_hash(row["pw_hash"] if row else dummy_hash, pw) and row is not None
        if not ok:
            _fail(ip)
            return jsonify(error="Wrong username or password. New here? Use Create account."), 401
        _fails.pop(ip, None)
        sign_in(row["id"], row["username"], d.get("remember", True))
        return jsonify(ok=True, username=row["username"], created=False)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        if not session.get("uid"):
            return jsonify(error="Not signed in"), 401
        return jsonify(username=session.get("user"))
