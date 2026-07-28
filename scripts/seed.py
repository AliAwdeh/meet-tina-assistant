from app.auth.passwords import hash_password
from app.core.database import Base, SessionLocal, engine
from app.models.entities import User


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(User).filter(User.email == "admin@example.com").first():
            db.add(
                User(
                    name="Admin",
                    email="admin@example.com",
                    role="admin",
                    password_hash=hash_password("ChangeMeNow123!"),
                )
            )
            db.commit()
            print("Created admin@example.com / ChangeMeNow123!")
        else:
            print("Seed user already exists.")


if __name__ == "__main__":
    main()
