from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = INSTANCE_DIR / "school.db"
UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_RESTORE_EXT = {"db", "sqlite", "sqlite3"}
ADMIN_LOGIN_PATH = "/xtspolsjhulupjoppsup-lmkzcodup"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SecureAdmin!42")
ADMIN_ROLES = {"Admin"}
USER_ROLES = {"Staff"}
ALL_ROLES = ("Admin", "Staff")

app = Flask(__name__, instance_path=str(INSTANCE_DIR), instance_relative_config=True)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-school-portal-secret-change-me"),
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    TEMPLATES_AUTO_RELOAD=True,
)

UPLOAD_DIR.mkdir(exist_ok=True)


# -------------------------
# Database helpers
# -------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def ensure_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    name = column_def.split()[0]
    if name not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def init_db() -> None:
    INSTANCE_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Admin', 'Staff')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS school_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                school_name TEXT NOT NULL DEFAULT 'School',
                admission_prefix TEXT NOT NULL DEFAULT 'ADM-',
                admission_suffix TEXT NOT NULL DEFAULT '',
                student_name_prefix TEXT NOT NULL DEFAULT '',
                student_name_suffix TEXT NOT NULL DEFAULT '',
                currency_code TEXT NOT NULL DEFAULT 'KES'
            );

            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_no TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                grade TEXT NOT NULL,
                guardian_name TEXT,
                guardian_phone TEXT,
                guardian_email TEXT,
                alt_guardian_name TEXT,
                alt_guardian_phone TEXT,
                alt_guardian_email TEXT,
                student_phone TEXT,
                student_email TEXT,
                medical_condition TEXT,
                allergies TEXT,
                special_info TEXT,
                notes TEXT,
                payment_status TEXT NOT NULL DEFAULT 'Pending' CHECK(payment_status IN ('Paid', 'Pending')),
                balance REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                reference_no TEXT,
                recorded_by INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Posted' CHECK(status IN ('Posted', 'Reversed')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reversed_at TEXT,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(recorded_by) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                actor_name TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE SET NULL
            );
            """
        )

        ensure_column(conn, "school_settings", "school_name TEXT NOT NULL DEFAULT 'School'")
        ensure_column(conn, "school_settings", "admission_prefix TEXT NOT NULL DEFAULT 'ADM-'")
        ensure_column(conn, "school_settings", "admission_suffix TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "school_settings", "student_name_prefix TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "school_settings", "student_name_suffix TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "school_settings", "currency_code TEXT NOT NULL DEFAULT 'KES'")

        ensure_column(conn, "students", "guardian_name TEXT")
        ensure_column(conn, "students", "guardian_phone TEXT")
        ensure_column(conn, "students", "guardian_email TEXT")
        ensure_column(conn, "students", "alt_guardian_name TEXT")
        ensure_column(conn, "students", "alt_guardian_phone TEXT")
        ensure_column(conn, "students", "alt_guardian_email TEXT")
        ensure_column(conn, "students", "student_phone TEXT")
        ensure_column(conn, "students", "student_email TEXT")
        ensure_column(conn, "students", "medical_condition TEXT")
        ensure_column(conn, "students", "allergies TEXT")
        ensure_column(conn, "students", "special_info TEXT")
        ensure_column(conn, "students", "notes TEXT")
        ensure_column(conn, "students", "deleted_at TEXT")
        ensure_column(conn, "users", "deleted_at TEXT")

        if conn.execute("SELECT COUNT(*) AS c FROM school_settings").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO school_settings(id, school_name, admission_prefix, admission_suffix, student_name_prefix, student_name_suffix, currency_code) VALUES (1, ?, ?, ?, ?, ?, ?)",
                ("School", "ADM-", "", "", "", "KES"),
            )

        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO users(full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                ("System Administrator", ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), "Admin"),
            )
            conn.execute(
                "INSERT INTO users(full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                ("Finance Staff", "staff", generate_password_hash("SecureStaff!42"), "Staff"),
            )
        else:
            # Keep the seeded accounts usable even if an old database exists.
            seed_rows = [
                (ADMIN_USERNAME, "System Administrator", "Admin", ADMIN_PASSWORD),
                ("staff", "Finance Staff", "Staff", "SecureStaff!42"),
            ]
            for username, full_name, role, password in seed_rows:
                row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE users SET full_name = ?, role = ?, password_hash = ?, deleted_at = NULL WHERE username = ?",
                        (full_name, role, generate_password_hash(password), username),
                    )
                else:
                    conn.execute(
                        "INSERT INTO users(full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
                        (full_name, username, generate_password_hash(password), role),
                    )
        if conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"] == 0:
            students = [
                (
                    "ADM-A001", "Aisha M.", "Grade 8",
                    "Mary M.", "0700000001", "mary@example.com",
                    None, None, None,
                    None, None,
                    None, None,
                    "Requires quiet seating during exams.",
                    None,
                    "Paid", 0.0, 1
                ),
                (
                    "ADM-A002", "Brian K.", "Grade 9",
                    "John K.", "0700000002", "john@example.com",
                    None, None, None,
                    None, None,
                    "Asthma", None,
                    None,
                    None,
                    "Pending", 12000.0, 1
                ),
                (
                    "ADM-A003", "Zuri N.", "Grade 7",
                    "Esther N.", "0700000003", "esther@example.com",
                    None, None, None,
                    None, None,
                    None, "Peanut allergy",
                    None,
                    None,
                    "Paid", 0.0, 1
                ),
                (
                    "ADM-A004", "Kelvin O.", "Grade 10",
                    "Paul O.", "0700000004", "paul@example.com",
                    None, None, None,
                    None, None,
                    None, None,
                    None,
                    "Needs transport accountability notes.",
                    "Pending", 8000.0, 1
                ),
            ]

            conn.executemany(
                """
                INSERT INTO students(
                    admission_no, full_name, grade,
                    guardian_name, guardian_phone, guardian_email,
                    alt_guardian_name, alt_guardian_phone, alt_guardian_email,
                    student_phone, student_email,
                    medical_condition, allergies, special_info, notes,
                    payment_status, balance, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                students,
            )

        if conn.execute("SELECT COUNT(*) AS c FROM payments").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO payments(student_id, amount, method, reference_no, recorded_by, status) VALUES (1, 15000, 'M-Pesa', 'MPESA-0001', 1, 'Posted')"
            )
            conn.execute(
                "INSERT INTO payments(student_id, amount, method, reference_no, recorded_by, status) VALUES (2, 3000, 'Cash', 'CASH-0002', 2, 'Posted')"
            )

        if conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO audit_log(actor_id, actor_name, action, details) VALUES (1, 'System Administrator', 'System Seed', 'Initial users, students and payments created.')"
            )
        conn.commit()


