from __future__ import annotations

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
DB_PATH = INSTANCE_DIR / "jevicarn.db"
UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_RESTORE_EXT = {"db", "sqlite", "sqlite3"}

app = Flask(__name__, instance_path=str(INSTANCE_DIR), instance_relative_config=True)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-jevicarn-secret-change-me"),
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

            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_no TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                grade TEXT NOT NULL,
                guardian TEXT,
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
        cur = conn.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0:
            conn.execute(
                """INSERT INTO users(full_name, username, password_hash, role)
                   VALUES (?, ?, ?, ?)""",
                ("System Administrator", "admin", generate_password_hash("Admin@123"), "Admin"),
            )
            conn.execute(
                """INSERT INTO users(full_name, username, password_hash, role)
                   VALUES (?, ?, ?, ?)""",
                ("Finance Staff", "staff", generate_password_hash("Finance@123"), "Staff"),
            )
        cur = conn.execute("SELECT COUNT(*) AS c FROM students")
        if cur.fetchone()["c"] == 0:
            students = [
                ("A001", "Aisha M.", "Grade 8", "Mary M.", "Paid", 0.0, 1),
                ("A002", "Brian K.", "Grade 9", "John K.", "Pending", 12000.0, 1),
                ("A003", "Zuri N.", "Grade 7", "Esther N.", "Paid", 0.0, 1),
                ("A004", "Kelvin O.", "Grade 10", "Paul O.", "Pending", 8000.0, 1),
            ]
            conn.executemany(
                """INSERT INTO students(admission_no, full_name, grade, guardian, payment_status, balance, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                students,
            )
        cur = conn.execute("SELECT COUNT(*) AS c FROM payments")
        if cur.fetchone()["c"] == 0:
            conn.execute(
                """INSERT INTO payments(student_id, amount, method, reference_no, recorded_by, status)
                   VALUES (1, 15000, 'M-Pesa', 'MPESA-0001', 1, 'Posted')"""
            )
            conn.execute(
                """INSERT INTO payments(student_id, amount, method, reference_no, recorded_by, status)
                   VALUES (2, 3000, 'Cash', 'CASH-0002', 2, 'Posted')"""
            )
        cur = conn.execute("SELECT COUNT(*) AS c FROM audit_log")
        if cur.fetchone()["c"] == 0:
            conn.execute(
                """INSERT INTO audit_log(actor_id, actor_name, action, details)
                   VALUES (1, 'System Administrator', 'System Seed', 'Initial users, students and payments created.')"""
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
        g.user = q("SELECT id, full_name, username, role FROM users WHERE id = ?", (user_id,), one=True)


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
    return "Admin Dashboard" if role == "Admin" else "Finance Workspace"


def allowed_filename(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_RESTORE_EXT


# -------------------------
# Context / templates
# -------------------------
@app.context_processor
def inject_globals():
    return {
        "now_year": datetime.now().year,
        "current_user": current_user(),
        "workspace_for": workspace_for,
    }


# -------------------------
# Routes
# -------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard")) if current_user() else redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = q("SELECT * FROM users WHERE username = ?", (username,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            audit(user["id"], user["full_name"], "Login", f"{user['username']} logged in.")
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        audit(user["id"], user["full_name"], "Logout", f"{user['username']} logged out.")
    session.clear()
    return redirect(url_for("login"))


@app.route("/sw.js")
def service_worker():
    # Serve the service worker from the root scope so it can control the entire site.
    return send_file(BASE_DIR / "static" / "sw.js", mimetype="application/javascript", conditional=True, max_age=0)


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    role = user["role"]
    students = q(
        "SELECT * FROM students ORDER BY created_at DESC, id DESC LIMIT 20"
    )
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
    audits = q("SELECT * FROM audit_log ORDER BY created_at DESC, id DESC LIMIT 20")
    users = q("SELECT id, full_name, username, role, created_at FROM users ORDER BY created_at DESC")
    summary = {
        "students": q("SELECT COUNT(*) AS c FROM students", one=True)["c"],
        "active_students": q("SELECT COUNT(*) AS c FROM students WHERE active = 1", one=True)["c"],
        "collections": q(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE status = 'Posted'",
            one=True,
        )["total"],
        "active_users": q("SELECT COUNT(*) AS c FROM users", one=True)["c"] if role == "Admin" else None,
        "paid": q("SELECT COUNT(*) AS c FROM students WHERE payment_status = 'Paid'", one=True)["c"],
        "pending": q("SELECT COUNT(*) AS c FROM students WHERE payment_status = 'Pending'", one=True)["c"],
    }
    selected_student_id = request.args.get("student_id", type=int)
    selected_student = None
    student_payments = []
    if selected_student_id:
        selected_student = q("SELECT * FROM students WHERE id = ?", (selected_student_id,), one=True)
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
        role=role,
        workspace=workspace_for(role),
        summary=summary,
        students=students,
        payments=payments,
        audits=audits,
        users=users,
        selected_student=selected_student,
        selected_student_payments=student_payments,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    message = None
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        if not full_name:
            flash("Full name is required.", "danger")
        else:
            if password:
                execute(
                    "UPDATE users SET full_name = ?, password_hash = ? WHERE id = ?",
                    (full_name, generate_password_hash(password), user["id"]),
                )
            else:
                execute("UPDATE users SET full_name = ? WHERE id = ?", (full_name, user["id"]))
            session["user_id"] = user["id"]
            audit(user["id"], full_name, "Profile Update", "Profile details updated.")
            flash("Profile updated successfully.", "success")
            return redirect(url_for("profile"))
    profile_user = q("SELECT id, full_name, username, role, created_at FROM users WHERE id = ?", (user["id"],), one=True)
    return render_template("profile.html", profile_user=profile_user, workspace=workspace_for(profile_user["role"]))


@app.route("/students/add", methods=["POST"])
@login_required
def add_student():
    admission_no = request.form.get("admission_no", "").strip()
    full_name = request.form.get("full_name", "").strip()
    grade = request.form.get("grade", "").strip()
    guardian = request.form.get("guardian", "").strip()
    if not admission_no or not full_name or not grade:
        flash("Admission number, student name, and grade are required.", "danger")
        return redirect(url_for("dashboard"))
    try:
        execute(
            """INSERT INTO students(admission_no, full_name, grade, guardian, payment_status, balance, active)
               VALUES (?, ?, ?, ?, 'Pending', 0, 1)""",
            (admission_no, full_name, grade, guardian),
        )
        audit(current_user()["id"], current_user()["full_name"], "Add Student", f"{full_name} ({admission_no}) created.")
        flash("Student added.", "success")
    except sqlite3.IntegrityError:
        flash("Admission number already exists.", "danger")
    return redirect(url_for("dashboard"))


@app.route("/students/<int:student_id>/update", methods=["POST"])
@login_required
def update_student(student_id: int):
    student = q("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        abort(404)
    full_name = request.form.get("full_name", "").strip()
    grade = request.form.get("grade", "").strip()
    guardian = request.form.get("guardian", "").strip()
    payment_status = request.form.get("payment_status", "Pending")
    balance = request.form.get("balance", "0").strip()
    active = 1 if request.form.get("active") == "1" else 0
    execute(
        """UPDATE students SET full_name = ?, grade = ?, guardian = ?, payment_status = ?, balance = ?, active = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (full_name or student["full_name"], grade or student["grade"], guardian, payment_status, float(balance or 0), active, student_id),
    )
    audit(current_user()["id"], current_user()["full_name"], "Edit Student", f"Student {student['admission_no']} updated.")
    flash("Student updated.", "success")
    return redirect(url_for("dashboard", student_id=student_id))


