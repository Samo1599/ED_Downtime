
"""
ED Downtime – Cerner-like Board (V5 Full Single-File)
Internal ED use only — Downtime Tool © 2025
Developed by Samy Aly (ID 20155)

V5 adds:
- Auto DB upgrades on startup
- Auto-Backup scheduler (hourly) + manual Backup Now
- Admin password reset page + Reset admin to default button
- Auto-Logout / Session Lock on idle
- Clinical Orders V5 with fixed Bundles/Checkboxes
- Discharge Summary V5:
    * Diagnosis / Chief Complaint
    * Referral to clinic
    * Home medication
    * Auto-Summary PDF (full ED course)
- Sticker HTML + ZPL 5x3cm (fixed)
"""
from flask import (
    Flask, request, g, redirect, url_for,
    render_template, session, Response, send_from_directory, flash, jsonify
)
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from jinja2 import DictLoader
import os, io, csv, shutil, threading, time

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

# ============================================================
# Application Setup
# ============================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = "CHANGE_ME_TO_SECURE_KEY"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

# Session lifetime (2 hours default)
app.config["PERMANENT_SESSION_LIFETIME"] = 7200

# Idle auto-logout (minutes)
IDLE_TIMEOUT_SECONDS = 15 * 60

DATABASE = "triage_ed.db"
UPLOAD_FOLDER = "uploads"
BACKUP_FOLDER = "backups"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

APP_FOOTER_TEXT = "Downtime Tool © 2025 — Developed by: Samy Aly | ID 20155"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}

# ============================================================
# Database Helper
# ============================================================

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db:
        db.close()

# ============================================================
# Security Headers
# ============================================================

@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    resp.headers["Cache-Control"] = "no-store"
    return resp

# ============================================================
# Activity Logging
# ============================================================

def init_logging_table():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            visit_id TEXT,
            username TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()