def q(sql: str, params: Iterable[Any] = (), one: bool = False):
    cur = get_db().execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    db = get_db()
    cur = db.execute(sql, tuple(params))
    db.commit()
    return cur.lastrowid


def audit(actor_id: int | None, actor_name: str, action: str, details: str) -> None:
    execute(
        "INSERT INTO audit_log(actor_id, actor_name, action, details) VALUES (?, ?, ?, ?)",
        (actor_id, actor_name, action, details),
    )


# -------------------------
# Session / auth helpers
# -------------------------
@app.before_request
def load_current_user() -> None:
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        g.user = q("SELECT id, full_name, username, role FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,), one=True)


def current_user() -> sqlite3.Row | None:
    return getattr(g, "user", None)


def login_required(view: Callable):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


def role_required(*roles: str):
    def decorator(view: Callable):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user or user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def workspace_for(role: str) -> str:
    return "Admin Command Centre" if role == "Admin" else "User & Finance Workspace"


def allowed_filename(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_RESTORE_EXT


def school_settings() -> sqlite3.Row:
    row = q("SELECT * FROM school_settings WHERE id = 1", one=True)
    if row is None:
        execute(
            "INSERT OR IGNORE INTO school_settings(id, school_name, admission_prefix, admission_suffix, student_name_prefix, student_name_suffix, currency_code) VALUES (1, 'School', 'ADM-', '', '', '', 'KES')"
        )
        row = q("SELECT * FROM school_settings WHERE id = 1", one=True)
    return row


def next_admission_no() -> str:
    settings = school_settings()
    prefix = settings["admission_prefix"] or "ADM-"
    suffix = settings["admission_suffix"] or ""
    like = f"{prefix}%{suffix}" if suffix else f"{prefix}%"
    row = q("SELECT admission_no FROM students WHERE admission_no LIKE ? ORDER BY id DESC LIMIT 1", (like,), one=True)
    next_num = 1
    if row and row["admission_no"]:
        middle = row["admission_no"]
        if suffix and middle.endswith(suffix):
            middle = middle[len(prefix):-len(suffix)]
        else:
            middle = middle[len(prefix):]
        digits = "".join(ch for ch in middle if ch.isdigit())
        try:
            next_num = int(digits) + 1
        except ValueError:
            next_num = q("SELECT COUNT(*) AS c FROM students", one=True)["c"] + 1
    return f"{prefix}{next_num:03d}{suffix}"


# -------------------------
# Context / templates
# -------------------------
@app.context_processor
def inject_globals():
    settings = school_settings()
    return {
        "now_year": datetime.now().year,
        "current_user": current_user(),
        "workspace_for": workspace_for,
        "school_settings": settings,
        "admin_login_path": ADMIN_LOGIN_PATH,
        "theme_color": "#f3f4f6",
        "theme_accent": "#10a37f",
    }


# -------------------------
# Routes
# -------------------------
@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if user["role"] == "Admin" else "dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        user = current_user()
        return redirect(url_for("admin_dashboard" if user["role"] == "Admin" else "dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = q("SELECT * FROM users WHERE username = ? AND deleted_at IS NULL", (username,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            if user["role"] == "Admin":
                error = "Use the administrator entry point for admin access."
            else:
                session.clear()
                session["user_id"] = user["id"]
                audit(user["id"], user["full_name"], "Login", f"{user['username']} logged in.")
                return redirect(url_for("dashboard"))
        else:
            error = "Invalid username or password."

    return render_template("login.html", error=error, portal_title=school_settings()["school_name"], mode="user")


@app.route(ADMIN_LOGIN_PATH, methods=["GET", "POST"])
def admin_login():
    if current_user():
        user = current_user()
        if user["role"] == "Admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = q("SELECT * FROM users WHERE username = ? AND deleted_at IS NULL", (username,), one=True)
        if user and user["role"] == "Admin" and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            audit(user["id"], user["full_name"], "Admin Login", f"{user['username']} logged in through the admin entry point.")
            return redirect(url_for("admin_dashboard"))
        error = "Invalid administrator credentials."

    return render_template("login.html", error=error, portal_title=school_settings()["school_name"], mode="admin")


@app.route("/coming-soon/<feature>")
def coming_soon(feature: str):
    labels = {
        "google": "Google sign-in",
        "microsoft": "Microsoft sign-in",
        "forgot-password": "Password recovery",
    }
    flash(f"{labels.get(feature, feature.replace('-', ' ').title())} coming soon.", "warning")
    return redirect(request.referrer or url_for("login"))


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        audit(user["id"], user["full_name"], "Logout", f"{user['username']} logged out.")
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


@app.route("/sw.js")
def service_worker():
    return send_file(BASE_DIR / "static" / "sw.js", mimetype="application/javascript", conditional=True, max_age=0)


@app.route("/dashboard")
@login_required
@role_required("Staff")
def dashboard():
    user = current_user()
    settings = school_settings()
    selected_grade = request.args.get("grade", "").strip() or None
    grade_params = (selected_grade,) if selected_grade else ()
    student_where = "WHERE deleted_at IS NULL AND grade = ?" if selected_grade else "WHERE deleted_at IS NULL"
    payment_where = "WHERE s.deleted_at IS NULL AND s.grade = ?" if selected_grade else "WHERE s.deleted_at IS NULL"

    students = q(f"SELECT * FROM students {student_where} ORDER BY created_at DESC, id DESC LIMIT 24", grade_params)
    payments = q(
        f"""
        SELECT p.*, s.full_name AS student_name, s.admission_no, u.full_name AS recorded_by_name
        FROM payments p
        JOIN students s ON s.id = p.student_id
        JOIN users u ON u.id = p.recorded_by
        {payment_where}
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 24
        """,
        grade_params,
    )
    audits = q("SELECT * FROM audit_log ORDER BY created_at DESC, id DESC LIMIT 12")
    available_grades = [row["grade"] for row in q("SELECT DISTINCT grade FROM students WHERE deleted_at IS NULL ORDER BY grade")]

    summary_students = q(f"SELECT COUNT(*) AS c FROM students {student_where}", grade_params, one=True)["c"]
    summary_active = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND grade = ? AND active = 1" if selected_grade else "SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND active = 1", grade_params if selected_grade else (), one=True)["c"]
    summary_paid = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND grade = ? AND payment_status = 'Paid'" if selected_grade else "SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND payment_status = 'Paid'", grade_params if selected_grade else (), one=True)["c"]
    summary_pending = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND grade = ? AND payment_status = 'Pending'" if selected_grade else "SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND payment_status = 'Pending'", grade_params if selected_grade else (), one=True)["c"]
    summary_collections = q("SELECT COALESCE(SUM(amount), 0) AS total FROM payments p JOIN students s ON s.id = p.student_id WHERE p.status = 'Posted' AND s.grade = ?" if selected_grade else "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE status = 'Posted'", grade_params if selected_grade else (), one=True)["total"]
    total_balance = q("SELECT COALESCE(SUM(balance), 0) AS total FROM students WHERE deleted_at IS NULL AND active = 1", one=True)["total"]

    summary = {
        "students": summary_students,
        "active_students": summary_active,
        "collections": summary_collections,
        "paid": summary_paid,
        "pending": summary_pending,
        "balance": total_balance,
    }

    selected_student_id = request.args.get("student_id", type=int)
    selected_student = None
    student_payments = []
    if selected_student_id:
        selected_student = q("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (selected_student_id,), one=True)
        if selected_grade and selected_student and selected_student["grade"] != selected_grade:
            selected_student = None
        if selected_student:
            student_payments = q(
                """
                SELECT p.*, u.full_name AS recorded_by_name
                FROM payments p
                JOIN users u ON u.id = p.recorded_by
                WHERE p.student_id = ?
                ORDER BY p.created_at DESC, p.id DESC
                """,
                (selected_student_id,),
            )
    if not selected_student and students:
        selected_student = students[0]
        student_payments = q(
            """
            SELECT p.*, u.full_name AS recorded_by_name
            FROM payments p
            JOIN users u ON u.id = p.recorded_by
            WHERE p.student_id = ?
            ORDER BY p.created_at DESC, p.id DESC
            """,
            (students[0]["id"],),
        )

    return render_template(
        "dashboard.html",
        role=user["role"],
        workspace=workspace_for(user["role"]),
        summary=summary,
        students=students,
        payments=payments,
        audits=audits,
        selected_student=selected_student,
        selected_student_payments=student_payments,
        selected_grade=selected_grade,
        available_grades=available_grades,
        settings=settings,
    )


@app.route("/admin-dashboard")
@login_required
@role_required("Admin")
def admin_dashboard():
    settings = school_settings()
    students = q("SELECT * FROM students WHERE deleted_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 20")
    payments = q(
        """
        SELECT p.*, s.full_name AS student_name, s.admission_no, u.full_name AS recorded_by_name
        FROM payments p
        JOIN students s ON s.id = p.student_id
        JOIN users u ON u.id = p.recorded_by
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 20
        """
    )
    users = q("SELECT id, full_name, username, role, created_at FROM users WHERE deleted_at IS NULL ORDER BY created_at DESC")
    audits = q("SELECT * FROM audit_log ORDER BY created_at DESC, id DESC LIMIT 20")
    total_students = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL", one=True)["c"]
    active_students = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND active = 1", one=True)["c"]
    paid_students = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND payment_status = 'Paid'", one=True)["c"]
    pending_students = q("SELECT COUNT(*) AS c FROM students WHERE deleted_at IS NULL AND payment_status = 'Pending'", one=True)["c"]
    total_income = q("SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE status = 'Posted'", one=True)["total"]
    total_balance = q("SELECT COALESCE(SUM(balance), 0) AS total FROM students WHERE deleted_at IS NULL", one=True)["total"]
    avg_balance = q("SELECT COALESCE(AVG(balance), 0) AS total FROM students", one=True)["total"]
    categories = {
        "students": q("SELECT grade, COUNT(*) AS c FROM students GROUP BY grade ORDER BY grade"),
        "employees": q("SELECT role, COUNT(*) AS c FROM users GROUP BY role ORDER BY role"),
        "payments": q("SELECT method, COUNT(*) AS c FROM payments WHERE status = 'Posted' GROUP BY method ORDER BY method"),
    }
    return render_template(
        "admin_dashboard.html",
        workspace=workspace_for("Admin"),
        settings=settings,
        students=students,
        payments=payments,
        users=users,
        audits=audits,
        summary={
            "total_students": total_students,
            "active_students": active_students,
            "paid_students": paid_students,
            "pending_students": pending_students,
            "total_income": total_income,
            "total_balance": total_balance,
            "avg_balance": avg_balance,
        },
        categories=categories,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        if not full_name:
            flash("Full name is required.", "danger")
        else:
            if password:
                execute("UPDATE users SET full_name = ?, password_hash = ? WHERE id = ?", (full_name, generate_password_hash(password), user["id"]))
            else:
                execute("UPDATE users SET full_name = ? WHERE id = ?", (full_name, user["id"]))
            session["user_id"] = user["id"]
            audit(user["id"], full_name, "Profile Update", "Profile details updated.")
            flash("Profile updated successfully.", "success")
            return redirect(url_for("profile"))
    profile_user = q("SELECT id, full_name, username, role, created_at FROM users WHERE id = ?", (user["id"],), one=True)
    return render_template("profile.html", profile_user=profile_user, workspace=workspace_for(profile_user["role"]))


@app.route("/settings", methods=["POST"])
@login_required
@role_required("Admin")
def save_settings():
    school_name = request.form.get("school_name", "").strip() or "School"
    admission_prefix = request.form.get("admission_prefix", "").strip() or "ADM-"
    admission_suffix = request.form.get("admission_suffix", "").strip()
    student_name_prefix = request.form.get("student_name_prefix", "").strip()
    student_name_suffix = request.form.get("student_name_suffix", "").strip()
    currency_code = request.form.get("currency_code", "").strip() or "KES"
    execute(
        """
        UPDATE school_settings
        SET school_name = ?, admission_prefix = ?, admission_suffix = ?, student_name_prefix = ?, student_name_suffix = ?, currency_code = ?
        WHERE id = 1
        """,
        (school_name, admission_prefix, admission_suffix, student_name_prefix, student_name_suffix, currency_code),
    )
    audit(current_user()["id"], current_user()["full_name"], "Update Settings", f"School settings updated for {school_name}.")
    flash("School settings updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/students/add", methods=["POST"])
@login_required
def add_student():
    admission_no = request.form.get("admission_no", "").strip()
    full_name = request.form.get("full_name", "").strip()
    grade = request.form.get("grade", "").strip()
    guardian_name = request.form.get("guardian_name", "").strip()
    guardian_phone = request.form.get("guardian_phone", "").strip()
    guardian_email = request.form.get("guardian_email", "").strip()
    alt_guardian_name = request.form.get("alt_guardian_name", "").strip()
    alt_guardian_phone = request.form.get("alt_guardian_phone", "").strip()
    alt_guardian_email = request.form.get("alt_guardian_email", "").strip()
    student_phone = request.form.get("student_phone", "").strip()
    student_email = request.form.get("student_email", "").strip()
    medical_condition = request.form.get("medical_condition", "").strip()
    allergies = request.form.get("allergies", "").strip()
    special_info = request.form.get("special_info", "").strip()
    notes = request.form.get("notes", "").strip()
    if not full_name or not grade:
        flash("Student name and grade are required.", "danger")
        return redirect(request.referrer or url_for("dashboard"))

    settings = school_settings()
    if not admission_no:
        admission_no = next_admission_no()
    if settings["student_name_prefix"]:
        full_name = f"{settings['student_name_prefix']} {full_name}".strip()
    if settings["student_name_suffix"]:
        full_name = f"{full_name} {settings['student_name_suffix']}".strip()

    try:
        execute(
            """
            INSERT INTO students(
                admission_no, full_name, grade,
                guardian_name, guardian_phone, guardian_email,
                alt_guardian_name, alt_guardian_phone, alt_guardian_email,
                student_phone, student_email, medical_condition, allergies, special_info, notes,
                payment_status, balance, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', 0, 1)
            """,
            (
                admission_no,
                full_name,
                grade,
                guardian_name,
                guardian_phone,
                guardian_email,
                alt_guardian_name,
                alt_guardian_phone,
                alt_guardian_email,
                student_phone,
                student_email,
                medical_condition,
                allergies,
                special_info,
                notes,
            ),
        )
        audit(current_user()["id"], current_user()["full_name"], "Add Student", f"{full_name} ({admission_no}) created.")
        flash("Student added.", "success")
    except sqlite3.IntegrityError:
        flash("Admission number already exists.", "danger")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/students/<int:student_id>/update", methods=["POST"])
@login_required
def update_student(student_id: int):
    student = q("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        abort(404)
    fields = {
        "full_name": request.form.get("full_name", student["full_name"]).strip(),
        "admission_no": request.form.get("admission_no", student["admission_no"]).strip(),
        "grade": request.form.get("grade", student["grade"]).strip(),
        "guardian_name": request.form.get("guardian_name", "").strip(),
        "guardian_phone": request.form.get("guardian_phone", "").strip(),
        "guardian_email": request.form.get("guardian_email", "").strip(),
        "alt_guardian_name": request.form.get("alt_guardian_name", "").strip(),
        "alt_guardian_phone": request.form.get("alt_guardian_phone", "").strip(),
        "alt_guardian_email": request.form.get("alt_guardian_email", "").strip(),
        "student_phone": request.form.get("student_phone", "").strip(),
        "student_email": request.form.get("student_email", "").strip(),
        "medical_condition": request.form.get("medical_condition", "").strip(),
        "allergies": request.form.get("allergies", "").strip(),
        "special_info": request.form.get("special_info", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "payment_status": request.form.get("payment_status", "Pending"),
        "balance": request.form.get("balance", "0").strip(),
        "active": 1 if request.form.get("active") == "1" else 0,
    }
    execute(
        """
        UPDATE students SET
            admission_no = ?, full_name = ?, grade = ?, guardian_name = ?, guardian_phone = ?, guardian_email = ?,
            alt_guardian_name = ?, alt_guardian_phone = ?, alt_guardian_email = ?, student_phone = ?, student_email = ?,
            medical_condition = ?, allergies = ?, special_info = ?, notes = ?,
            payment_status = ?, balance = ?, active = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            fields["admission_no"],
            fields["full_name"],
            fields["grade"],
            fields["guardian_name"],
            fields["guardian_phone"],
            fields["guardian_email"],
            fields["alt_guardian_name"],
            fields["alt_guardian_phone"],
            fields["alt_guardian_email"],
            fields["student_phone"],
            fields["student_email"],
            fields["medical_condition"],
            fields["allergies"],
            fields["special_info"],
            fields["notes"],
            fields["payment_status"],
            float(fields["balance"] or 0),
            fields["active"],
            student_id,
        ),
    )
    audit(current_user()["id"], current_user()["full_name"], "Edit Student", f"Student {student['admission_no']} updated.")
    flash("Student updated.", "success")
    return redirect(request.referrer or url_for("dashboard", student_id=student_id))


@app.route("/payments/add", methods=["POST"])
@login_required
def add_payment():
    admission_no = request.form.get("admission_no", "").strip()
    amount = request.form.get("amount", "").strip()
    method = request.form.get("method", "").strip()
    reference_no = request.form.get("reference_no", "").strip()
    student = q("SELECT * FROM students WHERE admission_no = ? AND deleted_at IS NULL", (admission_no,), one=True)
    if not student:
        flash("Student admission number not found.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    try:
        amount_f = float(amount)
        if amount_f <= 0:
            raise ValueError
    except ValueError:
        flash("Enter a valid payment amount.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    if method not in {"Cash", "M-Pesa", "Bank", "Cheque"}:
        flash("Select a valid payment method.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    execute(
        "INSERT INTO payments(student_id, amount, method, reference_no, recorded_by, status) VALUES (?, ?, ?, ?, ?, 'Posted')",
        (student["id"], amount_f, method, reference_no, current_user()["id"]),
    )
    new_balance = max(0, float(student["balance"]) - amount_f)
    new_status = "Paid" if new_balance == 0 else "Pending"
    execute("UPDATE students SET balance = ?, payment_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_balance, new_status, student["id"]))
    audit(current_user()["id"], current_user()["full_name"], "Record Payment", f"{amount_f:.2f} recorded for {student['admission_no']} using {method}.")
    flash("Payment recorded.", "success")
    return redirect(request.referrer or url_for("dashboard", student_id=student["id"]))


@app.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@login_required
@role_required("Admin")
def reverse_payment(payment_id: int):
    payment = q("SELECT * FROM payments WHERE id = ?", (payment_id,), one=True)
    if not payment:
        abort(404)
    if payment["status"] == "Reversed":
        flash("Payment is already reversed.", "warning")
        return redirect(request.referrer or url_for("admin_dashboard"))
    execute("UPDATE payments SET status = 'Reversed', reversed_at = CURRENT_TIMESTAMP WHERE id = ?", (payment_id,))
    student = q("SELECT * FROM students WHERE id = ?", (payment["student_id"],), one=True)
    if student:
        updated_balance = float(student["balance"]) + float(payment["amount"])
        execute("UPDATE students SET balance = ?, payment_status = 'Pending', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (updated_balance, student["id"]))
    audit(current_user()["id"], current_user()["full_name"], "Reverse Payment", f"Payment #{payment_id} reversed.")
    flash("Payment reversed.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/users/add", methods=["POST"])
@login_required
@role_required("Admin")
def add_user():
    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "Staff")
    if not full_name or not username or not password:
        flash("All user fields are required.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))
    if role not in ALL_ROLES:
        flash("Invalid role selected.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))
    try:
        execute("INSERT INTO users(full_name, username, password_hash, role) VALUES (?, ?, ?, ?)", (full_name, username, generate_password_hash(password), role))
        audit(current_user()["id"], current_user()["full_name"], "Add User", f"{full_name} ({username}) added as {role}.")
        flash("User created.", "success")
    except sqlite3.IntegrityError:
        flash("Username already exists.", "danger")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("Admin")
def delete_user(user_id: int):
    user = q("SELECT id, full_name, username, role FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,), one=True)
    if not user:
        abort(404)
    if current_user()["id"] == user_id:
        flash("You cannot delete the account you are currently using.", "warning")
        return redirect(request.referrer or url_for("admin_dashboard"))
    active_admins = q("SELECT COUNT(*) AS c FROM users WHERE deleted_at IS NULL AND role = 'Admin'", one=True)["c"]
    if user["role"] == "Admin" and active_admins <= 1:
        flash("At least one active admin must remain.", "warning")
        return redirect(request.referrer or url_for("admin_dashboard"))
    execute("UPDATE users SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    audit(current_user()["id"], current_user()["full_name"], "Delete User", f"{user['full_name']} ({user['username']}) archived.")
    flash("User archived.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/students/<int:student_id>/delete", methods=["POST"])
@login_required
@role_required("Admin")
def delete_student(student_id: int):
    student = q("SELECT id, full_name, admission_no FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        abort(404)
    execute("UPDATE students SET deleted_at = CURRENT_TIMESTAMP, active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (student_id,))
    audit(current_user()["id"], current_user()["full_name"], "Delete Student", f"{student['full_name']} ({student['admission_no']}) archived.")
    flash("Student archived.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/export/<kind>")
@login_required
@role_required("Admin")
def export_data(kind: str):
    mapping = {
        "students": (
            "Student Export",
            [
                "admission_no", "full_name", "grade", "guardian_name", "guardian_phone", "guardian_email",
                "alt_guardian_name", "alt_guardian_phone", "alt_guardian_email", "student_phone", "student_email",
                "medical_condition", "allergies", "special_info", "notes", "payment_status", "balance", "active",
            ],
            "SELECT admission_no, full_name, grade, guardian_name, guardian_phone, guardian_email, alt_guardian_name, alt_guardian_phone, alt_guardian_email, student_phone, student_email, medical_condition, allergies, special_info, notes, payment_status, balance, active FROM students WHERE deleted_at IS NULL ORDER BY id",
        ),
        "users": (
            "Employee Export",
            ["full_name", "username", "role", "created_at"],
            "SELECT full_name, username, role, created_at FROM users WHERE deleted_at IS NULL ORDER BY id",
        ),
        "payments": (
            "Payments Export",
            ["student_id", "amount", "method", "reference_no", "recorded_by", "status", "created_at"],
            "SELECT student_id, amount, method, reference_no, recorded_by, status, created_at FROM payments ORDER BY id",
        ),
        "audit": (
            "Audit Export",
            ["actor_name", "action", "details", "created_at"],
            "SELECT actor_name, action, details, created_at FROM audit_log ORDER BY id",
        ),
    }
    if kind not in mapping:
        abort(404)
    title, headers, sql = mapping[kind]
    rows = q(sql)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row[h] for h in headers])
    data = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
    data.seek(0)
    filename = f"{kind}_export.csv"
    audit(current_user()["id"], current_user()["full_name"], "Export Data", f"{title} downloaded.")
    return send_file(data, as_attachment=True, download_name=filename, mimetype="text/csv")


@app.route("/backup/download")
@login_required
@role_required("Admin")
def backup_download():
    if not DB_PATH.exists():
        abort(404)
    return send_file(DB_PATH, as_attachment=True, download_name="school_backup.sqlite3")


@app.route("/backup/restore", methods=["POST"])
@login_required
@role_required("Admin")
def backup_restore():
    file = request.files.get("backup_file")
    if not file or not file.filename:
        flash("Choose a database backup file first.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))
    if not allowed_filename(file.filename):
        flash("Only .db, .sqlite, or .sqlite3 files are allowed.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))
    safe_name = secure_filename(file.filename)
    temp_path = UPLOAD_DIR / safe_name
    file.save(temp_path)
    try:
        with sqlite3.connect(temp_path) as test_conn:
            test_conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        backup_old = DB_PATH.with_suffix(".bak")
        if DB_PATH.exists():
            if backup_old.exists():
                backup_old.unlink()
            DB_PATH.replace(backup_old)
        temp_path.replace(DB_PATH)
        init_db()
        flash("Backup restored successfully.", "success")
        audit(current_user()["id"], current_user()["full_name"], "Restore Backup", f"Backup restored from {safe_name}.")
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        flash(f"Restore failed: {exc}", "danger")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/api/student/<int:student_id>")
@login_required
def api_student(student_id: int):
    student = q("SELECT * FROM students WHERE id = ? AND deleted_at IS NULL", (student_id,), one=True)
    if not student:
        return jsonify({"error": "Not found"}), 404
    payments = q(
        """
        SELECT p.*, u.full_name AS recorded_by_name
        FROM payments p
        JOIN users u ON u.id = p.recorded_by
        WHERE p.student_id = ?
        ORDER BY p.created_at DESC, p.id DESC
        """,
        (student_id,),
    )
    return jsonify({"student": dict(student), "payments": [dict(p) for p in payments]})


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", title="Access denied", message="You do not have permission to access this area."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", title="Not found", message="The page you requested could not be found."), 404


@app.errorhandler(413)
def too_large(_):
    return render_template("error.html", title="File too large", message="The uploaded file is too large."), 413


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
