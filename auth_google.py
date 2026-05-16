"""
Login con Google OAuth2 / OpenID Connect via Authlib.

Gestione automatica di: state CSRF, scambio code, verifica id_token, JWKS via
OIDC discovery. Il provider viene registrato all'avvio in base alle credenziali
presenti nel config. Cambiare client_id/secret richiede un riavvio.
"""

from authlib.integrations.flask_client import OAuth

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"

oauth = OAuth()


def setup_google_oauth(app, client_id: str, client_secret: str) -> bool:
    """Registra il provider Google su Authlib se le credenziali sono presenti."""
    oauth.init_app(app)
    if not client_id or not client_secret:
        return False
    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=GOOGLE_DISCOVERY,
        client_kwargs={"scope": "openid email profile"},
    )
    return True


def google_attivo() -> bool:
    """True se il provider Google e' stato registrato."""
    return "google" in getattr(oauth, "_clients", {})
