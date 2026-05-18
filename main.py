from fastmcp import FastMCP
import calendar
import csv
from datetime import datetime
import json
import os
import sqlite3
import tempfile

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
DB_PATH = os.environ.get(
    "EXPENSE_TRACKER_DB_PATH",
    os.path.join(DATA_DIR, "expenses.db")
)
EXPORTS_PATH = os.environ.get(
    "EXPENSE_TRACKER_EXPORTS_PATH",
    os.path.join(DATA_DIR, "exports")
)

mcp = FastMCP("ExpenseTracker")

def db_connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    with db_connect() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS recurring_expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT '',
                months INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS recurring_expense_entries(
                recurring_id INTEGER NOT NULL,
                expense_id INTEGER NOT NULL,
                PRIMARY KEY (recurring_id, expense_id),
                FOREIGN KEY (recurring_id) REFERENCES recurring_expenses(id),
                FOREIGN KEY (expense_id) REFERENCES expenses(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS budgets(
                year_month TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                PRIMARY KEY (year_month, category)
            )
        """)

init_db()

def load_categories():
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def rows_to_dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

def validate_category_pair(category, subcategory=""):
    valid_categories = load_categories()

    if category not in valid_categories:
        return False, f"Invalid category: {category}"

    if subcategory and subcategory not in valid_categories[category]:
        return False, f"Invalid subcategory '{subcategory}' for category '{category}'"

    return True, "Category is valid"

def validate_expense_data(expense):
    required_fields = ("date", "amount", "category")
    missing_fields = [field for field in required_fields if field not in expense]

    if missing_fields:
        return False, f"Missing required fields: {', '.join(missing_fields)}"

    try:
        amount = float(expense["amount"])
    except (TypeError, ValueError):
        return False, "amount must be a number"

    if amount <= 0:
        return False, "amount must be greater than zero"

    return validate_category_pair(expense["category"], expense.get("subcategory", ""))

def parse_monthly_date(date_text):
    return datetime.strptime(date_text, "%Y-%m-%d").date()

def add_months(date_value, months):
    month_index = date_value.month - 1 + months
    year = date_value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date_value.day, calendar.monthrange(year, month)[1])
    return date_value.replace(year=year, month=month, day=day)

def get_expenses_for_range(start_date, end_date):
    with db_connect() as c:
        cur = c.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN ? AND ?
            ORDER BY date ASC, id ASC
            """,
            (start_date, end_date)
        )
        return rows_to_dicts(cur)

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

@mcp.tool()
def validate_category(category, subcategory=""):
    '''Validate a category and optional subcategory against categories.json.'''
    is_valid, message = validate_category_pair(category, subcategory)
    return {"status": "ok" if is_valid else "error", "message": message}

@mcp.tool()
def add_expense(date, amount, category, subcategory="", note=""):
    '''Add a new expense entry to the database.'''
    is_valid, message = validate_category_pair(category, subcategory)
    if not is_valid:
        return {"status": "error", "message": message}

    with db_connect() as c:
        cur = c.execute(
            "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
            (date, amount, category, subcategory, note)
        )
        return {"status": "ok", "id": cur.lastrowid}

@mcp.tool()
def bulk_add_expenses(expenses):
    '''Add multiple expense entries in one transaction.'''
    if not isinstance(expenses, list) or not expenses:
        return {"status": "error", "message": "Expenses must be a non-empty list"}

    for index, expense in enumerate(expenses, start=1):
        if not isinstance(expense, dict):
            return {"status": "error", "message": f"Expense #{index} must be an object"}

        is_valid, message = validate_expense_data(expense)
        if not is_valid:
            return {"status": "error", "message": f"Expense #{index}: {message}"}

    with db_connect() as c:
        ids = []

        for expense in expenses:
            cur = c.execute(
                "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
                (
                    expense["date"],
                    expense["amount"],
                    expense["category"],
                    expense.get("subcategory", ""),
                    expense.get("note", "")
                )
            )
            ids.append(cur.lastrowid)

        return {"status": "ok", "count": len(ids), "ids": ids}

@mcp.tool()
def recurring_expense(start_date, amount, category, subcategory="", note="", months=12):
    '''Set up a monthly recurring expense and create its expense entries.'''
    is_valid, message = validate_category_pair(category, subcategory)
    if not is_valid:
        return {"status": "error", "message": message}

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"status": "error", "message": "amount must be a number"}

    if amount <= 0:
        return {"status": "error", "message": "amount must be greater than zero"}

    try:
        start = parse_monthly_date(start_date)
    except ValueError:
        return {"status": "error", "message": "start_date must use YYYY-MM-DD format"}

    try:
        months = int(months)
    except (TypeError, ValueError):
        return {"status": "error", "message": "months must be a number"}

    months = max(1, min(months, 120))

    with db_connect() as c:
        cur = c.execute(
            """
            INSERT INTO recurring_expenses(start_date, amount, category, subcategory, note, months)
            VALUES (?,?,?,?,?,?)
            """,
            (start_date, amount, category, subcategory, note, months)
        )
        recurring_id = cur.lastrowid
        expense_ids = []

        for month_offset in range(months):
            expense_date = add_months(start, month_offset).isoformat()
            cur = c.execute(
                "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
                (expense_date, amount, category, subcategory, note)
            )
            expense_id = cur.lastrowid
            expense_ids.append(expense_id)
            c.execute(
                """
                INSERT INTO recurring_expense_entries(recurring_id, expense_id)
                VALUES (?,?)
                """,
                (recurring_id, expense_id)
            )

        return {
            "status": "ok",
            "recurring_id": recurring_id,
            "count": len(expense_ids),
            "expense_ids": expense_ids
        }
    
