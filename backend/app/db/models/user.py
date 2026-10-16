"""Users of the system: customers and support staff."""

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import Role
from app.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A login. Customers link to a commerce customer id; staff never do."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("(role = 'customer') = (customer_id IS NOT NULL)", name="customer_link"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(str_enum(Role, "user_role"))
    customer_id: Mapped[str | None] = mapped_column(String(64))
