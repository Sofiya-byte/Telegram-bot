from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Product(Base):
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    store = Column(String, nullable=False)
    price = Column(Float, nullable=False)

    def __repr__(self):
        return f"Product(name={self.name}, store={self.store}, price={self.price})"


class Admin(Base):
    __tablename__ = 'admins'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True)

    def __repr__(self):
        return f"Admin(user_id={self.user_id})"
