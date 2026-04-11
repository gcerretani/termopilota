"""
Autenticazione utenti con SQLite + Flask-Login.

L'admin iniziale viene creato dalle variabili d'ambiente ADMIN_USER e ADMIN_PASSWORD
al primo avvio (se la tabella utenti e' vuota).
"""

import os
import sqlite3
import secrets
import logging
from datetime import datetime

from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "users.db")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea la tabella utenti se non esiste. Crea l'admin da ENV se la tabella e' vuota."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # Crea admin da variabili d'ambiente se nessun utente esiste
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        admin_user = os.environ.get("ADMIN_USER", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_pass:
            admin_pass = secrets.token_urlsafe(12)
            logger.warning("ADMIN_PASSWORD non impostata. Password generata: %s", admin_pass)
        create_user(admin_user, admin_pass, is_admin=True, conn=conn)
        logger.info("Utente admin '%s' creato.", admin_user)

    conn.close()


class User(UserMixin):
    """Modello utente per Flask-Login."""

    def __init__(self, id, username, password_hash, is_admin, created_at):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.is_admin = bool(is_admin)
        self.created_at = created_at

    @staticmethod
    def get(user_id) -> "User | None":
        conn = _get_db()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            return User(**dict(row))
        return None

    @staticmethod
    def get_by_username(username: str) -> "User | None":
        conn = _get_db()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if row:
            return User(**dict(row))
        return None


def authenticate(username: str, password: str) -> "User | None":
    """Verifica credenziali. Restituisce User o None."""
    user = User.get_by_username(username)
    if user and check_password_hash(user.password_hash, password):
        return user
    return None


def create_user(username: str, password: str, is_admin: bool = False, conn=None) -> bool:
    """Crea un nuovo utente. Restituisce True se creato, False se username esiste gia'."""
    close = False
    if conn is None:
        conn = _get_db()
        close = True
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), int(is_admin), datetime.now().isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        if close:
            conn.close()


def list_users() -> list:
    """Lista tutti gli utenti (senza password hash)."""
    conn = _get_db()
    rows = conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    """Elimina un utente. Restituisce True se eliminato."""
    conn = _get_db()
    cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def setup_auth(app):
    """Configura Flask-Login sull'app Flask."""
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        secret = secrets.token_hex(32)
        logger.warning("SECRET_KEY non impostata, generata casualmente (le sessioni non sopravvivranno ai riavvii).")
    app.secret_key = secret

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Effettua il login per accedere."
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(int(user_id))

    init_db()
