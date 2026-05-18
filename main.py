from fastmcp import FastMCP
import asyncio
import calendar
import csv
from datetime import datetime
import json
import os
import tempfile

from sqlalchemy import and_, delete, func, select

from db import get_session
from models import Budget, Expense, RecurringExpense, RecurringExpenseEntry


BASE_DIR = os.path.dirname(__file__)
CATEGORIES_PATH = os.environ.get(
    "EXPENSE_TRACKER_CATEGORIES_PATH",
    os.path.join(BASE_DIR, "categories.json")
)


def is_writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        fd, test_path = tempfile.mkstemp(prefix=".write-test-", dir=path)
        os.close(fd)
        os.remove(test_path)
        return True
    except OSError:
        return False


def default_data_dir():
    configured_data_dir = os.environ.get("EXPENSE_TRACKER_DATA_DIR")
    if configured_data_dir:
        return configured_data_dir

    if is_writable_dir(BASE_DIR):
        return BASE_DIR

    return os.path.join(tempfile.gettempdir(), "expense-tracker")


DATA_DIR = default_data_dir()
EXPORTS_PATH = os.environ.get(
    "EXPENSE_TRACKER_EXPORTS_PATH",
    os.path.join(DATA_DIR, "exports")
)

mcp = FastMCP("ExpenseTracker")


async def load_categories():
    return await asyncio.to_thread(read_categories)


def read_categories():
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


async def validate_category_pair(category, subcategory=""):
    valid_categories = await load_categories()

    if category not in valid_categories:
        return False, f"Invalid category: {category}"

    if subcategory and subcategory not in valid_categories[category]:
        return False, f"Invalid subcategory '{subcategory}' for category '{category}'"

    return True, "Category is valid"


def parse_date(date_text, field_name="date"):
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must use YYYY-MM-DD format")


def parse_year_month(year_month):
    try:
        return datetime.strptime(year_month, "%Y-%m").date()
    except (TypeError, ValueError):
        raise ValueError("year_month must use YYYY-MM format")


def parse_positive_amount(amount):
    try:
        parsed_amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("amount must be a number")

    if parsed_amount <= 0:
        raise ValueError("amount must be greater than zero")

    return parsed_amount


async def validate_expense_data(expense):
    required_fields = ("date", "amount", "category")
    missing_fields = [field for field in required_fields if field not in expense]

    if missing_fields:
        return False, f"Missing required fields: {', '.join(missing_fields)}"

    try:
        parse_date(expense["date"])
        parse_positive_amount(expense["amount"])
    except ValueError as exc:
        return False, str(exc)

    return await validate_category_pair(expense["category"], expense.get("subcategory", ""))


def add_months(date_value, months):
    month_index = date_value.month - 1 + months
    year = date_value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date_value.day, calendar.monthrange(year, month)[1])
    return date_value.replace(year=year, month=month, day=day)


def expense_to_dict(expense):
    return {
        "id": expense.id,
        "date": expense.date.isoformat(),
        "amount": expense.amount,
        "category": expense.category,
        "subcategory": expense.subcategory,
        "note": expense.note,
    }


async def get_expenses_for_range(start_date, end_date):
    start = parse_date(start_date, "start_date")
    end = parse_date(end_date, "end_date")

    async with get_session() as session:
        result = await session.scalars(
            select(Expense)
            .where(Expense.date.between(start, end))
            .order_by(Expense.date.asc(), Expense.id.asc())
        )
        return [expense_to_dict(expense) for expense in result.all()]


def safe_filename_part(value):
    return "".join(
        char if char.isalnum() or char in ("-", "_") else "_"
        for char in str(value)
    )


def escape_pdf_text(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(path, title, lines):
    text_lines = [title, "", *lines]
    content_parts = ["BT", "/F1 11 Tf", "50 770 Td", "14 TL"]

    for line in text_lines:
        content_parts.append(f"({escape_pdf_text(line[:95])}) Tj")
        content_parts.append("T*")

    content_parts.append("ET")
    content = "\n".join(content_parts).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]

    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n")
        offsets = [0]

        for index, obj in enumerate(objects, start=1):
            offsets.append(f.tell())
            f.write(f"{index} 0 obj\n".encode("ascii"))
            f.write(obj)
            f.write(b"\nendobj\n")

        xref_offset = f.tell()
        f.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        f.write(b"0000000000 65535 f \n")

        for offset in offsets[1:]:
            f.write(f"{offset:010d} 00000 n \n".encode("ascii"))

        f.write(
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )


