"""Gemeinsames Jinja-Environment für alle Router.

Bündelt die Template-Globals, damit Flash-Messages und CSRF-Felder in
jedem Template verfügbar sind, ohne sie durch jeden einzelnen
Response-Kontext zu reichen.
"""

from markupsafe import Markup, escape
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import settings
from app.flash import get_flashes
from app.security import csrf_token

templates = Jinja2Templates(directory="templates")


def csrf_input(request: Request) -> Markup:
    """Verstecktes Formularfeld mit dem CSRF-Token der Session."""
    return Markup(
        f'<input type="hidden" name="csrf_token" value="{escape(csrf_token(request))}">'
    )


def icon(name: str, fill: bool = False, cls: str = "") -> Markup:
    """Phosphor-Icon (lokal unter /static/vendor/phosphor eingebunden).

    Rein dekorativ und daher für Screenreader ausgeblendet: Die Bedeutung
    muss immer zusätzlich im Text stehen.
    """
    weight = "ph-fill" if fill else "ph"
    extra = f" {escape(cls)}" if cls else ""
    return Markup(f'<i class="{weight} ph-{escape(name)}{extra}" aria-hidden="true"></i>')


templates.env.globals["csrf_input"] = csrf_input
templates.env.globals["icon"] = icon
templates.env.globals["get_flashes"] = get_flashes
templates.env.globals["settings"] = settings
