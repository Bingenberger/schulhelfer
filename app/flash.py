"""Einmalige Rückmeldungen über die Session ("Flash Messages").

Jede zustandsändernde Aktion soll sich selbst bestätigen. Die Meldung
wird in der Session abgelegt, beim nächsten Seitenaufbau ausgegeben und
dabei entfernt.
"""

from starlette.requests import Request

_KEY = "_flash"

SUCCESS = "success"
INFO = "info"
WARNING = "warning"
ERROR = "error"


def flash(request: Request, message: str, category: str = SUCCESS) -> None:
    messages = request.session.get(_KEY, [])
    messages.append({"category": category, "message": message})
    request.session[_KEY] = messages


def get_flashes(request: Request) -> list[dict]:
    messages = request.session.pop(_KEY, [])
    return messages
