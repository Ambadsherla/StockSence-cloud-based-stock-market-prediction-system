"""
backend/auth.py
Login / register / logout for StockSense.
 * Users are stored in data/users.db (SQLite) with salted password hashes (never plain text).
 * Every page and /api route needs a signed-in session, except /login, /healthz and the auth routes.
 * 5 wrong passwords from one address lock that address for 10 minutes.
 * POST requests must be JSON, which stops cross-site form posts (simple CSRF protection).
.env options (all optional):
   SECRET_KEY    session signing key (auto-generated into data/.secret_key if missing)
   ALLOW_SIGNUP  1 (default) lets people register, 0 closes registration
   SIGNUP_CODE   if set, registration also needs this invite code
   COOKIE_SECURE set to 1 once the site runs on HTTPS
"""
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta

from flask import jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

OPEN_PATHS = {"/login", "/healthz", "/api/login", "/api/register", "/api/me"}
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
MAX_FAILS, WINDOW = 5, 600
_fails = {}   # ip -> [count, first_failure_time]


def _secret(data_dir):
    if os.getenv("SECRET_KEY"):
        return os.getenv("SECRET_KEY")
    path = os.path.join(data_dir, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return open(path).read().strip()


def _ip():
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"


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
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "users.db")
    frontend = os.path.join(base_dir, "frontend")

    app.secret_key = _secret(data_dir)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    dummy_hash = generate_password_hash("not-a-real-password")   # keeps timing equal for unknown users

    def query(sql, args=(), commit=False):
        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, args)
            if commit:
                conn.commit()
            return cur.fetchone() if not commit else cur.lastrowid

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

    @app.get("/login")
    def login_page():
        if session.get("uid"):
            return redirect("/")
        return send_from_directory(frontend, "login.html")

    @app.get("/healthz")
    def healthz():
        return "ok"

    def sign_in(uid, username):
        session.clear()
        session.permanent = True
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
        try:
            uid = query("INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
                        (user, generate_password_hash(pw), datetime.now().isoformat(timespec="seconds")),
                        commit=True)
        except sqlite3.IntegrityError:
            return jsonify(error="That username is already taken."), 409
        sign_in(uid, user)
        return jsonify(ok=True, username=user)

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
            return jsonify(error="Wrong username or password."), 401
        _fails.pop(ip, None)
        sign_in(row["id"], row["username"])
        return jsonify(ok=True, username=row["username"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        if not session.get("uid"):
            return jsonify(error="Not signed in"), 401
        return jsonify(username=session.get("user"))