from database import init_db, SessionLocal
from models import Product, Admin


def populate_database():
    init_db()
    db = SessionLocal()

    db.query(Product).delete()
    db.query(Admin).delete()

    # Заменить айди на свой, для получения прав администратора
    admin = Admin(user_id=-1)
    db.add(admin)

    db.commit()

    db.close()


if __name__ == "__main__":
    populate_database()
