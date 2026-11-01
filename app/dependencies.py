from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser
from app.security import verify_csrf


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    admin = db.get(AdminUser, admin_id)
    if not admin:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return admin


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    return get_current_admin(request, db)


async def require_csrf(request: Request) -> None:
    """Prüft das CSRF-Token zustandsändernder Admin-Formulare.

    ``request.form()`` wird von FastAPI zwischengespeichert, die
    eigentlichen Formularparameter der Route bleiben also lesbar.
    """
    form = await request.form()
    submitted = str(form.get("csrf_token") or "")
    if not verify_csrf(request, submitted):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Das Sicherheitstoken des Formulars ist abgelaufen. "
                "Bitte laden Sie die Seite neu und versuchen Sie es erneut."
            ),
        )
