"""initial postgres schema

Revision ID: 0001_initial_postgres_schema
Revises:
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_postgres_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("subcategory", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
    )
    op.create_index("ix_expenses_date", "expenses", ["date"])
    op.create_index("ix_expenses_category", "expenses", ["category"])

    op.create_table(
        "recurring_expenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("subcategory", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "recurring_expense_entries",
        sa.Column("recurring_id", sa.Integer(), nullable=False),
        sa.Column("expense_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recurring_id"],
            ["recurring_expenses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recurring_id", "expense_id"),
    )

    op.create_table(
        "budgets",
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("year_month", "category"),
    )


def downgrade():
    op.drop_table("budgets")
    op.drop_table("recurring_expense_entries")
    op.drop_table("recurring_expenses")
    op.drop_index("ix_expenses_category", table_name="expenses")
    op.drop_index("ix_expenses_date", table_name="expenses")
    op.drop_table("expenses")