@mcp.tool()
def list_expenses(start_date, end_date):
    '''List expense entries within an inclusive date range.'''
    with db_connect() as c:
        cur = c.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN ? AND ?
            ORDER BY id ASC
            """,
            (start_date, end_date)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

@mcp.tool()
def get_expense(id):
    '''Get a single expense entry by id.'''
    with db_connect() as c:
        cur = c.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            WHERE id = ?
            """,
            (id,)
        )
        row = cur.fetchone()

        if row is None:
            return {"status": "error", "message": f"Expense with id {id} not found"}

        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

@mcp.tool()
def edit_expense(id, date=None, amount=None, category=None, subcategory=None, note=None):
    '''Edit an existing expense entry. Only provided fields will be updated.'''
    with db_connect() as c:
        if category is not None or subcategory is not None:
            cur = c.execute(
                "SELECT category, subcategory FROM expenses WHERE id = ?",
                (id,)
            )
            row = cur.fetchone()

            if row is None:
                return {"status": "error", "message": f"Expense with id {id} not found"}

            current_category, current_subcategory = row
            next_category = category if category is not None else current_category
            next_subcategory = subcategory if subcategory is not None else current_subcategory
            is_valid, message = validate_category_pair(next_category, next_subcategory)

            if not is_valid:
                return {"status": "error", "message": message}

        fields = {
            "date": date,
            "amount": amount,
            "category": category,
            "subcategory": subcategory,
            "note": note,
        }
        provided_fields = {
            field: value
            for field, value in fields.items()
            if value is not None
        }
        
        if not provided_fields:
            return {"status": "error", "message": "No fields to update"}
        
        updates = [f"{field} = ?" for field in provided_fields]
        params = [*provided_fields.values(), id]
        query = f"UPDATE expenses SET {', '.join(updates)} WHERE id = ?"
        cur = c.execute(query, params)
        
        if cur.rowcount == 0:
            return {"status": "error", "message": f"Expense with id {id} not found"}
        
        return {"status": "ok", "message": f"Updated expense {id}"}

@mcp.tool()
def delete_expense(id):
    '''Delete an expense entry by id.'''
    with db_connect() as c:
        cur = c.execute("DELETE FROM expenses WHERE id = ?", (id,))
        
        if cur.rowcount == 0:
            return {"status": "error", "message": f"Expense with id {id} not found"}
        
        return {"status": "ok", "message": f"Deleted expense {id}"}

@mcp.tool()
def search_expenses(date=None, category=None, amount=None, min_amount=None, max_amount=None):
    '''Search expenses by criteria. Returns all matching entries with IDs.'''
    with db_connect() as c:
        conditions = []
        params = []
        
        # Build dynamic WHERE clauses
        field_map = {'date': date, 'category': category, 'amount': amount}
        for field, value in field_map.items():
            if value is not None:
                conditions.append(f"{field} = ?")
                params.append(value)
        
        if min_amount is not None:
            conditions.append("amount >= ?")
            params.append(min_amount)
        
        if max_amount is not None:
            conditions.append("amount <= ?")
            params.append(max_amount)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT id, date, amount, category, subcategory, note FROM expenses WHERE {where_clause} ORDER BY date DESC, id DESC"
        
        cur = c.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

@mcp.tool()
def recent_expenses(limit=10):
    '''List the most recently added expense entries.'''
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Limit must be a number"}

    limit = max(1, min(limit, 100))

    with db_connect() as c:
        cur = c.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

@mcp.tool()
def summarize(start_date, end_date, category=None):
    '''Summarize expenses by category within an inclusive date range.'''
    with db_connect() as c:
        query = (
            """
            SELECT category, SUM(amount) AS total_amount
            FROM expenses
            WHERE date BETWEEN ? AND ?
            """
        )
        params = [start_date, end_date]

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " GROUP BY category ORDER BY category ASC"

        cur = c.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