def write_csv_export(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "date", "amount", "category", "subcategory", "note"]
        )
        writer.writeheader()
        writer.writerows(rows)


@mcp.tool()
async def validate_category(category, subcategory=""):
    '''Validate a category and optional subcategory against categories.json.'''
    is_valid, message = await validate_category_pair(category, subcategory)
    return {"status": "ok" if is_valid else "error", "message": message}


@mcp.tool()
async def add_expense(date, amount, category, subcategory="", note=""):
    '''Add a new expense entry to the database.'''
    is_valid, message = await validate_category_pair(category, subcategory)
    if not is_valid:
        return {"status": "error", "message": message}

    try:
        expense_date = parse_date(date)
        amount = parse_positive_amount(amount)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    async with get_session() as session:
        expense = Expense(
            date=expense_date,
            amount=amount,
            category=category,
            subcategory=subcategory,
            note=note,
        )
        session.add(expense)
        await session.commit()
        return {"status": "ok", "id": expense.id}


@mcp.tool()
async def bulk_add_expenses(expenses):
    '''Add multiple expense entries in one transaction.'''
    if not isinstance(expenses, list) or not expenses:
        return {"status": "error", "message": "Expenses must be a non-empty list"}

    prepared_expenses = []
    for index, expense in enumerate(expenses, start=1):
        if not isinstance(expense, dict):
            return {"status": "error", "message": f"Expense #{index} must be an object"}

        is_valid, message = await validate_expense_data(expense)
        if not is_valid:
            return {"status": "error", "message": f"Expense #{index}: {message}"}

        prepared_expenses.append(
            Expense(
                date=parse_date(expense["date"]),
                amount=parse_positive_amount(expense["amount"]),
                category=expense["category"],
                subcategory=expense.get("subcategory", ""),
                note=expense.get("note", ""),
            )
        )

    async with get_session() as session:
        session.add_all(prepared_expenses)
        await session.flush()
        ids = [expense.id for expense in prepared_expenses]
        await session.commit()
        return {"status": "ok", "count": len(ids), "ids": ids}


@mcp.tool()
async def recurring_expense(start_date, amount, category, subcategory="", note="", months=12):
    '''Set up a monthly recurring expense and create its expense entries.'''
    is_valid, message = await validate_category_pair(category, subcategory)
    if not is_valid:
        return {"status": "error", "message": message}

    try:
        start = parse_date(start_date, "start_date")
        amount = parse_positive_amount(amount)
        months = int(months)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    months = max(1, min(months, 120))

    async with get_session() as session:
        recurring = RecurringExpense(
            start_date=start,
            amount=amount,
            category=category,
            subcategory=subcategory,
            note=note,
            months=months,
            active=True,
        )
        session.add(recurring)
        await session.flush()

        expense_ids = []
        for month_offset in range(months):
            expense = Expense(
                date=add_months(start, month_offset),
                amount=amount,
                category=category,
                subcategory=subcategory,
                note=note,
            )
            session.add(expense)
            await session.flush()
            expense_ids.append(expense.id)
            session.add(
                RecurringExpenseEntry(
                    recurring_id=recurring.id,
                    expense_id=expense.id,
                )
            )

        await session.commit()
        return {
            "status": "ok",
            "recurring_id": recurring.id,
            "count": len(expense_ids),
            "expense_ids": expense_ids,
        }


@mcp.tool()
async def list_expenses(start_date, end_date):
    '''List expense entries within an inclusive date range.'''
    try:
        start = parse_date(start_date, "start_date")
        end = parse_date(end_date, "end_date")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    async with get_session() as session:
        result = await session.scalars(
            select(Expense)
            .where(Expense.date.between(start, end))
            .order_by(Expense.id.asc())
        )
        return [expense_to_dict(expense) for expense in result.all()]