def log_action(action, visit_id=None, details=None):
    db = get_db()
    db.execute("""
        INSERT INTO activity_log (action, visit_id, username, details, created_at)
        VALUES (?,?,?,?,?)
    """, (
        action,
        visit_id,
        session.get("username","UNKNOWN"),
        details,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    db.commit()

# ============================================================
# DB Initialization + Auto Upgrades
# ============================================================

def table_columns(table):
    cur = get_db().cursor()
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}

def ensure_column(table, col, col_type):
    cols = table_columns(table)
    if col not in cols:
        get_db().execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        get_db().commit()

def init_db():
    db = get_db()
    cur = db.cursor()

    # Users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Patients
    cur.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            id_number TEXT,
            phone TEXT,
            insurance TEXT,
            insurance_no TEXT,
            dob TEXT,
            sex TEXT,
            nationality TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL
        )
    """)

    # Visits
    cur.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT UNIQUE NOT NULL,
            patient_id INTEGER NOT NULL,
            queue_no INTEGER NOT NULL,
            triage_status TEXT DEFAULT 'NO',
            triage_cat TEXT,
            comment TEXT,
            payment_details TEXT,

            allergy_status TEXT,
            allergy_details TEXT,
            pulse_rate TEXT,
            resp_rate TEXT,
            bp_systolic TEXT,
            bp_diastolic TEXT,
            temperature TEXT,
            consciousness_level TEXT,
            spo2 TEXT,
            pain_score TEXT,
            weight TEXT,
            height TEXT,

            status TEXT DEFAULT 'OPEN',
            closed_at TEXT,
            closed_by TEXT,

            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)

    # Clinical orders
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clinical_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT NOT NULL,
            diagnosis TEXT,
            radiology_orders TEXT,
            lab_orders TEXT,
            medications TEXT,
            duplicated_from INTEGER,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            updated_at TEXT,
            updated_by TEXT
        )
    """)

    # Nursing notes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nursing_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL
        )
    """)

    # Discharge summaries (V5)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS discharge_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT UNIQUE NOT NULL,
            diagnosis_cc TEXT,
            referral_clinic TEXT,
            home_medication TEXT,
            summary_text TEXT,
            auto_summary_text TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            updated_at TEXT,
            updated_by TEXT
        )
    """)

    # Lab Requests
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lab_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT NOT NULL,
            test_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'REQUESTED',
            result_text TEXT,
            requested_at TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            received_at TEXT,
            received_by TEXT,
            reported_at TEXT,
            reported_by TEXT
        )
    """)

    # Radiology Requests
    cur.execute("""
        CREATE TABLE IF NOT EXISTS radiology_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT NOT NULL,
            test_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'REQUESTED',
            report_text TEXT,
            requested_at TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            done_at TEXT,
            done_by TEXT,
            reported_at TEXT,
            reported_by TEXT
        )
    """)

    # Attachments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT NOT NULL
        )
    """)

    # Chat messages
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_visits_vid ON visits(visit_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_visits_pid ON visits(patient_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_idno ON patients(id_number);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_ins ON patients(insurance_no);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_vid ON clinical_orders(visit_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_vid ON nursing_notes(visit_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dis_vid ON discharge_summaries(visit_id);")

    db.commit()
    init_logging_table()

    # SQLite performance pragmas
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA synchronous = NORMAL;")
    db.execute("PRAGMA temp_store = MEMORY;")
    db.execute("PRAGMA cache_size = -64000;")
    db.commit()

    # Auto-upgrade legacy DBs missing V5 columns
    try:
        # Discharge V5 columns
        ensure_column("discharge_summaries", "diagnosis_cc", "TEXT")
        ensure_column("discharge_summaries", "referral_clinic", "TEXT")
        ensure_column("discharge_summaries", "home_medication", "TEXT")
        ensure_column("discharge_summaries", "auto_summary_text", "TEXT")
        # Visit cancellation metadata (for reception cancel feature)
        ensure_column("visits", "cancel_reason", "TEXT")
        ensure_column("visits", "cancelled_by", "TEXT")
        ensure_column("visits", "cancelled_at", "TEXT")
        # Triage extra fields
        ensure_column("visits", "allergy_details", "TEXT")
        ensure_column("visits", "weight", "TEXT")
        ensure_column("visits", "height", "TEXT")
        ensure_column("visits", "payment_details", "TEXT")
    except Exception:
        pass

# ============================================================
# Generators
# ============================================================

def generate_visit_id():
    today = datetime.now().strftime("%Y%m%d")
    cur = get_db().cursor()
    last = cur.execute("""
        SELECT visit_id FROM visits
        WHERE visit_id LIKE ?
        ORDER BY id DESC LIMIT 1
    """,(today+"%",)).fetchone()
    last_num = int(last["visit_id"][-5:]) if last else 0
    return f"{today}{last_num+1:05d}"

def generate_queue_no():
    today = datetime.now().strftime("%Y-%m-%d")
    cur = get_db().cursor()
    last = cur.execute("""
        SELECT MAX(queue_no) as q FROM visits
        WHERE date(created_at)=date(?)
    """,(today,)).fetchone()
    return (last["q"] + 1) if last and last["q"] else 1

# ============================================================
# Auth Helpers
# ============================================================

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrap

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrap(*args, **kwargs):
            if session.get("role") not in roles:
                flash("You do not have permission for this page.", "danger")
                return redirect(url_for("ed_board"))
            return f(*args, **kwargs)
        return wrap
    return decorator

def allowed_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".",1)[1].lower()
    banned = {"exe","bat","js","sh","php"}
    return ext in ALLOWED_EXTENSIONS and ext not in banned

def clean_text(v):
    if not v:
        return ""
    return v.replace("'", " ").replace(";", " ").replace("--", " ")


def get_page_args(default_per_page=25, max_per_page=100):
    try:
        page = int(request.args.get("page", 1))
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get("per_page", default_per_page))
    except Exception:
        per_page = default_per_page
    per_page = max(5, min(per_page, max_per_page))
    page = max(1, page)
    offset = (page - 1) * per_page
    return page, per_page, offset

# ============================================================
# Auto logout on idle
# ============================================================

@app.before_request
def session_idle_check():
    if "user_id" in session:
        last = session.get("last_activity")
        now_ts = int(time.time())
        if last and (now_ts - last) > IDLE_TIMEOUT_SECONDS:
            try:
                log_action("AUTO_LOGOUT_IDLE")
            except Exception:
                pass
            session.clear()
            flash("Session expired due to inactivity.", "warning")
            return redirect(url_for("login"))
        session["last_activity"] = now_ts

# ============================================================
# Backup (manual + scheduler)
# ============================================================

def do_backup():
    if not os.path.exists(DATABASE):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_FOLDER, f"triage_ed_{ts}.db")
    try:
        shutil.copy2(DATABASE, dst)
        return dst
    except Exception:
        return None

def backup_scheduler_loop():
    while True:
        time.sleep(3600)  # hourly
        do_backup()

def start_backup_scheduler_once():
    # Avoid duplicate threads on debug reload
    if app.config.get("DEBUG") and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    t = threading.Thread(target=backup_scheduler_loop, daemon=True)
    t.start()

# ============================================================
# Login / Logout
# ============================================================

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()
        cur = get_db().cursor()
        u = cur.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u and check_password_hash(u["password_hash"], password):
            session["user_id"] = u["id"]
            session["username"] = u["username"]
            session["role"] = u["role"]
            session.permanent = True
            session["last_activity"] = int(time.time())
            log_action("LOGIN", details=username)
            return redirect(url_for("ed_board"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    try:
        log_action("LOGOUT")
    except Exception:
        pass
    session.clear()
    return redirect(url_for("login"))

# ============================================================
# Admin Users + Reset Password + Backups
# ============================================================

@app.route("/admin/users", methods=["GET","POST"])
@login_required
@role_required("admin")
def admin_users():
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()
        role = request.form.get("role","reception").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
        else:
            try:
                cur.execute("""
                    INSERT INTO users (username, password_hash, role, created_at)
                    VALUES (?,?,?,?)
                """, (
                    username,
                    generate_password_hash(password),
                    role,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                db.commit()
                log_action("CREATE_USER", details=f"{username}:{role}")
                flash("User account created successfully.", "success")
            except sqlite3.IntegrityError:
                flash("Username already exists.", "danger")

    users = cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id DESC").fetchall()
    return render_template("admin_users.html", users=users)

@app.route("/admin/reset_password", methods=["GET","POST"])
@login_required
@role_required("admin")
def admin_reset_password():
    db = get_db(); cur = db.cursor()
    users = cur.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()

    if request.method == "POST":
        uid = request.form.get("user_id")
        new_pass = request.form.get("new_password","").strip()
        if not uid or not new_pass:
            flash("Select user and enter new password.", "danger")
        elif len(new_pass) < 6:
            flash("Password must be at least 6 characters.", "danger")
        else:
            u = cur.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            if u:
                db.execute("UPDATE users SET password_hash=? WHERE id=?",
                           (generate_password_hash(new_pass), uid))
                db.commit()
                log_action("RESET_PASSWORD", details=u["username"])
                flash(f"Password reset for {u['username']}.", "success")
            else:
                flash("User not found.", "danger")

    return render_template("admin_reset_password.html", users=users)

@app.route("/admin/reset_admin_default")
@login_required
@role_required("admin")
def admin_reset_admin_default():
    db=get_db(); cur=db.cursor()
    admin = cur.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if admin:
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash("admin12"), admin["id"]))
        db.commit()
        log_action("RESET_ADMIN_DEFAULT")
        flash("Admin password reset to default (admin12).", "success")
    return redirect(url_for("admin_reset_password"))


@app.route("/admin/logs")
@login_required
@role_required("admin")
def admin_logs():
    cur = get_db().cursor()

    visit_f = request.args.get("visit_id","").strip()
    user_f  = request.args.get("user","").strip()
    dfrom   = request.args.get("date_from","").strip()
    dto     = request.args.get("date_to","").strip()

    sql = "SELECT * FROM activity_log WHERE 1=1"
    params = []
    if visit_f:
        sql += " AND visit_id LIKE ?"; params.append(f"%{visit_f}%")
    if user_f:
        sql += " AND username=?"; params.append(user_f)
    if dfrom:
        sql += " AND date(created_at) >= date(?)"; params.append(dfrom)
    if dto:
        sql += " AND date(created_at) <= date(?)"; params.append(dto)

    sql += " ORDER BY id DESC LIMIT 1000"
    logs = cur.execute(sql, params).fetchall()
    users = cur.execute("SELECT DISTINCT username FROM activity_log ORDER BY username").fetchall()

    return render_template("admin_logs.html", logs=logs, users=users,
                           visit_f=visit_f, user_f=user_f, dfrom=dfrom, dto=dto)

@app.route("/admin/logs.csv")
@login_required
@role_required("admin")
def export_logs_csv():
    cur = get_db().cursor()

    visit_f = request.args.get("visit_id","").strip()
    user_f  = request.args.get("user","").strip()
    dfrom   = request.args.get("date_from","").strip()
    dto     = request.args.get("date_to","").strip()

    sql = "SELECT id, action, visit_id, username, details, created_at FROM activity_log WHERE 1=1"
    params = []
    if visit_f:
        sql += " AND visit_id LIKE ?"; params.append(f"%{visit_f}%")
    if user_f:
        sql += " AND username=?"; params.append(user_f)
    if dfrom:
        sql += " AND date(created_at) >= date(?)"; params.append(dfrom)
    if dto:
        sql += " AND date(created_at) <= date(?)"; params.append(dto)

    sql += " ORDER BY id DESC"
    rows = cur.execute(sql, params).fetchall()

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["ID","Action","Visit ID","User","Details","Created At"])
    for r in rows:
        w.writerow([r["id"], r["action"], r["visit_id"], r["username"], r["details"], r["created_at"]])

    return Response(output.getvalue().encode("utf-8-sig"),
                    mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=activity_logs.csv"})

@app.route("/admin/logs.pdf")
@login_required
@role_required("admin")
def export_logs_pdf():
    cur = get_db().cursor()

    visit_f = request.args.get("visit_id","").strip()
    user_f  = request.args.get("user","").strip()
    dfrom   = request.args.get("date_from","").strip()
    dto     = request.args.get("date_to","").strip()

    sql = "SELECT id, action, visit_id, username, details, created_at FROM activity_log WHERE 1=1"
    params = []
    if visit_f:
        sql += " AND visit_id LIKE ?"; params.append(f"%{visit_f}%")
    if user_f:
        sql += " AND username=?"; params.append(user_f)
    if dfrom:
        sql += " AND date(created_at) >= date(?)"; params.append(dfrom)
    if dto:
        sql += " AND date(created_at) <= date(?)"; params.append(dto)

    sql += " ORDER BY id DESC"
    rows = cur.execute(sql, params).fetchall()

    buffer=io.BytesIO()
    c=canvas.Canvas(buffer, pagesize=A4)
    width,height=A4; y=height-2*cm
    c.setFont("Helvetica-Bold",14); c.drawString(2*cm,y,"Activity Logs"); y-=0.8*cm
    c.setFont("Helvetica",9)
    c.drawString(2*cm,y,f"Filters: visit={visit_f or 'ALL'} | user={user_f or 'ALL'} | from={dfrom or '-'} to={dto or '-'}"); y-=0.7*cm
    c.setFont("Helvetica",8)
    for r in rows:
        line=f"{r['created_at']} | {r['username']} | {r['action']} | {r['visit_id'] or '-'} | {(r['details'] or '')[:60]}"
        c.drawString(2*cm,y,line); y-=0.4*cm
        if y<2*cm: c.showPage(); y=height-2*cm; c.setFont("Helvetica",8)
    c.setFont("Helvetica-Oblique",8); c.drawString(2*cm,1.2*cm,APP_FOOTER_TEXT)
    c.showPage(); c.save(); buffer.seek(0)

    return Response(buffer.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition":"inline; filename=activity_logs.pdf"})

@app.route("/admin/backup")
@login_required
@role_required("admin")
def admin_backup():
    if not os.path.exists(DATABASE):
        return "DB not found", 404
    return send_from_directory(".", DATABASE, as_attachment=True)

@app.route("/admin/backup_now")
@login_required
@role_required("admin")
def admin_backup_now():
    path = do_backup()
    if path:
        flash("Backup created successfully.", "success")
        log_action("BACKUP_NOW", details=os.path.basename(path))
    else:
        flash("Backup failed.", "danger")
    return redirect(url_for("ed_board"))
@app.route("/admin/restore", methods=["GET","POST"])
@login_required
@role_required("admin")
def admin_restore():
    """
    Restore DB from uploaded backup file.
    """
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a backup file.", "danger")
            return redirect(url_for("admin_restore"))

        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".db"):
            flash("Invalid file type. Please upload a .db backup file.", "danger")
            return redirect(url_for("admin_restore"))

        temp_path = os.path.join(BACKUP_FOLDER, f"restore_{int(time.time())}_{filename}")
        file.save(temp_path)

        try:
            # safety backup of current DB before overwrite
            if os.path.exists(DATABASE):
                shutil.copy2(DATABASE, DATABASE + ".before_restore.bak")

            shutil.copy2(temp_path, DATABASE)

            log_action("BACKUP_RESTORE", details=filename)
            flash("Database restored successfully. Please restart the app/server.", "success")
        except Exception as e:
            flash(f"Restore failed: {e}", "danger")

        return redirect(url_for("ed_board"))

    return render_template("admin_restore.html")




# ============================================================
# Register / Search
# ============================================================

@app.route("/register", methods=["GET","POST"])
@login_required
@role_required("reception","admin")
def register_patient():
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        name = request.form.get("name","").strip()
        id_number = request.form.get("id_number","").strip()
        phone = request.form.get("phone","").strip()
        insurance = request.form.get("insurance","").strip()
        insurance_no = request.form.get("insurance_no","").strip()
        dob = request.form.get("dob","").strip()
        sex = request.form.get("sex","").strip()
        nationality = request.form.get("nationality","").strip()
        payment_details = request.form.get("payment_details","").strip()

        if not name:
            flash("Patient name is required.", "danger")
            return redirect(url_for("register_patient"))

        cur.execute("""
            INSERT INTO patients
            (name, id_number, phone, insurance, insurance_no, dob, sex, nationality,
             created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """,(
            name, id_number, phone, insurance, insurance_no, dob, sex, nationality,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session.get("username")
        ))
        patient_id = cur.lastrowid

        visit_id = generate_visit_id()
        queue_no = generate_queue_no()

        cur.execute("""
            INSERT INTO visits
            (visit_id, patient_id, queue_no, triage_status, status,
             payment_details, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?)
        """,(
            visit_id, patient_id, queue_no, "NO", "OPEN",
            payment_details,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session.get("username")
        ))
        db.commit()

        log_action("REGISTER_PATIENT", visit_id=visit_id, details=f"Name={name}")
        flash(f"Patient registered. Visit ID: {visit_id}", "success")
        return redirect(url_for("ed_board"))

    return render_template("register.html")


@app.route("/search")
@login_required
def search_patients():
    query = request.args.get("q","").strip()
    visit_f = request.args.get("visit_id","").strip()
    user_f  = request.args.get("user","").strip()
    dfrom   = request.args.get("date_from","").strip()
    dto     = request.args.get("date_to","").strip()

    cur = get_db().cursor()
    sql = """
        SELECT v.visit_id, v.queue_no, v.triage_status, v.triage_cat, v.status, v.payment_details, v.created_at, v.created_by,
               p.name, p.id_number, p.phone, p.insurance, p.insurance_no
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE 1=1
    """
    params = []

    if query:
        like = f"%{query}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR p.insurance_no LIKE ? OR v.visit_id LIKE ?)"
        params += [like, like, like, like]

    if visit_f:
        sql += " AND v.visit_id LIKE ?"
        params.append(f"%{visit_f}%")

    if user_f:
        sql += " AND v.created_by=?"
        params.append(user_f)

    if dfrom:
        sql += " AND date(v.created_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(v.created_at) <= date(?)"
        params.append(dto)

    page, per_page, offset = get_page_args(25)

    count_sql = "SELECT COUNT(*) FROM (" + sql + ")"
    total = cur.execute(count_sql, params).fetchone()[0]
    pages = (total + per_page - 1) // per_page

    sql += " ORDER BY v.id DESC LIMIT ? OFFSET ?"
    params2 = params + [per_page, offset]
    results = cur.execute(sql, params2).fetchall()

    users = cur.execute("SELECT DISTINCT created_by FROM visits ORDER BY created_by").fetchall()

    return render_template("search.html", q=query, results=results,
                           visit_f=visit_f, user_f=user_f, dfrom=dfrom, dto=dto, users=users,
                           page=page, pages=pages, per_page=per_page, total=total)

# ============================================================
# ED Board
# ============================================================


@app.route("/")
@login_required
def ed_board():
    status_filter = request.args.get("status","ALL")
    cat_filter = request.args.get("cat","ALL")
    visit_f = request.args.get("visit_id","").strip()
    user_f  = request.args.get("user","").strip()
    dfrom   = request.args.get("date_from","").strip()
    dto     = request.args.get("date_to","").strip()

    sql = """
        SELECT v.visit_id, v.queue_no, v.triage_status, v.triage_cat,
               v.status, v.payment_details, v.created_at, v.created_by,
               p.name, p.id_number, p.insurance
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE 1=1
    """
    params = []

    if status_filter != "ALL":
        sql += " AND v.status=?"
        params.append(status_filter)
    if cat_filter != "ALL":
        sql += " AND v.triage_cat=?"
        params.append(cat_filter)

    if visit_f:
        sql += " AND v.visit_id LIKE ?"
        params.append(f"%{visit_f}%")
    if user_f:
        sql += " AND v.created_by=?"
        params.append(user_f)
    if dfrom:
        sql += " AND date(v.created_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(v.created_at) <= date(?)"
        params.append(dto)

    page, per_page, offset = get_page_args(30)

    count_sql = "SELECT COUNT(*) FROM (" + sql + ")"
    total = get_db().cursor().execute(count_sql, params).fetchone()[0]
    pages = (total + per_page - 1) // per_page

    sql += """
        ORDER BY 
            CASE 
                WHEN v.triage_cat='ES1' THEN 1
                WHEN v.triage_cat='ES2' THEN 2
                WHEN v.triage_cat='ES3' THEN 3
                WHEN v.triage_cat='ES4' THEN 4
                WHEN v.triage_cat='ES5' THEN 5
                ELSE 6
            END,
            v.id DESC
        LIMIT ? OFFSET ?
    """
    params2 = params + [per_page, offset]
    visits = get_db().cursor().execute(sql, params2).fetchall()
    users = get_db().cursor().execute("SELECT DISTINCT created_by FROM visits ORDER BY created_by").fetchall()

    return render_template("ed_board.html", visits=visits,
                           status_filter=status_filter, cat_filter=cat_filter,
                           visit_f=visit_f, user_f=user_f, dfrom=dfrom, dto=dto, users=users,
                           page=page, pages=pages, per_page=per_page, total=total)

@app.route("/export/ed_board.csv")
@login_required
def export_ed_board_csv():
    cur = get_db().cursor()
    rows = cur.execute("""
        SELECT v.queue_no, v.visit_id, p.name, p.id_number, p.phone,
               p.insurance, p.insurance_no, v.triage_status,
               v.triage_cat, v.status, v.payment_details, v.created_at
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        ORDER BY v.id DESC LIMIT 500
    """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Queue","VisitID","Name","ID","Phone",
        "Insurance","Insurance No",
        "TriageStatus","CAT","Status","Payment Details","Created"
    ])
    for r in rows:
        writer.writerow(list(r))

    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ed_board.csv"}
    )

# ============================================================
# Patient Details / Edit / Attachments / Close Visit
# ============================================================

@app.route("/patient/<visit_id>")
@login_required
def patient_details(visit_id):
    cur = get_db().cursor()
    visit = cur.execute("""
        SELECT v.*, 
               p.name, p.id_number, p.phone, p.insurance, p.insurance_no,
               p.dob, p.sex, p.nationality
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """,(visit_id,)).fetchone()

    if not visit:
        flash("Requested record not found.", "danger")
        return redirect(url_for("ed_board"))

    attachments = cur.execute("""
        SELECT * FROM attachments WHERE visit_id=? ORDER BY id DESC
    """,(visit_id,)).fetchall()

    # How many clinical orders exist for this visit (used to decide if reception can cancel)
    try:
        orders_row = cur.execute(
            "SELECT COUNT(*) AS c FROM clinical_orders WHERE visit_id=?",
            (visit_id,)
        ).fetchone()
        orders_count = orders_row["c"] if orders_row is not None else 0
    except Exception:
        orders_count = 0

    # Lab & radiology results for read-only display
    try:
        lab_reqs = cur.execute("""
            SELECT * FROM lab_requests
            WHERE visit_id=?
            ORDER BY id ASC
        """, (visit_id,)).fetchall()
    except Exception:
        lab_reqs = []

    try:
        rad_reqs = cur.execute("""
            SELECT * FROM radiology_requests
            WHERE visit_id=?
            ORDER BY id ASC
        """, (visit_id,)).fetchall()
    except Exception:
        rad_reqs = []

    return render_template("patient_details.html",
                           visit=visit,
                           attachments=attachments,
                           lab_reqs=lab_reqs,
                           rad_reqs=rad_reqs,
                           orders_count=orders_count)

@app.route("/patient/<visit_id>/edit", methods=["GET","POST"])
@login_required
@role_required("reception","admin")
def edit_patient(visit_id):
    db = get_db()
    cur = db.cursor()
    rec = cur.execute("""
        SELECT v.visit_id, v.payment_details, v.comment, v.queue_no, v.status, p.*
        FROM visits v JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    if not rec:
        flash("Requested record not found.", "danger")
        return redirect(url_for("ed_board"))

    if request.method == "POST":
        name = request.form.get("name","").strip()
        id_number = request.form.get("id_number","").strip()
        phone = request.form.get("phone","").strip()
        insurance = request.form.get("insurance","").strip()
        insurance_no = request.form.get("insurance_no","").strip()
        dob = request.form.get("dob","").strip()
        sex = request.form.get("sex","").strip()
        nationality = request.form.get("nationality","").strip()
        payment_details = request.form.get("payment_details","").strip()

        if not name:
            flash("Patient name is required.", "danger")
            return redirect(url_for("edit_patient", visit_id=visit_id))

        db.execute("""
            UPDATE patients SET
                name=?, id_number=?, phone=?, insurance=?, insurance_no=?,
                dob=?, sex=?, nationality=?
            WHERE id=?
        """,(name, id_number, phone, insurance, insurance_no, dob, sex, nationality, rec["id"]))
        db.execute("UPDATE visits SET payment_details=? WHERE visit_id=?", (payment_details, visit_id))
        db.commit()

        log_action("EDIT_PATIENT", visit_id=visit_id, details=f"Name={name}")
        flash("Patient updated successfully.", "success")
        return redirect(url_for("patient_details", visit_id=visit_id))

    return render_template("edit_patient.html", r=rec)

@app.route("/patient/<visit_id>/upload_id", methods=["POST"])
@login_required
@role_required("reception","nurse","admin")
def upload_id(visit_id):
    if "file" not in request.files:
        flash("No file selected.", "danger")
        return redirect(url_for("patient_details", visit_id=visit_id))

    file = request.files["file"]
    if file.filename == "":
        flash("Please choose a file.", "danger")
        return redirect(url_for("patient_details", visit_id=visit_id))

    if not allowed_file(file.filename):
        flash("Invalid file type.", "danger")
        return redirect(url_for("patient_details", visit_id=visit_id))

    safe_name = secure_filename(file.filename)
    filename = f"{visit_id}_{int(datetime.now().timestamp())}_{safe_name}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))

    db = get_db()
    db.execute("""
        INSERT INTO attachments (visit_id, filename, uploaded_at, uploaded_by)
        VALUES (?,?,?,?)
    """,(visit_id, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username")))
    db.commit()
    log_action("UPLOAD_ATTACHMENT", visit_id=visit_id, details=filename)
    flash("Attachment uploaded successfully.", "success")
    return redirect(url_for("patient_details", visit_id=visit_id))

@app.route("/attachment/<int:att_id>/delete", methods=["POST"])
@login_required
@role_required("reception","admin")
def delete_attachment(att_id):
    db = get_db()
    cur = db.cursor()
    a = cur.execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
    if not a:
        flash("Attachment not found.", "danger")
        return redirect(url_for("ed_board"))
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, a["filename"]))
    except:
        pass
    db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
    db.commit()
    log_action("DELETE_ATTACHMENT", visit_id=a["visit_id"], details=a["filename"])
    flash("Attachment deleted.", "success")
    return redirect(url_for("patient_details", visit_id=a["visit_id"]))

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/visit/<visit_id>/close", methods=["POST"])
@login_required
@role_required("doctor","admin")
def close_visit(visit_id):
    status = request.form.get("status","DISCHARGED").strip().upper()
    if status not in ["DISCHARGED","ADMITTED","TRANSFERRED","LAMA","EXPIRED","IN_TREATMENT"]:
        status = "DISCHARGED"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username")
    db = get_db()
    db.execute("""
        UPDATE visits SET status=?, closed_at=?, closed_by=? WHERE visit_id=?
    """,(status, now, user, visit_id))
    db.commit()
    log_action("CLOSE_VISIT", visit_id=visit_id, details=status)
    flash(f"Visit closed as {status}.", "success")
    return redirect(url_for("ed_board"))

@app.route("/visit/<visit_id>/cancel", methods=["POST"])
@login_required
@role_required("reception","admin")
def cancel_visit(visit_id):
    """Allow reception to cancel a visit that has not yet been seen by the doctor.

    Rules:
    - Visit must still be OPEN.
    - There must be no clinical orders for this visit.
    - A text reason is mandatory.
    """
    reason = (request.form.get("cancel_reason") or "").strip()
    if not reason:
        flash("Please enter a reason for cancellation.", "danger")
        return redirect(url_for("patient_details", visit_id=visit_id))

    db = get_db()
    cur = db.cursor()

    visit = cur.execute(
        "SELECT id, status FROM visits WHERE visit_id=?",
        (visit_id,),
    ).fetchone()
    if not visit:
        flash("Visit not found.", "danger")
        return redirect(url_for("ed_board"))

    if visit["status"] != "OPEN":
        flash("This visit is already closed or cancelled.", "danger")
        return redirect(url_for("patient_details", visit_id=visit_id))

    # If there are clinical orders, we assume the doctor has already seen the patient
    try:
        orders_row = cur.execute(
            "SELECT COUNT(*) AS c FROM clinical_orders WHERE visit_id=?",
            (visit_id,),
        ).fetchone()
        orders_count = orders_row["c"] if orders_row is not None else 0
    except Exception:
        orders_count = 0

    if orders_count > 0:
        flash("This visit cannot be cancelled because it has already been seen by a doctor.", "danger")
        return redirect(url_for("patient_details", visit_id=visit_id))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = session.get("username", "")

    cur.execute(
        """UPDATE visits
               SET status='CANCELLED',
                   cancel_reason=?,
                   cancelled_by=?,
                   cancelled_at=?,
                   closed_at=?,
                   closed_by=?
             WHERE visit_id=? AND status='OPEN'""",
        (reason, username, now, now, username, visit_id),
    )
    db.commit()

    log_action("CANCEL_VISIT", visit_id=visit_id, details=reason)
    flash("Visit has been cancelled successfully by Reception.", "info")
    return redirect(url_for("ed_board"))


# ============================================================
# Triage
# ============================================================

@app.route("/triage/<visit_id>", methods=["GET","POST"])
@login_required
@role_required("nurse","doctor","admin")
def triage(visit_id):
    db = get_db()
    cur = db.cursor()
    visit = cur.execute("""
        SELECT v.*, 
               p.name, p.id_number, p.phone,
               p.insurance, p.insurance_no,
               p.dob, p.sex, p.nationality
        FROM visits v JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    if not visit:
        flash("Visit not found.", "danger")
        return redirect(url_for("ed_board"))

    if request.method == "POST":
        allergy = request.form.get("allergy_status","").strip()
        allergy_details = request.form.get("allergy_details","").strip()
        pr = request.form.get("pulse_rate","").strip()
        rr = request.form.get("resp_rate","").strip()
        bp_sys = request.form.get("bp_systolic","").strip()
        bp_dia = request.form.get("bp_diastolic","").strip()
        temp = request.form.get("temperature","").strip()
        gcs = request.form.get("consciousness_level","").strip()
        spo2 = request.form.get("spo2","").strip()
        pain = request.form.get("pain_score","").strip()
        weight = request.form.get("weight","").strip()
        height = request.form.get("height","").strip()
        cat = request.form.get("triage_cat","").strip()
        comment = request.form.get("comment","").strip()

        if not cat:
            flash("Triage Category (ES) is required.", "danger")
            return redirect(url_for("triage", visit_id=visit_id))

        db.execute("""
            UPDATE visits SET
                triage_status='YES',
                triage_cat=?,
                comment=?,
                allergy_status=?,
                allergy_details=?,
                pulse_rate=?,
                resp_rate=?,
                bp_systolic=?,
                bp_diastolic=?,
                temperature=?,
                consciousness_level=?,
                spo2=?,
                pain_score=?,
                weight=?,
                height=?
            WHERE visit_id=?
        """,(cat, comment, allergy, allergy_details, pr, rr, bp_sys, bp_dia, temp, gcs, spo2, pain, weight, height, visit_id))
        db.commit()
        log_action("TRIAGE_UPDATE", visit_id=visit_id, details=f"CAT={cat}")
        flash("Triage saved successfully.", "success")
        return redirect(url_for("patient_details", visit_id=visit_id))

    return render_template("triage.html", visit=visit)

# ============================================================
# Auto Summary Builder
# ============================================================

def build_auto_summary(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.*, p.name, p.id_number, p.phone, p.insurance, p.insurance_no,
               p.dob, p.sex, p.nationality
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return ""

    # Clinical orders
    orders = cur.execute("""
        SELECT * FROM clinical_orders
        WHERE visit_id=?
        ORDER BY id ASC
    """, (visit_id,)).fetchall()

    # Nursing notes
    notes = cur.execute("""
        SELECT * FROM nursing_notes
        WHERE visit_id=?
        ORDER BY id ASC
    """, (visit_id,)).fetchall()

    # Lab requests/results
    try:
        lab_reqs = cur.execute("""
            SELECT * FROM lab_requests
            WHERE visit_id=?
            ORDER BY id ASC
        """, (visit_id,)).fetchall()
    except Exception:
        lab_reqs = []

    # Radiology requests/reports
    try:
        rad_reqs = cur.execute("""
            SELECT * FROM radiology_requests
            WHERE visit_id=?
            ORDER BY id ASC
        """, (visit_id,)).fetchall()
    except Exception:
        rad_reqs = []

    # Discharge summary (doctor examination / history)
    try:
        discharge = cur.execute("""
            SELECT summary_text
            FROM discharge_summaries
            WHERE visit_id=?
        """, (visit_id,)).fetchone()
    except Exception:
        discharge = None
    discharge_text = (discharge["summary_text"] or "").strip() if discharge else ""

    lines = []

    # Basic info
    lines.append(f"Visit ID: {visit_id}")
    lines.append(
        f"Patient: {visit['name']} | ID: {visit['id_number'] or '-'} | "
        f"INS: {visit['insurance'] or '-'}"
    )
    if visit["comment"]:
        lines.append(f"Chief Complaint / Comment: {visit['comment']}")
    lines.append("")

    # Triage + Vitals
    lines.append("Triage & Vital Signs:")
    lines.append(
        f" - Triage Status: {visit['triage_status']} | "
        f"CAT: {visit['triage_cat'] or '-'}"
    )

    allergy_text = visit["allergy_status"] or "-"
    if visit["allergy_details"]:
        allergy_text += f" ({visit['allergy_details']})"
    lines.append(f" - Allergy: {allergy_text}")

    lines.append(
        f" - PR: {visit['pulse_rate'] or '-'} bpm, "
        f"RR: {visit['resp_rate'] or '-'} /min, "
        f"Temp: {visit['temperature'] or '-'} C"
    )
    lines.append(
        f" - BP: {visit['bp_systolic'] or '-'} / {visit['bp_diastolic'] or '-'} , "
        f"SpO2: {visit['spo2'] or '-'}%"
    )
    lines.append(
        f" - Consciousness: {visit['consciousness_level'] or '-'} , "
        f"Pain: {visit['pain_score'] or '-'} /10"
    )
    lines.append(
        f" - Weight: {visit['weight'] or '-'} kg , "
        f"Height: {visit['height'] or '-'} cm"
    )
    lines.append("")

    # Doctor examination / history (from discharge summary)
    if discharge_text:
        lines.append("Doctor Examination / History:")
        for line in discharge_text.splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
        lines.append("")

    # Clinical orders
    if orders:
        lines.append("Clinical Orders (chronological):")
        for o in orders:
            lines.append(f" Order #{o['id']} | {o['created_at']} by {o['created_by']}")
            if o["diagnosis"]:
                lines.append(f"  - Diagnosis: {o['diagnosis']}")
            if o["radiology_orders"]:
                lines.append(f"  - Radiology: {o['radiology_orders']}")
            if o["lab_orders"]:
                lines.append(f"  - Labs: {o['lab_orders']}")
            if o["medications"]:
                lines.append(f"  - Medications: {o['medications']}")
            lines.append("")
    else:
        lines.append("Clinical Orders: None")
        lines.append("")

    # Labs
    if lab_reqs:
        lines.append("Lab Tests & Results:")
        for l in lab_reqs:
            line = f" Lab #{l['id']} | {l['test_name']} | Status: {l['status']}"
            if l["result_text"]:
                line += f" | Result: {l['result_text']}"
            lines.append(line)

            details = []
            if l["requested_at"]:
                details.append(f"Req: {l['requested_at']} by {l['requested_by'] or '-'}")
            if l["received_at"]:
                details.append(f"Sample: {l['received_at']} by {l['received_by'] or '-'}")
            if l["reported_at"]:
                details.append(f"Reported: {l['reported_at']} by {l['reported_by'] or '-'}")
            if details:
                lines.append("  " + " | ".join(details))
        lines.append("")
    else:
        lines.append("Lab Tests & Results: None")
        lines.append("")

    # Radiology
    if rad_reqs:
        lines.append("Radiology Studies & Reports:")
        for r in rad_reqs:
            lines.append(
                f" Study #{r['id']} | {r['test_name']} | Status: {r['status']}"
            )
            details = []
            if r["requested_at"]:
                details.append(f"Req: {r['requested_at']} by {r['requested_by'] or '-'}")
            if r["done_at"]:
                details.append(f"Done: {r['done_at']} by {r['done_by'] or '-'}")
            if r["reported_at"]:
                details.append(f"Reported: {r['reported_at']} by {r['reported_by'] or '-'}")
            if details:
                lines.append("  " + " | ".join(details))
            if r["report_text"]:
                lines.append(f"  Report: {r['report_text']}")
            lines.append("")
    else:
        lines.append("Radiology Studies & Reports: None")
        lines.append("")

    # Nursing notes
    if notes:
        lines.append("Nursing Notes:")
        for n in notes:
            lines.append(f" {n['created_at']} | {n['created_by']}: {n['note_text']}")
        lines.append("")
    else:
        lines.append("Nursing Notes: None")
        lines.append("")

    # Final status
    lines.append(f"Final Visit Status: {visit['status']}")
    if visit["closed_at"]:
        lines.append(
            f"Closed At: {visit['closed_at']} by {visit['closed_by'] or '-'}"
        )

    return "\n".join(lines).strip()


def build_patient_short_summary(visit_id):
    """Patient-friendly summary:
       - Diagnosis
       - Referral
       - ED medications given
       - Lab results (only REPORTED)
       - Radiology results (only REPORTED)
       - Home medication
    """
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return ""

    summary = cur.execute("""
        SELECT diagnosis_cc, referral_clinic, home_medication, summary_text
        FROM discharge_summaries
        WHERE visit_id=?
    """, (visit_id,)).fetchone()

    # ED medications from clinical orders
    orders = cur.execute("""
        SELECT medications
        FROM clinical_orders
        WHERE visit_id=?
    """, (visit_id,)).fetchall()

    # Only REPORTED labs
    labs = cur.execute("""
        SELECT test_name, status, result_text
        FROM lab_requests
        WHERE visit_id=?
    """, (visit_id,)).fetchall()

    # Only REPORTED radiology
    rads = cur.execute("""
        SELECT test_name, status, report_text
        FROM radiology_requests
        WHERE visit_id=?
    """, (visit_id,)).fetchall()

    lines = []

    # Header
    lines.append(f"Visit ID: {visit_id}")
    lines.append(f"Patient: {visit['name']} | ID: {visit['id_number'] or '-'} | INS: {visit['insurance'] or '-'}")
    lines.append("")

    # Diagnosis
    lines.append("Diagnosis:")
    if summary and summary["diagnosis_cc"]:
        lines.append(f" - {summary['diagnosis_cc']}")
    else:
        lines.append(" - Not documented")
    lines.append("")

    # Doctor examination / history
    if summary and summary["summary_text"]:
        lines.append("Doctor Examination / History:")
        for line in summary["summary_text"].splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
        lines.append("")

    # Referral
    if summary and summary["referral_clinic"]:
        lines.append("Referral Clinic:")
        lines.append(f" - {summary['referral_clinic']}")
        lines.append("")

    # ED Medications
    ed_meds = []
    for o in orders:
        if o["medications"]:
            for m in o["medications"].split(","):
                m = m.strip()
                if m and m not in ed_meds:
                    ed_meds.append(m)

    lines.append("Medications Given in ED:")
    if ed_meds:
        for m in ed_meds:
            lines.append(f" - {m}")
    else:
        lines.append(" - None")
    lines.append("")

    # Lab Results (ONLY reported)
    reported_labs = [l for l in labs if l["status"] == "REPORTED"]
    lines.append("Lab Results:")
    if reported_labs:
        for l in reported_labs:
            result = l["result_text"] or "-"
            lines.append(f" - {l['test_name']}: {result}")
    else:
        lines.append(" - No reported results yet")
    lines.append("")

    # Radiology Results (ONLY reported)
    reported_rads = [r for r in rads if r["status"] == "REPORTED"]
    lines.append("Radiology Reports:")
    if reported_rads:
        for r in reported_rads:
            report = r["report_text"] or "-"
            lines.append(f" - {r['test_name']}: {report}")
    else:
        lines.append(" - No reported imaging yet")
    lines.append("")

    # Home medication
    lines.append("Home Medication (Pharmacy):")
    if summary and summary["home_medication"]:
        for line in summary["home_medication"].splitlines():
            if line.strip():
                lines.append(f" - {line.strip()}")
    else:
        lines.append(" - None")
    lines.append("")

    return "\n".join(lines).strip()

# ============================================================
# Clinical Orders + Notes + Discharge
# ============================================================

@app.route("/clinical_orders/<visit_id>")
@login_required
def clinical_orders_page(visit_id):
    db = get_db()
    cur = db.cursor()
    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance
        FROM visits v JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    if not visit:
        flash("Visit not found.", "danger")
        return redirect(url_for("ed_board"))

    orders = cur.execute("""
        SELECT * FROM clinical_orders WHERE visit_id=? ORDER BY id DESC
    """,(visit_id,)).fetchall()
    notes = cur.execute("""
        SELECT * FROM nursing_notes WHERE visit_id=? ORDER BY id DESC
    """,(visit_id,)).fetchall()
    summary = cur.execute("""
        SELECT * FROM discharge_summaries WHERE visit_id=?
    """,(visit_id,)).fetchone()

    lab_reqs = cur.execute("""
        SELECT * FROM lab_requests
        WHERE visit_id=?
        ORDER BY id DESC
    """,(visit_id,)).fetchall()

    rad_reqs = cur.execute("""
        SELECT * FROM radiology_requests
        WHERE visit_id=?
        ORDER BY id DESC
    """,(visit_id,)).fetchall()

    return render_template("clinical_orders.html",
                           visit=visit,
                           orders=orders,
                           notes=notes,
                           summary=summary,
                           lab_reqs=lab_reqs,
                           rad_reqs=rad_reqs)

@app.route("/clinical_orders/<visit_id>/add", methods=["POST"])
@login_required
@role_required("doctor","nurse","admin")
def add_clinical_order(visit_id):
    """
    Add new clinical order and auto-create lab/radiology requests.
    """
    diagnosis = clean_text(request.form.get("diagnosis","").strip())
    radiology = clean_text(request.form.get("radiology_orders","").strip())
    labs      = clean_text(request.form.get("lab_orders","").strip())
    meds      = clean_text(request.form.get("medications","").strip())

    db   = get_db()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username","UNKNOWN")

    # 1) save clinical order
    db.execute("""
        INSERT INTO clinical_orders
        (visit_id, diagnosis, radiology_orders, lab_orders, medications,
         duplicated_from, created_at, created_by)
        VALUES (?,?,?,?,?,?,?,?)
    """,(visit_id, diagnosis, radiology, labs, meds,
         None, now, user))
    db.commit()

    # 2) create lab requests
    if labs:
        tests = [t.strip() for t in labs.split(",") if t.strip()]
        for test in tests:
            db.execute("""
                INSERT INTO lab_requests
                (visit_id, test_name, status, requested_at, requested_by)
                VALUES (?,?,?,?,?)
            """,(visit_id, test, "REQUESTED", now, user))
        db.commit()

    # 3) create radiology requests
    if radiology:
        tests = [t.strip() for t in radiology.split(",") if t.strip()]
        for test in tests:
            db.execute("""
                INSERT INTO radiology_requests
                (visit_id, test_name, status, requested_at, requested_by)
                VALUES (?,?,?,?,?)
            """,(visit_id, test, "REQUESTED", now, user))
        db.commit()

    # 4) update auto summary
    auto_text = build_auto_summary(visit_id)
    db.execute("""
        INSERT INTO discharge_summaries (visit_id, auto_summary_text, summary_text, created_at, created_by)
        VALUES (?,?,?,?,?)
        ON CONFLICT(visit_id) DO UPDATE SET auto_summary_text=excluded.auto_summary_text, updated_at=?, updated_by=?
    """,(visit_id, auto_text, None, now, user, now, user))
    db.commit()

    log_action("ADD_ORDER", visit_id=visit_id)
    flash("Clinical Order saved, lab/radiology requests created.", "success")
    return redirect(url_for("clinical_orders_page", visit_id=visit_id))

@app.route("/clinical_orders/<visit_id>/update/<int:oid>", methods=["POST"])
@login_required
@role_required("doctor","nurse","admin")
def update_clinical_order(visit_id, oid):
    diagnosis = clean_text(request.form.get("diagnosis","").strip())
    radiology = clean_text(request.form.get("radiology_orders","").strip())
    labs = clean_text(request.form.get("lab_orders","").strip())
    meds = clean_text(request.form.get("medications","").strip())

    db = get_db()
    db.execute("""
        UPDATE clinical_orders SET diagnosis=?, radiology_orders=?, lab_orders=?, medications=?,
                                   updated_at=?, updated_by=?
        WHERE id=? AND visit_id=?
    """,(diagnosis, radiology, labs, meds,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username"),
         oid, visit_id))
    db.commit()

    auto_text = build_auto_summary(visit_id)
    db.execute("UPDATE discharge_summaries SET auto_summary_text=?, updated_at=?, updated_by=? WHERE visit_id=?",
               (auto_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username"), visit_id))
    db.commit()

    log_action("UPDATE_ORDER", visit_id=visit_id, details=str(oid))
    flash("Clinical Order updated.", "success")
    return redirect(url_for("clinical_orders_page", visit_id=visit_id))

@app.route("/clinical_orders/<visit_id>/duplicate/<int:oid>")
@login_required
@role_required("doctor","nurse","admin")
def duplicate_clinical_order(visit_id, oid):
    db = get_db()
    old = db.execute("SELECT * FROM clinical_orders WHERE id=? AND visit_id=?", (oid, visit_id)).fetchone()
    if not old:
        flash("Order not found.", "danger")
        return redirect(url_for("clinical_orders_page", visit_id=visit_id))

@app.route("/clinical_orders/<visit_id>/delete/<int:oid>", methods=["POST"])
@login_required
@role_required("doctor","nurse","admin")
def delete_clinical_order(visit_id, oid):
    db = get_db()
    order = db.execute("SELECT id FROM clinical_orders WHERE id=? AND visit_id=?", (oid, visit_id)).fetchone()
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("clinical_orders_page", visit_id=visit_id))

    db.execute("DELETE FROM clinical_orders WHERE id=? AND visit_id=?", (oid, visit_id))
    db.commit()

    # re-build auto summary after delete
    try:
        auto_text = build_auto_summary(visit_id)
        db.execute("UPDATE discharge_summaries SET auto_summary_text=?, updated_at=?, updated_by=? WHERE visit_id=?",
                   (auto_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username"), visit_id))
        db.commit()
    except Exception:
        pass

    log_action("DELETE_ORDER", visit_id=visit_id, details=str(oid))
    flash("Clinical Order deleted.", "success")
    return redirect(url_for("clinical_orders_page", visit_id=visit_id))


    db.execute("""
        INSERT INTO clinical_orders
        (visit_id, diagnosis, radiology_orders, lab_orders, medications, duplicated_from, created_at, created_by)
        VALUES (?,?,?,?,?,?,?,?)
    """,(visit_id, old["diagnosis"], old["radiology_orders"], old["lab_orders"], old["medications"],
         oid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username")))
    db.commit()

    auto_text = build_auto_summary(visit_id)
    db.execute("UPDATE discharge_summaries SET auto_summary_text=?, updated_at=?, updated_by=? WHERE visit_id=?",
               (auto_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username"), visit_id))
    db.commit()

    log_action("DUPLICATE_ORDER", visit_id=visit_id, details=str(oid))
    flash("Order duplicated.", "success")
    return redirect(url_for("clinical_orders_page", visit_id=visit_id))

@app.route("/clinical_orders/<visit_id>/pdf/<int:oid>")
@login_required
def clinical_order_pdf(visit_id, oid):
    db = get_db()
    cur = db.cursor()
    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance
        FROM visits v JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    order = cur.execute("""
        SELECT * FROM clinical_orders WHERE id=? AND visit_id=?
    """,(oid, visit_id)).fetchone()
    if not order or not visit:
        return "Not found", 404

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Clinical Order")
    y -= 1*cm
    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit: {visit_id}   Order#: {oid}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}   ID: {visit['id_number']}")
    y -= 0.8*cm

    def section(title, text):
        nonlocal y
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2*cm, y, title)
        y -= 0.6*cm
        c.setFont("Helvetica", 10)
        for line in (text or "-").splitlines():
            c.drawString(2.2*cm, y, line[:110])
            y -= 0.45*cm
            if y < 2*cm:
                c.showPage(); y = height - 2*cm
        y -= 0.3*cm

    section("Diagnosis / Chief Complaint:", order["diagnosis"])
    section("Radiology orders:", order["radiology_orders"])
    section("Lab orders:", order["lab_orders"])
    section("Medications:", order["medications"])

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)
    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(buffer.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition": "inline; filename=clinical_order.pdf"})

@app.route("/nursing_notes/<visit_id>/add", methods=["POST"])
@login_required
@role_required("nurse","doctor","admin")
def add_nursing_note(visit_id):
    text = clean_text(request.form.get("note_text","").strip())
    if not text:
        flash("Enter note text.", "danger")
        return redirect(url_for("clinical_orders_page", visit_id=visit_id))

    db = get_db()
    db.execute("""
        INSERT INTO nursing_notes (visit_id, note_text, created_at, created_by)
        VALUES (?,?,?,?)
    """,(visit_id, text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username")))
    db.commit()

    auto_text = build_auto_summary(visit_id)
    db.execute("UPDATE discharge_summaries SET auto_summary_text=?, updated_at=?, updated_by=? WHERE visit_id=?",
               (auto_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("username"), visit_id))
    db.commit()

    log_action("ADD_NOTE", visit_id=visit_id)
    flash("Nursing note saved.", "success")
    return redirect(url_for("clinical_orders_page", visit_id=visit_id))

@app.route("/nursing_notes/<visit_id>/pdf")
@login_required
def nursing_notes_pdf(visit_id):
    db = get_db(); cur=db.cursor()
    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance
        FROM visits v JOIN patients p ON p.id=v.patient_id WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    notes = cur.execute("""
        SELECT * FROM nursing_notes WHERE visit_id=? ORDER BY id ASC
    """,(visit_id,)).fetchall()
    if not visit:
        return "Not found", 404

    buffer=io.BytesIO(); c=canvas.Canvas(buffer, pagesize=A4)
    width,height=A4; y=height-2*cm
    c.setFont("Helvetica-Bold",16); c.drawString(2*cm,y,"Nursing Notes"); y-=1*cm
    c.setFont("Helvetica",11)
    c.drawString(2*cm,y,f"Visit: {visit_id}"); y-=0.7*cm
    c.drawString(2*cm,y,f"Patient: {visit['name']}   ID: {visit['id_number']}"); y-=1*cm

    if not notes:
        c.drawString(2*cm,y,"No nursing notes.")
    else:
        for n in notes:
            c.setFont("Helvetica-Bold",9)
            c.drawString(2*cm,y,f"{n['created_at']} | {n['created_by']}"[:120]); y-=0.4*cm
            c.setFont("Helvetica",10)
            for line in (n["note_text"] or "-").splitlines():
                c.drawString(2.2*cm,y,line[:110]); y-=0.4*cm
                if y<2*cm: c.showPage(); y=height-2*cm
            y-=0.2*cm

    c.setFont("Helvetica-Oblique",8); c.drawString(2*cm,1.2*cm,APP_FOOTER_TEXT)
    c.showPage(); c.save(); buffer.seek(0)
    return Response(buffer.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition":"inline; filename=nursing_notes.pdf"})

@app.route("/discharge/<visit_id>/save", methods=["POST"])
@login_required
@role_required("doctor","reception","admin")
def discharge_save(visit_id):
    diagnosis_cc = clean_text(request.form.get("diagnosis_cc","").strip())
    referral_clinic = clean_text(request.form.get("referral_clinic","").strip())
    home_medication = clean_text(request.form.get("home_medication","").strip())
    text = clean_text(request.form.get("summary_text","").strip())

    db=get_db(); cur=db.cursor()
    exists = cur.execute("SELECT * FROM discharge_summaries WHERE visit_id=?", (visit_id,)).fetchone()
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"); user=session.get("username")

    if exists:
        db.execute("""
            UPDATE discharge_summaries SET
                diagnosis_cc=?, referral_clinic=?, home_medication=?, summary_text=?,
                updated_at=?, updated_by=?
            WHERE visit_id=?
        """,(diagnosis_cc, referral_clinic, home_medication, text, now, user, visit_id))
    else:
        db.execute("""
            INSERT INTO discharge_summaries
            (visit_id, diagnosis_cc, referral_clinic, home_medication, summary_text, created_at, created_by)
            VALUES (?,?,?,?,?,?,?)
        """,(visit_id, diagnosis_cc, referral_clinic, home_medication, text, now, user))
    db.commit()

    # Refresh auto-summarized ED course (including updated doctor examination / history)
    auto_text = build_auto_summary(visit_id)
    db.execute(
        "UPDATE discharge_summaries SET auto_summary_text=? WHERE visit_id=?",
        (auto_text, visit_id),
    )
    db.commit()

    log_action("SAVE_DISCHARGE", visit_id=visit_id)
    flash("Discharge summary saved.", "success")
    return redirect(url_for("clinical_orders_page", visit_id=visit_id))
@app.route("/discharge/<visit_id>/pdf")
@login_required
def discharge_summary_pdf(visit_id):
    db=get_db(); cur=db.cursor()
    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance
        FROM visits v JOIN patients p ON p.id=v.patient_id WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    summary = cur.execute("SELECT * FROM discharge_summaries WHERE visit_id=?", (visit_id,)).fetchone()
    if not visit:
        return "Not found", 404

    auto_text = summary["auto_summary_text"] if summary and summary["auto_summary_text"] else build_auto_summary(visit_id)

    buffer=io.BytesIO(); c=canvas.Canvas(buffer, pagesize=A4)
    width,height=A4; y=height-2*cm

    c.setFont("Helvetica-Bold",16); c.drawString(2*cm,y,"Discharge Summary (V5)"); y-=1*cm
    c.setFont("Helvetica",11)
    c.drawString(2*cm,y,f"Visit: {visit_id}"); y-=0.7*cm
    c.drawString(2*cm,y,f"Patient: {visit['name']}   ID: {visit['id_number']}"); y-=0.8*cm

    def draw_multiline(label, text):
        nonlocal y
        c.setFont("Helvetica-Bold",11); c.drawString(2*cm,y,label); y-=0.6*cm
        c.setFont("Helvetica",10)
        for line in (text or "-").splitlines():
            c.drawString(2.3*cm,y,line[:110]); y-=0.45*cm
            if y<2*cm: c.showPage(); y=height-2*cm
        y-=0.3*cm

    draw_multiline("Diagnosis / Chief Complaint:", summary["diagnosis_cc"] if summary else "")
    draw_multiline("Referral to Clinic:", summary["referral_clinic"] if summary else "")
    draw_multiline("Home Medication:", summary["home_medication"] if summary else "")
    draw_multiline("Doctor Examination / History:", summary["summary_text"] if summary else "")

    c.setFont("Helvetica-Bold",12); c.drawString(2*cm,y,"Auto ED Course Summary:"); y-=0.6*cm
    c.setFont("Helvetica",9)
    for line in (auto_text or "-").splitlines():
        c.drawString(2.3*cm,y,line[:130]); y-=0.4*cm
        if y<2*cm: c.showPage(); y=height-2*cm

    c.setFont("Helvetica-Oblique",8); c.drawString(2*cm,1.2*cm,APP_FOOTER_TEXT)
    c.showPage(); c.save(); buffer.seek(0)
    return Response(buffer.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition":"inline; filename=discharge_summary.pdf"})

# ============================================================
# Lab Board (Requests / Results)
# ============================================================

@app.route("/lab_board")
@login_required
@role_required("lab", "admin", "doctor", "nurse")
def lab_board():
    """
    Lab board for viewing and updating lab requests.
    Supports simple search by patient, ID, visit or test.
    """
    status_filter = request.args.get("status", "PENDING")
    q = request.args.get("q", "").strip()

    cur = get_db().cursor()
    sql = """
        SELECT lr.*,
               v.visit_id,
               p.name,
               p.id_number
        FROM lab_requests lr
        JOIN visits v ON v.visit_id = lr.visit_id
        JOIN patients p ON p.id = v.patient_id
        WHERE 1=1
    """
    params = []

    # Status filter
    if status_filter == "PENDING":
        sql += " AND lr.status IN ('REQUESTED','RECEIVED')"
    elif status_filter == "REPORTED":
        sql += " AND lr.status='REPORTED'"
    elif status_filter == "ALL":
        pass
    else:
        status_filter = "PENDING"
        sql += " AND lr.status IN ('REQUESTED','RECEIVED')"

    # Text search
    if q:
        like = f"%{q}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR v.visit_id LIKE ? OR lr.test_name LIKE ?)"
        params.extend([like, like, like, like])

    sql += " ORDER BY lr.id DESC LIMIT 500"
    rows = cur.execute(sql, params).fetchall()

    return render_template(
        "lab_board.html",
        rows=rows,
        status_filter=status_filter,
        q=q
    )


@app.route("/lab_request/<int:rid>/receive", methods=["POST"])
@login_required
@role_required("lab", "admin")
def lab_receive_sample(rid):
    """
    Mark sample as received for a lab request.
    """
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")

    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(url_for("lab_board"))

    if row["status"] != "REQUESTED":
        flash("Cannot receive sample in current status.", "warning")
        return redirect(url_for("lab_board"))

    db.execute("""
        UPDATE lab_requests
        SET status='RECEIVED', received_at=?, received_by=?
        WHERE id=?
    """,(now, user, rid))
    db.commit()

    log_action("LAB_RECEIVE", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Sample received recorded.", "success")
    return redirect(url_for("lab_board"))


@app.route("/lab_request/<int:rid>/report", methods=["POST"])
@login_required
@role_required("lab", "admin")
def lab_report_result(rid):
    """
    Enter lab result and mark as REPORTED.
    """
    result = clean_text(request.form.get("result_text", "").strip())
    if not result:
        flash("Please enter result before saving.", "danger")
        return redirect(url_for("lab_board"))

    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(url_for("lab_board"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")

    db.execute("""
        UPDATE lab_requests
        SET status='REPORTED', result_text=?, reported_at=?, reported_by=?
        WHERE id=?
    """,(result, now, user, rid))
    db.commit()

    log_action("LAB_REPORT", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Lab result saved.", "success")
    return redirect(url_for("lab_board"))


# ============================================================
# Radiology Board (Requests / Reports)
# ============================================================

@app.route("/radiology_board")
@login_required
@role_required("radiology", "admin", "doctor", "nurse")
def radiology_board():
    """
    Radiology board for viewing and updating radiology requests.
    Supports simple search by patient, ID, visit or study.
    """
    status_filter = request.args.get("status", "PENDING")
    q = request.args.get("q", "").strip()

    cur = get_db().cursor()
    sql = """
        SELECT rr.*,
               v.visit_id,
               p.name,
               p.id_number
        FROM radiology_requests rr
        JOIN visits v ON v.visit_id = rr.visit_id
        JOIN patients p ON p.id = v.patient_id
        WHERE 1=1
    """
    params = []

    # Status filter
    if status_filter == "PENDING":
        sql += " AND rr.status IN ('REQUESTED','DONE')"
    elif status_filter == "REPORTED":
        sql += " AND rr.status='REPORTED'"
    elif status_filter == "ALL":
        pass
    else:
        status_filter = "PENDING"
        sql += " AND rr.status IN ('REQUESTED','DONE')"

    # Text search
    if q:
        like = f"%{q}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR v.visit_id LIKE ? OR rr.test_name LIKE ?)"
        params.extend([like, like, like, like])

    sql += " ORDER BY rr.id DESC LIMIT 500"
    rows = cur.execute(sql, params).fetchall()

    return render_template(
        "radiology_board.html",
        rows=rows,
        status_filter=status_filter,
        q=q
    )


@app.route("/radiology_request/<int:rid>/done", methods=["POST"])
@login_required
@role_required("radiology", "admin")
def radiology_mark_done(rid):
    """
    Mark that radiology study has been done.
    """
    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM radiology_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Radiology request not found.", "danger")
        return redirect(url_for("radiology_board"))

    if row["status"] != "REQUESTED":
        flash("Cannot mark as done in current status.", "warning")
        return redirect(url_for("radiology_board"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")

    db.execute("""
        UPDATE radiology_requests
        SET status='DONE', done_at=?, done_by=?
        WHERE id=?
    """,(now, user, rid))
    db.commit()

    log_action("RAD_DONE", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Marked as done.", "success")
    return redirect(url_for("radiology_board"))


@app.route("/radiology_request/<int:rid>/report", methods=["POST"])
@login_required
@role_required("radiology", "admin")
def radiology_report_result(rid):
    """
    Enter radiology report and mark as REPORTED.
    """
    report = clean_text(request.form.get("report_text", "").strip())
    if not report:
        flash("Please enter report text before saving.", "danger")
        return redirect(url_for("radiology_board"))

    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM radiology_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Radiology request not found.", "danger")
        return redirect(url_for("radiology_board"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")

    db.execute("""
        UPDATE radiology_requests
        SET status='REPORTED', report_text=?, reported_at=?, reported_by=?
        WHERE id=?
    """,(report, now, user, rid))
    db.commit()

    log_action("RAD_REPORT", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Radiology report saved.", "success")
    return redirect(url_for("radiology_board"))


# ============================================================
# Auto Summary PDF (Nursing / Staff Copy)
# ============================================================

@app.route("/auto_summary/<visit_id>/pdf")
@login_required
@role_required("nurse","doctor","admin")
def auto_summary_pdf(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance, v.created_at
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    auto_text = build_auto_summary(visit_id)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Auto ED Course Summary")
    y -= 1.0*cm

    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit ID: {visit['visit_id']}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"ID: {visit['id_number'] or '-'}    INS: {visit['insurance'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Date: {visit['created_at']}")
    y -= 0.9*cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Summary:")
    y -= 0.6*cm

    c.setFont("Helvetica", 9)
    if not auto_text.strip():
        c.drawString(2.2*cm, y, "None")
    else:
        for line in auto_text.splitlines():
            c.drawString(2.2*cm, y, line[:130])
            y -= 0.42*cm
            if y < 2*cm:
                c.showPage()
                y = height - 2*cm
                c.setFont("Helvetica", 9)

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)

    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=auto_summary.pdf"}
    )


@app.route("/patient_summary/<visit_id>/pdf")
@login_required
def patient_summary_pdf(visit_id):
    """Patient-friendly short summary PDF without internal staff details."""
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance, v.created_at
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    text = build_patient_short_summary(visit_id)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "ED Visit Summary - Patient Copy")
    y -= 1.0*cm

    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit ID: {visit['visit_id']}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"ID: {visit['id_number'] or '-'}    INS: {visit['insurance'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Date: {visit['created_at']}")
    y -= 0.8*cm

    c.setFont("Helvetica", 10)
    for line in (text or "-").splitlines():
        c.drawString(2*cm, y, line[:120])
        y -= 0.40*cm
        if y < 2*cm:
            c.showPage()
            y = height - 2*cm
            c.setFont("Helvetica", 10)

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)

    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=patient_short_summary.pdf"}
    )
@app.route("/lab_results/<visit_id>/pdf")
@login_required
def lab_results_pdf(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance, v.created_at
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    labs = cur.execute("""
        SELECT * FROM lab_requests
        WHERE visit_id=?
        ORDER BY id ASC
    """, (visit_id,)).fetchall()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Lab Results")
    y -= 1.0*cm

    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit: {visit['visit_id']}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}   ID: {visit['id_number'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"INS: {visit['insurance'] or '-'}   Date: {visit['created_at']}")
    y -= 1.0*cm

    if not labs:
        c.setFont("Helvetica", 10)
        c.drawString(2*cm, y, "No lab requests/results for this visit.")
    else:
        for l in labs:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(2*cm, y, f"Lab #{l['id']} - {l['test_name']}")
            y -= 0.4*cm

            c.setFont("Helvetica", 9)
            status_line = f"Status: {l['status']}"
            if l["result_text"]:
                status_line += f" | Result: {l['result_text']}"
            c.drawString(2*cm, y, status_line[:130])
            y -= 0.35*cm

            details = []
            if l["requested_at"]:
                details.append(f"Req: {l['requested_at']} by {l['requested_by'] or '-'}")
            if l["received_at"]:
                details.append(f"Sample: {l['received_at']} by {l['received_by'] or '-'}")
            if l["reported_at"]:
                details.append(f"Reported: {l['reported_at']} by {l['reported_by'] or '-'}")
            if details:
                c.drawString(2*cm, y, " | ".join(details)[:130])
                y -= 0.35*cm

            y -= 0.2*cm
            if y < 2*cm:
                c.showPage()
                y = height - 2*cm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)
    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=lab_results.pdf"}
    )


@app.route("/radiology_results/<visit_id>/pdf")
@login_required
def radiology_results_pdf(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance, v.created_at
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    rads = cur.execute("""
        SELECT * FROM radiology_requests
        WHERE visit_id=?
        ORDER BY id ASC
    """, (visit_id,)).fetchall()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Radiology Reports")
    y -= 1.0*cm

    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit: {visit['visit_id']}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}   ID: {visit['id_number'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"INS: {visit['insurance'] or '-'}   Date: {visit['created_at']}")
    y -= 1.0*cm

    if not rads:
        c.setFont("Helvetica", 10)
        c.drawString(2*cm, y, "No radiology requests/reports for this visit.")
    else:
        for r in rads:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(2*cm, y, f"Study #{r['id']} - {r['test_name']}")
            y -= 0.4*cm

            c.setFont("Helvetica", 9)
            status_line = f"Status: {r['status']}"
            c.drawString(2*cm, y, status_line[:130])
            y -= 0.35*cm

            details = []
            if r["requested_at"]:
                details.append(f"Req: {r['requested_at']} by {r['requested_by'] or '-'}")
            if r["done_at"]:
                details.append(f"Done: {r['done_at']} by {r['done_by'] or '-'}")
            if r["reported_at"]:
                details.append(f"Reported: {r['reported_at']} by {r['reported_by'] or '-'}")
            if details:
                c.drawString(2*cm, y, " | ".join(details)[:130])
                y -= 0.35*cm

            if r["report_text"]:
                for line in (r["report_text"] or "-").splitlines():
                    c.drawString(2*cm, y, ("Report: " + line)[:130])
                    y -= 0.35*cm
                    if y < 2*cm:
                        c.showPage()
                        y = height - 2*cm

            y -= 0.2*cm
            if y < 2*cm:
                c.showPage()
                y = height - 2*cm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)
    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=radiology_results.pdf"}
    )


@app.route("/home_med/<visit_id>/pdf")
@login_required
def home_med_pdf(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id, p.name, p.id_number, p.insurance, v.created_at
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    summary = cur.execute("""
        SELECT home_medication
        FROM discharge_summaries
        WHERE visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    home_med = summary["home_medication"] if summary and summary["home_medication"] else ""

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Home Medication")
    y -= 1.0*cm

    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit ID: {visit['visit_id']}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"ID: {visit['id_number'] or '-'}    INS: {visit['insurance'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Date: {visit['created_at']}")
    y -= 1.0*cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Medication:")
    y -= 0.6*cm

    c.setFont("Helvetica", 10)
    if not home_med.strip():
        c.drawString(2.2*cm, y, "None")
    else:
        for line in home_med.splitlines():
            c.drawString(2.2*cm, y, line[:110])
            y -= 0.45*cm
            if y < 2*cm:
                c.showPage()
                y = height - 2*cm
                c.setFont("Helvetica", 10)

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)

    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=home_medication.pdf"}
    )

# ============================================================
# Sticker HTML + ZPL
# ============================================================

@app.route("/sticker/<visit_id>")
@login_required
def sticker_html(visit_id):
    cur=get_db().cursor()
    v=cur.execute("""
        SELECT v.visit_id, v.queue_no, v.created_at, p.name, p.id_number, p.insurance
        FROM visits v JOIN patients p ON p.id=v.patient_id WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    if not v: return "Not found",404
    time_only=v["created_at"][11:16]
    return render_template("sticker.html", v=v, time_only=time_only)

@app.route("/sticker/<visit_id>/zpl")
@login_required
def sticker_zpl(visit_id):
    cur = get_db().cursor()
    v = cur.execute("""
        SELECT v.created_at, p.name, p.id_number, p.insurance, p.dob
        FROM visits v 
        JOIN patients p ON p.id=v.patient_id 
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not v:
        return "Not Found", 404

    # Extract time only (HH:MM)
    t = v["created_at"][11:16]

    # Compute age in years based on DOB and visit date
    age_str = "-"
    dob_str = v.get("dob") if isinstance(v, dict) else v["dob"]
    if dob_str:
        try:
            dob_dt = datetime.strptime(dob_str, "%Y-%m-%d")
            visit_date = datetime.strptime(v["created_at"][:10], "%Y-%m-%d")
            age_years = visit_date.year - dob_dt.year - (
                (visit_date.month, visit_date.day) < (dob_dt.month, dob_dt.day)
            )
            age_str = str(age_years)
        except Exception:
            age_str = "-"

    # ZPL label 50.8mm x 31.75mm (2" x 1.25") - landscape
    # 203 dpi → width ≈ 406 dots, height ≈ 254 dots
    zpl = f"""
^XA
^PW406
^LL254
^FWR
^CI28

^CF0,24
^FO20,20^FDED DOWNTIME^FS

^CF0,26
^FO110,20^FDNAME: {v['name']}^FS
^FO200,20^FDID: {v['id_number'] or '-'}^FS
^FO290,20^FDAGE: {age_str}^FS
^FO360,20^FDINS: {v['insurance'] or '-'}^FS
^FO430,20^FDTIME: {t}^FS

^XZ
"""
    return Response(zpl, mimetype="text/plain")


# ============================================================
# Templates (Single-file)
# ============================================================

TEMPLATES = {
"base.html": """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ED Downtime</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <meta http-equiv="refresh" content="2700">
  <style>
    body { background:#f7f7f7; }
    .nav-link { font-weight:600; }
    .badge-triage-no { background:#999; }
    .badge-triage-yes { background:#198754; }
    .cat-red { background:#dc3545; }
    .cat-yellow { background:#ffc107; color:#000; }
    .cat-green { background:#198754; }
    .cat-orange { background:#fd7e14; }
    .cat-none { background:#6c757d; }
  </style>
</head>
<body>
<nav class="navbar navbar-light bg-white border-bottom px-3">
  <span class="navbar-brand fw-bold">ED Downtime</span>

  <div class="d-flex gap-3 align-items-center">
    <a class="nav-link" href="{{ url_for('ed_board') }}">ED Board</a>
    <a class="nav-link" href="{{ url_for('search_patients') }}">Search</a>
    <a class="nav-link" href="{{ url_for('chat_page') }}">Live Chat</a>
    {% if session.get('role') in ['lab','admin','doctor','nurse'] %}
      <a class="nav-link" href="{{ url_for('lab_board') }}">Lab Board</a>
    {% endif %}
    {% if session.get('role') in ['radiology','admin','doctor','nurse'] %}
      <a class="nav-link" href="{{ url_for('radiology_board') }}">Radiology Board</a>
    {% endif %}
    {% if session.get('role') in ['reception','admin'] %}
      <a class="nav-link" href="{{ url_for('register_patient') }}">Register</a>
    {% endif %}
    {% if session.get('role')=='admin' %}
      <a class="nav-link" href="{{ url_for('admin_users') }}">Users</a>
      <a class="nav-link" href="{{ url_for('admin_reset_password') }}">Reset Password</a>
      <a class="nav-link" href="{{ url_for('admin_logs') }}">Logs</a>
      <a class="nav-link" href="{{ url_for('admin_backup') }}">Backup DB</a>
      <a class="nav-link text-primary" href="{{ url_for('admin_backup_now') }}">Backup Now</a>
      <a class="nav-link text-danger" href="{{ url_for('admin_restore') }}">Restore DB</a>
    {% endif %}
    <span class="text-muted">User: {{ session.get('username') }} ({{ session.get('role') }})</span>
    <a class="text-danger nav-link" href="{{ url_for('logout') }}">Logout</a>
  </div>
  <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">🔄 Manual Refresh</button>
</nav>

<div class="container py-3">
  {% block content %}{% endblock %}
</div>

<footer class="text-center text-muted small py-3">
  {{ footer_text }}
</footer>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
""",

"login.html": """
{% extends "base.html" %}
{% block content %}
<div class="row justify-content-center mt-5">
  <div class="col-md-4">
    <h4 class="mb-3 text-center">Login</h4>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% for category, msg in messages %}
        <div class="alert alert-{{ category }}">{{ msg }}</div>
      {% endfor %}
    {% endwith %}
    <form method="POST">
      <input class="form-control mb-2" name="username" placeholder="username">
      <input class="form-control mb-2" name="password" placeholder="password" type="password">
      <button class="btn btn-primary w-100">Login</button>
    </form>
    <div class="text-muted small mt-2">
    </div>
  </div>
</div>
{% endblock %}
""",

"admin_users.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Users Management</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<form method="POST" class="card p-3 mb-3">
  <div class="row g-2">
    <div class="col-md-4"><input class="form-control" name="username" placeholder="Username"></div>
    <div class="col-md-4"><input class="form-control" name="password" placeholder="Password"></div>
    <div class="col-md-3">
      <select class="form-select" name="role">
        <option value="reception">reception</option>
        <option value="nurse">nurse</option>
        <option value="doctor">doctor</option>
        <option value="lab">lab</option>
        <option value="radiology">radiology</option>
        <option value="admin">admin</option>
      </select>
    </div>
    <div class="col-md-1 d-grid"><button class="btn btn-primary">Add</button></div>
  </div>
</form>

<table class="table table-sm table-striped bg-white">
  <thead><tr><th>ID</th><th>Username</th><th>Role</th><th>Created</th></tr></thead>
  <tbody>
    {% for u in users %}
      <tr><td>{{ u.id }}</td><td>{{ u.username }}</td><td>{{ u.role }}</td><td>{{ u.created_at }}</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
""",

"admin_reset_password.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Admin Password Reset</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<form method="POST" class="card p-3 bg-white mb-3">
  <div class="row g-2 align-items-end">
    <div class="col-md-5">
      <label class="form-label fw-bold">Select User</label>
      <select class="form-select" name="user_id" required>
        <option value="">-- choose --</option>
        {% for u in users %}
          <option value="{{ u.id }}">{{ u.username }} ({{ u.role }})</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-md-5">
      <label class="form-label fw-bold">New Password</label>
      <input class="form-control" name="new_password" type="text" required>
    </div>
    <div class="col-md-2 d-grid">
      <button class="btn btn-primary">Reset</button>
    </div>
  </div>
</form>

<a class="btn btn-outline-danger btn-sm" href="{{ url_for('admin_reset_admin_default') }}">
  Reset admin to default (admin12)
</a>
{% endblock %}
""",

"admin_logs.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Activity Logs (Last 1000)</h4>

<form method="GET" class="card p-3 mb-3 bg-white">
  <div class="row g-2 align-items-end">
    <div class="col-md-3">
      <label class="form-label fw-bold">Visit ID</label>
      <input class="form-control" name="visit_id" value="{{ visit_f or '' }}" placeholder="visit id">
    </div>
    <div class="col-md-3">
      <label class="form-label fw-bold">User</label>
      <select class="form-select" name="user">
        <option value="">ALL</option>
        {% for u in users %}
          <option value="{{u.username}}" {% if user_f==u.username %}selected{% endif %}>{{u.username}}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-md-2">
      <label class="form-label fw-bold">From</label>
      <input class="form-control" type="date" name="date_from" value="{{ dfrom or '' }}">
    </div>
    <div class="col-md-2">
      <label class="form-label fw-bold">To</label>
      <input class="form-control" type="date" name="date_to" value="{{ dto or '' }}">
    </div>
    <div class="col-md-2 d-grid">
      <button class="btn btn-primary">Filter</button>
    </div>
  </div>

  <div class="mt-2 d-flex gap-2">
    <a class="btn btn-outline-success btn-sm"
       href="{{ url_for('export_logs_csv', visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto) }}">Export CSV</a>
    <a class="btn btn-outline-danger btn-sm"
       href="{{ url_for('export_logs_pdf', visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto) }}">Export PDF</a>
  </div>
</form>

<table class="table table-sm table-striped bg-white">
  <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Visit</th><th>Details</th></tr></thead>
  <tbody>
  {% for l in logs %}
    <tr>
      <td>{{ l.created_at }}</td>
      <td>{{ l.username }}</td>
      <td>{{ l.action }}</td>
      <td>{{ l.visit_id or '-' }}</td>
      <td>{{ l.details or '-' }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
""",
"admin_restore.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Restore Database from Backup</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="card p-3 bg-white">
  <p class="text-danger small mb-2">
    ⚠️ Warning: restoring a backup will overwrite the current database file.
    A safety copy (*.before_restore.bak) will be created automatically.
  </p>

  <form method="POST" enctype="multipart/form-data">
    <div class="mb-2">
      <label class="form-label fw-bold">Select backup .db file</label>
      <input type="file" name="file" class="form-control" required>
    </div>
    <button class="btn btn-danger mt-2">Restore Now</button>
    <a class="btn btn-secondary mt-2" href="{{ url_for('ed_board') }}">Cancel</a>
  </form>
</div>
{% endblock %}
""",

"register.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Register Patient</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<form method="POST" class="card p-3 bg-white">
  <div class="row g-2">
    <div class="col-md-6"><label class="form-label fw-bold">Name</label><input class="form-control" name="name" required></div>
    <div class="col-md-6"><label class="form-label fw-bold">ID Number</label><input class="form-control" name="id_number"></div>
    <div class="col-md-4"><label class="form-label fw-bold">Phone</label><input class="form-control" name="phone"></div>
    <div class="col-md-4"><label class="form-label fw-bold">Insurance</label><input class="form-control" name="insurance"></div>
    <div class="col-md-4"><label class="form-label fw-bold">Insurance No</label><input class="form-control" name="insurance_no"></div>
    <div class="col-md-4"><label class="form-label fw-bold">DOB</label><input class="form-control" name="dob" placeholder="YYYY-MM-DD"></div>
    <div class="col-md-2"><label class="form-label fw-bold">Sex</label>
      <select class="form-select" name="sex"><option value=""></option><option>M</option><option>F</option></select></div>
    <div class="col-md-6"><label class="form-label fw-bold">Nationality</label><input class="form-control" name="nationality"></div>
    <div class="col-md-12"><label class="form-label fw-bold">Payment Details</label><input class="form-control" name="payment_details"></div>
  </div>
  <button class="btn btn-primary mt-3">Save & Create Visit</button>
</form>
{% endblock %}
""",

"search.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Search Patients</h4>

<form class="card p-3 mb-3 bg-white" method="GET">
  <div class="row g-2 align-items-end">
    <div class="col-md-4">
      <label class="form-label fw-bold">Free Search</label>
      <input class="form-control" name="q" placeholder="Search by name / visit / ID / insurance no" value="{{ q }}">
    </div>
    <div class="col-md-2">
      <label class="form-label fw-bold">Visit ID</label>
      <input class="form-control" name="visit_id" value="{{ visit_f or '' }}">
    </div>
    <div class="col-md-2">
      <label class="form-label fw-bold">User</label>
      <select class="form-select" name="user">
        <option value="">ALL</option>
        {% for u in users %}
          <option value="{{u.created_by}}" {% if user_f==u.created_by %}selected{% endif %}>{{u.created_by}}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-md-2">
      <label class="form-label fw-bold">From</label>
      <input class="form-control" type="date" name="date_from" value="{{ dfrom or '' }}">
    </div>
    <div class="col-md-2">
      <label class="form-label fw-bold">To</label>
      <input class="form-control" type="date" name="date_to" value="{{ dto or '' }}">
    </div>
    <div class="col-md-12 mt-2 d-grid">
      <button class="btn btn-primary btn-sm">Filter</button>
    </div>
  </div>
</form>


{% if q and not results %}<div class="text-muted">No results</div>{% endif %}

<table class="table table-sm bg-white">
  <thead>
    <tr>
      <th>Visit</th><th>Queue</th><th>Name</th><th>ID</th><th>INS</th><th>INS No</th>
      <th>Phone</th><th>Payment</th><th>Triage</th><th>CAT</th><th>Status</th><th>Actions</th>
    </tr>
  </thead>
  <tbody>
  {% for r in results %}
    <tr>
      <td>{{ r.visit_id }}</td>
      <td class="fw-bold">{{ r.queue_no }}</td>
      <td>{{ r.name }}</td>
      <td>{{ r.id_number }}</td>
      <td>{{ r.insurance }}</td>
      <td>{{ r.insurance_no }}</td>
      <td>{{ r.phone }}</td>
      <td>{{ r.payment_details or '-' }}</td>
      <td>{{ r.triage_status }}</td>
      <td>
        {% set cat = (r.triage_cat or '').lower() %}
        {% if cat == 'es1' %}<span class="badge cat-red">ES1</span>
        {% elif cat == 'es2' %}<span class="badge cat-orange">ES2</span>
        {% elif cat == 'es3' %}<span class="badge cat-yellow">ES3</span>
        {% elif cat == 'es4' %}<span class="badge cat-green">ES4</span>
        {% elif cat == 'es5' %}<span class="badge cat-none">ES5</span>
        {% else %}<span class="badge cat-none">-</span>{% endif %}
      </td>
      <td>{{ r.status }}</td>
      <td><a class="btn btn-sm btn-outline-primary" href="{{ url_for('patient_details', visit_id=r.visit_id) }}">Open</a></td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}

<nav class="d-flex justify-content-between align-items-center mt-2">
  <div class="small text-muted">
    Page {{page}} / {{pages}} - Total: {{total}}
  </div>
  <ul class="pagination pagination-sm mb-0">
    <li class="page-item {% if page<=1 %}disabled{% endif %}">
      <a class="page-link" href="{{ url_for('ed_board', status=status_filter, cat=cat_filter, visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto, per_page=per_page, page=page-1) }}">Prev</a>
    </li>
    <li class="page-item {% if page>=pages %}disabled{% endif %}">
      <a class="page-link" href="{{ url_for('ed_board', status=status_filter, cat=cat_filter, visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto, per_page=per_page, page=page+1) }}">Next</a>
    </li>
  </ul>
  <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">🔄 Manual Refresh</button>
</nav>

""",

"ed_board.html": """
{% extends "base.html" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-2">
  <h4 class="mb-0">ED Board</h4>
  <a class="btn btn-sm btn-outline-primary" href="{{ url_for('export_ed_board_csv') }}">Export CSV</a>
</div>


<form class="card p-2 mb-3 bg-white" method="GET">
  <div class="row g-2 align-items-end">
    <div class="col-md-2">
      <label class="form-label fw-bold small">Status</label>
      <select name="status" class="form-select form-select-sm" onchange="this.form.submit()">
        <option value="ALL" {% if status_filter=="ALL" %}selected{% endif %}>ALL</option>
        <option value="OPEN" {% if status_filter=="OPEN" %}selected{% endif %}>OPEN</option>
        <option value="IN_TREATMENT" {% if status_filter=="IN_TREATMENT" %}selected{% endif %}>IN_TREATMENT</option>
        <option value="ADMITTED" {% if status_filter=="ADMITTED" %}selected{% endif %}>ADMITTED</option>
        <option value="DISCHARGED" {% if status_filter=="DISCHARGED" %}selected{% endif %}>DISCHARGED</option>
        <option value="TRANSFERRED" {% if status_filter=="TRANSFERRED" %}selected{% endif %}>TRANSFERRED</option>
        <option value="LAMA" {% if status_filter=="LAMA" %}selected{% endif %}>LAMA</option>
        <option value="EXPIRED" {% if status_filter=="EXPIRED" %}selected{% endif %}>EXPIRED</option>
        <option value="CANCELLED" {% if status_filter=="CANCELLED" %}selected{% endif %}>CANCELLED</option>
      </select>
    </div>

    <div class="col-md-2">
      <label class="form-label fw-bold small">Triage ES</label>
      <select name="cat" class="form-select form-select-sm" onchange="this.form.submit()">
        <option value="ALL" {% if cat_filter=="ALL" %}selected{% endif %}>All ES</option>
        <option value="ES1" {% if cat_filter=="ES1" %}selected{% endif %}>ES1</option>
        <option value="ES2" {% if cat_filter=="ES2" %}selected{% endif %}>ES2</option>
        <option value="ES3" {% if cat_filter=="ES3" %}selected{% endif %}>ES3</option>
        <option value="ES4" {% if cat_filter=="ES4" %}selected{% endif %}>ES4</option>
        <option value="ES5" {% if cat_filter=="ES5" %}selected{% endif %}>ES5</option>
      </select>
    </div>

    <div class="col-md-2">
      <label class="form-label fw-bold small">Visit ID</label>
      <input class="form-control form-control-sm" name="visit_id" value="{{ visit_f or '' }}">
    </div>

    <div class="col-md-2">
      <label class="form-label fw-bold small">User</label>
      <select class="form-select form-select-sm" name="user" onchange="this.form.submit()">
        <option value="">ALL</option>
        {% for u in users %}
          <option value="{{u.created_by}}" {% if user_f==u.created_by %}selected{% endif %}>{{u.created_by}}</option>
        {% endfor %}
      </select>
    </div>

    <div class="col-md-2">
      <label class="form-label fw-bold small">From</label>
      <input class="form-control form-control-sm" type="date" name="date_from" value="{{ dfrom or '' }}" onchange="this.form.submit()">
    </div>

    <div class="col-md-2">
      <label class="form-label fw-bold small">To</label>
      <input class="form-control form-control-sm" type="date" name="date_to" value="{{ dto or '' }}" onchange="this.form.submit()">
    </div>
  </div>
</form>


<table class="table table-sm table-striped bg-white">
  <thead>
    <tr>
      <th>Queue</th><th>Visit</th><th>Name</th><th>ID</th><th>INS</th>
      <th>Payment</th><th>Triage</th><th>ES</th><th>Status</th><th>Created</th><th>Actions</th>
    </tr>
  </thead>
  <tbody>
    {% for v in visits %}
    <tr>
      <td class="fw-bold">{{ v.queue_no }}</td>
      <td>{{ v.visit_id }}</td>
      <td>{{ v.name }}</td>
      <td>{{ v.id_number }}</td>
      <td>{{ v.insurance }}</td>
      <td style="max-width:220px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{{ v.payment_details or '-' }}</td>
      <td>
        {% if v.triage_status=='YES' %}<span class="badge badge-triage-yes">YES</span>
        {% else %}<span class="badge badge-triage-no">NO</span>{% endif %}
      </td>
      <td>
        {% set cat = (v.triage_cat or '').lower() %}
        {% if cat == 'es1' %}<span class="badge cat-red">ES1</span>
        {% elif cat == 'es2' %}<span class="badge cat-orange">ES2</span>
        {% elif cat == 'es3' %}<span class="badge cat-yellow">ES3</span>
        {% elif cat == 'es4' %}<span class="badge cat-green">ES4</span>
        {% elif cat == 'es5' %}<span class="badge cat-none">ES5</span>
        {% else %}<span class="badge cat-none">-</span>{% endif %}
      </td>
      <td>{{ v.status }}</td>
      <td>{{ v.created_at }}</td>
    <td class="d-flex gap-1 flex-wrap">
    <a class="btn btn-sm btn-outline-primary"
       href="{{ url_for('patient_details', visit_id=v.visit_id) }}">
       Open
    </a>

    {% if session.get('role') in ['nurse','doctor','admin'] %}
        <a class="btn btn-sm btn-outline-success"
           href="{{ url_for('triage', visit_id=v.visit_id) }}">
           Triage
        </a>
    {% endif %}

    {% if session.get('role') != 'reception' %}
    <a class="btn btn-sm btn-outline-primary"
       target="_blank"
       href="{{ url_for('patient_summary_pdf', visit_id=v.visit_id) }}">
       ED Visit Summary - Patient Copy
    </a>
    {% endif %}

    <a class="btn btn-sm btn-outline-secondary"
       target="_blank"
       href="{{ url_for('home_med_pdf', visit_id=v.visit_id) }}">
       Print Home Med
    </a>
</td>

      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}

<nav class="d-flex justify-content-between align-items-center mt-2">
  <div class="small text-muted">
    Page {{page}} / {{pages}} - Total: {{total}}
  </div>
  <ul class="pagination pagination-sm mb-0">
    <li class="page-item {% if page<=1 %}disabled{% endif %}">
      <a class="page-link" href="{{ url_for('ed_board', status=status_filter, cat=cat_filter, visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto, per_page=per_page, page=page-1) }}">Prev</a>
    </li>
    <li class="page-item {% if page>=pages %}disabled{% endif %}">
      <a class="page-link" href="{{ url_for('ed_board', status=status_filter, cat=cat_filter, visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto, per_page=per_page, page=page+1) }}">Next</a>
    </li>
  </ul>
  <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">🔄 Manual Refresh</button>
</nav>

""",

"patient_details.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Patient Details – Visit {{ visit.visit_id }}</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="card p-3 bg-white mb-3">
  <div><strong>Name:</strong> {{ visit.name }}</div>
  <div><strong>ID:</strong> {{ visit.id_number or '-' }}</div>
  <div><strong>Phone:</strong> {{ visit.phone or '-' }}</div>
  <div><strong>Insurance:</strong> {{ visit.insurance or '-' }}</div>
  <div><strong>Insurance No:</strong> {{ visit.insurance_no or '-' }}</div>
  <div><strong>Payment Details:</strong> {{ visit.payment_details or '-' }}</div>
  <div><strong>Patient Complaint:</strong> {{ visit.comment or '-' }}</div>
  <div>
    <strong>Allergy:</strong>
    {{ visit.allergy_status or '-' }}
    {% if visit.allergy_details %}
      – {{ visit.allergy_details }}
    {% endif %}
  </div>
  <div><strong>Triage Status:</strong> {{ visit.triage_status }}</div>
  <div><strong>Status:</strong> {{ visit.status }}</div>
  {% if visit.status == 'CANCELLED' %}
    <div><strong>Cancel Reason:</strong> {{ visit.cancel_reason or '-' }}</div>
    <div><strong>Cancelled By:</strong> {{ visit.cancelled_by or '-' }}</div>
  {% endif %}

  <div class="mt-2"><strong>Vital Signs:</strong>
    <div class="small text-muted">
      PR: {{ visit.pulse_rate or '-' }} bpm |
      RR: {{ visit.resp_rate or '-' }}/min |
      BP: {{ visit.bp_systolic or '-' }}/{{ visit.bp_diastolic or '-' }} |
      Temp: {{ visit.temperature or '-' }} °C |
      SpO₂: {{ visit.spo2 or '-' }}% |
      Consciousness: {{ visit.consciousness_level or '-' }} |
      Pain: {{ visit.pain_score or '-' }}/10 |
      Wt: {{ visit.weight or '-' }} kg |
      Ht: {{ visit.height or '-' }} cm
    </div>
  </div>


  <div class="mt-2"><strong>Triage ES:</strong>
    {% set cat = (visit.triage_cat or '').lower() %}
    {% if cat == 'es1' %}<span class="badge cat-red">ES1</span>
    {% elif cat == 'es2' %}<span class="badge cat-orange">ES2</span>
    {% elif cat == 'es3' %}<span class="badge cat-yellow">ES3</span>
    {% elif cat == 'es4' %}<span class="badge cat-green">ES4</span>
    {% elif cat == 'es5' %}<span class="badge cat-none">ES5</span>
    {% else %}<span class="badge cat-none">-</span>{% endif %}
  </div>
</div>

{% if session.get('role') == 'reception' %}
<div class="card p-3 bg-white mb-3">
  <h6 class="fw-bold mb-2">Investigations (Read-only for Reception)</h6>
  <div class="row">
    <div class="col-md-6 mb-2">
      <strong>Lab Results:</strong>
      {% if lab_reqs %}
        <ul class="small mb-0">
          {% for l in lab_reqs %}
            {% if l.status == 'REPORTED' %}
              <li>{{ l.test_name }}: {{ l.result_text or '-' }}</li>
            {% else %}
              <li>{{ l.test_name }} – {{ l.status }}</li>
            {% endif %}
          {% endfor %}
        </ul>
      {% else %}
        <div class="small text-muted">No lab requests for this visit.</div>
      {% endif %}
    </div>
    <div class="col-md-6 mb-2">
      <strong>Radiology Reports:</strong>
      {% if rad_reqs %}
        <ul class="small mb-0">
          {% for r in rad_reqs %}
            {% if r.status == 'REPORTED' %}
              <li>{{ r.test_name }}: {{ r.report_text or '-' }}</li>
            {% else %}
              <li>{{ r.test_name }} – {{ r.status }}</li>
            {% endif %}
          {% endfor %}
        </ul>
      {% else %}
        <div class="small text-muted">No radiology requests for this visit.</div>
      {% endif %}
    </div>
  </div>
  <div class="small text-muted mt-1">
    * View only – reception cannot edit results.
  </div>
</div>
{% endif %}

</div>

<div class="d-flex gap-2 mb-3 flex-wrap">
  {% if session.get('role') in ['reception','admin'] %}
    <a class="btn btn-outline-warning" href="{{ url_for('edit_patient', visit_id=visit.visit_id) }}">Edit Patient</a>
  {% endif %}

  {% if session.get('role') in ['nurse','doctor','admin'] %}
    <a class="btn btn-success" href="{{ url_for('triage', visit_id=visit.visit_id) }}">Triage</a>
  {% endif %}

  {% if session.get('role') != 'reception' %}
    <a class="btn btn-primary" href="{{ url_for('clinical_orders_page', visit_id=visit.visit_id) }}">Clinical Orders</a>
  {% endif %}

  {% if session.get('role') in ['nurse','doctor','admin'] %}
    <a class="btn btn-outline-dark" target="_blank" href="{{ url_for('auto_summary_pdf', visit_id=visit.visit_id) }}">Auto Summary</a>
  {% endif %}

  {% if session.get('role') in ['nurse','doctor','admin'] %}
    <a class="btn btn-outline-primary" target="_blank" href="{{ url_for('patient_summary_pdf', visit_id=visit.visit_id) }}">ED Visit Summary - Patient Copy</a>
  {% endif %}
  {% if session.get('role') in ['reception','nurse','doctor','admin'] %}
    <a class="btn btn-outline-secondary" target="_blank" href="{{ url_for('home_med_pdf', visit_id=visit.visit_id) }}">Home Medication</a>
  {% endif %}

  <a class="btn btn-outline-dark" target="_blank" href="{{ url_for('sticker_html', visit_id=visit.visit_id) }}">Sticker</a>
  <a class="btn btn-outline-secondary" target="_blank" href="{{ url_for('sticker_zpl', visit_id=visit.visit_id) }}">ZPL</a>
</div>

{% if session.get('role') in ['reception','admin'] and visit.status == 'OPEN' and orders_count == 0 %}
<div class="card p-3 bg-white mb-3">
  <h6 class="fw-bold mb-2 text-danger">Cancel Visit</h6>
  <form method="POST" action="{{ url_for('cancel_visit', visit_id=visit.visit_id) }}" onsubmit="return confirm('Are you sure you want to cancel this visit?');">
    <label class="form-label fw-bold small">Reason for cancellation</label>
    <textarea name="cancel_reason" class="form-control mb-2" rows="2" required></textarea>
    <button class="btn btn-danger btn-sm">Cancel Visit</button>
  </form>
  <div class="small text-muted mt-1">
    Use only if patient registered but did not see the doctor.
  </div>
</div>
{% endif %}

{% if session.get('role') in ['doctor','admin'] %}
<form method="POST" action="{{ url_for('close_visit', visit_id=visit.visit_id) }}" class="card p-3 bg-white mb-3">
  <h6 class="fw-bold">Update Status</h6>
  <div class="row g-2 align-items-end">
    <div class="col-md-4">
      <label class="form-label fw-bold small mb-1">&nbsp;</label>
      <select class="form-select" name="status">
        <option>DISCHARGED</option>
        <option>ADMITTED</option>
        <option>TRANSFERRED</option>
        <option>LAMA</option>
        <option>EXPIRED</option>
        <option>IN_TREATMENT</option>
      </select>
    </div>
    <div class="col-md-3">
      <button class="btn btn-danger w-100">Update Status</button>
    </div>
  </div>
</form>
{% endif %}

<div class="card p-3 bg-white mb-3">
  <h6>Attach Patient ID</h6>
  <form method="POST" enctype="multipart/form-data" action="{{ url_for('upload_id', visit_id=visit.visit_id) }}">
    <input type="file" name="file" class="form-control mb-2">
    <button class="btn btn-sm btn-primary">Upload</button>
  </form>

  {% if attachments %}
    <hr>
    <ul>
      {% for a in attachments %}
        <li>
          <a href="{{ url_for('uploaded_file', filename=a.filename) }}" target="_blank">{{ a.filename }}</a>
          ({{ a.uploaded_at }})
          {% if session.get('role') in ['reception','admin'] %}
            - {% if session.get('role') in ['reception','admin'] %}
              <form method="POST" action="{{ url_for('delete_attachment', att_id=a.id) }}" style="display:inline"
                    onsubmit="return confirm('Delete this attachment?');">
                <button class="btn btn-sm btn-link text-danger p-0">Delete</button>
              </form>
            {% endif %}
          {% endif %}
        </li>
      {% endfor %}
    </ul>
  {% endif %}
</div>
{% endblock %}
""",

"edit_patient.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Edit Patient – Visit {{ r.visit_id }}</h4>

<form method="POST" class="card p-3 bg-white">
  <div class="row g-2">
    <div class="col-md-6"><label class="form-label fw-bold">Name</label><input class="form-control" name="name" value="{{ r.name }}" required></div>
    <div class="col-md-6"><label class="form-label fw-bold">ID Number</label><input class="form-control" name="id_number" value="{{ r.id_number }}"></div>
    <div class="col-md-4"><label class="form-label fw-bold">Phone</label><input class="form-control" name="phone" value="{{ r.phone }}"></div>
    <div class="col-md-4"><label class="form-label fw-bold">Insurance</label><input class="form-control" name="insurance" value="{{ r.insurance }}"></div>
    <div class="col-md-4"><label class="form-label fw-bold">Insurance No</label><input class="form-control" name="insurance_no" value="{{ r.insurance_no }}"></div>
    <div class="col-md-4"><label class="form-label fw-bold">DOB</label><input class="form-control" name="dob" value="{{ r.dob }}"></div>
    <div class="col-md-2"><label class="form-label fw-bold">Sex</label>
      <select class="form-select" name="sex">
        <option value="" {% if not r.sex %}selected{% endif %}></option>
        <option value="M" {% if r.sex=='M' %}selected{% endif %}>M</option>
        <option value="F" {% if r.sex=='F' %}selected{% endif %}>F</option>
      </select>
    </div>
    <div class="col-md-6"><label class="form-label fw-bold">Nationality</label><input class="form-control" name="nationality" value="{{ r.nationality }}"></div>
    <div class="col-md-12"><label class="form-label fw-bold">Payment Details</label><input class="form-control" name="payment_details" value="{{ r.payment_details }}"></div>
  </div>
  <div class="mt-3 d-flex gap-2">
    <button class="btn btn-success">Save Changes</button>
    <a class="btn btn-secondary" href="{{ url_for('patient_details', visit_id=r.visit_id) }}">Cancel</a>
  </div>
</form>
{% endblock %}
""",


"triage.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Triage – Visit {{ visit.visit_id }}</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<form method="POST" class="card p-3 bg-white">
  <div class="mb-2"><strong>Patient:</strong> {{ visit.name }} | ID: {{ visit.id_number }} | INS: {{ visit.insurance }}</div>

  <label class="form-label fw-bold mt-2">Patient's Complaint</label>
  <input class="form-control" name="comment" value="{{ visit.comment or '' }}">

  <label class="form-label fw-bold mt-2">Allergy</label>
  <div class="row g-2">
    <div class="col-md-4">
      <select class="form-select" name="allergy_status" id="allergy_status">
        {% set al = visit.allergy_status or '' %}
        <option value="" {% if al=='' %}selected{% endif %}>-- Select --</option>
        <option value="No" {% if al=='No' %}selected{% endif %}>No</option>
        <option value="Yes" {% if al=='Yes' %}selected{% endif %}>Yes</option>
      </select>
    </div>
    <div class="col-md-8"
         id="allergy_details_group"
         style="display: {% if (visit.allergy_status or '') == 'Yes' %}block{% else %}none{% endif %};">
      <input class="form-control"
             name="allergy_details"
             placeholder="Cause of allergy (drug / food / other)"
             value="{{ visit.allergy_details or '' }}">
    </div>
  </div>

  <hr class="my-3">

  <h6 class="fw-bold">Vital Signs</h6>
  <div class="row g-2">
    <div class="col-md-4">
      <label class="form-label">Pulse Rate (bpm)</label>
      <input class="form-control" name="pulse_rate" value="{{ visit.pulse_rate or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">Resp Rate (/min)</label>
      <input class="form-control" name="resp_rate" value="{{ visit.resp_rate or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">Temp (°C)</label>
      <input class="form-control" name="temperature" value="{{ visit.temperature or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">BP Systolic</label>
      <input class="form-control" name="bp_systolic" value="{{ visit.bp_systolic or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">BP Diastolic</label>
      <input class="form-control" name="bp_diastolic" value="{{ visit.bp_diastolic or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">SpO₂ (%)</label>
      <input class="form-control" name="spo2" value="{{ visit.spo2 or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">Weight (kg)</label>
      <input class="form-control" name="weight" value="{{ visit.weight or '' }}">
    </div>
    <div class="col-md-4">
      <label class="form-label">Height (cm)</label>
      <input class="form-control" name="height" value="{{ visit.height or '' }}">
    </div>
    <div class="col-md-6">
      <label class="form-label">Level of Consciousness</label>
      <select class="form-select" name="consciousness_level">
        {% set cl = visit.consciousness_level or '' %}
        <option value="" {% if cl=='' %}selected{% endif %}>-- Select --</option>
        <option value="Alert" {% if cl=='Alert' %}selected{% endif %}>Alert</option>
        <option value="Verbal" {% if cl=='Verbal' %}selected{% endif %}>Verbal</option>
        <option value="Pain" {% if cl=='Pain' %}selected{% endif %}>Pain</option>
        <option value="Unresponsive" {% if cl=='Unresponsive' %}selected{% endif %}>Unresponsive</option>
      </select>
      <div class="form-text">AVPU scale</div>
    </div>
    <div class="col-md-6">
      <label class="form-label">Pain Score (0–10)</label>
      <input class="form-control" name="pain_score" value="{{ visit.pain_score or '' }}">
    </div>
  </div>

  <hr class="my-3">

  <label class="form-label fw-bold mt-2">Triage Category (ES)</label>
  <select class="form-select" name="triage_cat" required>
    <option value="">-- Select --</option>
    <option value="ES1" {% if visit.triage_cat=='ES1' %}selected{% endif %}>ES1</option>
    <option value="ES2" {% if visit.triage_cat=='ES2' %}selected{% endif %}>ES2</option>
    <option value="ES3" {% if visit.triage_cat=='ES3' %}selected{% endif %}>ES3</option>
    <option value="ES4" {% if visit.triage_cat=='ES4' %}selected{% endif %}>ES4</option>
    <option value="ES5" {% if visit.triage_cat=='ES5' %}selected{% endif %}>ES5</option>
  </select>

  <button class="btn btn-success mt-3">Save Triage</button>
</form>

<script>
document.addEventListener('DOMContentLoaded', function () {
  const allergySelect = document.getElementById('allergy_status');
  const detailsGroup = document.getElementById('allergy_details_group');
  if (!allergySelect || !detailsGroup) return;

  function toggleDetails() {
    if (allergySelect.value === 'Yes') {
      detailsGroup.style.display = 'block';
    } else {
      detailsGroup.style.display = 'none';
    }
  }

  allergySelect.addEventListener('change', toggleDetails);
  toggleDetails();
});
</script>
{% endblock %}
""",

"lab_board.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Lab Board</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<form method="GET" class="card p-2 mb-3 bg-white">
  <div class="row g-2 align-items-end">
    <div class="col-md-3 col-sm-4">
      <label class="form-label fw-bold small mb-1">Status</label>
      <select name="status" class="form-select form-select-sm">
        <option value="PENDING" {% if status_filter=='PENDING' %}selected{% endif %}>Pending / Received</option>
        <option value="REPORTED" {% if status_filter=='REPORTED' %}selected{% endif %}>Reported</option>
        <option value="ALL" {% if status_filter=='ALL' %}selected{% endif %}>All</option>
      </select>
    </div>
    <div class="col-md-5 col-sm-8">
      <label class="form-label fw-bold small mb-1">Search</label>
      <input type="text"
             name="q"
             value="{{ q or '' }}"
             class="form-control form-control-sm"
             placeholder="Name / ID / Visit / Test">
    </div>
    <div class="col-md-2 col-sm-6">
      <label class="form-label fw-bold small mb-1">&nbsp;</label>
      <button class="btn btn-sm btn-primary w-100">Search / Filter</button>
    </div>
    <div class="col-md-2 col-sm-6 text-end small text-muted">
      Showing last 500 requests only.
    </div>
  </div>
</form>


<table class="table table-sm table-striped table-hover bg-white align-middle">
  <thead class="table-light">
    <tr>
      <th style="width:60px;">#</th>
      <th>Visit</th>
      <th>Patient</th>
      <th>ID</th>
      <th>Test</th>
      <th>Status</th>
      <th>Result</th>
      <th style="width:220px;">Actions</th>
    </tr>
  </thead>
  <tbody>
  {% if not rows %}
    <tr>
      <td colspan="8" class="text-center text-muted small py-3">
        No lab requests found for current filter.
      </td>
    </tr>
  {% else %}
    {% for r in rows %}
      <tr>
        <td>{{ r.id }}</td>
        <td class="fw-bold">{{ r.visit_id }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.id_number or '-' }}</td>
        <td>{{ r.test_name }}</td>
        <td>
          {% if r.status == 'REQUESTED' %}
            <span class="badge bg-secondary">Waiting sample</span>
          {% elif r.status == 'RECEIVED' %}
            <span class="badge bg-warning text-dark">Sample received</span>
          {% elif r.status == 'REPORTED' %}
            <span class="badge bg-success">Reported</span>
          {% else %}
            <span class="badge bg-light text-muted">{{ r.status }}</span>
          {% endif %}
        </td>
        <td style="max-width:260px; white-space:pre-wrap; font-size:0.85rem;">
          {{ r.result_text or '-' }}
        </td>
        <td>
          <div class="d-flex flex-column gap-1">
            {% if session.get('role') in ['lab','admin'] %}
              {% if r.status == 'REQUESTED' %}
                <form method="POST"
                      action="{{ url_for('lab_receive_sample', rid=r.id) }}">
                  <button class="btn btn-sm btn-outline-primary w-100">
                    ✅ Receive Sample
                  </button>
                </form>
              {% endif %}

              <button class="btn btn-sm btn-outline-secondary w-100 mt-1"
                      type="button"
                      data-bs-toggle="collapse"
                      data-bs-target="#edit{{ r.id }}">
                ✏️ Edit / Add Result
              </button>

              <div class="collapse mt-1" id="edit{{ r.id }}">
                <form method="POST"
                      action="{{ url_for('lab_report_result', rid=r.id) }}">
                  <div class="input-group input-group-sm mb-1">
                    <input type="text"
                           name="result_text"
                           class="form-control"
                           placeholder="Enter result..."
                           value="{{ r.result_text or '' }}">
                  </div>
                  <button class="btn btn-sm btn-success w-100">
                    💾 Save Result
                  </button>
                </form>
              </div>

              {% if r.status == 'REPORTED' %}
                <span class="small text-muted mt-1">
                  {{ r.reported_at or '' }} | {{ r.reported_by or '' }}
                </span>
              {% endif %}
            {% else %}
              <span class="small text-muted">Read-only</span>
            {% endif %}
          </div>
        </td>
      </tr>
    {% endfor %}
  {% endif %}
  </tbody>
</table>
{% endblock %}
""",

"radiology_board.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Radiology Board</h4>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<form method="GET" class="card p-2 mb-3 bg-white">
  <div class="row g-2 align-items-end">
    <div class="col-md-3 col-sm-4">
      <label class="form-label fw-bold small mb-1">Status</label>
      <select name="status" class="form-select form-select-sm">
        <option value="PENDING" {% if status_filter=='PENDING' %}selected{% endif %}>Pending / Done</option>
        <option value="REPORTED" {% if status_filter=='REPORTED' %}selected{% endif %}>Reported</option>
        <option value="ALL" {% if status_filter=='ALL' %}selected{% endif %}>All</option>
      </select>
    </div>
    <div class="col-md-5 col-sm-8">
      <label class="form-label fw-bold small mb-1">Search</label>
      <input type="text"
             name="q"
             value="{{ q or '' }}"
             class="form-control form-control-sm"
             placeholder="Name / ID / Visit / Study">
    </div>
    <div class="col-md-2 col-sm-6">
      <label class="form-label fw-bold small mb-1">&nbsp;</label>
      <button class="btn btn-sm btn-primary w-100">Search / Filter</button>
    </div>
    <div class="col-md-2 col-sm-6 text-end small text-muted">
      Showing last 500 requests only.
    </div>
  </div>
</form>


<table class="table table-sm table-striped table-hover bg-white align-middle">
  <thead class="table-light">
    <tr>
      <th style="width:60px;">#</th>
      <th>Visit</th>
      <th>Patient</th>
      <th>ID</th>
      <th>Study</th>
      <th>Status</th>
      <th>Report</th>
      <th style="width:260px;">Actions</th>
    </tr>
  </thead>
  <tbody>
  {% if not rows %}
    <tr>
      <td colspan="8" class="text-center text-muted small py-3">
        No radiology requests found for current filter.
      </td>
    </tr>
  {% else %}
    {% for r in rows %}
      <tr>
        <td>{{ r.id }}</td>
        <td class="fw-bold">{{ r.visit_id }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.id_number or '-' }}</td>
        <td>{{ r.test_name }}</td>
        <td>
          {% if r.status == 'REQUESTED' %}
            <span class="badge bg-secondary">Waiting</span>
          {% elif r.status == 'DONE' %}
            <span class="badge bg-warning text-dark">Done</span>
          {% elif r.status == 'REPORTED' %}
            <span class="badge bg-success">Reported</span>
          {% else %}
            <span class="badge bg-light text-muted">{{ r.status }}</span>
          {% endif %}
        </td>
        <td style="max-width:260px; white-space:pre-wrap; font-size:0.85rem;">
          {{ r.report_text or '-' }}
        </td>
        <td>
          <div class="d-flex flex-column gap-1">
            {% if session.get('role') in ['radiology','admin'] %}
              {% if r.status == 'REQUESTED' %}
                <form method="POST"
                      action="{{ url_for('radiology_mark_done', rid=r.id) }}">
                  <button class="btn btn-sm btn-outline-primary w-100">
                    ✅ Mark as Done
                  </button>
                </form>
              {% endif %}

              <button class="btn btn-sm btn-outline-secondary w-100 mt-1"
                      type="button"
                      data-bs-toggle="collapse"
                      data-bs-target="#edit{{ r.id }}">
                ✏️ Edit / Add Report
              </button>

              <div class="collapse mt-1" id="edit{{ r.id }}">
                <form method="POST"
                      action="{{ url_for('radiology_report_result', rid=r.id) }}">
                  <div class="input-group input-group-sm mb-1">
                    <textarea name="report_text"
                              class="form-control"
                              rows="3"
                              placeholder="Enter radiology report...">{{ r.report_text or '' }}</textarea>
                  </div>
                  <button class="btn btn-sm btn-success w-100">
                    💾 Save Report
                  </button>
                </form>
              </div>

              {% if r.status == 'REPORTED' %}
                <span class="small text-muted mt-1">
                  {{ r.reported_at or '' }} | {{ r.reported_by or '' }}
                </span>
              {% endif %}
            {% else %}
              <span class="small text-muted">Read-only</span>
            {% endif %}
          </div>
        </td>
      </tr>
    {% endfor %}
  {% endif %}
  </tbody>
</table>
{% endblock %}
""",
"clinical_orders.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-2">Clinical Orders – Visit {{ visit.visit_id }}</h4>
<div class="mb-3 text-muted">
  Patient: {{ visit.name }} | ID: {{ visit.id_number }} | Insurance: {{ visit.insurance }}
</div>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="mb-3 d-flex flex-wrap gap-2">
  <a class="btn btn-sm btn-outline-secondary"
     target="_blank"
     href="{{ url_for('lab_results_pdf', visit_id=visit.visit_id) }}">
    Print Lab Results PDF
  </a>

  <a class="btn btn-sm btn-outline-secondary"
     target="_blank"
     href="{{ url_for('radiology_results_pdf', visit_id=visit.visit_id) }}">
    Print Radiology Reports PDF
  </a>

  <a class="btn btn-sm btn-outline-dark"
     target="_blank"
     href="{{ url_for('auto_summary_pdf', visit_id=visit.visit_id) }}">
    Auto ED Course Summary PDF
  </a>
</div>

<div class="row g-3">
  <div class="col-lg-7">
    <div class="card p-3 bg-white">
      <h6 class="fw-bold mb-2">Add New Clinical Order</h6>

      <div class="mb-3 d-flex flex-wrap gap-2">
        <button type="button" class="btn btn-sm btn-outline-primary" onclick="applyBundle('chest_pain')">Chest Pain Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-danger" onclick="applyBundle('stroke')">Stroke Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-dark" onclick="applyBundle('trauma')">Trauma Bundle</button>
        
        <button type="button" class="btn btn-sm btn-outline-success" onclick="applyBundle('abdominal_pain')">Abdominal Pain Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-info" onclick="applyBundle('sob')">SOB Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-warning" onclick="applyBundle('sepsis')">Sepsis Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-primary" onclick="applyBundle('fever')">Fever Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-dark" onclick="applyBundle('gi_bleed')">GI Bleed Bundle</button>
        <button type="button" class="btn btn-sm btn-outline-danger" onclick="applyBundle('anaphylaxis')">Anaphylaxis Bundle</button>
<button type="button" class="btn btn-sm btn-outline-secondary" onclick="clearAllBundles()">Clear Selections</button>
      </div>

      {% if session.get('role') not in ['reception'] %}
      <form method="POST" action="{{ url_for('add_clinical_order', visit_id=visit.visit_id) }}">
        <label class="form-label fw-bold">Diagnosis / Chief Complaint</label>
        <textarea class="form-control mb-3" name="diagnosis" rows="2" placeholder="Write diagnosis or chief complaint..."></textarea>

        <label class="form-label fw-bold">Radiology Orders</label>
        <div class="border rounded p-2 mb-2" style="max-height:180px; overflow:auto;">
          {% set rad_list = [
            "X-Ray Chest","X-Ray Pelvis","X-Ray C-Spine","X-Ray L-Spine",
            "CT Brain Without Contrast","CT Brain With Contrast","CT C-Spine","CT Chest","CT Abdomen/Pelvis",
            "CT Angio Brain/Neck","CT Trauma Pan-Scan",
            "MRI Brain","MRI Spine",
            "US Abdomen","US Pelvis","US DVT Lower Limb","FAST Ultrasound"
          ] %}
          {% for item in rad_list %}
            <div class="form-check">
              <input class="form-check-input rad-item" type="checkbox" value="{{ item }}" id="rad_{{ loop.index }}">
              <label class="form-check-label" for="rad_{{ loop.index }}">{{ item }}</label>
            </div>
          {% endfor %}
        </div>

        <div class="input-group input-group-sm mb-2">
          <input type="text" class="form-control" id="rad_other" placeholder="Add other radiology (optional)">
          <button class="btn btn-outline-secondary" type="button" onclick="addOther('rad')">Add</button>
        </div>

        <textarea class="form-control mb-3" id="radiology_text" name="radiology_orders" rows="2"
                  placeholder="Selected radiology appear here..." readonly></textarea>

        <label class="form-label fw-bold">Lab Orders</label>
        <div class="border rounded p-2 mb-2" style="max-height:180px; overflow:auto;">
          {% set lab_list = [
            "CBC","CMP (Kidney/Liver)","Electrolytes","CRP","ESR",
            "Troponin","CK-MB","PT/PTT/INR","RBS (Random Blood Sugar)","ABG",
            "Urine Analysis","Blood Culture","Lactate","D-Dimer","Lipase","BHCG (Pregnancy Test)",
            "Type & Screen / Crossmatch"
          ] %}
          {% for item in lab_list %}
            <div class="form-check">
              <input class="form-check-input lab-item" type="checkbox" value="{{ item }}" id="lab_{{ loop.index }}">
              <label class="form-check-label" for="lab_{{ loop.index }}">{{ item }}</label>
            </div>
          {% endfor %}
        </div>

        <div class="input-group input-group-sm mb-2">
          <input type="text" class="form-control" id="lab_other" placeholder="Add other lab (optional)">
          <button class="btn btn-outline-secondary" type="button" onclick="addOther('lab')">Add</button>
        </div>

        <textarea class="form-control mb-3" id="lab_text" name="lab_orders" rows="2"
                  placeholder="Selected labs appear here..." readonly></textarea>

        <label class="form-label fw-bold">Medications</label>
        <div class="border rounded p-2 mb-2" style="max-height:200px; overflow:auto;">
          {% set med_list = [
            "Paracetamol IV","Diclofenac IM","Tramadol IV","Morphine IV",
            "Ondansetron IV","Metoclopramide IV",
            "Ceftriaxone IV","Piperacillin/Tazobactam (Tazocin)","Cefazolin IV",
            "Salbutamol Neb","Duolin Neb","Hydrocortisone IV","Pantoprazole IV",
            "Aspirin PO 300mg","Nitroglycerin SL","Heparin SC/IV",
            "Labetalol IV",
            "Tetanus Toxoid IM",
            "Normal Saline 0.9%","Ringer Lactate","D5W"
          ] %}
          {% for item in med_list %}
            <div class="form-check">
              <input class="form-check-input med-item" type="checkbox" value="{{ item }}" id="med_{{ loop.index }}">
              <label class="form-check-label" for="med_{{ loop.index }}">{{ item }}</label>
            </div>
          {% endfor %}
        </div>

        <div class="input-group input-group-sm mb-2">
          <input type="text" class="form-control" id="med_other" placeholder="Add other medication (optional)">
          <button class="btn btn-outline-secondary" type="button" onclick="addOther('med')">Add</button>
        </div>

        <textarea class="form-control mb-3" id="med_text" name="medications" rows="2"
                  placeholder="Selected medications appear here..." readonly></textarea>

        <div class="d-flex gap-2 mt-2">
          <button class="btn btn-primary">Save Clinical Order</button>
          <a class="btn btn-secondary" href="{{ url_for('patient_details', visit_id=visit.visit_id) }}">Back</a>
        </div>
      </form>
      {% else %}
        <div class="alert alert-warning mb-0">
          Reception role has no access to create clinical orders.
        </div>
      {% endif %}
    </div>
  </div>

  <div class="col-lg-5">
    <div class="card p-3 bg-white mb-3">
      <div class="d-flex justify-content-between align-items-center">
        <h6 class="fw-bold mb-2">Nursing Notes</h6>
        <a class="btn btn-sm btn-outline-primary"
           target="_blank"
           href="{{ url_for('nursing_notes_pdf', visit_id=visit.visit_id) }}">Print Notes PDF</a>
      </div>

      {% if session.get('role') in ['nurse','doctor','admin'] %}
      <form method="POST" action="{{ url_for('add_nursing_note', visit_id=visit.visit_id) }}">
        <textarea class="form-control mb-2" name="note_text" rows="3" placeholder="Write nursing note..."></textarea>
        <button class="btn btn-sm btn-primary">Save Note</button>
      </form>
      {% else %}
        <div class="text-muted small">Nursing notes are read-only for this role.</div>
      {% endif %}

      <hr>
      {% if notes %}
        <div style="max-height:220px; overflow:auto;">
          {% for n in notes %}
            <div class="border rounded p-2 mb-2">
              <div class="small fw-bold">{{ n.created_at }} | {{ n.created_by }}</div>
              <div class="small">{{ n.note_text }}</div>
            </div>
          {% endfor %}
        </div>
      {% else %}
        <div class="text-muted small">No nursing notes yet.</div>
      {% endif %}
    </div>

    {% if session.get('role') != 'nurse' %}
    <div class="card p-3 bg-white">
      <div class="d-flex justify-content-between align-items-center">
        <h6 class="fw-bold mb-2">Discharge Summary V5</h6>
        <a class="btn btn-sm btn-outline-secondary"
           target="_blank"
           href="{{ url_for('discharge_summary_pdf', visit_id=visit.visit_id) }}">Auto-Summary PDF</a>
      </div>

      <form method="POST" action="{{ url_for('discharge_save', visit_id=visit.visit_id) }}">
        <label class="form-label fw-bold small mt-2">Diagnosis / Chief Complaint</label>
        <textarea class="form-control mb-2" name="diagnosis_cc" rows="2"
          placeholder="Diagnosis / chief complaint...">{{ summary.diagnosis_cc if summary else '' }}</textarea>

        <label class="form-label fw-bold small">Referral to Clinic</label>
        <input class="form-control mb-2"
               name="referral_clinic"
               list="clinic_list"
               placeholder="Select / type clinic"
               value="{{ summary.referral_clinic if summary else '' }}">
        <datalist id="clinic_list">
          <option value="ED / Emergency">
          <option value="General Medicine OPD">
          <option value="General Surgery OPD">
          <option value="Pediatrics OPD">
          <option value="Obstetrics & Gynecology OPD">
          <option value="Orthopedics OPD">
          <option value="Cardiology OPD">
          <option value="Neurology OPD">
          <option value="ENT OPD">
          <option value="Ophthalmology OPD">
          <option value="Urology OPD">
          <option value="Dermatology OPD">
          <option value="Psychiatry OPD">
          <option value="Dental OPD">
          <option value="Oncology OPD">
          <option value="Endocrinology OPD">
          <option value="Nephrology OPD">
          <option value="Pulmonology OPD">
          <option value="ICU">
          <option value="LDR">
        </datalist>

        <label class="form-label fw-bold small">Home Medication</label>
        <textarea class="form-control mb-2" name="home_medication" rows="2"
          placeholder="Home discharge meds...">{{ summary.home_medication if summary else '' }}</textarea>

        <label class="form-label fw-bold small">Doctor Examination / History</label>
        <textarea class="form-control mb-2" name="summary_text" rows="4"
                  placeholder="Write doctor examination / patient history...">{{ summary.summary_text if summary else '' }}</textarea>

        <button class="btn btn-sm btn-success">Save Summary</button>
      </form>
    </div>
    {% endif %}
  </div>
</div>

<div class="card p-3 bg-white mt-3">
  <h6 class="fw-bold mb-2">Lab Requests / Results</h6>
  {% if not lab_reqs %}
    <div class="text-muted small">No lab requests for this visit.</div>
  {% else %}
    <table class="table table-sm mb-0">
      <thead>
        <tr>
          <th style="width:60px;">#</th>
          <th>Test</th>
          <th>Status</th>
          <th>Result</th>
          <th class="small">Requested</th>
        </tr>
      </thead>
      <tbody>
        {% for l in lab_reqs %}
          <tr>
            <td>{{ l.id }}</td>
            <td>{{ l.test_name }}</td>
            <td>
              {% if l.status == 'REQUESTED' %}
                <span class="badge bg-secondary">Waiting Sample</span>
              {% elif l.status == 'RECEIVED' %}
                <span class="badge bg-warning text-dark">Sample Received</span>
              {% elif l.status == 'REPORTED' %}
                <span class="badge bg-success">Result Ready</span>
              {% else %}
                <span class="badge bg-light text-muted">{{ l.status }}</span>
              {% endif %}
            </td>
            <td style="max-width:260px;white-space:pre-wrap;font-size:0.85rem;">
              {{ l.result_text or '-' }}
            </td>
            <td class="small text-muted">
              {{ l.requested_at or '-' }}<br>
              by {{ l.requested_by or '-' }}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% endif %}
</div>

<div class="card p-3 bg-white mt-3">
  <h6 class="fw-bold mb-2">Radiology Requests / Reports</h6>
  {% if not rad_reqs %}
    <div class="text-muted small">No radiology requests for this visit.</div>
  {% else %}
    <table class="table table-sm mb-0">
      <thead>
        <tr>
          <th style="width:60px;">#</th>
          <th>Study</th>
          <th>Status</th>
          <th>Report</th>
          <th class="small">Requested</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rad_reqs %}
          <tr>
            <td>{{ r.id }}</td>
            <td>{{ r.test_name }}</td>
            <td>
              {% if r.status == 'REQUESTED' %}
                <span class="badge bg-secondary">Waiting</span>
              {% elif r.status == 'DONE' %}
                <span class="badge bg-warning text-dark">Done</span>
              {% elif r.status == 'REPORTED' %}
                <span class="badge bg-success">Report Ready</span>
              {% else %}
                <span class="badge bg-light text-muted">{{ r.status }}</span>
              {% endif %}
            </td>
            <td style="max-width:260px;white-space:pre-wrap;font-size:0.85rem;">
              {{ r.report_text or '-' }}
            </td>
            <td class="small text-muted">
              {{ r.requested_at or '-' }}<br>
              by {{ r.requested_by or '-' }}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% endif %}
</div>

<div class="card p-3 bg-white mt-3">
  <h6 class="fw-bold mb-2">Previous Clinical Orders</h6>

  {% if not orders %}
    <div class="text-muted small">No clinical orders yet.</div>
  {% else %}
    {% for o in orders %}
      <div class="border rounded p-2 mb-2">
        <div class="d-flex justify-content-between align-items-center">
          <div class="fw-bold">
            Order #{{ o.id }}
            <span class="text-muted small ms-2">{{ o.created_at }} by {{ o.created_by }}</span>
            {% if o.updated_at %}
              <span class="text-muted small ms-2">| Updated: {{ o.updated_at }} by {{ o.updated_by }}</span>
            {% endif %}
          </div>

          <div class="d-flex gap-1">
            <a class="btn btn-sm btn-outline-secondary"
               target="_blank"
               href="{{ url_for('clinical_order_pdf', visit_id=visit.visit_id, oid=o.id) }}">Print PDF</a>

            {% if session.get('role') in ['nurse','doctor','admin'] %}
              <form method="post" class="d-inline" action="{{ url_for('delete_clinical_order', visit_id=visit.visit_id, oid=o.id) }}" onsubmit="return confirm('Delete this order?');">
                <button class="btn btn-sm btn-outline-danger">Delete</button>
              </form>

              <button class="btn btn-sm btn-outline-dark"
                      data-bs-toggle="collapse"
                      data-bs-target="#edit{{ o.id }}">Edit</button>
            {% endif %}
          </div>
        </div>

        <div class="mt-2 small">
          <div><strong>Diagnosis / Chief Complaint:</strong><br>{{ o.diagnosis or '-' }}</div>
          <div class="mt-1"><strong>Radiology:</strong><br>{{ o.radiology_orders or '-' }}</div>
          <div class="mt-1"><strong>Lab:</strong><br>{{ o.lab_orders or '-' }}</div>
          <div class="mt-1"><strong>Medications:</strong><br>{{ o.medications or '-' }}</div>
        </div>

        <div class="collapse mt-2" id="edit{{ o.id }}">
          <form method="POST"
                action="{{ url_for('update_clinical_order', visit_id=visit.visit_id, oid=o.id) }}"
                class="bg-light p-2 rounded">
            <label class="form-label fw-bold small">Diagnosis / Chief Complaint</label>
            <textarea class="form-control mb-2" name="diagnosis" rows="2">{{ o.diagnosis or '' }}</textarea>

            <label class="form-label fw-bold small">Radiology Orders</label>
            <textarea class="form-control mb-2" name="radiology_orders" rows="2">{{ o.radiology_orders or '' }}</textarea>

            <label class="form-label fw-bold small">Lab Orders</label>
            <textarea class="form-control mb-2" name="lab_orders" rows="2">{{ o.lab_orders or '' }}</textarea>

            <label class="form-label fw-bold small">Medications</label>
            <textarea class="form-control mb-2" name="medications" rows="2">{{ o.medications or '' }}</textarea>

            <button class="btn btn-sm btn-success">Save Changes</button>
          </form>
        </div>
      </div>
    {% endfor %}
  {% endif %}
</div>

<script>
function syncChecked(className, targetId){
  const checked = Array.from(document.querySelectorAll('.'+className+':checked')).map(cb => cb.value);
  document.getElementById(targetId).value = checked.join(", ");
}

document.addEventListener('change', function(e){
  if(e.target.classList.contains('rad-item')) syncChecked('rad-item','radiology_text');
  if(e.target.classList.contains('lab-item')) syncChecked('lab-item','lab_text');
  if(e.target.classList.contains('med-item')) syncChecked('med-item','med_text');
});

function addOther(prefix){
  const input = document.getElementById(prefix+'_other');
  const val = (input.value || '').trim();
  if(!val) return;

  const tId = prefix==='rad' ? 'radiology_text' : prefix==='lab' ? 'lab_text' : 'med_text';
  const t = document.getElementById(tId);
  const cur = t.value ? t.value.split(',').map(x=>x.trim()).filter(Boolean) : [];
  if(!cur.includes(val)) cur.push(val);
  t.value = cur.join(', ');
  input.value = '';
}

['rad_other','lab_other','med_other'].forEach(id=>{
  const el = document.getElementById(id);
  if(el){
    el.addEventListener('keydown', (e)=>{
      if(e.key==='Enter'){ e.preventDefault(); addOther(id.split('_')[0]); }
    });
  }
});

const bundles = {
  chest_pain: {
    radiology: ["X-Ray Chest"],
    labs: ["Troponin","CK-MB","CBC","Electrolytes","PT/PTT/INR","D-Dimer","RBS (Random Blood Sugar)"],
    meds: ["Aspirin PO 300mg","Nitroglycerin SL","Morphine IV","Ondansetron IV","Normal Saline 0.9%"]
  },
  stroke: {
    radiology: ["CT Brain Without Contrast","CT Angio Brain/Neck"],
    labs: ["CBC","Electrolytes","PT/PTT/INR","RBS (Random Blood Sugar)"],
    meds: ["Normal Saline 0.9%","Labetalol IV"]
  },
  trauma: {
    radiology: ["CT Trauma Pan-Scan","X-Ray Chest","X-Ray Pelvis","FAST Ultrasound"],
    labs: ["CBC","CMP (Kidney/Liver)","PT/PTT/INR","Lactate","Type & Screen / Crossmatch","ABG"],
    meds: ["Tetanus Toxoid IM","Cefazolin IV","Morphine IV","Ringer Lactate","Normal Saline 0.9%"]
  },
abdominal_pain: {
  radiology: ["US Abdomen","CT Abdomen/Pelvis"],
  labs: ["CBC","CRP","Electrolytes","LFT","Lipase","Urine Analysis","BHCG (Pregnancy Test)"],
  meds: ["Paracetamol IV/PO","Ondansetron IV","Hyoscine (Buscopan) IV/IM","Normal Saline 0.9%"]
},
sob: {
  radiology: ["X-Ray Chest","CT Chest"],
  labs: ["CBC","Electrolytes","ABG","D-Dimer","Troponin","BNP","RBS (Random Blood Sugar)"],
  meds: ["Oxygen Therapy","Salbutamol Nebulizer","Ipratropium Nebulizer","Hydrocortisone IV","Normal Saline 0.9%"]
},
sepsis: {
  radiology: ["X-Ray Chest","US Abdomen"],
  labs: ["CBC","CRP","Lactate","Blood Culture","Urine Analysis","Electrolytes","ABG"],
  meds: ["Broad Spectrum Antibiotic (per policy)","Normal Saline 0.9% Bolus"]
},
fever: {
  radiology: ["X-Ray Chest","US Abdomen"],
  labs: ["CBC","CRP","Urine Analysis","Blood Culture","RBS (Random Blood Sugar)"],
  meds: ["Paracetamol IV/PO","Normal Saline 0.9%"]
},
gi_bleed: {
  radiology: ["X-Ray Chest"],
  labs: ["CBC","PT/PTT/INR","Electrolytes","Type & Screen / Crossmatch"],
  meds: ["Pantoprazole IV","Normal Saline 0.9%","Tranexamic Acid IV (if indicated)"]
},
anaphylaxis: {
  radiology: [],
  labs: ["CBC","ABG"],
  meds: ["Epinephrine IM","Hydrocortisone IV","Chlorpheniramine IV/IM","Normal Saline 0.9%","Salbutamol Nebulizer"]
}
};

function clearAllBundles(){
  document.querySelectorAll('.rad-item,.lab-item,.med-item').forEach(cb => cb.checked=false);
  syncChecked('rad-item','radiology_text');
  syncChecked('lab-item','lab_text');
  syncChecked('med-item','med_text');
}

function applyBundle(name){
  clearAllBundles();
  const b = bundles[name];
  if(!b) return;

  document.querySelectorAll('.rad-item').forEach(cb => cb.checked = b.radiology.includes(cb.value));
  document.querySelectorAll('.lab-item').forEach(cb => cb.checked = b.labs.includes(cb.value));
  document.querySelectorAll('.med-item').forEach(cb => cb.checked = b.meds.includes(cb.value));

  syncChecked('rad-item','radiology_text');
  syncChecked('lab-item','lab_text');
  syncChecked('med-item','med_text');
}
</script>

{% endblock %}
""",

"sticker.html": """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sticker</title>
  <style>
    @page {
      size: 5cm 3cm;
      margin: 0;
    }
    body { margin:0; padding:0; font-family: Arial; }
    .label {
      width: 5cm; height: 3cm;
      border:1px solid #000; padding:0.15cm;
      box-sizing:border-box;
    }
    .row { font-size:8pt; margin:0.03cm 0; }
    .title { font-weight:bold; font-size:9pt; }
    #btnPrint { margin-top:10px; padding:6px 12px; font-size:12px; }
    @media print {
      body { margin:0; padding:0; }
      #btnPrint { display:none; }
    }
  </style>
</head>
<body onload="window.print()">
  <div class="label">
    <div class="row title">NAME: {{ v.name }}</div>
    <div class="row">ID: {{ v.id_number or '-' }}</div>
    <div class="row">INS: {{ v.insurance or '-' }}</div>
    <div class="row">TIME: {{ time_only }}</div>
  </div>
  <button id="btnPrint" onclick="window.print()">Print Again</button>
</body>
</html>
"""

,"chat.html": """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Live Chat – Staff</h4>

<div class="row">
  <div class="col-md-8">
    <div class="card bg-white">
      <div class="card-body" style="height:400px; overflow-y:auto;" id="chat-box">
        <div class="text-muted small">Loading messages...</div>
      </div>
      <div class="card-footer">
        <div class="input-group">
          <input type="text" id="chat-input" class="form-control" placeholder="اكتب رسالتك واضغط Enter أو Send ...">
          <button class="btn btn-primary" id="chat-send-btn">Send</button>
        </div>
        <div class="small text-muted mt-1">
          القناة الحالية: كل الموظفين (Public ED Room)
        </div>
      </div>
    </div>
  </div>
</div>

<script>
(function() {
  const chatBox = document.getElementById("chat-box");
  const input   = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");

  let lastTimestamp = "";

  function appendMessage(msg) {
    const div = document.createElement("div");
    div.className = "mb-1";
    div.innerHTML =
      '<span class="badge bg-light text-dark me-1">' +
      (msg.username || "User") +
      '</span>' +
      '<span class="small text-muted me-1">[' + msg.created_at + ']</span>' +
      '<span>' + escapeHtml(msg.message) + '</span>';
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  function escapeHtml(text) {
    if (!text) return "";
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function playBeep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.value = 0.05;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      setTimeout(function() {
        osc.stop();
        ctx.close();
      }, 200);
    } catch (e) {
      console.log("Beep error", e);
    }
  }

  async function loadMessages() {
    try {
      const url = lastTimestamp
        ? "/chat/messages?after=" + encodeURIComponent(lastTimestamp)
        : "/chat/messages";

      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok) return;

      if (data.messages && data.messages.length > 0) {
        const isInitial = !lastTimestamp;
        data.messages.forEach(function(m) {
          appendMessage(m);
          lastTimestamp = m.created_at;
        });
        if (!isInitial) {
          playBeep();
        }
      } else if (!lastTimestamp) {
        chatBox.innerHTML = '<div class="text-muted small">لا يوجد رسائل بعد. اكتب أول رسالة 👋</div>';
      }
    } catch (e) {
      // ignore
    }
  }

  async function sendMessage() {
    const text = (input.value || "").trim();
    if (!text) return;

    try {
      const res = await fetch("/chat/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text })
      });
      const data = await res.json();
      if (data.ok) {
        input.value = "";
        loadMessages();
      }
    } catch (e) {
      alert("Error sending message");
    }
  }

  sendBtn.addEventListener("click", sendMessage);
  input.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      sendMessage();
    }
  });

  loadMessages();
  setInterval(loadMessages, 3000);
})();
</script>
{% endblock %}
"""
}

@app.context_processor
def inject_footer():
    return dict(footer_text=APP_FOOTER_TEXT)

app.jinja_loader = DictLoader(TEMPLATES)


# ============================================================
# Bootstrap (works for Gunicorn/Render/PythonAnywhere)
# ============================================================

_bootstrapped = False

def bootstrap_once():
    """Initialize DB, default admin, and background scheduler once per process."""
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    try:
        with app.app_context():
            init_db()
            cur = get_db().cursor()
            admin = cur.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            if not admin:
                cur.execute(
                    "INSERT INTO users (username,password_hash,role,created_at) VALUES (?,?,?,?)",
                    ("admin", generate_password_hash("admin12"), "admin",
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                get_db().commit()
            # Start backup thread (guarded inside function)
            start_backup_scheduler_once()
    except Exception as e:
        # Don't kill WSGI import; errors will show in logs
        try:
            print("Bootstrap error:", e)
        except Exception:
            pass

@app.before_request
def _ensure_bootstrap():
    # First request triggers bootstrap on WSGI servers
    bootstrap_once()


# ============================================================
# Live Chat (Staff)
# ============================================================

@app.route("/chat")
@login_required
def chat_page():
    return render_template("chat.html")

@app.route("/chat/send", methods=["POST"])
@login_required
def chat_send():
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()

    if not msg:
        return jsonify({"ok": False, "error": "Empty message"}), 400

    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = session.get("username", "UNKNOWN")

    db.execute(
        """
        INSERT INTO chat_messages (username, message, created_at)
        VALUES (?,?,?)
        """,
        (username, msg, now),
    )
    db.commit()

    try:
        log_action("CHAT_SEND", details=f"{username}: {msg[:50]}")
    except Exception:
        pass

    return jsonify({"ok": True})

@app.route("/chat/messages")
@login_required
def chat_messages():
    after = (request.args.get("after") or "").strip()

    cur = get_db().cursor()
    sql = "SELECT username, message, created_at FROM chat_messages"
    params = []

    if after:
        sql += " WHERE datetime(created_at) > datetime(?)"
        params.append(after)

    sql += " ORDER BY datetime(created_at) ASC LIMIT 200"

    rows = cur.execute(sql, params).fetchall()

    messages = [
        {
            "username": r["username"],
            "message": r["message"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]

    return jsonify({"ok": True, "messages": messages})

# ============================================================
# Run
# ============================================================


if __name__ == "__main__":
    bootstrap_once()
    app.run(host="0.0.0.0", port=5000, debug=True)
