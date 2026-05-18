from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    subcategory: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")


class RecurringExpense(Base):
    __tablename__ = "recurring_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    subcategory: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    entries: Mapped[list["RecurringExpenseEntry"]] = relationship(
        back_populates="recurring_expense",
        cascade="all, delete-orphan",
    )


class RecurringExpenseEntry(Base):
    __tablename__ = "recurring_expense_entries"

    recurring_id: Mapped[int] = mapped_column(
        ForeignKey("recurring_expenses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"),
        primary_key=True,
    )

    recurring_expense: Mapped[RecurringExpense] = relationship(back_populates="entries")
    expense: Mapped[Expense] = relationship()


class Budget(Base):
    __tablename__ = "budgets"

    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    category: Mapped[str] = mapped_column(String(100), primary_key=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