@mcp.tool()
async def get_expense(id):
    '''Get a single expense entry by id.'''
    async with get_session() as session:
        expense = await session.get(Expense, id)

        if expense is None:
            return {"status": "error", "message": f"Expense with id {id} not found"}

        return expense_to_dict(expense)


@mcp.tool()
async def edit_expense(id, date=None, amount=None, category=None, subcategory=None, note=None):
    '''Edit an existing expense entry. Only provided fields will be updated.'''
    async with get_session() as session:
        expense = await session.get(Expense, id)
        if expense is None:
            return {"status": "error", "message": f"Expense with id {id} not found"}

        next_category = category if category is not None else expense.category
        next_subcategory = subcategory if subcategory is not None else expense.subcategory
        if category is not None or subcategory is not None:
            is_valid, message = await validate_category_pair(next_category, next_subcategory)
            if not is_valid:
                return {"status": "error", "message": message}

        provided_fields = {
            "date": date,
            "amount": amount,
            "category": category,
            "subcategory": subcategory,
            "note": note,
        }
        provided_fields = {
            field: value
            for field, value in provided_fields.items()
            if value is not None
        }

        if not provided_fields:
            return {"status": "error", "message": "No fields to update"}

        try:
            if date is not None:
                expense.date = parse_date(date)
            if amount is not None:
                expense.amount = parse_positive_amount(amount)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        if category is not None:
            expense.category = category
        if subcategory is not None:
            expense.subcategory = subcategory
        if note is not None:
            expense.note = note

        await session.commit()
        return {"status": "ok", "message": f"Updated expense {id}"}


@mcp.tool()
async def delete_expense(id):
    '''Delete an expense entry by id.'''
    async with get_session() as session:
        result = await session.execute(delete(Expense).where(Expense.id == id))
        await session.commit()

        if result.rowcount == 0:
            return {"status": "error", "message": f"Expense with id {id} not found"}

        return {"status": "ok", "message": f"Deleted expense {id}"}