@app.route("/payments/add", methods=["POST"])
@login_required
def add_payment():
    admission_no = request.form.get("admission_no", "").strip()
    amount = request.form.get("amount", "").strip()
    method = request.form.get("method", "").strip()
    reference_no = request.form.get("reference_no", "").strip()
    student = q("SELECT * FROM students WHERE admission_no = ?", (admission_no,), one=True)
    if not student:
        flash("Student admission number not found.", "danger")
        return redirect(url_for("dashboard"))
    try:
        amount_f = float(amount)
        if amount_f <= 0:
            raise ValueError
    except ValueError:
        flash("Enter a valid payment amount.", "danger")
        return redirect(url_for("dashboard"))
    if method not in {"Cash", "M-Pesa", "Bank", "Cheque"}:
        flash("Select a valid payment method.", "danger")
        return redirect(url_for("dashboard"))
    execute(
        """INSERT INTO payments(student_id, amount, method, reference_no, recorded_by, status)
           VALUES (?, ?, ?, ?, ?, 'Posted')""",
        (student["id"], amount_f, method, reference_no, current_user()["id"]),
    )
    new_balance = max(0, float(student["balance"]) - amount_f)
    new_status = "Paid" if new_balance == 0 else "Pending"
    execute(
        "UPDATE students SET balance = ?, payment_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_balance, new_status, student["id"]),
    )
    audit(current_user()["id"], current_user()["full_name"], "Record Payment", f"{amount_f:.2f} recorded for {student['admission_no']} using {method}.")
    flash("Payment recorded.", "success")
    return redirect(url_for("dashboard", student_id=student["id"]))


