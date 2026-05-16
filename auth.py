"""
Autenticazione utenti con SQLite + Flask-Login.

L'admin iniziale viene creato dalle variabili d'ambiente ADMIN_USER e ADMIN_PASSWORD
al primo avvio (se la tabella utenti e' vuota).
"""

import logging
import os
import secrets
import sqlite3
from datetime import datetime

from flask_login import LoginManager, UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "users.db")
SECRET_KEY_FILE = os.path.join(DB_DIR, ".secret_key")

# Hash dummy per evitare timing attack su username inesistenti.
_DUMMY_HASH = generate_password_hash("__dummy_password_for_timing_safe_compare__")


def _normalize_email(email):
    if not email:
        return None
    e = str(email).strip().lower()
    return e or None


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
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            google_sub TEXT UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        admin_user = os.environ.get("ADMIN_USER", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "")
        admin_email = _normalize_email(os.environ.get("ADMIN_EMAIL", ""))
        if not admin_pass:
            admin_pass = secrets.token_urlsafe(12)
            logger.warning("ADMIN_PASSWORD non impostata. Password generata: %s", admin_pass)
        create_user(admin_user, admin_pass, is_admin=True, email=admin_email, conn=conn)
        logger.info("Utente admin '%s' creato.", admin_user)

    conn.close()


class User(UserMixin):
    """Modello utente per Flask-Login."""

    def __init__(self, id, username, password_hash, is_admin, created_at,
                 email=None, is_active=1, google_sub=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = bool(is_admin)
        # Flask-Login usa is_active per gestire il login; manteniamo il valore intero
        # come attributo `_active` e esponiamo la property `is_active`.
        self._active = bool(is_active)
        self.google_sub = google_sub
        self.created_at = created_at

    @property
    def is_active(self) -> bool:
        return self._active

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

    @staticmethod
    def get_by_email(email: str) -> "User | None":
        e = _normalize_email(email)
        if not e:
            return None
        conn = _get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (e,)).fetchone()
        conn.close()
        if row:
            return User(**dict(row))
        return None

    @staticmethod
    def get_by_google_sub(google_sub: str) -> "User | None":
        if not google_sub:
            return None
        conn = _get_db()
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        conn.close()
        if row:
            return User(**dict(row))
        return None


def authenticate(username: str, password: str) -> "User | None":
    """Verifica credenziali. Restituisce User o None.

    Esegue sempre un check_password_hash anche se l'utente non esiste, per evitare
    di rivelare quali username sono presenti tramite analisi dei tempi di risposta.
    """
    user = User.get_by_username(username)
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return None
    if not user.is_active:
        return None
    if not check_password_hash(user.password_hash, password):
        return None
    return user


def create_user(username: str, password: str, is_admin: bool = False,
                email=None, conn=None) -> bool:
    """Crea un nuovo utente. Restituisce True se creato, False se username/email esistono."""
    close = False
    if conn is None:
        conn = _get_db()
        close = True
    try:
        conn.execute(
            """INSERT INTO users (username, email, password_hash, is_admin, is_active, created_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (username, _normalize_email(email), generate_password_hash(password),
             int(is_admin), datetime.now().isoformat()),
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
    rows = conn.execute(
        """SELECT id, username, email, is_admin, is_active, google_sub, created_at
           FROM users ORDER BY id"""
    ).fetchall()
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


def count_admin_attivi() -> int:
    conn = _get_db()
    row = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()
    conn.close()
    return row[0]


def update_user_email(user_id: int, email) -> bool:
    """Aggiorna l'email di un utente. Restituisce False in caso di conflitto unique."""
    conn = _get_db()
    try:
        cur = conn.execute("UPDATE users SET email = ? WHERE id = ?",
                           (_normalize_email(email), user_id))
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def set_admin(user_id: int, is_admin: bool) -> bool:
    """Promuove/declassa un utente. Restituisce False se sarebbe l'ultimo admin attivo."""
    if not is_admin:
        user = User.get(user_id)
        if user and user.is_admin and user.is_active and count_admin_attivi() <= 1:
            return False
    conn = _get_db()
    cur = conn.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                       (int(bool(is_admin)), user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_active(user_id: int, active: bool) -> bool:
    """Attiva/disattiva un utente. Restituisce False se sarebbe l'ultimo admin attivo."""
    if not active:
        user = User.get(user_id)
        if user and user.is_admin and user.is_active and count_admin_attivi() <= 1:
            return False
    conn = _get_db()
    cur = conn.execute("UPDATE users SET is_active = ? WHERE id = ?",
                       (int(bool(active)), user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_password(user_id: int, new_password: str) -> bool:
    """Imposta la password di un utente (uso admin)."""
    if not new_password:
        return False
    conn = _get_db()
    cur = conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(new_password), user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Cambio password self-service: richiede la password attuale."""
    if not new_password:
        return False
    user = User.get(user_id)
    if user is None or not check_password_hash(user.password_hash, old_password):
        return False
    return set_password(user_id, new_password)


def link_google_account(user_id: int, google_sub: str) -> bool:
    """Associa un account Google a un utente locale (solo se non ne ha gia' uno)."""
    if not google_sub:
        return False
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE users SET google_sub = ? WHERE id = ? AND google_sub IS NULL",
            (google_sub, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def _carica_o_crea_secret_key() -> str:
    """Carica SECRET_KEY da ENV o da file persistente. Genera se manca."""
    env_secret = os.environ.get("SECRET_KEY", "").strip()
    if env_secret:
        return env_secret
    os.makedirs(DB_DIR, exist_ok=True)
    if os.path.exists(SECRET_KEY_FILE):
        try:
            with open(SECRET_KEY_FILE, encoding="utf-8") as f:
                stored = f.read().strip()
                if stored:
                    return stored
        except OSError:
            pass
    nuovo = secrets.token_hex(32)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(SECRET_KEY_FILE, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(nuovo)
    except OSError as e:
        logger.warning("Impossibile salvare SECRET_KEY su disco: %s", e)
    return nuovo


def setup_auth(app):
    """Configura Flask-Login sull'app Flask."""
    app.secret_key = _carica_o_crea_secret_key()

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.login_message = "Effettua il login per accedere."
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.get(int(user_id))
        if user is None or not user.is_active:
            return None
        return user

    init_db()