@mcp.tool()
async def search_expenses(date=None, category=None, amount=None, min_amount=None, max_amount=None):
    '''Search expenses by criteria. Returns all matching entries with IDs.'''
    conditions = []

    try:
        if date is not None:
            conditions.append(Expense.date == parse_date(date))
        if amount is not None:
            conditions.append(Expense.amount == float(amount))
        if min_amount is not None:
            conditions.append(Expense.amount >= float(min_amount))
        if max_amount is not None:
            conditions.append(Expense.amount <= float(max_amount))
    except (TypeError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    if category is not None:
        conditions.append(Expense.category == category)

    query = select(Expense).order_by(Expense.date.desc(), Expense.id.desc())
    if conditions:
        query = query.where(and_(*conditions))

    async with get_session() as session:
        result = await session.scalars(query)
        return [expense_to_dict(expense) for expense in result.all()]


@mcp.tool()
async def recent_expenses(limit=10):
    '''List the most recently added expense entries.'''
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Limit must be a number"}

    limit = max(1, min(limit, 100))

    async with get_session() as session:
        result = await session.scalars(
            select(Expense).order_by(Expense.id.desc()).limit(limit)
        )
        return [expense_to_dict(expense) for expense in result.all()]


@mcp.tool()
async def summarize(start_date, end_date, category=None):
    '''Summarize expenses by category within an inclusive date range.'''
    try:
        start = parse_date(start_date, "start_date")
        end = parse_date(end_date, "end_date")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    query = (
        select(Expense.category, func.sum(Expense.amount).label("total_amount"))
        .where(Expense.date.between(start, end))
        .group_by(Expense.category)
        .order_by(Expense.category.asc())
    )

    if category:
        query = query.where(Expense.category == category)

    async with get_session() as session:
        result = await session.execute(query)
        return [
            {"category": row.category, "total_amount": float(row.total_amount or 0)}
            for row in result.all()
        ]


@mcp.tool()
async def summarize_by_month(year):
    '''Summarize total expenses for each month in a year.'''
    year = str(year)
    try:
        start = parse_date(f"{year}-01-01", "year")
        end = parse_date(f"{year}-12-31", "year")
    except ValueError:
        return {"status": "error", "message": "year must use YYYY format"}

    month_expr = func.to_char(Expense.date, "YYYY-MM")
    query = (
        select(month_expr.label("month"), func.sum(Expense.amount).label("total_amount"))
        .where(Expense.date.between(start, end))
        .group_by(month_expr)
        .order_by(month_expr.asc())
    )

    async with get_session() as session:
        result = await session.execute(query)
        return [
            {"month": row.month, "total_amount": float(row.total_amount or 0)}
            for row in result.all()
        ]


@mcp.tool()
async def export_expenses(start_date, end_date, format="csv"):
    '''Export expenses for a date range to CSV or PDF.'''
    format = format.lower()
    if format not in ("csv", "pdf"):
        return {"status": "error", "message": "format must be csv or pdf"}

    try:
        rows = await get_expenses_for_range(start_date, end_date)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    await asyncio.to_thread(os.makedirs, EXPORTS_PATH, exist_ok=True)
    safe_start = safe_filename_part(start_date)
    safe_end = safe_filename_part(end_date)
    filename = f"expenses_{safe_start}_to_{safe_end}.{format}"
    path = os.path.join(EXPORTS_PATH, filename)

    if format == "csv":
        await asyncio.to_thread(write_csv_export, path, rows)
    else:
        lines = [
            f"{row['date']} | {row['amount']} | {row['category']} | {row['subcategory']} | {row['note']}"
            for row in rows
        ]
        if not lines:
            lines = ["No expenses found for this date range."]

        await asyncio.to_thread(
            write_simple_pdf,
            path,
            f"Expenses from {start_date} to {end_date}",
            lines,
        )

    return {"status": "ok", "format": format, "count": len(rows), "path": path}


@mcp.tool()
async def budget_set(year_month, category, amount):
    '''Set or update a monthly budget for a category. year_month should be YYYY-MM.'''
    try:
        parse_year_month(year_month)
        amount = parse_positive_amount(amount)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    is_valid, message = await validate_category_pair(category)
    if not is_valid:
        return {"status": "error", "message": message}

    async with get_session() as session:
        budget = await session.get(
            Budget,
            {"year_month": year_month, "category": category},
        )
        if budget is None:
            budget = Budget(year_month=year_month, category=category, amount=amount)
            session.add(budget)
        else:
            budget.amount = amount

        await session.commit()

    return {
        "status": "ok",
        "message": f"Budget set for {category} in {year_month}",
        "year_month": year_month,
        "category": category,
        "amount": amount,
    }


@mcp.tool()
async def budget_check(year_month, category=None, warning_threshold=0.8):
    '''Check monthly spending against category budgets.'''
    try:
        month_start = parse_year_month(year_month)
        warning_threshold = float(warning_threshold)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    warning_threshold = max(0, min(warning_threshold, 1))

    if category is not None:
        is_valid, message = await validate_category_pair(category)
        if not is_valid:
            return {"status": "error", "message": message}

    month_end = month_start.replace(
        day=calendar.monthrange(month_start.year, month_start.month)[1]
    )

    budget_query = select(Budget).where(Budget.year_month == year_month)
    if category is not None:
        budget_query = budget_query.where(Budget.category == category)

    async with get_session() as session:
        budgets = (await session.scalars(budget_query)).all()
        results = []

        for budget in budgets:
            spent_amount = await session.scalar(
                select(func.coalesce(func.sum(Expense.amount), 0.0)).where(
                    Expense.date.between(month_start, month_end),
                    Expense.category == budget.category,
                )
            )
            spent_amount = float(spent_amount or 0)
            usage_percent = (spent_amount / budget.amount) if budget.amount else 0

            if spent_amount > budget.amount:
                status = "over_budget"
            elif usage_percent >= warning_threshold:
                status = "near_limit"
            else:
                status = "ok"

            results.append({
                "category": budget.category,
                "budget_amount": budget.amount,
                "spent_amount": spent_amount,
                "remaining_amount": budget.amount - spent_amount,
                "usage_percent": round(usage_percent * 100, 2),
                "status": status,
            })

    return {"status": "ok", "year_month": year_month, "budgets": results}


@mcp.resource("expense://categories", mime_type="application/json")
async def categories():
    return await asyncio.to_thread(read_categories_text)


def read_categories_text():
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