@app.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@login_required
@role_required("Admin")
def reverse_payment(payment_id: int):
    payment = q("SELECT * FROM payments WHERE id = ?", (payment_id,), one=True)
    if not payment:
        abort(404)
    if payment["status"] == "Reversed":
        flash("Payment is already reversed.", "warning")
        return redirect(url_for("dashboard"))
    execute(
        "UPDATE payments SET status = 'Reversed', reversed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (payment_id,),
    )
    student = q("SELECT * FROM students WHERE id = ?", (payment["student_id"],), one=True)
    if student:
        updated_balance = float(student["balance"]) + float(payment["amount"])
        execute(
            "UPDATE students SET balance = ?, payment_status = 'Pending', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (updated_balance, student["id"]),
        )
    audit(current_user()["id"], current_user()["full_name"], "Reverse Payment", f"Payment #{payment_id} reversed.")
    flash("Payment reversed.", "success")
    return redirect(url_for("dashboard"))


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
        return redirect(url_for("dashboard"))
    if role not in {"Admin", "Staff"}:
        flash("Invalid role selected.", "danger")
        return redirect(url_for("dashboard"))
    try:
        execute(
            "INSERT INTO users(full_name, username, password_hash, role) VALUES (?, ?, ?, ?)",
            (full_name, username, generate_password_hash(password), role),
        )
        audit(current_user()["id"], current_user()["full_name"], "Add User", f"{full_name} ({username}) added as {role}.")
        flash("User created.", "success")
    except sqlite3.IntegrityError:
        flash("Username already exists.", "danger")
    return redirect(url_for("dashboard"))


@app.route("/backup/download")
@login_required
@role_required("Admin")
def backup_download():
    if not DB_PATH.exists():
        abort(404)
    return send_file(DB_PATH, as_attachment=True, download_name="jevicarn_school_system_backup.sqlite3")


@app.route("/backup/restore", methods=["POST"])
@login_required
@role_required("Admin")
def backup_restore():
    file = request.files.get("backup_file")
    if not file or not file.filename:
        flash("Choose a database backup file first.", "danger")
        return redirect(url_for("dashboard"))
    if not allowed_filename(file.filename):
        flash("Only .db, .sqlite, or .sqlite3 files are allowed.", "danger")
        return redirect(url_for("dashboard"))
    safe_name = secure_filename(file.filename)
    temp_path = UPLOAD_DIR / safe_name
    file.save(temp_path)
    try:
        with sqlite3.connect(temp_path) as test_conn:
            test_conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        # Swap only after validation
        backup_old = DB_PATH.with_suffix(".bak")
        if DB_PATH.exists():
            if backup_old.exists():
                backup_old.unlink()
            DB_PATH.replace(backup_old)
        temp_path.replace(DB_PATH)
        init_db()  # ensure schema present if restored file is partial
        flash("Backup restored successfully.", "success")
        audit(current_user()["id"], current_user()["full_name"], "Restore Backup", f"Backup restored from {safe_name}.")
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        flash(f"Restore failed: {exc}", "danger")
    return redirect(url_for("dashboard"))


@app.route("/api/student/<int:student_id>")
@login_required
def api_student(student_id: int):
    student = q("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
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
    return jsonify(
        {
            "student": dict(student),
            "payments": [dict(p) for p in payments],
        }
    )


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", title="Forbidden", message="You do not have permission to access this area."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", title="Not Found", message="The page you requested could not be found."), 404


@app.errorhandler(413)
def too_large(_):
    return render_template("error.html", title="File Too Large", message="The uploaded file is too large."), 413


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