@mcp.tool()
def summarize_by_month(year):
    '''Summarize total expenses for each month in a year.'''
    year = str(year)
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    with db_connect() as c:
        cur = c.execute(
            """
            SELECT substr(date, 1, 7) AS month, SUM(amount) AS total_amount
            FROM expenses
            WHERE date BETWEEN ? AND ?
            GROUP BY month
            ORDER BY month ASC
            """,
            (start_date, end_date)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

@mcp.tool()
def export_expenses(start_date, end_date, format="csv"):
    '''Export expenses for a date range to CSV or PDF.'''
    format = format.lower()
    if format not in ("csv", "pdf"):
        return {"status": "error", "message": "format must be csv or pdf"}

    rows = get_expenses_for_range(start_date, end_date)
    os.makedirs(EXPORTS_PATH, exist_ok=True)
    safe_start = safe_filename_part(start_date)
    safe_end = safe_filename_part(end_date)
    filename = f"expenses_{safe_start}_to_{safe_end}.{format}"
    path = os.path.join(EXPORTS_PATH, filename)

    if format == "csv":
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["id", "date", "amount", "category", "subcategory", "note"]
            )
            writer.writeheader()
            writer.writerows(rows)
    else:
        lines = [
            f"{row['date']} | {row['amount']} | {row['category']} | {row['subcategory']} | {row['note']}"
            for row in rows
        ]
        if not lines:
            lines = ["No expenses found for this date range."]

        write_simple_pdf(path, f"Expenses from {start_date} to {end_date}", lines)

    return {"status": "ok", "format": format, "count": len(rows), "path": path}

@mcp.tool()
def budget_set(year_month, category, amount):
    '''Set or update a monthly budget for a category. year_month should be YYYY-MM.'''
    try:
        datetime.strptime(year_month, "%Y-%m")
    except ValueError:
        return {"status": "error", "message": "year_month must use YYYY-MM format"}

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"status": "error", "message": "amount must be a number"}

    if amount <= 0:
        return {"status": "error", "message": "amount must be greater than zero"}

    is_valid, message = validate_category_pair(category)
    if not is_valid:
        return {"status": "error", "message": message}

    with db_connect() as c:
        c.execute(
            """
            INSERT INTO budgets(year_month, category, amount)
            VALUES (?,?,?)
            ON CONFLICT(year_month, category) DO UPDATE SET amount = excluded.amount
            """,
            (year_month, category, amount)
        )

    return {
        "status": "ok",
        "message": f"Budget set for {category} in {year_month}",
        "year_month": year_month,
        "category": category,
        "amount": amount
    }

@mcp.tool()
def budget_check(year_month, category=None, warning_threshold=0.8):
    '''Check monthly spending against category budgets.'''
    try:
        month_start = datetime.strptime(year_month, "%Y-%m").date()
    except ValueError:
        return {"status": "error", "message": "year_month must use YYYY-MM format"}

    try:
        warning_threshold = float(warning_threshold)
    except (TypeError, ValueError):
        return {"status": "error", "message": "warning_threshold must be a number"}

    warning_threshold = max(0, min(warning_threshold, 1))

    if category is not None:
        is_valid, message = validate_category_pair(category)
        if not is_valid:
            return {"status": "error", "message": message}

    month_end = month_start.replace(
        day=calendar.monthrange(month_start.year, month_start.month)[1]
    )

    with db_connect() as c:
        query = "SELECT category, amount FROM budgets WHERE year_month = ?"
        params = [year_month]

        if category is not None:
            query += " AND category = ?"
            params.append(category)

        budgets = c.execute(query, params).fetchall()
        results = []

        for budget_category, budget_amount in budgets:
            cur = c.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM expenses
                WHERE date BETWEEN ? AND ? AND category = ?
                """,
                (month_start.isoformat(), month_end.isoformat(), budget_category)
            )
            spent_amount = cur.fetchone()[0]
            usage_percent = (spent_amount / budget_amount) if budget_amount else 0

            if spent_amount > budget_amount:
                status = "over_budget"
            elif usage_percent >= warning_threshold:
                status = "near_limit"
            else:
                status = "ok"

            results.append({
                "category": budget_category,
                "budget_amount": budget_amount,
                "spent_amount": spent_amount,
                "remaining_amount": budget_amount - spent_amount,
                "usage_percent": round(usage_percent * 100, 2),
                "status": status
            })

    return {"status": "ok", "year_month": year_month, "budgets": results}

@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    # Read fresh each time so you can edit the file without restarting
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
