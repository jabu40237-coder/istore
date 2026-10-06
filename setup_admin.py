"""First-run setup: creates the SUPER_ADMIN account. Run once:

    ./venv/bin/python setup_admin.py <email> <username> <password>
"""
import sys

from db import init_db
from services.auth import ensure_roles, create_user
from models import User, Role, UserRole
from db import get_session


def main():
    if len(sys.argv) != 4:
        print("usage: setup_admin.py <email> <username> <password>")
        sys.exit(1)
    email, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    init_db()
    ensure_roles()
    uid, err = create_user(email, username, password, name="Admin",
                           roles=("SUPER_ADMIN", "ADMIN"))
    if err == "email_exists":
        # promote existing user
        db = get_session()
        try:
            u = db.query(User).filter_by(email=email).first()
            for rn in ("SUPER_ADMIN", "ADMIN"):
                r = db.query(Role).filter_by(name=rn).first()
                if r and not db.query(UserRole).filter_by(
                        user_id=u.id, role_id=r.id).first():
                    db.add(UserRole(user_id=u.id, role_id=r.id))
            db.commit()
            print(f"promoted existing user {email} to SUPER_ADMIN")
        finally:
            db.close()
        return
    if err:
        print("error:", err)
        sys.exit(1)
    print(f"SUPER_ADMIN created: {email} (id={uid})")


if __name__ == "__main__":
    main()
