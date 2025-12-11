
"""
ED Downtime - Cerner-like Board (V5 Full Single-File)
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
    render_template, render_template_string, session, Response, send_from_directory, flash, jsonify
)
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from jinja2 import DictLoader
import os, io, csv, shutil, threading, time
import textwrap

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors

# ============================================================
# Application Setup
# ============================================================

import secrets

try:
    from config import APP_CONFIG
except ImportError:
    APP_CONFIG = {}
app = Flask(__name__)
app.config["SECRET_KEY"] = APP_CONFIG.get(
    "SECRET_KEY",
    os.environ.get(
        "SECRET_KEY",
        "9f4c0c51a7b3e4e1c2d9a8f73b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b",
    ),
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "0") == "1"
app.config["SESSION_COOKIE_SECURE"] = SECURE_COOKIES


# CSRF protection helpers
def generate_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token

app.jinja_env.globals["csrf_token"] = generate_csrf_token

# Endpoints that are exempt from CSRF checks (JSON APIs, etc.)
CSRF_EXEMPT_ENDPOINTS = {"chat_send", "delete_clinical_order"}

@app.before_request
def csrf_protect():
    if request.method == "POST":
        endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
        if endpoint in CSRF_EXEMPT_ENDPOINTS:
            return
        session_token = session.get("csrf_token")
        form_token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not session_token or not form_token or session_token != form_token:
            return "CSRF validation failed.", 400

# Session lifetime (2 hours default)
app.config["PERMANENT_SESSION_LIFETIME"] = 7200

# Idle auto-logout (minutes)
IDLE_TIMEOUT_SECONDS = 15 * 60

DATABASE = APP_CONFIG.get("DATABASE", "triage_ed.db")
UPLOAD_FOLDER = APP_CONFIG.get("UPLOAD_FOLDER", "uploads")
BACKUP_FOLDER = APP_CONFIG.get("BACKUP_FOLDER", "backups")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

APP_FOOTER_TEXT = "Downtime Tool © 2025 — Developed by: Samy Aly | ID 20155"

DEFAULT_RAD_ITEMS = [
    "X-Ray Chest PA/AP",
    "X-Ray Abdomen",
    "X-Ray Pelvis",
    "X-Ray Skull",
    "X-Ray C-Spine",
    "X-Ray T-Spine",
    "X-Ray L-Spine",
    "X-Ray Thoraco-Lumbar",
    "X-Ray Shoulder",
    "X-Ray Humerus",
    "X-Ray Elbow",
    "X-Ray Forearm",
    "X-Ray Wrist",
    "X-Ray Hand",
    "X-Ray Hip",
    "X-Ray Femur",
    "X-Ray Knee",
    "X-Ray Leg/Ankle",
    "X-Ray Foot/Toes",
    "CT Brain Without Contrast",
    "CT Brain With Contrast",
    "CT Temporal Bones",
    "CT C-Spine",
    "CT Dorsal Spine",
    "CT L-S Spine",
    "CT Chest",
    "CT Abdomen",
    "CT Abdomen/Pelvis",
    "CT KUB",
    "CT Angio Brain/Neck",
    "CT Angio Chest (PE Study)",
    "CT Trauma Pan-Scan",
    "MRI Brain",
    "MRI Pituitary",
    "MRI C-Spine",
    "MRI T-Spine",
    "MRI L-Spine",
    "MRI Knee",
    "MRI Shoulder",
    "US Abdomen",
    "US Hepatobiliary",
    "US Pelvis",
    "US KUB",
    "US Scrotum/Testes",
    "US DVT Lower Limb",
    "US DVT Upper Limb",
    "FAST Ultrasound",
    "POCUS / Bedside US",
]

DEFAULT_LAB_ITEMS = [
    "CBC",
    "CMP (Kidney/Liver)",
    "Electrolytes",
    "CRP",
    "ESR",
    "Troponin",
    "CK-MB",
    "PT/PTT/INR",
    "RBS (Random Blood Sugar)",
    "ABG",
    "Lactate",
    "D-Dimer",
    "BNP",
    "LFT",
    "Urine Analysis",
    "Blood Culture",
    "BHCG (Pregnancy Test)",
    "Type & Screen / Crossmatch",
    "FBS (Fasting Blood Sugar)",
    "HbA1c",
    "Renal Function (BUN/Creatinine)",
    "Serum Urea",
    "Serum Creatinine",
    "Serum Sodium",
    "Serum Potassium",
    "Serum Chloride",
    "Serum Bicarbonate",
    "Calcium",
    "Magnesium",
    "Phosphate",
    "Lipase",
    "Amylase",
    "Total Bilirubin",
    "Direct Bilirubin",
    "AST",
    "ALT",
    "Alkaline Phosphatase",
    "Procalcitonin",
    "Serum Lactate (Sepsis)",
    "TSH",
    "Free T4",
    "Serum Ferritin",
    "Iron Studies",
    "Vitamin B12",
    "Folate",
    "Urine Culture",
    "Urine Microscopy",
    "Stool Analysis",
    "Stool Culture",
    "CSF Analysis",
    "CSF Culture",
    "HIV Rapid Test",
    "HBsAg",
    "HCV Antibody",
    "COVID-19 PCR",
    "Influenza PCR",
]

DEFAULT_MED_ITEMS = [
    "Paracetamol IV",
    "Paracetamol PO",
    "Paracetamol IV/PO",
    "Diclofenac IM",
    "Ibuprofen PO",
    "Ketorolac IV/IM",
    "Tramadol IV",
    "Morphine IV",
    "Fentanyl IV",
    "Midazolam IV",
    "Diazepam IV",
    "Ondansetron IV",
    "Metoclopramide IV",
    "Domperidone PO",
    "Ceftriaxone IV",
    "Cefotaxime IV",
    "Ceftazidime IV",
    "Cefazolin IV",
    "Piperacillin/Tazobactam (Tazocin)",
    "Meropenem IV",
    "Vancomycin IV",
    "Amoxicillin/Clavulanate IV",
    "Amoxicillin/Clavulanate PO",
    "Azithromycin IV",
    "Azithromycin PO",
    "Clarithromycin PO",
    "Metronidazole IV",
    "Clindamycin IV",
    "Gentamicin IV",
    "Ciprofloxacin IV",
    "Ciprofloxacin PO",
    "Levofloxacin IV",
    "Levofloxacin PO",
    "Broad Spectrum Antibiotic (per policy)",
    "Aspirin PO 300mg",
    "Aspirin PO 81mg",
    "Nitroglycerin SL",
    "Nitroglycerin Infusion",
    "Heparin SC/IV",
    "Enoxaparin SC",
    "Labetalol IV",
    "Metoprolol IV",
    "Furosemide IV",
    "Hydralazine IV",
    "Noradrenaline Infusion",
    "Dopamine Infusion",
    "Adrenaline Infusion",
    "Oxygen Therapy",
    "Salbutamol Neb",
    "Salbutamol Nebulizer",
    "Salbutamol MDI",
    "Duolin Neb",
    "Ipratropium Nebulizer",
    "Epinephrine IM",
    "Hydrocortisone IV",
    "Chlorpheniramine IV/IM",
    "Diphenhydramine IV/IM",
    "Pantoprazole IV",
    "Ranitidine IV",
    "Omeprazole PO",
    "Hyoscine (Buscopan) IV/IM",
    "Regular Insulin IV",
    "SC Insulin (sliding scale)",
    "Dextrose 50% IV Bolus",
    "Magnesium Sulfate IV",
    "Calcium Gluconate IV",
    "Sodium Bicarbonate IV",
    "Potassium Chloride IV Infusion",
    "Normal Saline 0.9%",
    "Normal Saline 0.9% Bolus",
    "Ringer Lactate",
    "D5W",
    "D5NS",
    "D10W",
    "Tranexamic Acid IV (if indicated)",
    "Tetanus Toxoid IM",
]


DEFAULT_HOME_MED_ITEMS = [
    "Paracetamol 500 mg tab – 1 tab PO – every 8h – for 3 days – PRN for pain",
    "Ibuprofen 400 mg tab – 1 tab PO – every 8h – for 3–5 days – after food",
    "Omeprazole 20 mg cap – 1 cap PO – once daily – for 14 days",
    "Amoxicillin/Clavulanate 1 g tab – 1 tab PO – every 12h – for 5 days",
    "Azithromycin 500 mg tab – 1 tab PO – once daily – for 3 days",
    "Paracetamol syrup 15 mg/kg/dose – every 6h – PRN fever",
    "Salbutamol inhaler – 2 puffs – every 6h – PRN wheeze",
]
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}
app.config["MAX_CONTENT_LENGTH"] = APP_CONFIG.get("MAX_CONTENT_LENGTH", 5 * 1024 * 1024)  # 5 MB upload limit

# Simple helper for wrapping long PDF lines so text doesn't get cut off.
from reportlab.lib.units import cm as _cm_for_wrap

def draw_wrapped_lines(c, text, x, y, max_chars, line_height, page_height, font_name="Helvetica", font_size=10):
    """Draw text with basic word wrapping on the PDF canvas."""
    c.setFont(font_name, font_size)

    # Fallback if text is empty
    if text is None or str(text).strip() == "":
        text = "-"

    for raw in str(text).splitlines():
        raw = raw.rstrip()
        if not raw:
            # Blank line -> just move cursor down
            y -= line_height
            if y < 2*_cm_for_wrap:
                c.showPage()
                y = page_height - 2*_cm_for_wrap
                c.setFont(font_name, font_size)
            continue

        for line in textwrap.wrap(raw, max_chars):
            c.drawString(x, y, line)
            y -= line_height
            if y < 2*_cm_for_wrap:
                c.showPage()
                y = page_height - 2*_cm_for_wrap
                c.setFont(font_name, font_size)

    return y

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
            gd_number TEXT,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Ensure legacy DBs have is_active and gd_number columns
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN gd_number TEXT")
    except sqlite3.OperationalError:
        pass


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
            location TEXT,
            bed_no TEXT,
            bed_status TEXT,
            task_reg TEXT,
            task_ekg TEXT,
            task_sepsis TEXT,
            visit_type TEXT,

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

    # Vital signs history (per-visit, time-stamped)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vital_signs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
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
            recorded_by TEXT
        )
    """)

    # Discharge summaries (V5)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS discharge_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT UNIQUE NOT NULL,
            diagnosis_cc TEXT,
            final_diagnosis TEXT,
            referral_clinic TEXT,
            home_medication TEXT,
            summary_text TEXT,
            investigations_summary TEXT,
            procedures_text TEXT,
            condition_on_discharge TEXT,
            followup_instructions TEXT,
            auto_summary_text TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            updated_at TEXT,
            updated_by TEXT
        )
    """)
    # Ensure new V5 columns exist if upgrading an older database
    ds_cols = [r[1] for r in cur.execute("PRAGMA table_info(discharge_summaries)").fetchall()]
    for col_name in (
        "final_diagnosis",
        "investigations_summary",
        "procedures_text",
        "condition_on_discharge",
        "followup_instructions",
    ):
        if col_name not in ds_cols:
            cur.execute(f"ALTER TABLE discharge_summaries ADD COLUMN {col_name} TEXT")

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
            collected_at TEXT,
            collected_by TEXT,
            received_at TEXT,
            received_by TEXT,
            in_lab_at TEXT,
            in_lab_by TEXT,
            reported_at TEXT,
            reported_by TEXT
        )
    """)
    # Ensure extended LIS columns exist if upgrading an older database
    cols = [r[1] for r in cur.execute("PRAGMA table_info(lab_requests)").fetchall()]
    for col_name in ("collected_at", "collected_by", "in_lab_at", "in_lab_by"):
        if col_name not in cols:
            cur.execute(f"ALTER TABLE lab_requests ADD COLUMN {col_name} TEXT")

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
            message  TEXT NOT NULL,
            room     TEXT,
            visit_id TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Catalog items (medications / labs / radiology)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items_medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items_labs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items_radiology (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS items_home_meds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER
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

    
    # Seed default catalogs (only if tables are empty)
    try:
        for table_name, items in (
            ("items_radiology", DEFAULT_RAD_ITEMS),
            ("items_labs", DEFAULT_LAB_ITEMS),
            ("items_medications", DEFAULT_MED_ITEMS),
            ("items_home_meds", DEFAULT_HOME_MED_ITEMS),
        ):
            try:
                count = cur.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            except Exception:
                count = 0
            if count == 0:
                cur.executemany(
                    f"INSERT INTO {table_name} (name, is_active, sort_order) VALUES (?,?,?)",
                    [(name, 1, idx + 1) for idx, name in enumerate(items)],
                )
        db.commit()
    except Exception:
        # If seeding fails, continue without crashing.
        pass

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
        ensure_column("visits", "triage_time", "TEXT")
        ensure_column("visits", "payment_details", "TEXT")
        ensure_column("visits", "location", "TEXT")
        ensure_column("visits", "bed_no", "TEXT")
        ensure_column("visits", "bed_status", "TEXT")
        ensure_column("visits", "task_reg", "TEXT")
        ensure_column("visits", "task_ekg", "TEXT")
        ensure_column("visits", "task_sepsis", "TEXT")
        ensure_column("visits", "visit_type", "TEXT")
        # Chat extra fields
        ensure_column("chat_messages", "room", "TEXT")
        ensure_column("chat_messages", "visit_id", "TEXT")
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


def calc_age(dob_str):
    """Return age in years from DOB string 'YYYY-MM-DD', or '' if invalid."""
    if not dob_str:
        return ""
    try:
        # Accept formats like YYYY-MM-DD or DD/MM/YYYY etc.
        s = dob_str.strip()
        if "-" in s:
            parts = s.split("-")
        elif "/" in s:
            parts = s.split("/")
        else:
            return ""
        parts = [p for p in parts if p]
        if len(parts) != 3:
            return ""
        # Try to guess ordering; assume year is the 4-digit part
        year_idx = 0
        for i, p in enumerate(parts):
            if len(p) == 4:
                year_idx = i
                break
        year = int(parts[year_idx])
        # remaining two are month and day
        others = [int(parts[i]) for i in range(3) if i != year_idx]
        if len(others) != 2:
            return ""
        month, day = others
        born = datetime(year, month, day)
        today = datetime.today()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if age < 0 or age > 150:
            return ""
        return age
    except Exception:
        return ""


def calc_minutes_between(start_str, end_str=None):
    """Return integer minutes between two timestamps (start -> end/now)."""
    if not start_str:
        return None
    try:
        s = str(start_str).strip()
        if not s:
            return None
        fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
        start_dt = None
        for fmt in fmts:
            try:
                start_dt = datetime.strptime(s, fmt)
                break
            except Exception:
                start_dt = None
        if start_dt is None:
            return None
        if end_str:
            es = str(end_str).strip()
            end_dt = None
            for fmt in fmts:
                try:
                    end_dt = datetime.strptime(es, fmt)
                    break
                except Exception:
                    end_dt = None
            if end_dt is None:
                return None
        else:
            end_dt = datetime.now()
        delta = end_dt - start_dt
        minutes = int(delta.total_seconds() // 60)
        if minutes < 0:
            return None
        return minutes
    except Exception:
        return None


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
        time.sleep(1800)  # every 30 minutes
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
        u = cur.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
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
        gd_number = request.form.get("gd_number","").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
        else:
            try:
                cur.execute("""
                    INSERT INTO users (username, password_hash, role, gd_number, created_at, is_active)
                    VALUES (?,?,?,?,?,1)
                """, (
                    username,
                    generate_password_hash(password),
                    role,
                    gd_number,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                db.commit()
                log_action("CREATE_USER", details=f"{username}:{role}")
                flash("User account created successfully.", "success")
            except sqlite3.IntegrityError:
                flash("Username already exists.", "danger")

    users = cur.execute("SELECT id, username, role, gd_number, created_at, is_active FROM users ORDER BY id DESC").fetchall()
    return render_template("admin_users.html", users=users)
@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@role_required("admin")
def admin_toggle_user(user_id):
    db = get_db()
    cur = db.cursor()
    u = cur.execute("SELECT username, is_active FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))
    new_status = 0 if u["is_active"] else 1
    db.execute("UPDATE users SET is_active=? WHERE id=?", (new_status, user_id))
    db.commit()
    action = "DEACTIVATE_USER" if new_status == 0 else "ACTIVATE_USER"
    log_action(action, details=u["username"])
    flash(f"User {u['username']} is now {'inactive' if new_status == 0 else 'active'}.", "success")
    return redirect(url_for("admin_users"))



@app.route("/admin/items", methods=["GET","POST"])
@login_required
@role_required("admin")
def admin_items():
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        kind = request.form.get("kind")
        item_id = request.form.get("item_id")
        name = (request.form.get("name") or "").strip()

        table_map = {
            "med": "items_medications",
            "lab": "items_labs",
            "rad": "items_radiology",
            "home": "items_home_meds",
        }
        if kind not in table_map:
            flash("Invalid list type.", "danger")
            return redirect(url_for("admin_items"))

        table = table_map[kind]

        try:
            if action == "add":
                if not name:
                    flash("Please enter item name.", "danger")
                else:
                    cur.execute(f"INSERT INTO {table} (name, is_active) VALUES (?, 1)", (name,))
                    db.commit()
                    flash("Item added.", "success")
            elif action == "rename":
                if not item_id or not name:
                    flash("Missing item or name.", "danger")
                else:
                    cur.execute(f"UPDATE {table} SET name=? WHERE id=?", (name, item_id))
                    db.commit()
                    flash("Item updated.", "success")
            elif action == "toggle":
                if not item_id:
                    flash("Missing item.", "danger")
                else:
                    row = cur.execute(f"SELECT is_active FROM {table} WHERE id=?", (item_id,)).fetchone()
                    if row is not None:
                        new_val = 0 if row["is_active"] else 1
                        cur.execute(f"UPDATE {table} SET is_active=? WHERE id=?", (new_val, item_id))
                        db.commit()
                        flash("Item status updated.", "success")
            elif action == "delete":
                if not item_id:
                    flash("Missing item.", "danger")
                else:
                    cur.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
                    db.commit()
                    flash("Item deleted.", "success")
        except sqlite3.IntegrityError:
            flash("Item with same name already exists.", "warning")
        except Exception as e:
            flash(f"Error updating items: {e}", "danger")

        return redirect(url_for("admin_items"))

    meds = cur.execute(
        "SELECT * FROM items_medications ORDER BY is_active DESC, COALESCE(sort_order, 9999), name"
    ).fetchall()
    labs = cur.execute(
        "SELECT * FROM items_labs ORDER BY is_active DESC, COALESCE(sort_order, 9999), name"
    ).fetchall()
    rads = cur.execute(
        "SELECT * FROM items_radiology ORDER BY is_active DESC, COALESCE(sort_order, 9999), name"
    ).fetchall()
    home_meds = cur.execute(
        "SELECT * FROM items_home_meds ORDER BY is_active DESC, COALESCE(sort_order, 9999), name"
    ).fetchall()

    return render_template("admin_items.html", meds=meds, labs=labs, rads=rads, home_meds=home_meds)


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

@app.route("/admin/backup_file/<path:filename>")
@login_required
@role_required("admin")
def admin_backup_file(filename):
    # Download specific .db backup from BACKUP_FOLDER
    safe_name = secure_filename(filename)
    full_path = os.path.join(BACKUP_FOLDER, safe_name)
    if not os.path.exists(full_path):
        return "Backup not found", 404
    if not safe_name.lower().endswith(".db"):
        return "Invalid backup file", 400
    return send_from_directory(BACKUP_FOLDER, safe_name, as_attachment=True)

@app.route("/admin/restore_file/<path:filename>", methods=["GET","POST"])
@login_required
@role_required("admin")
def admin_restore_file(filename):
    """
    Restore DB directly from a selected backup file, with password confirmation.
    """
    safe_name = secure_filename(filename)
    src_path = os.path.join(BACKUP_FOLDER, safe_name)

    if (not os.path.exists(src_path)) or (not safe_name.lower().endswith(".db")):
        flash("Backup file not found or invalid.", "danger")
        return redirect(url_for("admin_restore"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        user_id = session.get("user_id")
        if not user_id:
            flash("Please login again.", "danger")
            return redirect(url_for("login"))

        cur = get_db().cursor()
        u = cur.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u or not check_password_hash(u["password_hash"], password):
            flash("Incorrect password. Restore cancelled.", "danger")
            return redirect(url_for("admin_restore_file", filename=filename))

        try:
            # safety backup of current DB before overwrite
            if os.path.exists(DATABASE):
                shutil.copy2(DATABASE, DATABASE + ".before_restore.bak")

            shutil.copy2(src_path, DATABASE)
            log_action("BACKUP_RESTORE_FILE", details=safe_name)
            flash("Database restored successfully from selected backup. Please restart the app/server.", "success")
        except Exception as e:
            flash(f"Restore from file failed: {e}", "danger")

        return redirect(url_for("ed_board"))

    # GET: show confirmation form
    return render_template("admin_restore_confirm.html", backup_name=safe_name)
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

    # GET: list available backup .db files (newest first)
    backups = []
    try:
        for fname in os.listdir(BACKUP_FOLDER):
            full = os.path.join(BACKUP_FOLDER, fname)
            if not os.path.isfile(full):
                continue
            if not fname.lower().endswith(".db"):
                continue
            st = os.stat(full)
            backups.append({
                "name": fname,
                "size_kb": st.st_size / 1024.0,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        backups.sort(key=lambda b: b["mtime"], reverse=True)
    except Exception:
        backups = []

    return render_template("admin_restore.html", backups=backups)





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
        visit_type = (request.form.get("visit_type","NEW") or "NEW").strip().upper()
        payment_details = request.form.get("payment_details","").strip()
        elig_file = request.files.get("eligibility_file")

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
             payment_details, visit_type, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?)
        """,(
            visit_id, patient_id, queue_no, "NO", "OPEN",
            payment_details,
            visit_type,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session.get("username")
        ))

        # Optional attachment from Register Patient (eligibility / ID)
        if elig_file and getattr(elig_file, "filename", ""):
            filename_original = elig_file.filename
            if allowed_file(filename_original):
                safe_name = secure_filename(filename_original)
                filename = f"{visit_id}_{int(datetime.now().timestamp())}_{safe_name}"
                elig_file.save(os.path.join(UPLOAD_FOLDER, filename))
                cur.execute("""
                    INSERT INTO attachments (visit_id, filename, uploaded_at, uploaded_by)
                    VALUES (?,?,?,?)
                """,(
                    visit_id,
                    filename,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    session.get("username")
                ))
            else:
                flash("Attachment file type is not allowed.", "danger")

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
    """
    ED Board: filterable/paged list of ED visits with simple triage coloring,
    wait time/length-of-stay calculation, and small summary counters.
    """
    status_filter = request.args.get("status", "ALL")
    cat_filter = request.args.get("cat", "ALL")
    visit_f = (request.args.get("visit_id", "") or "").strip()
    user_f = (request.args.get("user", "") or "").strip()
    dfrom = (request.args.get("date_from", "") or "").strip()
    dto = (request.args.get("date_to", "") or "").strip()

    sql = """
        SELECT v.visit_id,
               v.queue_no,
               v.triage_status,
               v.triage_cat,
               v.status,
               v.payment_details,
               v.created_at,
               v.created_by,
               v.closed_at,
               v.location,
               v.bed_no,
               v.bed_status,
               v.task_reg,
               v.task_ekg,
               v.task_sepsis,
               v.visit_type,
               v.allergy_status,
               v.allergy_details,
               p.name,
               p.id_number,
               p.insurance,
               (
                   SELECT a.filename
                   FROM attachments a
                   WHERE a.visit_id = v.visit_id
                     AND a.filename NOT LIKE '%_LAB_%'
                     AND a.filename NOT LIKE '%_RAD_%'
                   ORDER BY a.uploaded_at DESC
                   LIMIT 1
               ) AS id_attachment,
               p.dob AS dob
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE 1=1
    """
    params = []

    # Status / triage filters
    if status_filter != "ALL":
        sql += " AND v.status = ?"
        params.append(status_filter)
    if cat_filter != "ALL":
        sql += " AND v.triage_cat = ?"
        params.append(cat_filter)

    # Text filters
    if visit_f:
        sql += " AND v.visit_id LIKE ?"
        params.append(f"%{visit_f}%")
    if user_f:
        sql += " AND v.created_by = ?"
        params.append(user_f)

    # Date filters (based on created_at)
    if dfrom:
        sql += " AND date(v.created_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(v.created_at) <= date(?)"
        params.append(dto)

    page, per_page, offset = get_page_args(30)
    db = get_db()
    cur = db.cursor()

    base_sql = sql
    base_params = list(params)

    # Summary counters (respect current filters)
    status_counts = {}
    triage_counts = {}
    try:
        for row in cur.execute(
            "SELECT status, COUNT(*) AS c FROM (" + base_sql + ") GROUP BY status",
            base_params,
        ).fetchall():
            key = row["status"] or "UNKNOWN"
            status_counts[key] = row["c"]

        for row in cur.execute(
            "SELECT triage_cat, COUNT(*) AS c FROM (" + base_sql + ") GROUP BY triage_cat",
            base_params,
        ).fetchall():
            key = row["triage_cat"] or "UNK"
            triage_counts[key] = row["c"]
    except Exception:
        # In case SQLite or subquery fails for any reason, we just skip the summary.
        status_counts = {}
        triage_counts = {}

    # Count for pagination
    count_sql = "SELECT COUNT(*) FROM (" + base_sql + ")"
    total_row = cur.execute(count_sql, base_params).fetchone()
    try:
        total = total_row[0]
    except Exception:
        total = 0
    pages = (total + per_page - 1) // per_page if per_page else 1
    if pages <= 0:
        pages = 1

    # Main query with ordering & pagination
    sql = base_sql + """
        ORDER BY 
            CASE 
                WHEN v.triage_cat = 'ES1' THEN 1
                WHEN v.triage_cat = 'ES2' THEN 2
                WHEN v.triage_cat = 'ES3' THEN 3
                WHEN v.triage_cat = 'ES4' THEN 4
                WHEN v.triage_cat = 'ES5' THEN 5
                ELSE 6
            END,
            v.id DESC
        LIMIT ? OFFSET ?
    """
    params2 = base_params + [per_page, offset]
    rows = cur.execute(sql, params2).fetchall()

    visits = []
    for r in rows:
        d = dict(r)
        d["age"] = calc_age(d.get("dob"))

        # Wait time / LOS in minutes
        created_at = d.get("created_at")
        closed_at = d.get("closed_at")
        status_val = (d.get("status") or "").upper()

        # If closed and we have a closed_at timestamp, use LOS;
        # otherwise, measure from created_at until now.
        if status_val in ("DISCHARGED", "TRANSFERRED", "LAMA", "EXPIRED", "CANCELLED", "CLOSED") and closed_at:
            mins = calc_minutes_between(created_at, closed_at)
        else:
            mins = calc_minutes_between(created_at)

        d["waiting_minutes"] = mins
        if mins is None:
            d["waiting_text"] = "-"
            d["waiting_level"] = "none"
        else:
            try:
                mins_int = int(mins)
            except Exception:
                mins_int = mins
            # Human-readable text
            if isinstance(mins_int, int) and mins_int >= 60:
                h = mins_int // 60
                m = mins_int % 60
                if h >= 24:
                    days = h // 24
                    rem_h = h % 24
                    d["waiting_text"] = f"{days}d {rem_h}h"
                else:
                    d["waiting_text"] = f"{h}h {m:02d}m"
            else:
                d["waiting_text"] = f"{mins_int}m"

            # Simple traffic-light levels
            if mins_int is None:
                level = "none"
            elif mins_int < 30:
                level = "short"
            elif mins_int < 90:
                level = "medium"
            else:
                level = "long"
            d["waiting_level"] = level

        visits.append(d)

    users = cur.execute(
        "SELECT DISTINCT created_by FROM visits WHERE created_by IS NOT NULL AND TRIM(created_by) <> '' ORDER BY created_by"
    ).fetchall()

    return render_template(
        "ed_board.html",
        visits=visits,
        status_filter=status_filter,
        cat_filter=cat_filter,
        visit_f=visit_f,
        user_f=user_f,
        dfrom=dfrom,
        dto=dto,
        users=users,
        page=page,
        pages=pages,
        per_page=per_page,
        total=total,
        status_counts=status_counts,
        triage_counts=triage_counts,
    )
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

@app.route("/export/labs.csv")
@login_required

def export_labs_csv():
    """
    Export lab requests as CSV using the same filters as the Lab Board.
    Includes extra LIS timestamps and simple TAT breakdowns.
    """
    status_filter = request.args.get("status", "ALL")
    q = request.args.get("q", "").strip()
    dfrom = request.args.get("date_from", "").strip()
    dto = request.args.get("date_to", "").strip()

    cur = get_db().cursor()
    sql = """
        SELECT lr.id,
               v.visit_id,
               p.name,
               p.id_number,
               lr.test_name,
               lr.status,
               lr.requested_at,
               lr.collected_at,
               lr.received_at,
               lr.in_lab_at,
               lr.reported_at,
               lr.result_text,
               lr.requested_by,
               lr.collected_by,
               lr.received_by,
               lr.in_lab_by,
               lr.reported_by
        FROM lab_requests lr
        JOIN visits v ON v.visit_id = lr.visit_id
        JOIN patients p ON p.id = v.patient_id
        WHERE 1=1
    """
    params = []

    # Status filter (same as Lab Board)
    if status_filter == "PENDING":
        sql += " AND lr.status IN ('REQUESTED','COLLECTED','RECEIVED','IN_LAB')"
    elif status_filter == "REPORTED":
        sql += " AND lr.status='REPORTED'"
    elif status_filter == "ALL":
        pass
    else:
        # Unknown value -> fall back to ALL (no extra filter)
        status_filter = "ALL"

    # Date filters based on requested_at
    if dfrom:
        sql += " AND date(lr.requested_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(lr.requested_at) <= date(?)"
        params.append(dto)

    # Text search
    if q:
        like = f"%{q}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR v.visit_id LIKE ? OR lr.test_name LIKE ?)"
        params.extend([like, like, like, like])

    # Oldest first within each group, but no hard LIMIT for export
    sql += (
        " ORDER BY "
        " CASE WHEN lr.status IN ('REQUESTED','COLLECTED','RECEIVED','IN_LAB') THEN 0 ELSE 1 END, "
        " datetime(lr.requested_at) ASC, lr.id ASC"
    )

    rows = cur.execute(sql, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID","VisitID","Name","ID Number","Test",
        "Status",
        "RequestedAt","CollectedAt","ReceivedAt","InLabAt","ReportedAt",
        "RequestedBy","CollectedBy","ReceivedBy","InLabBy","ReportedBy",
        "ResultText",
        "TAT_req_to_collect_min",
        "TAT_collect_to_receive_min",
        "TAT_receive_to_inlab_min",
        "TAT_inlab_to_report_min",
        "TAT_req_to_report_min",
    ])

    for r in rows:
        # Compute simple TAT segments in minutes (can be None)
        req = r["requested_at"]
        col = r["collected_at"]
        rec = r["received_at"]
        inlab = r["in_lab_at"]
        rep = r["reported_at"]

        tat_req_collect = calc_minutes_between(req, col) if col else None
        tat_collect_receive = calc_minutes_between(col, rec) if col and rec else None
        tat_receive_inlab = calc_minutes_between(rec, inlab) if rec and inlab else None
        tat_inlab_report = calc_minutes_between(inlab, rep) if inlab and rep else None
        tat_req_report = calc_minutes_between(req, rep) if rep else None

        writer.writerow([
            r["id"],
            r["visit_id"],
            r["name"],
            r["id_number"],
            r["test_name"],
            r["status"],
            r["requested_at"],
            r["collected_at"],
            r["received_at"],
            r["in_lab_at"],
            r["reported_at"],
            r["requested_by"],
            r["collected_by"],
            r["received_by"],
            r["in_lab_by"],
            r["reported_by"],
            (r["result_text"] or "").replace("\r"," ").replace("\n"," "),
            tat_req_collect,
            tat_collect_receive,
            tat_receive_inlab,
            tat_inlab_report,
            tat_req_report,
        ])

    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=labs.csv"}
    )
@app.route("/export/radiology.csv")
@login_required
def export_radiology_csv():
    """
    Export radiology requests as CSV using the same filters as the Radiology Board.
    """
    status_filter = request.args.get("status", "ALL")
    q = request.args.get("q", "").strip()
    dfrom = request.args.get("date_from", "").strip()
    dto = request.args.get("date_to", "").strip()

    cur = get_db().cursor()
    sql = """
        SELECT rr.id,
               v.visit_id,
               p.name,
               p.id_number,
               rr.test_name,
               rr.status,
               rr.requested_at,
               rr.requested_by,
               rr.done_at,
               rr.done_by,
               rr.reported_at,
               rr.reported_by,
               rr.report_text
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
        # Unknown value -> fall back to ALL (no extra filter)
        status_filter = "ALL"

    # Date filters based on requested_at
    if dfrom:
        sql += " AND date(rr.requested_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(rr.requested_at) <= date(?)"
        params.append(dto)

    # Text search
    if q:
        like = f"%{q}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR v.visit_id LIKE ? OR rr.test_name LIKE ?)"
        params.extend([like, like, like, like])

    sql += " ORDER BY rr.id DESC"

    rows = cur.execute(sql, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID","VisitID","Name","ID Number","Study",
        "Status","RequestedAt","RequestedBy",
        "DoneAt","DoneBy",
        "ReportedAt","ReportedBy",
        "ReportText",
    ])
    for r in rows:
        writer.writerow([
            r["id"],
            r["visit_id"],
            r["name"],
            r["id_number"],
            r["test_name"],
            r["status"],
            r["requested_at"],
            r["requested_by"],
            r["done_at"],
            r["done_by"],
            r["reported_at"],
            r["reported_by"],
            (r["report_text"] or "").replace("\r", " ").replace("\n", " "),
        ])

    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=radiology.csv"}
    )


# ============================================================
# Patient Details / Edit / Attachments / Close Visit
# ============================================================



REPORTS_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-2">
  <h4 class="mb-0">Reports</h4>
</div>

<form class="card p-3 mb-3 bg-white" method="GET">
  <div class="row g-2 align-items-end">
    <div class="col-md-3 col-sm-6">
      <label class="form-label fw-bold small mb-1">From</label>
      <input type="date" name="date_from" value="{{ dfrom }}" class="form-control form-control-sm">
    </div>
    <div class="col-md-3 col-sm-6">
      <label class="form-label fw-bold small mb-1">To</label>
      <input type="date" name="date_to" value="{{ dto }}" class="form-control form-control-sm">
    </div>
    <div class="col-md-3 col-sm-6">
      <button class="btn btn-primary btn-sm mt-4">Apply</button>
      <a href="{{ url_for('reports') }}" class="btn btn-outline-secondary btn-sm mt-4">Reset</a>
    </div>
  </div>
</form>

<div class="row g-3 mb-3">
  <div class="col-md-3">
    <div class="card p-3 bg-white">
      <div class="text-muted small">Total visits</div>
      <div class="h4 mb-0">{{ total_visits }}</div>
    </div>
  </div>
  <div class="col-md-3">
    <div class="card p-3 bg-white">
      <div class="text-muted small">Open visits</div>
      <div class="h4 mb-0">{{ open_visits }}</div>
    </div>
  </div>
  <div class="col-md-3">
    <div class="card p-3 bg-white">
      <div class="text-muted small">Lab requests</div>
      <div class="h4 mb-0">{{ lab_requests }}</div>
    </div>
  </div>
  <div class="col-md-3">
    <div class="card p-3 bg-white">
      <div class="text-muted small">Radiology requests</div>
      <div class="h4 mb-0">{{ radiology_requests }}</div>
    </div>
  </div>
</div>

{% if visit_status_counts or lab_status_counts or rad_status_counts %}
<div class="card p-3 bg-white mb-3">
  <h6 class="mb-2">By status (for selected period)</h6>
  <div class="row g-2">
    <div class="col-lg-6">
      <div class="small fw-bold text-muted mb-1">Visits</div>
      {% set vs = visit_status_counts or {} %}
      <div class="d-flex flex-wrap gap-1">
        <span class="badge rounded-pill bg-success-subtle text-success border border-success">
          OPEN: <span class="fw-bold">{{ vs.get('OPEN', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-primary-subtle text-primary border border-primary">
          IN_TREATMENT: <span class="fw-bold">{{ vs.get('IN_TREATMENT', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-info-subtle text-info border border-info">
          ADMITTED: <span class="fw-bold">{{ vs.get('ADMITTED', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-secondary-subtle text-secondary border border-secondary">
          DISCHARGED: <span class="fw-bold">{{ vs.get('DISCHARGED', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-warning-subtle text-warning border border-warning">
          TRANSFERRED: <span class="fw-bold">{{ vs.get('TRANSFERRED', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-dark-subtle text-dark border border-dark">
          LAMA: <span class="fw-bold">{{ vs.get('LAMA', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-danger-subtle text-danger border border-danger">
          EXPIRED: <span class="fw-bold">{{ vs.get('EXPIRED', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-light text-muted border">
          CANCELLED: <span class="fw-bold">{{ vs.get('CANCELLED', 0) }}</span>
        </span>
      </div>
    </div>
    <div class="col-lg-3 col-md-6">
      <div class="small fw-bold text-muted mb-1">Labs</div>
      {% set ls = lab_status_counts or {} %}
      <div class="d-flex flex-wrap gap-1">
        <span class="badge rounded-pill bg-secondary-subtle text-secondary border border-secondary">
          Pending: <span class="fw-bold">{{ ls.get('REQUESTED', 0) + ls.get('COLLECTED', 0) + ls.get('RECEIVED', 0) + ls.get('IN_LAB', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-success-subtle text-success border border-success">
          Reported: <span class="fw-bold">{{ ls.get('REPORTED', 0) }}</span>
        </span>
      </div>
    </div>
    <div class="col-lg-3 col-md-6">
      <div class="small fw-bold text-muted mb-1">Radiology</div>
      {% set rs = rad_status_counts or {} %}
      <div class="d-flex flex-wrap gap-1">
        <span class="badge rounded-pill bg-secondary-subtle text-secondary border border-secondary">
          Pending: <span class="fw-bold">{{ rs.get('REQUESTED', 0) + rs.get('DONE', 0) }}</span>
        </span>
        <span class="badge rounded-pill bg-success-subtle text-success border border-success">
          Reported: <span class="fw-bold">{{ rs.get('REPORTED', 0) }}</span>
        </span>
      </div>
    </div>
  </div>
</div>
{% endif %}

<div class="card p-3 bg-white">
  <h6 class="mb-2">Daily counts</h6>
  <div class="table-responsive">
    <table class="table table-sm table-striped mb-0">
      <thead>
        <tr>
          <th>Date</th>
          <th>Visits</th>
          <th>Lab</th>
          <th>Radiology</th>
        </tr>
      </thead>
      <tbody>
        {% for row in daily %}
        <tr>
          <td>{{ row.date }}</td>
          <td>{{ row.visits }}</td>
          <td>{{ row.lab }}</td>
          <td>{{ row.rad }}</td>
        </tr>
        {% endfor %}
        {% if not daily %}
        <tr>
          <td colspan="4" class="text-muted text-center">No data for this period.</td>
        </tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</div>

{% endblock %}
"""


@app.route("/reports")
@login_required
@role_required("admin","doctor","nurse")
def reports():
    """
    Simple dashboard with statistics for visits, labs, and radiology,
    including status breakdown and daily counts for the selected period.
    """
    dfrom = (request.args.get("date_from", "") or "").strip()
    dto   = (request.args.get("date_to", "") or "").strip()

    # If only one date is provided, use it for both
    if dfrom and not dto:
        dto = dfrom
    elif dto and not dfrom:
        dfrom = dto

    # Default to last 7 days (including today) if both are empty
    if not dfrom or not dto:
        today = datetime.today().date()
        dfrom = (today - timedelta(days=6)).strftime("%Y-%m-%d")
        dto   = today.strftime("%Y-%m-%d")

    db = get_db()
    cur = db.cursor()

    # High level counts
    total_visits = cur.execute(
        "SELECT COUNT(*) FROM visits WHERE date(created_at) BETWEEN date(?) AND date(?)",
        (dfrom, dto),
    ).fetchone()[0]

    open_visits = cur.execute(
        "SELECT COUNT(*) FROM visits WHERE status='OPEN' "
        "AND date(created_at) BETWEEN date(?) AND date(?)",
        (dfrom, dto),
    ).fetchone()[0]

    lab_requests = cur.execute(
        "SELECT COUNT(*) FROM lab_requests "
        "WHERE date(requested_at) BETWEEN date(?) AND date(?)",
        (dfrom, dto),
    ).fetchone()[0]

    radiology_requests = cur.execute(
        "SELECT COUNT(*) FROM radiology_requests "
        "WHERE date(requested_at) BETWEEN date(?) AND date(?)",
        (dfrom, dto),
    ).fetchone()[0]

    # Visits by status (OPEN / IN_TREATMENT / ADMITTED / ... / CANCELLED)
    visit_status_counts = {}
    try:
        rows = cur.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM visits
            WHERE date(created_at) BETWEEN date(?) AND date(?)
            GROUP BY status
            """,
            (dfrom, dto),
        ).fetchall()
        for r in rows:
            key = (r["status"] or "UNKNOWN").upper()
            visit_status_counts[key] = r["c"]
    except Exception:
        visit_status_counts = {}

    # Lab by status
    lab_status_counts = {}
    try:
        rows = cur.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM lab_requests
            WHERE date(requested_at) BETWEEN date(?) AND date(?)
            GROUP BY status
            """,
            (dfrom, dto),
        ).fetchall()
        for r in rows:
            key = (r["status"] or "UNKNOWN").upper()
            lab_status_counts[key] = r["c"]
    except Exception:
        lab_status_counts = {}

    # Radiology by status
    rad_status_counts = {}
    try:
        rows = cur.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM radiology_requests
            WHERE date(requested_at) BETWEEN date(?) AND date(?)
            GROUP BY status
            """,
            (dfrom, dto),
        ).fetchall()
        for r in rows:
            key = (r["status"] or "UNKNOWN").upper()
            rad_status_counts[key] = r["c"]
    except Exception:
        rad_status_counts = {}

    # Daily breakdown (visits / lab / radiology)
    v_rows = cur.execute(
        "SELECT date(created_at) AS d, COUNT(*) AS c FROM visits "
        "WHERE date(created_at) BETWEEN date(?) AND date(?) "
        "GROUP BY date(created_at) ORDER BY d",
        (dfrom, dto),
    ).fetchall()
    l_rows = cur.execute(
        "SELECT date(requested_at) AS d, COUNT(*) AS c FROM lab_requests "
        "WHERE date(requested_at) BETWEEN date(?) AND date(?) "
        "GROUP BY date(requested_at) ORDER BY d",
        (dfrom, dto),
    ).fetchall()
    r_rows = cur.execute(
        "SELECT date(requested_at) AS d, COUNT(*) AS c FROM radiology_requests "
        "WHERE date(requested_at) BETWEEN date(?) AND date(?) "
        "GROUP BY date(requested_at) ORDER BY d",
        (dfrom, dto),
    ).fetchall()

    v_map = {row["d"]: row["c"] for row in v_rows}
    l_map = {row["d"]: row["c"] for row in l_rows}
    r_map = {row["d"]: row["c"] for row in r_rows}

    daily = []
    try:
        start_dt = datetime.strptime(dfrom, "%Y-%m-%d")
        end_dt   = datetime.strptime(dto, "%Y-%m-%d")
        d = start_dt
        while d <= end_dt:
            ds = d.strftime("%Y-%m-%d")
            daily.append({
                "date": ds,
                "visits": v_map.get(ds, 0),
                "lab": l_map.get(ds, 0),
                "rad": r_map.get(ds, 0),
            })
            d += timedelta(days=1)
    except Exception:
        daily = []

    return render_template_string(
        REPORTS_TEMPLATE,
        dfrom=dfrom,
        dto=dto,
        total_visits=total_visits,
        open_visits=open_visits,
        lab_requests=lab_requests,
        radiology_requests=radiology_requests,
        daily=daily,
        visit_status_counts=visit_status_counts,
        lab_status_counts=lab_status_counts,
        rad_status_counts=rad_status_counts,
    )

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


@app.route("/patient/<visit_id>/vitals_history")
@login_required
def vitals_history(visit_id):
    """Return JSON vitals history for a visit for use in front-end graphs."""
    cur = get_db().cursor()
    points = []

    # Try to read from dedicated history table (if available)
    try:
        rows = cur.execute(
            """
            SELECT recorded_at,
                   pulse_rate, resp_rate,
                   bp_systolic, bp_diastolic,
                   temperature, spo2, pain_score
            FROM vital_signs
            WHERE visit_id=?
            ORDER BY recorded_at ASC, id ASC
            """,
            (visit_id,),
        ).fetchall()
    except Exception:
        rows = []

    for r in rows or []:
        points.append(
            {
                "time": r["recorded_at"],
                "pulse": r["pulse_rate"],
                "resp": r["resp_rate"],
                "bp_sys": r["bp_systolic"],
                "bp_dia": r["bp_diastolic"],
                "temp": r["temperature"],
                "spo2": r["spo2"],
                "pain": r["pain_score"],
            }
        )

    # Fallback: if no history rows, expose single snapshot from visits table
    if not points:
        v = cur.execute(
            """
            SELECT triage_time, created_at,
                   pulse_rate, resp_rate,
                   bp_systolic, bp_diastolic,
                   temperature, spo2, pain_score
            FROM visits
            WHERE visit_id=?
            """,
            (visit_id,),
        ).fetchone()
        if v:
            ts = v["triage_time"] or v["created_at"]
            points.append(
                {
                    "time": ts,
                    "pulse": v["pulse_rate"],
                    "resp": v["resp_rate"],
                    "bp_sys": v["bp_systolic"],
                    "bp_dia": v["bp_diastolic"],
                    "temp": v["temperature"],
                    "spo2": v["spo2"],
                    "pain": v["pain_score"],
                }
            )

    return jsonify({"ok": True, "points": points})


@app.route("/visit/<visit_id>/location_bed", methods=["POST"])
@login_required
@role_required("reception","nurse","doctor","admin")
def update_location_bed(visit_id):
    """
    Simple helper to update ED Location / Bed / Bed Status from patient_details or ED Board.
    """
    db = get_db()
    cur = db.cursor()

    location = (request.form.get("location") or "").strip()
    bed_no = (request.form.get("bed_no") or "").strip()
    bed_status = (request.form.get("bed_status") or "").strip().upper()
    if bed_status not in ("EMPTY", "OCCUPIED", "DIRTY"):
        bed_status = ""

    cur.execute(
        """
        UPDATE visits
           SET location=?,
               bed_no=?,
               bed_status=?
         WHERE visit_id=?
        """,
        (location, bed_no, bed_status, visit_id),
    )
    db.commit()

    log_action("UPDATE_LOC_BED", visit_id=visit_id,
               details=f"{location}|{bed_no}|{bed_status}")
    flash("Location / Bed updated.", "success")
    return redirect(url_for("patient_details", visit_id=visit_id))


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

    filename = a["filename"] or ""
    # Reception cannot delete LAB / RAD result files
    if session.get("role") == "reception" and ("_LAB_" in filename or "_RAD_" in filename):
        flash("Reception cannot delete lab or radiology results.", "danger")
        return redirect(url_for("patient_details", visit_id=a["visit_id"]))

    try:
        os.remove(os.path.join(UPLOAD_FOLDER, filename))
    except Exception:
        pass

    db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
    db.commit()
    log_action("DELETE_ATTACHMENT", visit_id=a["visit_id"], details=filename)
    flash("Attachment deleted.", "success")
    return redirect(url_for("patient_details", visit_id=a["visit_id"]))


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    # Reception is not allowed to download LAB / RAD result files
    if session.get("role") == "reception" and ("_LAB_" in filename or "_RAD_" in filename):
        abort(403)
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/visit/<visit_id>/close", methods=["POST"])
@login_required
@role_required("doctor","admin")
def close_visit(visit_id):
    status = (request.form.get("status", "DISCHARGED") or "").strip().upper()

    allowed_statuses = [
        "DISCHARGED",
        "ADMITTED",
        "TRANSFERRED",
        "LAMA",
        "EXPIRED",
        "IN_TREATMENT",
        "CANCELLED",
        "CALLED",
        "VISIT_PROGRESS",
        "VISIT_COMPLETED",
        "REGISTERED",
    ]
    if status not in allowed_statuses:
        status = "DISCHARGED"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username")
    db = get_db()
    db.execute(
        """
        UPDATE visits
           SET status=?,
               closed_at=?,
               closed_by=?
         WHERE visit_id=?
        """,
        (status, now, user, visit_id),
    )
    db.commit()
    log_action("CLOSE_VISIT", visit_id=visit_id, details=status)
    flash(f"Visit closed as {status}.", "success")
    return redirect(url_for("ed_board"))



@app.route("/depart/<visit_id>", methods=["GET","POST"])
@login_required
@role_required("reception","nurse","doctor","admin")
def depart_workflow(visit_id):
    """
    ED Depart / Discharge workflow page.
    Shows a Cerner-like checklist (registration, sepsis, ECG)
    and quick links to discharge PDFs and close-visit.
    """
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.*,
               p.name,
               p.id_number,
               p.phone,
               p.insurance,
               p.insurance_no,
               p.dob,
               p.sex,
               p.nationality
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        flash("Visit not found.", "danger")
        return redirect(url_for("ed_board"))

    if request.method == "POST":
        # Update simple checklist tasks
        task_reg = "YES" if request.form.get("task_reg") else ""
        task_ekg = "YES" if request.form.get("task_ekg") else ""
        task_sepsis = "YES" if request.form.get("task_sepsis") else ""

        cur.execute(
            "UPDATE visits SET task_reg=?, task_ekg=?, task_sepsis=? WHERE visit_id=?",
            (task_reg, task_ekg, task_sepsis, visit_id),
        )
        db.commit()

        log_action(
            "UPDATE_VISIT_TASKS",
            visit_id=visit_id,
            details=f"REG={task_reg or 'NO'},EKG={task_ekg or 'NO'},SEP={task_sepsis or 'NO'}",
        )
        flash("Checklist tasks updated.", "success")
        return redirect(url_for("depart_workflow", visit_id=visit_id))

    # Orders count
    try:
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM clinical_orders WHERE visit_id=?",
            (visit_id,),
        ).fetchone()
        orders_count = row["c"] if row is not None else 0
    except Exception:
        orders_count = 0

    # Lab counts
    labs_total = labs_pending = labs_reported = 0
    try:
        lab_rows = cur.execute(
            "SELECT status, COUNT(*) AS c FROM lab_requests WHERE visit_id=? GROUP BY status",
            (visit_id,),
        ).fetchall()
        for r in lab_rows:
            st = (r["status"] or "").upper()
            c = r["c"] or 0
            labs_total += c
            if st in ("REQUESTED", "RECEIVED"):
                labs_pending += c
            elif st == "REPORTED":
                labs_reported += c
    except Exception:
        pass

    # Radiology counts
    rads_total = rads_pending = rads_reported = 0
    try:
        rad_rows = cur.execute(
            "SELECT status, COUNT(*) AS c FROM radiology_requests WHERE visit_id=? GROUP BY status",
            (visit_id,),
        ).fetchall()
        for r in rad_rows:
            st = (r["status"] or "").upper()
            c = r["c"] or 0
            rads_total += c
            if st in ("REQUESTED", "DONE"):
                rads_pending += c
            elif st == "REPORTED":
                rads_reported += c
    except Exception:
        pass

    # Discharge summary presence
    try:
        discharge = cur.execute(
            "SELECT diagnosis_cc, summary_text FROM discharge_summaries WHERE visit_id=?",
            (visit_id,),
        ).fetchone()
    except Exception:
        discharge = None

    discharge_exists = discharge is not None
    discharge_diag = ""
    if discharge:
        discharge_diag = (discharge["diagnosis_cc"] or "").strip() or ""

    # Log view
    try:
        log_action("DEPART_VIEW", visit_id=visit_id)
    except Exception:
        pass

    return render_template(
        "depart_workflow.html",
        visit=visit,
        orders_count=orders_count,
        labs_total=labs_total,
        labs_pending=labs_pending,
        labs_reported=labs_reported,
        rads_total=rads_total,
        rads_pending=rads_pending,
        rads_reported=rads_reported,
        discharge_exists=discharge_exists,
        discharge_diag=discharge_diag,
    )


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
    reason = ((request.form.get("cancel_reason") or request.form.get("reason")) or "").strip()
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

        # Single timestamp used for both visit record and vitals history
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                height=?,
                triage_time=?
            WHERE visit_id=?
        """,(cat, comment, allergy, allergy_details, pr, rr, bp_sys, bp_dia, temp, gcs, spo2, pain, weight, height,
             now_ts,
             visit_id))

        # Keep vitals history for trend graph
        try:
            db.execute("""
                INSERT INTO vital_signs (
                    visit_id, recorded_at,
                    pulse_rate, resp_rate,
                    bp_systolic, bp_diastolic,
                    temperature, consciousness_level,
                    spo2, pain_score,
                    weight, height,
                    recorded_by
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,(
                visit_id,
                now_ts,
                pr, rr,
                bp_sys, bp_dia,
                temp, gcs,
                spo2, pain,
                weight, height,
                session.get("username","UNKNOWN"),
            ))
        except Exception:
            # If the history table is missing for any reason, ignore so triage is not blocked
            pass

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

    # Chief complaint (optional)
    if visit["comment"]:
        lines.append("Chief Complaint:")
        lines.append(f" - {visit['comment']}")
        lines.append("")

    # Triage & Vitals
    lines.append("Triage & Vital Signs")
    lines.append("---------------------")
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
        lines.append("Doctor Examination / History")
        lines.append("----------------------------")
        for line in discharge_text.splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
        lines.append("")

    # Clinical orders
    if orders:
        lines.append("Clinical Orders (chronological)")
        lines.append("--------------------------------")
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
        lines.append("Lab Tests & Results")
        lines.append("-------------------")
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
        lines.append("Radiology Studies & Reports")
        lines.append("---------------------------")
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
        lines.append("Nursing Notes")
        lines.append("-------------")
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
       - Follow-up instructions
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
        SELECT diagnosis_cc,
               final_diagnosis,
               referral_clinic,
               home_medication,
               summary_text,
               investigations_summary,
               procedures_text,
               condition_on_discharge,
               followup_instructions
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
    if summary:
        final_dx = summary["final_diagnosis"]
        diag_cc = summary["diagnosis_cc"]
        if final_dx:
            lines.append(f" - Final: {final_dx}")
        if diag_cc:
            lines.append(f" - Chief complaint / provisional: {diag_cc}")
        if not final_dx and not diag_cc:
            lines.append(" - Not documented")
    else:
        lines.append(" - Not documented")
    lines.append("")

    # ED / Hospital course
    if summary and summary["summary_text"]:
        lines.append("ED / Hospital Course:")
        for line in summary["summary_text"].splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
        lines.append("")

    # Investigations summary (doctor text)
    if summary and summary["investigations_summary"]:
        lines.append("Investigations Summary:")
        for line in summary["investigations_summary"].splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
        lines.append("")

    # Procedures performed
    if summary and summary["procedures_text"]:
        lines.append("Procedures Performed:")
        for line in summary["procedures_text"].splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
        lines.append("")

    # Condition on discharge
    if summary and summary["condition_on_discharge"]:
        lines.append("Condition on discharge:")
        lines.append(f" - {summary['condition_on_discharge']}")
        lines.append("")

    # Referral
    if summary and summary["referral_clinic"]:
        lines.append("Referral Clinic:")
        lines.append(f" - {summary['referral_clinic']}")
        lines.append("")

    # ED Medications
    ed_meds = []
    for o in orders:
        meds_field = o["medications"]
        if meds_field:
            for m in meds_field.split(","):
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
            line = line.strip()
            if line:
                lines.append(f" - {line}")
    else:
        lines.append(" - None")
    lines.append("")

    # Follow-up instructions
    if summary and summary["followup_instructions"]:
        lines.append("Follow-up & instructions:")
        for line in summary["followup_instructions"].splitlines():
            line = line.strip()
            if line:
                lines.append(f" - {line}")
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

    # Dynamic catalog items for dropdown/checkbox lists
    try:
        rad_items = cur.execute(
            "SELECT * FROM items_radiology WHERE is_active=1 ORDER BY COALESCE(sort_order, 9999), name"
        ).fetchall()
        lab_items = cur.execute(
            "SELECT * FROM items_labs WHERE is_active=1 ORDER BY COALESCE(sort_order, 9999), name"
        ).fetchall()
        med_items = cur.execute(
            "SELECT * FROM items_medications WHERE is_active=1 ORDER BY COALESCE(sort_order, 9999), name"
        ).fetchall()
        home_med_items = cur.execute(
            "SELECT * FROM items_home_meds WHERE is_active=1 ORDER BY COALESCE(sort_order, 9999), name"
        ).fetchall()
    except Exception:
        # Fallback: empty lists if catalog tables missing
        rad_items = []
        lab_items = []
        med_items = []
        home_med_items = []

    return render_template("clinical_orders.html",
                           visit=visit,
                           orders=orders,
                           notes=notes,
                           summary=summary,
                           lab_reqs=lab_reqs,
                           rad_reqs=rad_reqs,
                           rad_items=rad_items,
                           lab_items=lab_items,
                           med_items=med_items,
                           home_med_items=home_med_items)

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

    # Optional patient ID image (from reception attachment) at top-right
    if id_image_path:
        try:
            img_w = 5*cm
            img_h = 6*cm
            c.drawImage(
                id_image_path,
                width - img_w - 2*cm,
                height - img_h - 2*cm,
                width=img_w,
                height=img_h,
                preserveAspectRatio=True,
                mask='auto',
            )
        except Exception:
            # If anything goes wrong with the image, continue without it
            pass

    # Optional patient ID image (from reception attachment) at top-right
    if id_image_path:
        try:
            img_w = 4*cm
            img_h = 3*cm
            c.drawImage(
                id_image_path,
                width - img_w - 2*cm,
                height - img_h - 2*cm,
                width=img_w,
                height=img_h,
                preserveAspectRatio=True,
                mask='auto',
            )
        except Exception:
            pass

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

    # Doctor name and signature line
    sig_y = max(2.0*cm, y - 1.5*cm)
    c.setFont("Helvetica", 9)
    c.drawString(2*cm, sig_y, f"Doctor: {doctor_display}")
    c.drawString(width/2, sig_y, "Signature: ______________________")

    # Doctor name and signature line
    sig_y = max(2.0*cm, y - 1.5*cm)
    c.setFont("Helvetica", 9)
    c.drawString(2*cm, sig_y, f"Doctor: {doctor_display}")
    c.drawString(width/2, sig_y, "Signature: ______________________")

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

    # Resolve doctor for signature (Home Medication)
    doctor_name = _resolve_doctor_name_for_visit(cur, visit, visit_id)
    doctor_display = _doctor_display_with_gd(cur, doctor_name)


    # Resolve doctor for signature (Auto Summary)
    doctor_name = _resolve_doctor_name_for_visit(cur, visit, visit_id)
    doctor_display = _doctor_display_with_gd(cur, doctor_name)

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
    final_diagnosis = clean_text(request.form.get("final_diagnosis","").strip())
    referral_clinic = clean_text(request.form.get("referral_clinic","").strip())
    home_medication = clean_text(request.form.get("home_medication","").strip())
    text = clean_text(request.form.get("summary_text","").strip())
    investigations_summary = clean_text(request.form.get("investigations_summary","").strip())
    procedures_text = clean_text(request.form.get("procedures_text","").strip())
    condition_on_discharge = clean_text(request.form.get("condition_on_discharge","").strip())
    followup_instructions = clean_text(request.form.get("followup_instructions","").strip())

    db=get_db(); cur=db.cursor()
    exists = cur.execute("SELECT * FROM discharge_summaries WHERE visit_id=?", (visit_id,)).fetchone()
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"); user=session.get("username")

    if exists:
        db.execute("""
            UPDATE discharge_summaries SET
                diagnosis_cc=?,
                final_diagnosis=?,
                referral_clinic=?,
                home_medication=?,
                summary_text=?,
                investigations_summary=?,
                procedures_text=?,
                condition_on_discharge=?,
                followup_instructions=?,
                updated_at=?,
                updated_by=?
            WHERE visit_id=?
        """,(diagnosis_cc, final_diagnosis, referral_clinic, home_medication, text,
             investigations_summary, procedures_text, condition_on_discharge,
             followup_instructions, now, user, visit_id))
    else:
        db.execute("""
            INSERT INTO discharge_summaries
            (visit_id, diagnosis_cc, final_diagnosis, referral_clinic, home_medication,
             summary_text, investigations_summary, procedures_text, condition_on_discharge,
             followup_instructions, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,(visit_id, diagnosis_cc, final_diagnosis, referral_clinic, home_medication,
             text, investigations_summary, procedures_text, condition_on_discharge,
             followup_instructions, now, user))
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
    db = get_db(); cur = db.cursor()
    visit = cur.execute("""
        SELECT v.visit_id,
               v.created_at AS visit_created_at,
               v.closed_at AS visit_closed_at,
               p.name,
               p.id_number,
               p.insurance,
               p.dob,
               p.sex,
               p.nationality
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_id = ?
    """, (visit_id,)).fetchone()
    summary = cur.execute("SELECT * FROM discharge_summaries WHERE visit_id=?", (visit_id,)).fetchone()
    if not visit:
        return "Not found", 404

    auto_text = summary["auto_summary_text"] if summary and summary["auto_summary_text"] else build_auto_summary(visit_id)

    buffer = io.BytesIO(); c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4; y = height - 2*cm

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "ED Discharge Summary"); y -= 1*cm

    c.setFont("Helvetica", 10)
    c.drawString(2*cm, y, f"Patient: {visit['name']}"); y -= 0.45*cm
    c.drawString(2*cm, y, f"ID / MRN: {visit['id_number']}   Insurance: {visit['insurance'] or '-'}"); y -= 0.45*cm
    c.drawString(2*cm, y, f"DOB: {visit['dob'] or '-'}   Sex: {visit['sex'] or '-'}   Nationality: {visit['nationality'] or '-'}"); y -= 0.45*cm
    c.drawString(2*cm, y, f"Visit ID: {visit['visit_id']}   Visit Date/Time: {visit['visit_created_at'] or '-'}"); y -= 0.8*cm

    def draw_multiline(label, text):
        nonlocal y
        c.setFont("Helvetica-Bold", 11)
        c.drawString(2*cm, y, label)
        y -= 0.55*cm

        y_local = draw_wrapped_lines(
            c,
            text or "-",
            x=2.3*cm,
            y=y,
            max_chars=95,
            line_height=0.45*cm,
            page_height=height,
            font_name="Helvetica",
            font_size=10,
        )
        y = y_local - 0.25*cm

    # Main sections
    draw_multiline("Diagnosis / Chief Complaint:", summary["diagnosis_cc"] if summary else "")
    draw_multiline("Final Diagnosis:", summary["final_diagnosis"] if summary else "")
    draw_multiline("ED / Hospital Course:", summary["summary_text"] if summary else "")
    draw_multiline("Investigations Summary:", summary["investigations_summary"] if summary else "")
    draw_multiline("Procedures Performed:", summary["procedures_text"] if summary else "")
    draw_multiline("Condition on Discharge:", summary["condition_on_discharge"] if summary else "")
    draw_multiline("Referral to Clinic:", summary["referral_clinic"] if summary else "")
    draw_multiline("Discharge Medications:", summary["home_medication"] if summary else "")
    draw_multiline("Follow-up & Instructions:", summary["followup_instructions"] if summary else "")

    # Auto-generated ED course summary (optional but helpful)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2*cm, y, "Auto ED Course Summary:")
    y -= 0.5*cm
    c.setFont("Helvetica", 9)
    y = draw_wrapped_lines(
        c,
        auto_text or "-",
        x=2.3*cm,
        y=y,
        max_chars=105,
        line_height=0.4*cm,
        page_height=height,
        font_name="Helvetica",
        font_size=9,
    )

    # Doctor name and signature line
    doctor_name = ""
    if summary:
        # sqlite3.Row supports dict-style access, not .get()
        try:
            doctor_name = summary["updated_by"] or summary["created_by"]
        except Exception:
            doctor_name = ""

    # Try to fetch GD / license number for the doctor from users table
    doctor_display = doctor_name or "______________________"
    if doctor_name:
        try:
            user_row = cur.execute(
                "SELECT * FROM users WHERE username=?",
                (doctor_name,)
            ).fetchone()
            if user_row is not None:
                gd_value = None
                try:
                    gd_value = user_row["gd_number"]
                except Exception:
                    gd_value = None
                if gd_value:
                    doctor_display = f"{doctor_name}  (GD: {gd_value})"
        except Exception:
            # If anything goes wrong (missing column, etc.), just fall back to the name only
            pass

    c.setFont("Helvetica", 9)
    c.drawString(2*cm, 2.0*cm, f"Doctor: {doctor_display}")
    c.drawString(width/2, 2.0*cm, "Signature: ______________________")

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2*cm, 1.2*cm, APP_FOOTER_TEXT)
    c.showPage(); c.save(); buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=discharge_summary.pdf"},
    )


# ============================================================
# Lab Board (Requests / Results) - Extended LIS workflow
# ============================================================

@app.route("/lab_board")
@login_required
@role_required("lab", "admin", "doctor", "nurse")
def lab_board():
    """
    Lab board for viewing and updating lab requests.
    Uses extended LIS workflow:
      REQUESTED → COLLECTED → RECEIVED → IN_LAB → REPORTED
    """
    status_filter = request.args.get("status", "ALL")
    q = request.args.get("q", "").strip()
    dfrom = request.args.get("date_from", "").strip()
    dto = request.args.get("date_to", "").strip()

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
        sql += " AND lr.status IN ('REQUESTED','COLLECTED','RECEIVED','IN_LAB')"
    elif status_filter == "REPORTED":
        sql += " AND lr.status='REPORTED'"
    elif status_filter == "ALL":
        pass
    else:
        # Unknown value -> fall back to ALL (no extra filter)
        status_filter = "ALL"

    # Date filters based on requested_at
    if dfrom:
        sql += " AND date(lr.requested_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(lr.requested_at) <= date(?)"
        params.append(dto)

    # Text search
    if q:
        like = f"%{q}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR v.visit_id LIKE ? OR lr.test_name LIKE ?)"
        params.extend([like, like, like, like])

    # Order: pending first (REQUESTED/COLLECTED/RECEIVED/IN_LAB), oldest request first
    sql += (
        " ORDER BY "
        " CASE WHEN lr.status IN ('REQUESTED','COLLECTED','RECEIVED','IN_LAB') THEN 0 ELSE 1 END, "
        " datetime(lr.requested_at) ASC, lr.id ASC"
        " LIMIT 500"
    )
    rows_raw = cur.execute(sql, params).fetchall()

    rows = []
    status_counts = {}
    for r in rows_raw:
        d = dict(r)
        st = d.get("status") or ""
        status_counts[st] = status_counts.get(st, 0) + 1

        req_ts = d.get("requested_at")
        rep_ts = d.get("reported_at")
        # Overall TAT from request to report (or now)
        if st == "REPORTED" and rep_ts:
            mins = calc_minutes_between(req_ts, rep_ts)
        else:
            mins = calc_minutes_between(req_ts, None)
        d["age_minutes"] = mins
        if mins is None:
            d["age_text"] = ""
            d["age_level"] = ""
        else:
            hours = mins // 60
            mm = mins % 60
            if hours:
                d["age_text"] = f"{hours}h {mm:02d}m"
            else:
                d["age_text"] = f"{mm}m"
            # classify delay for still-active requests
            if st in ("REQUESTED", "COLLECTED", "RECEIVED", "IN_LAB"):
                if mins >= 120:
                    d["age_level"] = "long"
                elif mins >= 60:
                    d["age_level"] = "medium"
                else:
                    d["age_level"] = "short"
            else:
                d["age_level"] = ""

        # Extra per-phase timestamps (for potential analytics / export)
        d["collected_at"] = d.get("collected_at")
        d["received_at"] = d.get("received_at")
        d["in_lab_at"] = d.get("in_lab_at")

        rows.append(d)

    pending_count = (
        status_counts.get("REQUESTED", 0)
        + status_counts.get("COLLECTED", 0)
        + status_counts.get("RECEIVED", 0)
        + status_counts.get("IN_LAB", 0)
    )
    reported_count = status_counts.get("REPORTED", 0)

    return render_template(
        "lab_board.html",
        rows=rows,
        status_filter=status_filter,
        q=q,
        date_from=dfrom,
        date_to=dto,
        status_counts=status_counts,
        pending_count=pending_count,
        reported_count=reported_count,
    )


@app.route("/lab_request/<int:rid>/collect", methods=["POST"])
@login_required
@role_required("lab", "nurse", "admin")
def lab_collect_sample(rid):
    """
    Mark a lab request as COLLECTED (sample taken from patient).
    """
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")
    next_url = request.referrer or url_for("lab_board")

    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(next_url)

    if row["status"] not in ("REQUESTED", "COLLECTED"):
        flash("Cannot mark collected in current status.", "warning")
        return redirect(next_url)

    db.execute(
        """
        UPDATE lab_requests
           SET status='COLLECTED',
               collected_at=COALESCE(collected_at, ?),
               collected_by=COALESCE(collected_by, ?)
         WHERE id=?
        """,
        (now, user, rid),
    )
    db.commit()

    log_action("LAB_COLLECT", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Sample marked as collected.", "success")
    return redirect(next_url)


@app.route("/lab_request/<int:rid>/receive", methods=["POST"])
@login_required
@role_required("lab", "admin")
def lab_receive_sample(rid):
    """
    Mark sample as RECEIVED in lab for a lab request.
    If collected_at is missing, it will be set now as well.
    """
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")
    next_url = request.referrer or url_for("lab_board")

    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(next_url)

    if row["status"] not in ("REQUESTED", "COLLECTED", "RECEIVED"):
        flash("Cannot receive sample in current status.", "warning")
        return redirect(next_url)

    db.execute(
        """
        UPDATE lab_requests
           SET status='RECEIVED',
               collected_at=COALESCE(collected_at, ?),
               collected_by=COALESCE(collected_by, ?),
               received_at=?,
               received_by=?
         WHERE id=?
        """,
        (
            now,
            user,
            now,
            user,
            rid,
        ),
    )
    db.commit()

    log_action("LAB_RECEIVE", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Sample marked as received in lab.", "success")
    return redirect(next_url)


@app.route("/lab_request/<int:rid>/start", methods=["POST"])
@login_required
@role_required("lab", "admin")
def lab_start_in_lab(rid):
    """
    Mark a lab request as IN_LAB (processing on analyser).
    """
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")
    next_url = request.referrer or url_for("lab_board")

    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(next_url)

    if row["status"] not in ("RECEIVED", "IN_LAB"):
        flash("Cannot start processing in current status.", "warning")
        return redirect(next_url)

    db.execute(
        """
        UPDATE lab_requests
           SET status='IN_LAB',
               in_lab_at=COALESCE(in_lab_at, ?),
               in_lab_by=COALESCE(in_lab_by, ?)
         WHERE id=?
        """,
        (now, user, rid),
    )
    db.commit()

    log_action("LAB_IN_LAB", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Sample marked as in-lab (processing).", "success")
    return redirect(next_url)


@app.route("/lab_request/<int:rid>/report", methods=["POST"])
@login_required
@role_required("lab", "admin")
def lab_report_result(rid):
    """
    Enter lab result and mark request as REPORTED.
    """
    result = clean_text(request.form.get("result_text", "").strip())
    next_url = request.referrer or url_for("lab_board")
    if not result:
        flash("Please enter result before saving.", "danger")
        return redirect(next_url)

    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(next_url)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user = session.get("username", "UNKNOWN")

    db.execute(
        """
        UPDATE lab_requests
           SET status='REPORTED',
               result_text=?,
               reported_at=?,
               reported_by=?,
               collected_at=COALESCE(collected_at, ?),
               collected_by=COALESCE(collected_by, ?),
               received_at=COALESCE(received_at, ?),
               received_by=COALESCE(received_by, ?),
               in_lab_at=COALESCE(in_lab_at, ?),
               in_lab_by=COALESCE(in_lab_by, ?)
         WHERE id=?
        """,
        (
            result,
            now,
            user,
            now,
            user,
            now,
            user,
            now,
            user,
            rid,
        ),
    )
    db.commit()

    log_action("LAB_REPORT", visit_id=row["visit_id"], details=f"RID={rid}")
    flash("Lab result saved.", "success")
    return redirect(next_url)


@app.route("/lab_request/<int:rid>/upload_file", methods=["POST"])
@login_required
@role_required("lab", "admin")
def lab_upload_result_file(rid):
    """
    Upload lab result file (PDF / image) linked to this lab request & visit.
    """
    next_url = request.referrer or url_for("lab_board")

    if "file" not in request.files:
        flash("No file selected.", "danger")
        return redirect(next_url)

    file = request.files["file"]
    if file.filename == "":
        flash("Please choose a file.", "danger")
        return redirect(next_url)

    if not allowed_file(file.filename):
        flash("Invalid file type.", "danger")
        return redirect(next_url)

    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(next_url)

    visit_id = row["visit_id"]
    safe_name = secure_filename(file.filename)
    filename = f"{visit_id}_LAB_{rid}_{int(datetime.now().timestamp())}_{safe_name}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))

    db.execute(
        """
        INSERT INTO attachments (visit_id, filename, uploaded_at, uploaded_by)
        VALUES (?,?,?,?)
        """,
        (
            visit_id,
            filename,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session.get("username", "UNKNOWN"),
        ),
    )
    db.commit()

    log_action("LAB_UPLOAD_FILE", visit_id=visit_id, details=f"RID={rid}|{filename}")
    flash("Lab result file uploaded successfully.", "success")
    return redirect(next_url)



@app.route("/lab_request/<int:rid>/delete", methods=["POST"])
@login_required
@role_required("doctor", "lab", "admin")
def lab_delete_request(rid):
    """
    Delete a single lab request for a visit.
    Only allowed if the request is not yet REPORTED.
    """
    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM lab_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Lab request not found.", "danger")
        return redirect(request.referrer or url_for("lab_board"))

    visit_id = row["visit_id"]
    status = (row["status"] or "").upper()

    if status == "REPORTED":
        flash("Cannot delete a REPORTED lab result.", "warning")
        return redirect(request.referrer or url_for("clinical_orders_page", visit_id=visit_id))

    cur.execute("DELETE FROM lab_requests WHERE id=?", (rid,))
    db.commit()

    try:
        log_action("LAB_DELETE", visit_id=visit_id, details=f"RID={rid}")
    except Exception:
        pass

    flash("Lab request deleted.", "success")
    next_url = request.referrer or url_for("clinical_orders_page", visit_id=visit_id)
    return redirect(next_url)


@app.route("/radiology_request/<int:rid>/upload_file", methods=["POST"])
@login_required
@role_required("radiology", "admin")
def radiology_upload_result_file(rid):
    """
    Upload radiology result file (PDF / image) linked to this radiology request & visit.
    """
    if "file" not in request.files:
        flash("No file selected.", "danger")
        return redirect(url_for("radiology_board"))

    file = request.files["file"]
    if file.filename == "":
        flash("Please choose a file.", "danger")
        return redirect(url_for("radiology_board"))

    if not allowed_file(file.filename):
        flash("Invalid file type.", "danger")
        return redirect(url_for("radiology_board"))

    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM radiology_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Radiology request not found.", "danger")
        return redirect(url_for("radiology_board"))

    visit_id = row["visit_id"]
    safe_name = secure_filename(file.filename)
    filename = f"{visit_id}_RAD_{rid}_{int(datetime.now().timestamp())}_{safe_name}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))

    db.execute(
        """
        INSERT INTO attachments (visit_id, filename, uploaded_at, uploaded_by)
        VALUES (?,?,?,?)
        """,
        (
            visit_id,
            filename,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session.get("username", "UNKNOWN"),
        ),
    )
    db.commit()

    log_action("RAD_UPLOAD_FILE", visit_id=visit_id, details=f"RID={rid}|{filename}")
    flash("Radiology result file uploaded successfully.", "success")
    return redirect(url_for("radiology_board"))


@app.route("/radiology_request/<int:rid>/delete", methods=["POST"])
@login_required
@role_required("doctor", "radiology", "admin")
def radiology_delete_request(rid):
    """
    Delete a single radiology request for a visit.
    Only allowed if the request is not yet REPORTED.
    """
    db = get_db()
    cur = db.cursor()
    row = cur.execute("SELECT * FROM radiology_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Radiology request not found.", "danger")
        return redirect(request.referrer or url_for("radiology_board"))

    visit_id = row["visit_id"]
    status = (row["status"] or "").upper()

    if status == "REPORTED":
        flash("Cannot delete a REPORTED radiology report.", "warning")
        return redirect(request.referrer or url_for("clinical_orders_page", visit_id=visit_id))

    cur.execute("DELETE FROM radiology_requests WHERE id=?", (rid,))
    db.commit()

    try:
        log_action("RAD_DELETE", visit_id=visit_id, details=f"RID={rid}")
    except Exception:
        pass

    flash("Radiology request deleted.", "success")
    next_url = request.referrer or url_for("clinical_orders_page", visit_id=visit_id)
    return redirect(next_url)


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
    Adds simple ageing (minutes since requested) and small summary counters.
    """
    status_filter = request.args.get("status", "ALL")
    q = request.args.get("q", "").strip()
    dfrom = request.args.get("date_from", "").strip()
    dto = request.args.get("date_to", "").strip()

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
        # Unknown value -> fall back to ALL (no extra filter)
        status_filter = "ALL"

    # Date filters based on requested_at
    if dfrom:
        sql += " AND date(rr.requested_at) >= date(?)"
        params.append(dfrom)
    if dto:
        sql += " AND date(rr.requested_at) <= date(?)"
        params.append(dto)

    # Text search
    if q:
        like = f"%{q}%"
        sql += " AND (p.name LIKE ? OR p.id_number LIKE ? OR v.visit_id LIKE ? OR rr.test_name LIKE ?)"
        params.extend([like, like, like, like])

    # Order: pending first, oldest requested_at first within each group
    sql += (
        " ORDER BY "
        " CASE WHEN rr.status IN ('REQUESTED','DONE') THEN 0 ELSE 1 END, "
        " datetime(rr.requested_at) ASC, rr.id ASC"
        " LIMIT 500"
    )
    rows_raw = cur.execute(sql, params).fetchall()

    rows = []
    status_counts = {}
    modality_counts = {}
    pending_count = 0
    reported_count = 0

    for r in rows_raw:
        d = dict(r)
        st = (d.get("status") or "").upper().strip()
        status_counts[st] = status_counts.get(st, 0) + 1

        if st in ("REQUESTED", "DONE"):
            pending_count += 1
        elif st == "REPORTED":
            reported_count += 1

        # Age / TAT in minutes
        req_ts = d.get("requested_at")
        rep_ts = d.get("reported_at")
        if st == "REPORTED" and rep_ts:
            mins = calc_minutes_between(req_ts, rep_ts)
        else:
            mins = calc_minutes_between(req_ts, None)
        d["age_minutes"] = mins

        if mins is None:
            d["age_text"] = "-"
            d["age_level"] = "none"
        else:
            try:
                mins_int = int(mins)
            except Exception:
                mins_int = mins

            # Human readable
            if isinstance(mins_int, int) and mins_int >= 60:
                h = mins_int // 60
                m = mins_int % 60
                if h >= 24:
                    days = h // 24
                    rem_h = h % 24
                    d["age_text"] = f"{days}d {rem_h}h"
                else:
                    d["age_text"] = f"{h}h {m:02d}m"
            else:
                d["age_text"] = f"{mins_int}m"

            # Age level for simple coloring
            if mins_int is None:
                level = "none"
            elif mins_int < 30:
                level = "short"
            elif mins_int < 60:
                level = "medium"
            elif mins_int < 120:
                level = "long"
            else:
                level = "verylong"
            d["age_level"] = level

        # Simple modality grouping from test_name
        tname = (d.get("test_name") or "").lower()
        modality = "Other"
        if "mri" in tname:
            modality = "MRI"
        elif "ct" in tname:
            modality = "CT"
        elif "ultrasound" in tname or " u/s" in tname or "u/s " in tname or " us " in tname:
            modality = "US"
        elif "xray" in tname or "x-ray" in tname or " xray" in tname or " cxr" in tname or " chest x" in tname:
            modality = "XR"
        d["modality"] = modality
        modality_counts[modality] = modality_counts.get(modality, 0) + 1

        rows.append(d)

    return render_template(
        "radiology_board.html",
        rows=rows,
        status_filter=status_filter,
        q=q,
        date_from=dfrom,
        date_to=dto,
        status_counts=status_counts,
        modality_counts=modality_counts,
        pending_count=pending_count,
        reported_count=reported_count,
    )



@app.route("/radiology_request/<int:rid>/done", methods=["POST"])
@login_required
@role_required("radiology", "nurse", "admin")
def radiology_mark_done(rid):
    """
    Mark that radiology study has been done.
    """
    db = get_db()
    cur = db.cursor()
    next_url = request.referrer or url_for("radiology_board")

    row = cur.execute("SELECT * FROM radiology_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        flash("Radiology request not found.", "danger")
        return redirect(next_url)

    if row["status"] != "REQUESTED":
        flash("Cannot mark as done in current status.", "warning")
        return redirect(next_url)

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
    return redirect(next_url)


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
        SELECT v.visit_id,
               p.name,
               p.id_number,
               p.insurance,
               p.dob AS dob,
               v.created_at,
               v.created_by,
               v.closed_by
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

    # Age for header
    age = calc_age(visit["dob"]) if "dob" in visit.keys() else ""

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Auto ED Course Summary")
    y -= 1.0*cm

    # Header block
    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit ID: {visit['visit_id']}")
    y -= 0.7*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Age: {age or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"ID: {visit['id_number'] or '-'}    INS: {visit['insurance'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Date: {visit['created_at']}")
    y -= 0.9*cm

    # Separator line
    c.line(2*cm, y, width - 2*cm, y)
    y -= 0.7*cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Summary:")
    y -= 0.6*cm

    c.setFont("Helvetica", 9)
    if not auto_text.strip():
        c.drawString(2.2*cm, y, "None")
    else:
        y = draw_wrapped_lines(
            c,
            auto_text,
            x=2.2*cm,
            y=y,
            max_chars=105,
            line_height=0.42*cm,
            page_height=height,
            font_name="Helvetica",
            font_size=9,
        )

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



def _resolve_doctor_name_for_visit(cur, visit_row, visit_id):
    """
    Try to determine the doctor username for this visit.

    Priority:
      1) discharge_summaries.updated_by / created_by
      2) clinical_orders.updated_by / created_by (latest row)
      3) visits.closed_by / created_by

    Only returns usernames whose users.role is 'doctor' or 'admin'
    (and is_active=1). If no matching doctor is found, returns "".
    """

    def _add_candidates_from_row(row, field_names, dest_list):
        if not row:
            return
        for fname in field_names:
            val = ""
            # Row may be a sqlite3.Row (dict-like) or tuple
            if isinstance(row, sqlite3.Row):
                try:
                    val = (row.get(fname) or "").strip()
                except Exception:
                    # Some sqlite3.Row versions do not implement .get()
                    try:
                        val = (row[fname] or "").strip()
                    except Exception:
                        val = ""
            else:
                # Fallback: try using column order if it is a tuple
                try:
                    idx = field_names.index(fname)
                    val = (row[idx] or "").strip()
                except Exception:
                    val = ""
            if val:
                dest_list.append(val)

    candidates = []

    # 1) Prefer explicit discharge summary doctor if available
    try:
        ds_row = cur.execute(
            "SELECT updated_by, created_by FROM discharge_summaries WHERE visit_id=?",
            (visit_id,),
        ).fetchone()
    except Exception:
        ds_row = None

    _add_candidates_from_row(ds_row, ["updated_by", "created_by"], candidates)

    # 2) Latest clinical_orders row (often entered by doctor)
    try:
        co_row = cur.execute(
            """
            SELECT updated_by, created_by
            FROM clinical_orders
            WHERE visit_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (visit_id,),
        ).fetchone()
    except Exception:
        co_row = None

    _add_candidates_from_row(co_row, ["updated_by", "created_by"], candidates)

    # 3) Fallback to visit closed_by / created_by
    if visit_row is not None:
        for fname in ("closed_by", "created_by"):
            try:
                val = (visit_row[fname] or "").strip()
            except Exception:
                val = ""
            if val:
                candidates.append(val)

    # Normalise, drop blanks / UNKNOWN, remove duplicates preserving order
    cleaned = []
    seen = set()
    for name in candidates:
        name = (name or "").strip()
        if not name:
            continue
        if name.upper() == "UNKNOWN":
            continue
        if name in seen:
            continue
        seen.add(name)
        cleaned.append(name)

    if not cleaned:
        return ""

    # Only accept real doctors/admins based on users table
    for uname in cleaned:
        try:
            user_row = cur.execute(
                "SELECT role FROM users WHERE username=? AND is_active=1",
                (uname,),
            ).fetchone()
        except Exception:
            user_row = None

        role = ""
        if user_row is not None:
            try:
                # sqlite3.Row supports dict-style access
                role = (user_row["role"] or "").lower()
            except Exception:
                try:
                    role = (user_row[0] or "").lower()
                except Exception:
                    role = ""

        if role in ("doctor", "admin"):
            return uname

    # No valid doctor/admin found -> return empty so PDF shows blank line
    return ""


def _doctor_display_with_gd(cur, doctor_name):
    """
    Return doctor display label (with GD number if available).

    If doctor_name is empty OR the user is not a doctor/admin,
    we return just a blank signature line ("______________________")
    instead of a wrong staff name (e.g. reception).
    """
    if not doctor_name:
        return "______________________"

    try:
        user_row = cur.execute(
            "SELECT role, gd_number FROM users WHERE username=? AND is_active=1",
            (doctor_name,),
        ).fetchone()
    except Exception:
        user_row = None

    if not user_row:
        # Username not found in users table – safer to leave it blank
        return "______________________"

    # Role check
    try:
        role = (user_row["role"] or "").lower()
    except Exception:
        try:
            role = (user_row[0] or "").lower()
        except Exception:
            role = ""

    if role not in ("doctor", "admin"):
        # Do not show reception / nurse names as "Doctor"
        return "______________________"

    # Extract GD number
    gd_value = None
    try:
        gd_value = user_row["gd_number"]
    except Exception:
        try:
            gd_value = user_row[1]
        except Exception:
            gd_value = None

    label = doctor_name
    if gd_value:
        label = f"{doctor_name}  (GD: {gd_value})"

    return label

@app.route("/patient_summary/<visit_id>/pdf")
@login_required
def patient_summary_pdf(visit_id):
    """Patient-friendly short summary PDF without internal staff details.
       This version also prints doctor name and signature line."""
    db = get_db()
    cur = db.cursor()

    # Visit + patient basic info
    visit = cur.execute(
        """
        SELECT v.visit_id,
               p.name,
               p.id_number,
               p.insurance,
               p.dob AS dob,
               v.created_at,
               v.created_by,
               v.closed_by
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_id = ?
        """,
        (visit_id,),
    ).fetchone()

    if not visit:
        return "Not found", 404

    # Resolve doctor name + GD number once for this visit
    doctor_name = _resolve_doctor_name_for_visit(cur, visit, visit_id)
    doctor_display = _doctor_display_with_gd(cur, doctor_name)

    # Patient-friendly course summary text
    text = build_patient_short_summary(visit_id)

    # Build PDF
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2 * cm

    # Compute age for header (if DOB valid)
    age = calc_age(visit["dob"]) if "dob" in visit.keys() else ""

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "ED Visit Summary - Patient Copy")
    y -= 1.0 * cm

    # Header block
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, f"Visit ID: {visit['visit_id']}")
    y -= 0.7 * cm
    c.drawString(2 * cm, y, f"Patient: {visit['name']}")
    y -= 0.6 * cm
    c.drawString(2 * cm, y, f"Age: {age or '-'}")
    y -= 0.6 * cm
    c.drawString(
        2 * cm,
        y,
        f"ID: {visit['id_number'] or '-'}    INS: {visit['insurance'] or '-'}",
    )
    y -= 0.6 * cm
    c.drawString(2 * cm, y, f"Date: {visit['created_at']}")
    y -= 0.9 * cm

    # Separator line before the body text
    c.line(2 * cm, y, width - 2 * cm, y)
    y -= 0.7 * cm

    c.setFont("Helvetica", 10)
    y = draw_wrapped_lines(
        c,
        text or "-",
        x=2 * cm,
        y=y,
        max_chars=100,
        line_height=0.40 * cm,
        page_height=height,
        font_name="Helvetica",
        font_size=10,
    )

    # Doctor name & signature line near bottom
    sig_y = 2.0 * cm
    c.setFont("Helvetica", 9)
    c.drawString(
        2 * cm,
        sig_y,
        f"Doctor: {doctor_display}",
    )
    c.drawString(width / 2, sig_y, "Signature: ______________________")

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2 * cm, 1.2 * cm, APP_FOOTER_TEXT)

    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=patient_short_summary.pdf"},
    )


@app.route("/triage/<visit_id>/pdf")
@login_required
@role_required("nurse","doctor","admin")
def triage_pdf(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.*,
               p.name, p.id_number, p.phone,
               p.insurance, p.insurance_no,
               p.dob, p.sex, p.nationality
        FROM visits v
        JOIN patients p ON p.id=v.patient_id
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    # Try to fetch an ID/eligibility attachment for this visit (non-lab/rad)
    id_image_path = None
    try:
        att = cur.execute(
            """
            SELECT filename
              FROM attachments
             WHERE visit_id=?
               AND filename NOT LIKE '%_LAB_%'
               AND filename NOT LIKE '%_RAD_%'
             ORDER BY uploaded_at ASC
             LIMIT 1
            """,
            (visit_id,),
        ).fetchone()
        if att and att["filename"]:
            candidate = os.path.join(UPLOAD_FOLDER, att["filename"])
            if os.path.exists(candidate):
                id_image_path = candidate
    except Exception:
        id_image_path = None

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    # Draw patient ID image (if any) in the top-right corner
    # مكبر بصورة أوضح مع تناسق أفضل في ورقة الترياج
    if id_image_path:
        try:
            # حجم أكبر للهوية (أعرض وأطول قليلاً ليكون النص واضحًا)
            img_w = 7*cm
            img_h = 4.5*cm
            c.drawImage(
                id_image_path,
                width - img_w - 2*cm,
                height - img_h - 2*cm,
                width=img_w,
                height=img_h,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            # If the image cannot be rendered (e.g. non-image file), ignore silently
            pass

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "ED Triage Sheet")
    y -= 1.0*cm

    c.setFont("Helvetica", 11)
    c.drawString(2*cm, y, f"Visit: {visit['visit_id']}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Patient: {visit['name']}   ID: {visit['id_number'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Phone: {visit['phone'] or '-'}   Insurance: {visit['insurance'] or '-'}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"DOB: {visit['dob'] or '-'}   Sex: {visit['sex'] or '-'}   Nationality: {visit['nationality'] or '-'}")
    y -= 1.0*cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2*cm, y, f"Triage Category: {visit['triage_cat'] or '-'}")
    y -= 0.5*cm

    # Triage time (when vitals were taken); falls back to visit creation time
    c.setFont("Helvetica", 10)
    try:
        triage_time = visit["triage_time"]
    except Exception:
        triage_time = None
    if not triage_time:
        try:
            triage_time = visit["created_at"]
        except Exception:
            triage_time = "-"
    c.drawString(2*cm, y, f"Triage Time: {triage_time or '-'}")
    y -= 0.8*cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "Patient's Complaint:")
    y -= 0.4*cm
    y = draw_wrapped_lines(
        c,
        visit["comment"] or "-",
        x=2*cm,
        y=y,
        max_chars=100,
        line_height=0.4*cm,
        page_height=height,
        font_name="Helvetica",
        font_size=9,
    )
    y -= 0.6*cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "Allergy:")
    y -= 0.4*cm
    c.setFont("Helvetica", 9)
    allergy_status = visit["allergy_status"] or "-"
    c.drawString(2*cm, y, f"Status: {allergy_status}")
    y -= 0.4*cm
    if visit["allergy_details"]:
        y = draw_wrapped_lines(
            c,
            visit["allergy_details"],
            x=2*cm,
            y=y,
            max_chars=100,
            line_height=0.4*cm,
            page_height=height,
            font_name="Helvetica",
            font_size=9,
        )
        y -= 0.4*cm
    y -= 0.2*cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "Vital Signs:")
    y -= 0.5*cm
    c.setFont("Helvetica", 9)

    lines = [
        f"Pulse: {visit['pulse_rate'] or '-'} bpm   Resp: {visit['resp_rate'] or '-'} /min   Temp: {visit['temperature'] or '-'} °C",
        f"BP: {visit['bp_systolic'] or '-'} / {visit['bp_diastolic'] or '-'} mmHg   SpO₂: {visit['spo2'] or '-'} %",
        f"Pain Score: {visit['pain_score'] or '-'} /10   Weight: {visit['weight'] or '-'} kg   Height: {visit['height'] or '-'} cm",
        f"Level of Consciousness: {visit['consciousness_level'] or '-'}",
    ]

    for line in lines:
        c.drawString(2*cm, y, line[:130])
        y -= 0.4*cm
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
        headers={"Content-Disposition": "inline; filename=triage.pdf"}
    )

@app.route("/lab_results/<visit_id>/pdf")
@login_required
@role_required("nurse","doctor","admin","lab","radiology")
def lab_results_pdf(visit_id):
    db = get_db()
    cur = db.cursor()

    visit = cur.execute("""
        SELECT v.visit_id,
               p.name,
               p.id_number,
               p.insurance,
               v.created_at,
               v.created_by,
               v.closed_by
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
            abn = False
            if l["result_text"]:
                status_line += f" | Result: {l['result_text']}"
                rt = str(l["result_text"]).lower()
                if ("high" in rt or "low" in rt or "crit" in rt or "abnormal" in rt
                    or "positive" in rt or "pos " in rt or "pos." in rt
                    or "مرتفع" in rt or "منخفض" in rt or "ايجابي" in rt or "إيجابي" in rt):
                    abn = True
            # Color abnormal results in red
            if abn:
                c.setFillColor(colors.red)
            else:
                c.setFillColor(colors.black)
            c.drawString(2*cm, y, status_line[:130])
            # Reset for the rest of the page
            c.setFillColor(colors.black)
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
@role_required("nurse","doctor","admin","lab","radiology")
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
                report_lines = (r["report_text"] or "").splitlines()
                if report_lines:
                    c.setFont("Helvetica-Bold", 9)
                    c.drawString(2*cm, y, "Report:")
                    y -= 0.35*cm
                    c.setFont("Helvetica", 9)
                    first = True
                    for line in report_lines:
                        line = line.rstrip()
                        if not line:
                            y -= 0.3*cm
                        else:
                            prefix = "" if first else "• "
                            c.drawString(2.3*cm, y, (prefix + line)[:120])
                            first = False
                            y -= 0.3*cm
                        if y < 2*cm:
                            c.showPage()
                            y = height - 2*cm
                            c.setFont("Helvetica", 9)

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

    # 1) نجيب بيانات الزيارة + الحقول اللازمة لاستخراج الطبيب (created_by / closed_by)
    visit = cur.execute("""
        SELECT v.visit_id,
               p.name,
               p.id_number,
               p.insurance,
               v.created_at,
               v.created_by,
               v.closed_by
        FROM visits v
        JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_id = ?
    """, (visit_id,)).fetchone()

    # 2) نجيب نص الـ Home Medication من discharge_summaries
    summary = cur.execute("""
        SELECT home_medication
        FROM discharge_summaries
        WHERE visit_id = ?
    """, (visit_id,)).fetchone()

    if not visit:
        return "Not found", 404

    # 3) استخراج اسم الطبيب + GD باستخدام الهيلبرز الموجودة
    doctor_name = _resolve_doctor_name_for_visit(cur, visit, visit_id)
    doctor_display = _doctor_display_with_gd(cur, doctor_name)

    home_med = summary["home_medication"] if summary and summary["home_medication"] else ""

    # 4) بناء الـ PDF
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2 * cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "Home Medication")
    y -= 1.0 * cm

    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, f"Visit ID: {visit['visit_id']}")
    y -= 0.7 * cm
    c.drawString(2 * cm, y, f"Patient: {visit['name']}")
    y -= 0.6 * cm
    c.drawString(
        2 * cm,
        y,
        f"ID: {visit['id_number'] or '-'}    INS: {visit['insurance'] or '-'}",
    )
    y -= 0.6 * cm
    c.drawString(2 * cm, y, f"Date: {visit['created_at']}")
    y -= 1.0 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Medication:")
    y -= 0.6 * cm

    c.setFont("Helvetica", 10)
    if not home_med.strip():
        c.drawString(2.2 * cm, y, "None")
    else:
        y = draw_wrapped_lines(
            c,
            home_med,
            x=2.2 * cm,
            y=y,
            max_chars=95,
            line_height=0.45 * cm,
            page_height=height,
            font_name="Helvetica",
            font_size=10,
        )

    # 5) سطر الطبيب + رقم GD + التوقيع (نفس أسلوب Patient Summary)
    sig_y = 2.0 * cm
    c.setFont("Helvetica", 9)
    c.drawString(2 * cm, sig_y, f"Doctor: {doctor_display}")
    c.drawString(width / 2, sig_y, "Signature: ______________________")

    # 6) الفوتر
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(2 * cm, 1.2 * cm, APP_FOOTER_TEXT)

    c.showPage()
    c.save()
    buffer.seek(0)

    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "inline; filename=home_medication.pdf"},
    )

# ============================================================
# Sticker HTML + ZPL
# ============================================================

@app.route("/sticker/<visit_id>")
@login_required
def sticker_html(visit_id):
    cur=get_db().cursor()
    v=cur.execute("""
        SELECT v.visit_id, v.queue_no, v.created_at, v.created_by, p.name, p.id_number, p.insurance, p.dob as dob
        FROM visits v JOIN patients p ON p.id=v.patient_id WHERE v.visit_id=?
    """,(visit_id,)).fetchone()
    if not v: return "Not found",404
    time_only=v["created_at"][11:16]
    # Age for sticker
    try:
        age = calc_age(v["dob"])
    except Exception:
        age = ""
    return render_template("sticker.html", v=v, time_only=time_only, age=age)

@app.route("/sticker/<visit_id>/zpl")
@login_required
def sticker_zpl(visit_id):
    cur = get_db().cursor()
    v = cur.execute("""
        SELECT v.created_at, p.name, p.id_number, p.insurance, p.dob as dob
        FROM visits v 
        JOIN patients p ON p.id=v.patient_id 
        WHERE v.visit_id=?
    """, (visit_id,)).fetchone()

    if not v:
        return "Not Found", 404

    # Extract time only
    t = v["created_at"][11:16]

    # Age for ZPL sticker
    try:
        age = calc_age(v["dob"])
    except Exception:
        age = ""

    # لو مفيش ID نستخدم visit_id في الباركود
    id_for_barcode = v["id_number"] or visit_id

    # ZPL 5x3 cm label مع Barcode فقط
    zpl = f"""
^XA
^PW400
^LL300

^CF0,22
^FO20,10^FDED DOWNTIME^FS

^CF0,20
^FO20,40^FDNAME: {v['name']}^FS
^FO20,70^FDAGE: {age or '-'}^FS
^FO20,100^FDID: {v['id_number'] or '-'}^FS
^FO20,130^FDINS: {v['insurance'] or '-'}^FS
^FO20,160^FDTIME: {t}^FS

^BY2,2,40
^FO20,200^BCN,40,Y,N,N^FD{id_for_barcode}^FS

^XZ
"""
    return Response(zpl, mimetype="text/plain")
# ============================================================
# Templates (Single-file)
# ============================================================

TEMPLATES = {'admin_items.html': '\n'
                     '{% extends "base.html" %}\n'
                     '{% block content %}\n'
                     '<h4 class="mb-3">Admin - Orders Items (Meds / Labs / Radiology / Home Medication)</h4>\n'
                     '\n'
                     '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                     '  {% if messages %}\n'
                     '    {% for category, msg in messages %}\n'
                     '      <div class="alert alert-{{ category }} mb-2">{{ msg }}</div>\n'
                     '    {% endfor %}\n'
                     '  {% endif %}\n'
                     '{% endwith %}\n'
                     '\n'
                     '<div class="row g-3">\n'
                     '  <!-- Radiology items -->\n'
                     '  <div class="col-lg-3 col-md-6">\n'
                     '    <div class="card bg-white p-3">\n'
                     '      <h6 class="fw-bold mb-2">Radiology Items</h6>\n'
                     '      <form method="POST" class="mb-2 d-flex gap-2">\n'
                     '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '        <input type="hidden" name="kind" value="rad">\n'
                     '        <input type="text" name="name" class="form-control form-control-sm" placeholder="Add '
                     'radiology test">\n'
                     '        <button class="btn btn-sm btn-primary" name="action" value="add">Add</button>\n'
                     '      </form>\n'
                     '      <div style="max-height: 320px; overflow:auto;">\n'
                     '        {% if rads %}\n'
                     '          {% for item in rads %}\n'
                     '            <form method="POST" class="d-flex align-items-center gap-2 mb-1">\n'
                     '              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '              <input type="hidden" name="kind" value="rad">\n'
                     '              <input type="hidden" name="item_id" value="{{ item.id }}">\n'
                     '              <input type="text" name="name" class="form-control form-control-sm" value="{{ '
                     'item.name }}">\n'
                     '              <button class="btn btn-sm btn-outline-primary" name="action" value="rename" '
                     'title="Save name">💾</button>\n'
                     '              <button class="btn btn-sm btn-outline-secondary" name="action" value="toggle" '
                     'title="Toggle active">\n'
                     '                {% if item.is_active %}Disable{% else %}Enable{% endif %}\n'
                     '              </button>\n'
                     '              <button class="btn btn-sm btn-outline-danger" name="action" value="delete"\n'
                     '                      onclick="return confirm(\'Delete this item?\');" '
                     'title="Delete">🗑</button>\n'
                     '            </form>\n'
                     '          {% endfor %}\n'
                     '        {% else %}\n'
                     '          <div class="text-muted small">No radiology items yet.</div>\n'
                     '        {% endif %}\n'
                     '      </div>\n'
                     '    </div>\n'
                     '  </div>\n'
                     '\n'
                     '  <!-- Lab items -->\n'
                     '  <div class="col-lg-3 col-md-6">\n'
                     '    <div class="card bg-white p-3">\n'
                     '      <h6 class="fw-bold mb-2">Lab Items</h6>\n'
                     '      <form method="POST" class="mb-2 d-flex gap-2">\n'
                     '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '        <input type="hidden" name="kind" value="lab">\n'
                     '        <input type="text" name="name" class="form-control form-control-sm" placeholder="Add lab '
                     'test">\n'
                     '        <button class="btn btn-sm btn-primary" name="action" value="add">Add</button>\n'
                     '      </form>\n'
                     '      <div style="max-height: 320px; overflow:auto;">\n'
                     '        {% if labs %}\n'
                     '          {% for item in labs %}\n'
                     '            <form method="POST" class="d-flex align-items-center gap-2 mb-1">\n'
                     '              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '              <input type="hidden" name="kind" value="lab">\n'
                     '              <input type="hidden" name="item_id" value="{{ item.id }}">\n'
                     '              <input type="text" name="name" class="form-control form-control-sm" value="{{ '
                     'item.name }}">\n'
                     '              <button class="btn btn-sm btn-outline-primary" name="action" value="rename" '
                     'title="Save name">💾</button>\n'
                     '              <button class="btn btn-sm btn-outline-secondary" name="action" value="toggle" '
                     'title="Toggle active">\n'
                     '                {% if item.is_active %}Disable{% else %}Enable{% endif %}\n'
                     '              </button>\n'
                     '              <button class="btn btn-sm btn-outline-danger" name="action" value="delete"\n'
                     '                      onclick="return confirm(\'Delete this item?\');" '
                     'title="Delete">🗑</button>\n'
                     '            </form>\n'
                     '          {% endfor %}\n'
                     '        {% else %}\n'
                     '          <div class="text-muted small">No lab items yet.</div>\n'
                     '        {% endif %}\n'
                     '      </div>\n'
                     '    </div>\n'
                     '  </div>\n'
                     '\n'
                     '  <!-- Medication items (ED orders) -->\n'
                     '  <div class="col-lg-3 col-md-6">\n'
                     '    <div class="card bg-white p-3">\n'
                     '      <h6 class="fw-bold mb-2">Medication Items</h6>\n'
                     '      <form method="POST" class="mb-2 d-flex gap-2">\n'
                     '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '        <input type="hidden" name="kind" value="med">\n'
                     '        <input type="text" name="name" class="form-control form-control-sm" placeholder="Add '
                     'medication">\n'
                     '        <button class="btn btn-sm btn-primary" name="action" value="add">Add</button>\n'
                     '      </form>\n'
                     '      <div style="max-height: 320px; overflow:auto;">\n'
                     '        {% if meds %}\n'
                     '          {% for item in meds %}\n'
                     '            <form method="POST" class="d-flex align-items-center gap-2 mb-1">\n'
                     '              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '              <input type="hidden" name="kind" value="med">\n'
                     '              <input type="hidden" name="item_id" value="{{ item.id }}">\n'
                     '              <input type="text" name="name" class="form-control form-control-sm" value="{{ '
                     'item.name }}">\n'
                     '              <button class="btn btn-sm btn-outline-primary" name="action" value="rename" '
                     'title="Save name">💾</button>\n'
                     '              <button class="btn btn-sm btn-outline-secondary" name="action" value="toggle" '
                     'title="Toggle active">\n'
                     '                {% if item.is_active %}Disable{% else %}Enable{% endif %}\n'
                     '              </button>\n'
                     '              <button class="btn btn-sm btn-outline-danger" name="action" value="delete"\n'
                     '                      onclick="return confirm(\'Delete this item?\');" '
                     'title="Delete">🗑</button>\n'
                     '            </form>\n'
                     '          {% endfor %}\n'
                     '        {% else %}\n'
                     '          <div class="text-muted small">No medication items yet.</div>\n'
                     '        {% endif %}\n'
                     '      </div>\n'
                     '    </div>\n'
                     '  </div>\n'
                     '\n'
                     '  <!-- Home Medication templates for Discharge Summary -->\n'
                     '  <div class="col-lg-3 col-md-6">\n'
                     '    <div class="card bg-white p-3">\n'
                     '      <h6 class="fw-bold mb-2">Home Medication Templates</h6>\n'
                     '      <p class="small text-muted mb-2">\n'
                     '        These items appear as suggestions in the <strong>Home Medication</strong> box in '
                     'Discharge Summary.\n'
                     '      </p>\n'
                     '      <form method="POST" class="mb-2 d-flex gap-2">\n'
                     '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '        <input type="hidden" name="kind" value="home">\n'
                     '        <input type="text" name="name" class="form-control form-control-sm" placeholder="Add '
                     'home med template">\n'
                     '        <button class="btn btn-sm btn-primary" name="action" value="add">Add</button>\n'
                     '      </form>\n'
                     '      <div style="max-height: 320px; overflow:auto;">\n'
                     '        {% if home_meds %}\n'
                     '          {% for item in home_meds %}\n'
                     '            <form method="POST" class="d-flex align-items-center gap-2 mb-1">\n'
                     '              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '              <input type="hidden" name="kind" value="home">\n'
                     '              <input type="hidden" name="item_id" value="{{ item.id }}">\n'
                     '              <input type="text" name="name" class="form-control form-control-sm" value="{{ '
                     'item.name }}">\n'
                     '              <button class="btn btn-sm btn-outline-primary" name="action" value="rename" '
                     'title="Save name">💾</button>\n'
                     '              <button class="btn btn-sm btn-outline-secondary" name="action" value="toggle" '
                     'title="Toggle active">\n'
                     '                {% if item.is_active %}Disable{% else %}Enable{% endif %}\n'
                     '              </button>\n'
                     '              <button class="btn btn-sm btn-outline-danger" name="action" value="delete"\n'
                     '                      onclick="return confirm(\'Delete this item?\');" '
                     'title="Delete">🗑</button>\n'
                     '            </form>\n'
                     '          {% endfor %}\n'
                     '        {% else %}\n'
                     '          <div class="text-muted small">No home medication templates yet.</div>\n'
                     '        {% endif %}\n'
                     '      </div>\n'
                     '    </div>\n'
                     '  </div>\n'
                     '</div>\n'
                     '\n'
                     '{% endblock %}\n',
 'admin_logs.html': '\n'
                    '{% extends "base.html" %}\n'
                    '{% block content %}\n'
                    '<h4 class="mb-3">Activity Logs (Last 1000)</h4>\n'
                    '\n'
                    '<form method="GET" class="card p-3 mb-3 bg-white">\n'
                    '  <div class="row g-2 align-items-end">\n'
                    '    <div class="col-md-3">\n'
                    '      <label class="form-label fw-bold">Visit ID</label>\n'
                    '      <input class="form-control" name="visit_id" value="{{ visit_f or \'\' }}" '
                    'placeholder="visit id">\n'
                    '    </div>\n'
                    '    <div class="col-md-3">\n'
                    '      <label class="form-label fw-bold">User</label>\n'
                    '      <select class="form-select" name="user">\n'
                    '        <option value="">ALL</option>\n'
                    '        {% for u in users %}\n'
                    '          <option value="{{u.username}}" {% if user_f==u.username %}selected{% endif '
                    '%}>{{u.username}}</option>\n'
                    '        {% endfor %}\n'
                    '      </select>\n'
                    '    </div>\n'
                    '    <div class="col-md-2">\n'
                    '      <label class="form-label fw-bold">From</label>\n'
                    '      <input class="form-control" type="date" name="date_from" value="{{ dfrom or \'\' }}">\n'
                    '    </div>\n'
                    '    <div class="col-md-2">\n'
                    '      <label class="form-label fw-bold">To</label>\n'
                    '      <input class="form-control" type="date" name="date_to" value="{{ dto or \'\' }}">\n'
                    '    </div>\n'
                    '    <div class="col-md-2 d-grid">\n'
                    '      <button class="btn btn-primary">Filter</button>\n'
                    '    </div>\n'
                    '  </div>\n'
                    '\n'
                    '  <div class="mt-2 d-flex gap-2">\n'
                    '    <a class="btn btn-outline-success btn-sm"\n'
                    '       href="{{ url_for(\'export_logs_csv\', visit_id=visit_f, user=user_f, date_from=dfrom, '
                    'date_to=dto) }}">Export CSV</a>\n'
                    '    <a class="btn btn-outline-danger btn-sm"\n'
                    '       href="{{ url_for(\'export_logs_pdf\', visit_id=visit_f, user=user_f, date_from=dfrom, '
                    'date_to=dto) }}">Export PDF</a>\n'
                    '  </div>\n'
                    '</form>\n'
                    '\n'
                    '<table class="table table-sm table-striped bg-white">\n'
                    '  '
                    '<thead><tr><th>Time</th><th>User</th><th>Action</th><th>Visit</th><th>Details</th></tr></thead>\n'
                    '  <tbody>\n'
                    '  {% for l in logs %}\n'
                    '    <tr>\n'
                    '      <td>{{ l.created_at }}</td>\n'
                    '      <td>{{ l.username }}</td>\n'
                    '      <td>{{ l.action }}</td>\n'
                    "      <td>{{ l.visit_id or '-' }}</td>\n"
                    "      <td>{{ l.details or '-' }}</td>\n"
                    '    </tr>\n'
                    '  {% endfor %}\n'
                    '  </tbody>\n'
                    '</table>\n'
                    '{% endblock %}\n',
 'admin_reset_password.html': '\n'
                              '{% extends "base.html" %}\n'
                              '{% block content %}\n'
                              '<h4 class="mb-3">Admin Password Reset</h4>\n'
                              '\n'
                              '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                              '  {% for category, msg in messages %}\n'
                              '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                              '  {% endfor %}\n'
                              '{% endwith %}\n'
                              '\n'
                              '<form method="POST" class="card p-3 bg-white mb-3">\n'
                              '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                              '  <div class="row g-2 align-items-end">\n'
                              '    <div class="col-md-5">\n'
                              '      <label class="form-label fw-bold">Select User</label>\n'
                              '      <select class="form-select" name="user_id" required>\n'
                              '        <option value="">-- choose --</option>\n'
                              '        {% for u in users %}\n'
                              '          <option value="{{ u.id }}">{{ u.username }} ({{ u.role }})</option>\n'
                              '        {% endfor %}\n'
                              '      </select>\n'
                              '    </div>\n'
                              '    <div class="col-md-5">\n'
                              '      <label class="form-label fw-bold">New Password</label>\n'
                              '      <input class="form-control" name="new_password" type="text" required>\n'
                              '    </div>\n'
                              '    <div class="col-md-2 d-grid">\n'
                              '      <button class="btn btn-primary">Reset</button>\n'
                              '    </div>\n'
                              '  </div>\n'
                              '</form>\n'
                              '\n'
                              '<a class="btn btn-outline-danger btn-sm" href="{{ '
                              'url_for(\'admin_reset_admin_default\') }}">\n'
                              '  Reset admin to default (admin12)\n'
                              '</a>\n'
                              '{% endblock %}\n',
 'admin_restore.html': '\n'
                       '{% extends "base.html" %}\n'
                       '{% block content %}\n'
                       '<h4 class="mb-3">Restore Database from Backup</h4>\n'
                       '\n'
                       '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                       '  {% for category, msg in messages %}\n'
                       '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                       '  {% endfor %}\n'
                       '{% endwith %}\n'
                       '\n'
                       '<div class="card p-3 bg-white">\n'
                       '  <p class="text-danger small mb-2">\n'
                       '    ⚠️ Warning: restoring a backup will overwrite the current database file.\n'
                       '    A safety copy (*.before_restore.bak) will be created automatically.\n'
                       '  </p>\n'
                       '\n'
                       '  <h6 class="fw-bold mt-2">Available backup files</h6>\n'
                       '  {% if backups %}\n'
                       '    <div class="table-responsive mb-3">\n'
                       '      <table class="table table-sm table-hover align-middle mb-0">\n'
                       '        <thead>\n'
                       '          <tr>\n'
                       '            <th>#</th>\n'
                       '            <th>File name</th>\n'
                       '            <th>Created / Modified</th>\n'
                       '            <th>Size (KB)</th>\n'
                       '            <th>Download</th>\n'
                       '            <th>Restore</th>\n'
                       '          </tr>\n'
                       '        </thead>\n'
                       '        <tbody>\n'
                       '          {% for b in backups %}\n'
                       '          <tr>\n'
                       '            <td>{{ loop.index }}</td>\n'
                       '            <td>\n'
                       '              <a href="{{ url_for(\'admin_backup_file\', filename=b.name) }}">\n'
                       '                {{ b.name }}\n'
                       '              </a>\n'
                       '            </td>\n'
                       '            <td>{{ b.mtime }}</td>\n'
                       '            <td>{{ "%.1f"|format(b.size_kb) }}</td>\n'
                       '            <td>\n'
                       '              <a class="btn btn-sm btn-outline-primary"\n'
                       '                 href="{{ url_for(\'admin_backup_file\', filename=b.name) }}">\n'
                       '                Download\n'
                       '              </a>\n'
                       '            </td>\n'
                       '            <td>\n'
                       '              <a class="btn btn-sm btn-outline-danger"\n'
                       '                 href="{{ url_for(\'admin_restore_file\', filename=b.name) }}">\n'
                       '                Restore this backup\n'
                       '              </a>\n'
                       '            </td>\n'
                       '          </tr>\n'
                       '          {% endfor %}\n'
                       '        </tbody>\n'
                       '      </table>\n'
                       '    </div>\n'
                       '  {% else %}\n'
                       '    <p class="text-muted small">No backup .db files found in the backups folder yet.</p>\n'
                       '  {% endif %}\n'
                       '\n'
                       '  <form method="POST" enctype="multipart/form-data" class="mt-2">\n'
                       '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                       '    <div class="mb-2">\n'
                       '      <label class="form-label fw-bold">Select backup .db file</label>\n'
                       '      <input type="file" name="file" class="form-control" required>\n'
                       '    </div>\n'
                       '    <button class="btn btn-danger mt-2">Restore Now</button>\n'
                       '    <a class="btn btn-secondary mt-2" href="{{ url_for(\'ed_board\') }}">Cancel</a>\n'
                       '  </form>\n'
                       '</div>\n'
                       '{% endblock %}\n',
 'admin_restore_confirm.html': '\n'
                               '{% extends "base.html" %}\n'
                               '{% block content %}\n'
                               '<h4 class="mb-3">Confirm Restore from Backup</h4>\n'
                               '\n'
                               '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                               '  {% for category, msg in messages %}\n'
                               '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                               '  {% endfor %}\n'
                               '{% endwith %}\n'
                               '\n'
                               '<div class="card p-3 bg-white">\n'
                               '  <p class="text-danger small mb-2">\n'
                               '    ⚠️ You are about to restore the database from backup file:\n'
                               '    <strong>{{ backup_name }}</strong>\n'
                               '  </p>\n'
                               '  <p class="text-muted small mb-2">\n'
                               '    This will overwrite the current database. A safety copy (*.before_restore.bak) '
                               'will be created automatically.\n'
                               '  </p>\n'
                               '\n'
                               '  <form method="POST">\n'
                               '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                               '    <div class="mb-2">\n'
                               '      <label class="form-label fw-bold">Re-enter your password to confirm</label>\n'
                               '      <input type="password" name="password" class="form-control" required autofocus>\n'
                               '    </div>\n'
                               '    <button class="btn btn-danger mt-2">Confirm Restore</button>\n'
                               '    <a class="btn btn-secondary mt-2" href="{{ url_for(\'admin_restore\') '
                               '}}">Cancel</a>\n'
                               '  </form>\n'
                               '</div>\n'
                               '{% endblock %}\n',
 'admin_users.html': '\n'
                     '{% extends "base.html" %}\n'
                     '{% block content %}\n'
                     '<h4 class="mb-3">Users Management</h4>\n'
                     '\n'
                     '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                     '  {% for category, msg in messages %}\n'
                     '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                     '  {% endfor %}\n'
                     '{% endwith %}\n'
                     '\n'
                     '<form method="POST" class="card p-3 mb-3">\n'
                     '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '  <div class="row g-2">\n'
                     '    <div class="col-md-3"><input class="form-control" name="username" '
                     'placeholder="Username"></div>\n'
                     '    <div class="col-md-3"><input class="form-control" name="password" '
                     'placeholder="Password"></div>\n'
                     '    <div class="col-md-3"><input class="form-control" name="gd_number" placeholder="GD Number / '
                     'License"></div>\n'
                     '    <div class="col-md-2">\n'
                     '      <select class="form-select" name="role">\n'
                     '        <option value="reception">reception</option>\n'
                     '        <option value="nurse">nurse</option>\n'
                     '        <option value="doctor">doctor</option>\n'
                     '        <option value="lab">lab</option>\n'
                     '        <option value="radiology">radiology</option>\n'
                     '        <option value="admin">admin</option>\n'
                     '      </select>\n'
                     '    </div>\n'
                     '    <div class="col-md-1 d-grid"><button class="btn btn-primary">Add</button></div>\n'
                     '  </div>\n'
                     '</form>\n'
                     '\n'
                     '<table class="table table-sm table-striped bg-white align-middle">\n'
                     '  <thead>\n'
                     '    <tr>\n'
                     '      <th>ID</th>\n'
                     '      <th>Username</th>\n'
                     '      <th>Role</th>\n'
                     '      <th>GD Number</th>\n'
                     '      <th>Created</th>\n'
                     '      <th>Status</th>\n'
                     '      <th style="width:180px;">Actions</th>\n'
                     '    </tr>\n'
                     '  </thead>\n'
                     '  <tbody>\n'
                     '    {% for u in users %}\n'
                     '      <tr>\n'
                     '        <td>{{ u.id }}</td>\n'
                     '        <td>{{ u.username }}</td>\n'
                     '        <td>{{ u.role }}</td>\n'
                     '        <td>{{ u.gd_number or "" }}</td>\n'
                     '        <td>{{ u.created_at }}</td>\n'
                     '        <td>\n'
                     '          {% if u.is_active %}\n'
                     '            <span class="badge bg-success">Active</span>\n'
                     '          {% else %}\n'
                     '            <span class="badge bg-secondary">Inactive</span>\n'
                     '          {% endif %}\n'
                     '        </td>\n'
                     '        <td>\n'
                     '          <div class="d-flex gap-1">\n'
                     '            <form method="POST"\n'
                     '                  action="{{ url_for(\'admin_toggle_user\', user_id=u.id) }}"\n'
                     '                  onsubmit="return confirm(\'Are you sure you want to change this user\'s active '
                     'status?\');">\n'
                     '              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                     '              {% if u.is_active %}\n'
                     '                <button class="btn btn-sm btn-outline-danger">Deactivate</button>\n'
                     '              {% else %}\n'
                     '                <button class="btn btn-sm btn-outline-success">Activate</button>\n'
                     '              {% endif %}\n'
                     '            </form>\n'
                     '          </div>\n'
                     '        </td>\n'
                     '      </tr>\n'
                     '    {% endfor %}\n'
                     '  </tbody>\n'
                     '</table>\n'
                     '{% endblock %}\n',
 'base.html': '\n'
              '<!doctype html>\n'
              '<html>\n'
              '<head>\n'
              '  <meta charset="utf-8">\n'
              '  <title>ED Downtime</title>\n'
              '  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" '
              'rel="stylesheet">\n'
              '  <meta http-equiv="refresh" content="2700">\n'
              '  <style>\n'
              '    body { background:#f7f7f7; }\n'
              '    .nav-link { font-weight:600; }\n'
              '    .badge-triage-no { background:#999; }\n'
              '    .badge-triage-yes { background:#198754; }\n'
              '    .cat-red { background:#dc3545; }\n'
              '    .cat-yellow { background:#ffc107; color:#000; }\n'
              '    .cat-green { background:#198754; }\n'
              '    .cat-orange { background:#fd7e14; }\n'
              '    .cat-none { background:#6c757d; }\n'
              '\n'
              '    .wait-cell { font-weight:600; text-align:center; }\n'
              '    .wait-short { background:#d1e7dd; }\n'
              '    .wait-medium { background:#fff3cd; }\n'
              '    .wait-long { background:#f8d7da; }\n'
              '    .wait-none { background:#e9ecef; }\n'
              '\n'
              '    .loc-pill {\n'
              '      display:inline-block;\n'
              '      padding:2px 8px;\n'
              '      border-radius:999px;\n'
              '      background:#e7f1ff;\n'
              '      color:#0d6efd;\n'
              '      border:1px solid #cfe2ff;\n'
              '      font-size:0.75rem;\n'
              '    }\n'
              '    .bed-empty { background:#e9ecef; }\n'
              '    .bed-occupied { background:#d1e7dd; }\n'
              '    .bed-dirty { background:#f8d7da; }\n'
              '\n'
              '    .allergy-pill {\n'
              '      font-size:0.7rem;\n'
              '      border-radius:999px;\n'
              '      padding:1px 6px;\n'
              '    }\n'
              '    .allergy-yes { background:#f8d7da; color:#842029; }\n'
              '    .allergy-nkda { background:#d1e7dd; color:#0f5132; }\n'
              '    .allergy-unknown { background:#e9ecef; color:#495057; }\n'
              '\n'
              '    .banner-allergy {\n'
              '      border-left:4px solid #dc3545;\n'
              '      background:#f8d7da;\n'
              '    }\n'
              '    .banner-no-allergy {\n'
              '      border-left:4px solid #6c757d;\n'
              '      background:#f1f3f5;\n'
              '    }\n'
              '\n'
              '    .task-pill {\n'
              '      font-size:0.7rem;\n'
              '      border-radius:999px;\n'
              '    }\n'
              '    .task-done {\n'
              '      background:#198754;\n'
              '      color:#fff;\n'
              '    }\n'
              '    .task-pending {\n'
              '      background:#e9ecef;\n'
              '      color:#495057;\n'
              '    }\n'
              '\n'
              '    /* ED Board layout tuning */\n'
              '    .ed-board-card {\n'
              '      border-radius: 0.75rem;\n'
              '      border: 1px solid #dee2e6;\n'
              '    }\n'
              '    .ed-board-table thead th {\n'
              '      font-size: 0.75rem;\n'
              '      text-transform: uppercase;\n'
              '      letter-spacing: 0.03em;\n'
              '      background-color: #f8f9fa;\n'
              '      position: sticky;\n'
              '      top: 0;\n'
              '      z-index: 1;\n'
              '    }\n'
              '    .ed-board-table tbody td {\n'
              '      font-size: 0.82rem;\n'
              '      vertical-align: middle;\n'
              '    }\n'
              '    .ed-board-table tbody tr:hover {\n'
              '      background-color: #f1f3f5;\n'
              '    }\n'
              '    .ed-board-table td.col-queue,\n'
              '    .ed-board-table td.col-age,\n'
              '    .ed-board-table td.col-es,\n'
              '    .ed-board-table td.col-status,\n'
              '    .ed-board-table td.col-user {\n'
              '      text-align: center;\n'
              '      white-space: nowrap;\n'
              '    }\n'
              '    .ed-board-table td.col-visit,\n'
              '    .ed-board-table td.col-id,\n'
              '    .ed-board-table td.col-insurance {\n'
              '      white-space: nowrap;\n'
              '    }\n'
              '    .ed-board-table td.col-payment {\n'
              '      max-width: 220px;\n'
              '      white-space: nowrap;\n'
              '      overflow: hidden;\n'
              '      text-overflow: ellipsis;\n'
              '    }\n'
              '    .wait-cell {\n'
              '      text-align: center;\n'
              '    }\n'
              '    .wait-pill {\n'
              '      display: inline-block;\n'
              '      padding: 2px 10px;\n'
              '      border-radius: 999px;\n'
              '      font-size: 0.78rem;\n'
              '      font-weight: 600;\n'
              '    }\n'
              '    .wait-pill.wait-short {\n'
              '      background:#d1e7dd;\n'
              '      color:#0f5132;\n'
              '    }\n'
              '    .wait-pill.wait-medium {\n'
              '      background:#fff3cd;\n'
              '      color:#664d03;\n'
              '    }\n'
              '    .wait-pill.wait-long {\n'
              '      background:#f8d7da;\n'
              '      color:#842029;\n'
              '    }\n'
              '    .wait-pill.wait-none {\n'
              '      background:#e9ecef;\n'
              '      color:#495057;\n'
              '    }\n'
              '\n'
              '    .ed-actions {\n'
              '      display: flex;\n'
              '      flex-direction: column;\n'
              '      gap: 2px;\n'
              '    }\n'
              '    .ed-actions-row {\n'
              '      display: flex;\n'
              '      flex-wrap: wrap;\n'
              '      gap: 4px;\n'
              '    }\n'
              '    .ed-actions-row .btn {\n'
              '      font-size: 0.72rem;\n'
              '      padding: 1px 8px;\n'
              '    }\n'
              '    .ed-actions-row .btn-outline-primary,\n'
              '    .ed-actions-row .btn-outline-success,\n'
              '    .ed-actions-row .btn-outline-secondary {\n'
              '      border-radius: 999px;\n'
              '    }\n'
              '    \n'
              '  </style>\n'
              '</head>\n'
              '<body>\n'
              '<nav class="navbar navbar-light bg-white border-bottom px-3">\n'
              '  <span class="navbar-brand fw-bold">ED Downtime</span>\n'
              '\n'
              '  <div class="d-flex gap-3 align-items-center">\n'
              '    <a class="nav-link" href="{{ url_for(\'ed_board\') }}">ED Board</a>\n'
              '    <a class="nav-link" href="{{ url_for(\'search_patients\') }}">Search</a>\n'
              '    <a class="nav-link position-relative" href="{{ url_for(\'chat_page\') }}">Live Chat{% if '
              'nav_chat_recent %}<span class="badge rounded-pill bg-info text-dark ms-1">{{ nav_chat_recent '
              '}}</span>{% endif %}</a>\n'
              "    {% if session.get('role') in ['admin','doctor','nurse'] %}\n"
              '      <a class="nav-link" href="{{ url_for(\'reports\') }}">Reports</a>\n'
              '    {% endif %}\n'
              "    {% if session.get('role') in ['lab','admin','doctor','nurse'] %}\n"
              '      <a class="nav-link position-relative" href="{{ url_for(\'lab_board\') }}">Lab Board{% if '
              'nav_lab_pending %}<span class="badge rounded-pill bg-danger ms-1">{{ nav_lab_pending }}</span>{% endif '
              '%}</a>\n'
              '    {% endif %}\n'
              "    {% if session.get('role') in ['radiology','admin','doctor','nurse'] %}\n"
              '      <a class="nav-link position-relative" href="{{ url_for(\'radiology_board\') }}">Radiology Board{% '
              'if nav_rad_pending %}<span class="badge rounded-pill bg-warning text-dark ms-1">{{ nav_rad_pending '
              '}}</span>{% endif %}</a>\n'
              '    {% endif %}\n'
              "    {% if session.get('role') in ['reception','admin'] %}\n"
              '      <a class="nav-link" href="{{ url_for(\'register_patient\') }}">Register</a>\n'
              '    {% endif %}\n'
              "    {% if session.get('role')=='admin' %}\n"
              '      <a class="nav-link" href="{{ url_for(\'admin_users\') }}">Users</a>\n'
              '      <a class="nav-link" href="{{ url_for(\'admin_items\') }}">Orders Items</a>\n'
              '      <a class="nav-link" href="{{ url_for(\'admin_reset_password\') }}">Reset Password</a>\n'
              '      <a class="nav-link" href="{{ url_for(\'admin_logs\') }}">Logs</a>\n'
              '      <a class="nav-link" href="{{ url_for(\'admin_backup\') }}">Backup DB</a>\n'
              '      <a class="nav-link text-primary" href="{{ url_for(\'admin_backup_now\') }}">Backup Now</a>\n'
              '      <a class="nav-link text-danger" href="{{ url_for(\'admin_restore\') }}">Restore DB</a>\n'
              '    {% endif %}\n'
              '    <span class="text-muted">User: {{ session.get(\'username\') }} ({{ session.get(\'role\') '
              '}})</span>\n'
              '    <a class="text-danger nav-link" href="{{ url_for(\'logout\') }}">Logout</a>\n'
              '  </div>\n'
              '  <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">🔄 Manual '
              'Refresh</button>\n'
              '</nav>\n'
              '\n'
              '<div class="container-fluid py-3 px-3">\n'
              '  {% block content %}{% endblock %}\n'
              '</div>\n'
              '\n'
              '<footer class="text-center text-muted small py-3">\n'
              '  {{ footer_text }}\n'
              '</footer>\n'
              '\n'
              '<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>\n'
              '</body>\n'
              '</html>\n',
 'chat.html': '\n'
              '{% extends "base.html" %}\n'
              '{% block content %}\n'
              '<h4 class="mb-3">Live Chat - Staff</h4>\n'
              '\n'
              '<div class="row">\n'
              '  <div class="col-md-8">\n'
              '    <div class="card bg-white">\n'
              '      <div class="card-body" style="height:400px; overflow-y:auto;" id="chat-box">\n'
              '        <div class="text-muted small">Loading messages...</div>\n'
              '      </div>\n'
              '      <div class="card-footer">\n'
              '        <div class="input-group">\n'
              '          <input type="text" id="chat-input" class="form-control" placeholder="Type your message and '
              'press Enter or click Send ...">\n'
              '          <button class="btn btn-primary" id="chat-send-btn">Send</button>\n'
              '        </div>\n'
              '        <div class="small text-muted mt-1">\n'
              '          Current channel: All staff (Public ED Room)\n'
              '        </div>\n'
              '      </div>\n'
              '    </div>\n'
              '  </div>\n'
              '</div>\n'
              '\n'
              '<script>\n'
              '(function() {\n'
              '  const chatBox = document.getElementById("chat-box");\n'
              '  const input   = document.getElementById("chat-input");\n'
              '  const sendBtn = document.getElementById("chat-send-btn");\n'
              '\n'
              '  let lastTimestamp = "";\n'
              '\n'
              '  function appendMessage(msg) {\n'
              '    const div = document.createElement("div");\n'
              '    div.className = "mb-1";\n'
              '    div.innerHTML =\n'
              '      \'<span class="badge bg-light text-dark me-1">\' +\n'
              '      (msg.username || "User") +\n'
              "      '</span>' +\n"
              '      \'<span class="small text-muted me-1">[\' + msg.created_at + \']</span>\' +\n'
              "      '<span>' + escapeHtml(msg.message) + '</span>';\n"
              '    chatBox.appendChild(div);\n'
              '    chatBox.scrollTop = chatBox.scrollHeight;\n'
              '  }\n'
              '\n'
              '  function escapeHtml(text) {\n'
              '    if (!text) return "";\n'
              '    return text\n'
              '      .replace(/&/g, "&amp;")\n'
              '      .replace(/</g, "&lt;")\n'
              '      .replace(/>/g, "&gt;");\n'
              '  }\n'
              '\n'
              '  function playBeep() {\n'
              '    try {\n'
              '      const ctx = new (window.AudioContext || window.webkitAudioContext)();\n'
              '      const osc = ctx.createOscillator();\n'
              '      const gain = ctx.createGain();\n'
              '      osc.type = "sine";\n'
              '      osc.frequency.value = 880;\n'
              '      gain.gain.value = 0.05;\n'
              '      osc.connect(gain);\n'
              '      gain.connect(ctx.destination);\n'
              '      osc.start();\n'
              '      setTimeout(function() {\n'
              '        osc.stop();\n'
              '        ctx.close();\n'
              '      }, 200);\n'
              '    } catch (e) {\n'
              '      console.log("Beep error", e);\n'
              '    }\n'
              '  }\n'
              '\n'
              '  async function loadMessages() {\n'
              '    try {\n'
              '      const url = lastTimestamp\n'
              '        ? "/chat/messages?after=" + encodeURIComponent(lastTimestamp)\n'
              '        : "/chat/messages";\n'
              '\n'
              '      const res = await fetch(url);\n'
              '      if (!res.ok) return;\n'
              '      const data = await res.json();\n'
              '      if (!data.ok) return;\n'
              '\n'
              '      if (data.messages && data.messages.length > 0) {\n'
              '        const isInitial = !lastTimestamp;\n'
              '        data.messages.forEach(function(m) {\n'
              '          appendMessage(m);\n'
              '          lastTimestamp = m.created_at;\n'
              '        });\n'
              '        if (!isInitial) {\n'
              '          playBeep();\n'
              '        }\n'
              '      } else if (!lastTimestamp) {\n'
              '        chatBox.innerHTML = \'<div class="text-muted small">No messages yet. Type the first message '
              "👋</div>';\n"
              '      }\n'
              '    } catch (e) {\n'
              '      // ignore\n'
              '    }\n'
              '  }\n'
              '\n'
              '  async function sendMessage() {\n'
              '    const text = (input.value || "").trim();\n'
              '    if (!text) return;\n'
              '\n'
              '    try {\n'
              '      const res = await fetch("/chat/send", {\n'
              '        method: "POST",\n'
              '        headers: { "Content-Type": "application/json" },\n'
              '        body: JSON.stringify({ message: text })\n'
              '      });\n'
              '      const data = await res.json();\n'
              '      if (data.ok) {\n'
              '        input.value = "";\n'
              '        loadMessages();\n'
              '      }\n'
              '    } catch (e) {\n'
              '      alert("Error sending message");\n'
              '    }\n'
              '  }\n'
              '\n'
              '  sendBtn.addEventListener("click", sendMessage);\n'
              '  input.addEventListener("keydown", function(e) {\n'
              '    if (e.key === "Enter") {\n'
              '      e.preventDefault();\n'
              '      sendMessage();\n'
              '    }\n'
              '  });\n'
              '\n'
              '  loadMessages();\n'
              '  setInterval(loadMessages, 3000);\n'
              '})();\n'
              '</script>\n'
              '{% endblock %}\n',
 'clinical_orders.html': '\n'
                         '{% extends "base.html" %}\n'
                         '{% block content %}\n'
                         '<h4 class="mb-2">Clinical Orders - Visit {{ visit.visit_id }}</h4>\n'
                         '<div class="mb-3 text-muted">\n'
                         '  Patient: {{ visit.name }} | ID: {{ visit.id_number }} | Insurance: {{ visit.insurance }}\n'
                         '</div>\n'
                         '\n'
                         '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                         '  {% for category, msg in messages %}\n'
                         '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                         '  {% endfor %}\n'
                         '{% endwith %}\n'
                         '\n'
                         '<div class="mb-3 d-flex flex-wrap gap-2">\n'
                         '  <a class="btn btn-sm btn-outline-secondary"\n'
                         '     target="_blank"\n'
                         '     href="{{ url_for(\'lab_results_pdf\', visit_id=visit.visit_id) }}">\n'
                         '    Print Lab Results PDF\n'
                         '  </a>\n'
                         '\n'
                         '  <a class="btn btn-sm btn-outline-secondary"\n'
                         '     target="_blank"\n'
                         '     href="{{ url_for(\'radiology_results_pdf\', visit_id=visit.visit_id) }}">\n'
                         '    Print Radiology Reports PDF\n'
                         '  </a>\n'
                         '\n'
                         '  <a class="btn btn-sm btn-outline-dark"\n'
                         '     target="_blank"\n'
                         '     href="{{ url_for(\'auto_summary_pdf\', visit_id=visit.visit_id) }}">\n'
                         '    Auto ED Course Summary PDF\n'
                         '  </a>\n'
                         '</div>\n'
                         '\n'
                         '<div class="row g-3">\n'
                         '  <div class="col-lg-7">\n'
                         '    <div class="card p-3 bg-white">\n'
                         '      <h6 class="fw-bold mb-2">Add New Clinical Order</h6>\n'
                         '\n'
                                                   '      <div class="mb-3 d-flex flex-wrap gap-2">\n'
                          '        <button type="button" class="btn btn-sm btn-outline-primary" onclick="applyBundle(\'chest_pain\')">Chest Pain Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-danger" onclick="applyBundle(\'stroke\')">Stroke Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-dark" onclick="applyBundle(\'trauma\')">Trauma Bundle</button>\n'
                          '\n'
                          '        <button type="button" class="btn btn-sm btn-outline-success" onclick="applyBundle(\'abdominal_pain\')">Abdominal Pain Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-info" onclick="applyBundle(\'sob\')">SOB Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-info" onclick="applyBundle(\'asthma_copd\')">Asthma / COPD Bundle</button>\n'
                          '\n'
                          '        <button type="button" class="btn btn-sm btn-outline-warning" onclick="applyBundle(\'sepsis\')">Sepsis Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-primary" onclick="applyBundle(\'fever\')">Fever Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-warning" onclick="applyBundle(\'peds_fever_sepsis\')">Pediatric Fever / Sepsis</button>\n'
                          '\n'
                          '        <button type="button" class="btn btn-sm btn-outline-dark" onclick="applyBundle(\'gi_bleed\')">GI Bleed Bundle</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-danger" onclick="applyBundle(\'anaphylaxis\')">Anaphylaxis Bundle</button>\n'
                          '\n'
                          '        <button type="button" class="btn btn-sm btn-outline-danger" onclick="applyBundle(\'cardiac_arrest\')">Cardiac Arrest / Peri-Arrest</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-warning" onclick="applyBundle(\'dka_hyperglycemia\')">DKA / Hyperglycemic Emergency</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-warning" onclick="applyBundle(\'hypoglycemia\')">Hypoglycemia</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-dark" onclick="applyBundle(\'poisoning_overdose\')">Poisoning / Overdose</button>\n'
                          '\n'
                          '        <button type="button" class="btn btn-sm btn-outline-danger" onclick="applyBundle(\'htn_emergency\')">Hypertensive Emergency / Acute Pulmonary Edema</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-success" onclick="applyBundle(\'renal_colic\')">Renal Colic / Flank Pain</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-warning" onclick="applyBundle(\'aki_electrolyte\')">AKI / Electrolyte Disturbance</button>\n'
                          '        <button type="button" class="btn btn-sm btn-outline-secondary" onclick="applyBundle(\'obstetric\')">Obstetric / Pregnant Emergency</button>\n'
                          '\n'
                          '        <button type="button" class="btn btn-sm btn-outline-secondary" onclick="clearAllBundles()">Clear Selections</button>\n'
                          '      </div>\n'
                         '\n'
                         "      {% if session.get('role') not in ['reception'] %}\n"
                         '      <form method="POST" action="{{ url_for(\'add_clinical_order\', '
                         'visit_id=visit.visit_id) }}">\n'
                         '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <label class="form-label fw-bold">Diagnosis / Chief Complaint</label>\n'
                         '        <textarea class="form-control mb-3" name="diagnosis" rows="2" placeholder="Write '
                         'diagnosis or chief complaint..."></textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold">Radiology Orders</label>\n'
                         '        <div class="border rounded p-2 mb-2" style="max-height:180px; overflow:auto;">\n'
                         '          {% if rad_items %}\n'
                         '          {% for item in rad_items %}\n'
                         '            <div class="form-check">\n'
                         '              <input class="form-check-input rad-item" type="checkbox" value="{{ item.name '
                         '}}" id="rad_{{ loop.index }}">\n'
                         '              <label class="form-check-label" for="rad_{{ loop.index }}">{{ item.name '
                         '}}</label>\n'
                         '            </div>\n'
                         '          {% endfor %}\n'
                         '          {% else %}\n'
                         '            <div class="text-muted small">No radiology items defined. Please ask admin to '
                         'add from Admin &raquo; Orders Items.</div>\n'
                         '          {% endif %}\n'
                         '        </div>\n'
                         '\n'
                         '        <div class="input-group input-group-sm mb-2">\n'
                         '          <input type="text" class="form-control" id="rad_other" placeholder="Search or add '
                         'radiology (optional)">\n'
                         '          <button class="btn btn-outline-secondary" type="button" '
                         'onclick="searchOrAdd(\'rad\')">Search / Add</button>\n'
                         '        </div>\n'
                         '\n'
                         '        <textarea class="form-control mb-3" id="radiology_text" name="radiology_orders" '
                         'rows="2"\n'
                         '                  placeholder="Selected radiology appear here..." readonly></textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold">Lab Orders</label>\n'
                         '        <div class="border rounded p-2 mb-2" style="max-height:180px; overflow:auto;">\n'
                         '          {% if lab_items %}\n'
                         '          {% for item in lab_items %}\n'
                         '            <div class="form-check">\n'
                         '              <input class="form-check-input lab-item" type="checkbox" value="{{ item.name '
                         '}}" id="lab_{{ loop.index }}">\n'
                         '              <label class="form-check-label" for="lab_{{ loop.index }}">{{ item.name '
                         '}}</label>\n'
                         '            </div>\n'
                         '          {% endfor %}\n'
                         '          {% else %}\n'
                         '            <div class="text-muted small">No lab items defined. Please ask admin to add from '
                         'Admin &raquo; Orders Items.</div>\n'
                         '          {% endif %}\n'
                         '        </div>\n'
                         '\n'
                         '        <div class="input-group input-group-sm mb-2">\n'
                         '          <input type="text" class="form-control" id="lab_other" placeholder="Search or add '
                         'lab (optional)">\n'
                         '          <button class="btn btn-outline-secondary" type="button" '
                         'onclick="searchOrAdd(\'lab\')">Search / Add</button>\n'
                         '        </div>\n'
                         '\n'
                         '        <textarea class="form-control mb-3" id="lab_text" name="lab_orders" rows="2"\n'
                         '                  placeholder="Selected labs appear here..." readonly></textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold">Medications</label>\n'
                         '        <div class="border rounded p-2 mb-2" style="max-height:200px; overflow:auto;">\n'
                         '          {% if med_items %}\n'
                         '          {% for item in med_items %}\n'
                         '            <div class="form-check d-flex align-items-center mb-1">\n'
                         '              <input class="form-check-input med-item me-2" type="checkbox" value="{{ '
                         'item.name }}" id="med_{{ loop.index }}">\n'
                         '              <label class="form-check-label flex-grow-1" for="med_{{ loop.index }}">{{ '
                         'item.name }}</label>\n'
                         '              <input type="text"\n'
                         '                     class="form-control form-control-sm ms-2 med-dose"\n'
                         '                     data-med="{{ item.name }}"\n'
                         '                     placeholder="Dose">\n'
                         '            </div>\n'
                         '          {% endfor %}\n'
                         '          {% else %}\n'
                         '            <div class="text-muted small">No medication items defined. Please ask admin to '
                         'add from Admin &raquo; Orders Items.</div>\n'
                         '          {% endif %}\n'
                         '        </div>\n'
                         '\n'
                         '        <div class="input-group input-group-sm mb-2">\n'
                         '          <input type="text" class="form-control" id="med_other" placeholder="Search or add '
                         'medication (optional)">\n'
                         '          <button class="btn btn-outline-secondary" type="button" '
                         'onclick="searchOrAdd(\'med\')">Search / Add</button>\n'
                         '        </div>\n'
                         '\n'
                         '        <textarea class="form-control mb-3" id="med_text" name="medications" rows="2"\n'
                         '                  placeholder="Selected medications appear here..." readonly></textarea>\n'
                         '\n'
                         '        <div class="d-flex gap-2 mt-2">\n'
                         '          <button class="btn btn-primary">Save Clinical Order</button>\n'
                         '          <a class="btn btn-secondary" href="{{ url_for(\'patient_details\', '
                         'visit_id=visit.visit_id) }}">Back</a>\n'
                         '        </div>\n'
                         '      </form>\n'
                         '      {% else %}\n'
                         '        <div class="alert alert-warning mb-0">\n'
                         '          Reception role has no access to create clinical orders.\n'
                         '        </div>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '  </div>\n'
                         '\n'
                         '  <div class="col-lg-5">\n'
                         '    <div class="card p-3 bg-white mb-3">\n'
                         '      <div class="d-flex justify-content-between align-items-center">\n'
                         '        <h6 class="fw-bold mb-2">Nursing Notes</h6>\n'
                         '        <a class="btn btn-sm btn-outline-primary"\n'
                         '           target="_blank"\n'
                         '           href="{{ url_for(\'nursing_notes_pdf\', visit_id=visit.visit_id) }}">Print Notes '
                         'PDF</a>\n'
                         '      </div>\n'
                         '\n'
                         "      {% if session.get('role') in ['nurse','doctor','admin'] %}\n"
                         '      <form method="POST" action="{{ url_for(\'add_nursing_note\', visit_id=visit.visit_id) '
                         '}}">\n'
                         '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <textarea class="form-control mb-2" name="note_text" rows="3" placeholder="Write '
                         'nursing note..."></textarea>\n'
                         '        <button class="btn btn-sm btn-primary">Save Note</button>\n'
                         '      </form>\n'
                         '      {% else %}\n'
                         '        <div class="text-muted small">Nursing notes are read-only for this role.</div>\n'
                         '      {% endif %}\n'
                         '\n'
                         '      <hr>\n'
                         '      {% if notes %}\n'
                         '        <div style="max-height:220px; overflow:auto;">\n'
                         '          {% for n in notes %}\n'
                         '            <div class="border rounded p-2 mb-2">\n'
                         '              <div class="small fw-bold">{{ n.created_at }} | {{ n.created_by }}</div>\n'
                         '              <div class="small">{{ n.note_text }}</div>\n'
                         '            </div>\n'
                         '          {% endfor %}\n'
                         '        </div>\n'
                         '      {% else %}\n'
                         '        <div class="text-muted small">No nursing notes yet.</div>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '\n'
                         "    {% if session.get('role') != 'nurse' %}\n"
                         '    <div class="card p-3 bg-white">\n'
                         '      <div class="d-flex justify-content-between align-items-center">\n'
                         '        <h6 class="fw-bold mb-2">Discharge Summary V5</h6>\n'
                         '        <a class="btn btn-sm btn-outline-secondary"\n'
                         '           target="_blank"\n'
                         '           href="{{ url_for(\'discharge_summary_pdf\', visit_id=visit.visit_id) '
                         '}}">Auto-Summary PDF</a>\n'
                         '      </div>\n'
                         '\n'
                         '      <form method="POST" action="{{ url_for(\'discharge_save\', visit_id=visit.visit_id) '
                         '}}">\n'
                         '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <label class="form-label fw-bold small mt-2">Diagnosis / Chief Complaint</label>\n'
                         '        <textarea class="form-control mb-2" name="diagnosis_cc" rows="2"\n'
                         '          placeholder="Diagnosis / chief complaint...">{{ summary.diagnosis_cc if summary '
                         "else '' }}</textarea>\n"
                         '\n'
                         '        <label class="form-label fw-bold small">Final Diagnosis</label>\n'
                         '        <textarea class="form-control mb-2" name="final_diagnosis" rows="2"\n'
                         '          placeholder="Final diagnosis (principal and secondary)...">{{ '
                         "summary.final_diagnosis if summary else '' }}</textarea>\n"
                         '\n'
                         '        <label class="form-label fw-bold small">Referral to Clinic</label>\n'
                         '        <input class="form-control mb-2"\n'
                         '               name="referral_clinic"\n'
                         '               list="clinic_list"\n'
                         '               placeholder="Select / type clinic"\n'
                         '               value="{{ summary.referral_clinic if summary else \'\' }}">\n'
                         '        <datalist id="clinic_list">\n'
                         '          <option value="ED / Emergency">\n'
                         '          <option value="General Medicine OPD">\n'
                         '          <option value="General Surgery OPD">\n'
                         '          <option value="Pediatrics OPD">\n'
                         '          <option value="Obstetrics & Gynecology OPD">\n'
                         '          <option value="Orthopedics OPD">\n'
                         '          <option value="Cardiology OPD">\n'
                         '          <option value="Neurology OPD">\n'
                         '          <option value="ENT OPD">\n'
                         '          <option value="Ophthalmology OPD">\n'
                         '          <option value="Urology OPD">\n'
                         '          <option value="Dermatology OPD">\n'
                         '          <option value="Psychiatry OPD">\n'
                         '          <option value="Dental OPD">\n'
                         '          <option value="Oncology OPD">\n'
                         '          <option value="Endocrinology OPD">\n'
                         '          <option value="Nephrology OPD">\n'
                         '          <option value="Pulmonology OPD">\n'
                         '          <option value="ICU">\n'
                         '          <option value="LDR">\n'
                         '        </datalist>\n'
                         '\n'
                         '\n'
                         '        <label class="form-label fw-bold small">Home Medication</label>\n'
                         '        <div class="form-text small text-muted mb-1">\n'
                         '          Template: <strong>Drug 500 mg tab – 1 tab PO – every 8h – for 3 days – PRN for '
                         'pain</strong>.\n'
                         '          Write <strong>one medicine per line</strong>.\n'
                         '        </div>\n'
                         '\n'
                         '        <div class="mb-2 d-flex gap-2 align-items-center">\n'
                         '          <input type="text"\n'
                         '                 id="home_med_search"\n'
                         '                 class="form-control form-control-sm"\n'
                         '                 list="home_med_templates"\n'
                         '                 placeholder="Search/Add home medication template">\n'
                         '          <datalist id="home_med_templates">\n'
                         '            {% for item in home_med_items or [] %}\n'
                         '              <option value="{{ item.name }}">\n'
                         '            {% endfor %}\n'
                         '          </datalist>\n'
                         '          <button type="button" class="btn btn-outline-primary btn-sm" '
                         'onclick="addHomeMedFromSearch()">Add</button>\n'
                         '        </div>\n'
                         '\n'
                         '        <textarea class="form-control mb-2" id="home_medication_text" name="home_medication" '
                         'rows="2"\n'
                         '          placeholder="Example: Paracetamol 500 mg tab – 1 tab PO – every 8h – for 3 '
                         'days">{{ summary.home_medication if summary else \'\' }}</textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold small">ED / Hospital Course</label>\n'
                         '        <textarea class="form-control mb-2" name="summary_text" rows="4"\n'
                         '                  placeholder="Brief summary of presentation, key exam findings, treatment '
                         'and response...">{{ summary.summary_text if summary else \'\' }}</textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold small">Investigations Summary</label>\n'
                         '        <textarea class="form-control mb-2" name="investigations_summary" rows="3"\n'
                         '                  placeholder="Key lab and imaging findings (only relevant/abnormal '
                         'results)...">{{ summary.investigations_summary if summary else \'\' }}</textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold small">Procedures Performed</label>\n'
                         '        <textarea class="form-control mb-2" name="procedures_text" rows="2"\n'
                         '                  placeholder="e.g. Suturing, nebulisation, plaster cast, wound '
                         'dressing...">{{ summary.procedures_text if summary else \'\' }}</textarea>\n'
                         '\n'
                         '        <label class="form-label fw-bold small">Condition on Discharge</label>\n'
                         '        <input class="form-control mb-2" name="condition_on_discharge"\n'
                         '               placeholder="e.g. Stable and improved" value="{{ '
                         'summary.condition_on_discharge if summary else \'\' }}">\n'
                         '\n'
                         '        <label class="form-label fw-bold small">Follow-up & Instructions</label>\n'
                         '        <textarea class="form-control mb-3" name="followup_instructions" rows="3"\n'
                         '                  placeholder="Follow-up clinic and safety-net advice (return to ED '
                         'if...)">{{ summary.followup_instructions if summary else \'\' }}</textarea>\n'
                         '\n'
                         '        <button class="btn btn-sm btn-success">Save Summary</button>\n'
                         '      </form>\n'
                         '    </div>\n'
                         '    {% endif %}\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '<div class="card p-3 bg-white mt-3">\n'
                         '  <h6 class="fw-bold mb-2">Lab Requests / Results</h6>\n'
                         '  {% if not lab_reqs %}\n'
                         '    <div class="text-muted small">No lab requests for this visit.</div>\n'
                         '  {% else %}\n'
                         '    <table class="table table-sm mb-0">\n'
                         '      <thead>\n'
                         '        <tr>\n'
                         '          <th style="width:60px;">#</th>\n'
                         '          <th>Test</th>\n'
                         '          <th>Status</th>\n'
                         '          <th>Result</th>\n'
                         '          <th class="small">Requested</th>\n'
                         '          <th style="width:120px;" class="small">Actions</th>\n'
                         '        </tr>\n'
                         '      </thead>\n'
                         '      <tbody>\n'
                         '        {% for l in lab_reqs %}\n'
                         '          <tr>\n'
                         '            <td>{{ l.id }}</td>\n'
                         '            <td>{{ l.test_name }}</td>\n'
                         '            <td>\n'
                         "              {% if l.status == 'REQUESTED' %}\n"
                         '                <span class="badge bg-secondary">Waiting Sample</span>\n'
                         "              {% elif l.status == 'RECEIVED' %}\n"
                         '                <span class="badge bg-warning text-dark">Sample Received</span>\n'
                         "              {% elif l.status == 'REPORTED' %}\n"
                         '                <span class="badge bg-success">Result Ready</span>\n'
                         '              {% else %}\n'
                         '                <span class="badge bg-light text-muted">{{ l.status }}</span>\n'
                         '              {% endif %}\n'
                         '            </td>\n'
                         '            <td style="max-width:260px;white-space:pre-wrap;font-size:0.85rem;">\n'
                         "              {{ l.result_text or '-' }}\n"
                         '            </td>\n'
                         '            <td class="small text-muted">\n'
                         "              {{ l.requested_at or '-' }}<br>\n"
                         "              by {{ l.requested_by or '-' }}\n"
                         '            </td>\n'
                         '            <td class="small">\n'
                         '              <div class="d-flex flex-wrap gap-1">\n'
                         "                {% if l.status == 'REQUESTED' and session.get('role') in "
                         "['nurse','lab','admin'] %}\n"
                         '                  <form method="POST"\n'
                         '                        action="{{ url_for(\'lab_collect_sample\', rid=l.id) }}"\n'
                         '                        class="d-inline">\n'
                         '                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '                    <button class="btn btn-sm btn-outline-primary"\n'
                         '                            onclick="return confirm(\'Mark sample as COLLECTED?\');">\n'
                         '                      Collect sample\n'
                         '                    </button>\n'
                         '                  </form>\n'
                         '                {% endif %}\n'
                         "                {% if l.status != 'REPORTED' and session.get('role') in "
                         "['doctor','lab','admin'] %}\n"
                         '                  <form method="POST"\n'
                         '                        action="{{ url_for(\'lab_delete_request\', rid=l.id) }}"\n'
                         '                        class="d-inline">\n'
                         '                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '                    <button class="btn btn-sm btn-outline-danger"\n'
                         '                            onclick="return confirm(\'Delete this lab request?\');">\n'
                         '                      🗑\n'
                         '                    </button>\n'
                         '                  </form>\n'
                         '                {% endif %}\n'
                         "                {% if session.get('role') in ['nurse','doctor','lab','radiology','admin'] "
                         '%}\n'
                         '                  <a class="btn btn-sm btn-outline-secondary"\n'
                         '                     href="{{ url_for(\'lab_results_pdf\', visit_id=visit.visit_id) }}"\n'
                         '                     target="_blank"\n'
                         '                     title="Open lab results PDF">\n'
                         '                    📄\n'
                         '                  </a>\n'
                         '                {% endif %}\n'
                         '              </div>\n'
                         '            </td>\n'
                         '          </tr>\n'
                         '        {% endfor %}\n'
                         '      </tbody>\n'
                         '    </table>\n'
                         '  {% endif %}\n'
                         '</div>\n'
                         '\n'
                         '<div class="card p-3 bg-white mt-3">\n'
                         '  <h6 class="fw-bold mb-2">Radiology Requests / Reports</h6>\n'
                         '  {% if not rad_reqs %}\n'
                         '    <div class="text-muted small">No radiology requests for this visit.</div>\n'
                         '  {% else %}\n'
                         '    <table class="table table-sm mb-0">\n'
                         '      <thead>\n'
                         '        <tr>\n'
                         '          <th style="width:60px;">#</th>\n'
                         '          <th>Study</th>\n'
                         '          <th>Status</th>\n'
                         '          <th>Report</th>\n'
                         '          <th class="small">Requested</th>\n'
                         '          <th style="width:140px;" class="small">Actions</th>\n'
                         '        </tr>\n'
                         '      </thead>\n'
                         '      <tbody>\n'
                         '        {% for r in rad_reqs %}\n'
                         '          <tr>\n'
                         '            <td>{{ r.id }}</td>\n'
                         '            <td>{{ r.test_name }}</td>\n'
                         '            <td>\n'
                         "              {% if r.status == 'REQUESTED' %}\n"
                         '                <span class="badge bg-secondary">Waiting</span>\n'
                         "              {% elif r.status == 'DONE' %}\n"
                         '                <span class="badge bg-warning text-dark">Done</span>\n'
                         "              {% elif r.status == 'REPORTED' %}\n"
                         '                <span class="badge bg-success">Report Ready</span>\n'
                         '              {% else %}\n'
                         '                <span class="badge bg-light text-muted">{{ r.status }}</span>\n'
                         '              {% endif %}\n'
                         '            </td>\n'
                         '            <td style="max-width:260px;white-space:pre-wrap;font-size:0.85rem;">\n'
                         "              {{ r.report_text or '-' }}\n"
                         '            </td>\n'
                         '            <td class="small text-muted">\n'
                         "              {{ r.requested_at or '-' }}<br>\n"
                         "              by {{ r.requested_by or '-' }}\n"
                         '            </td>\n'
                         '            <td class="small">\n'
                         '              <div class="d-flex flex-wrap gap-1">\n'
                         "                {% if r.status == 'REQUESTED' and session.get('role') in "
                         "['nurse','radiology','admin'] %}\n"
                         '                  <form method="POST"\n'
                         '                        action="{{ url_for(\'radiology_mark_done\', rid=r.id) }}"\n'
                         '                        class="d-inline">\n'
                         '                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '                    <button class="btn btn-sm btn-outline-primary"\n'
                         '                            onclick="return confirm(\'Mark study as DONE?\');">\n'
                         '                      Mark done\n'
                         '                    </button>\n'
                         '                  </form>\n'
                         '                {% endif %}\n'
                         "                {% if r.status != 'REPORTED' and session.get('role') in "
                         "['doctor','radiology','admin'] %}\n"
                         '                  <form method="POST"\n'
                         '                        action="{{ url_for(\'radiology_delete_request\', rid=r.id) }}"\n'
                         '                        class="d-inline">\n'
                         '                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '                    <button class="btn btn-sm btn-outline-danger"\n'
                         '                            onclick="return confirm(\'Delete this radiology request?\');">\n'
                         '                      🗑\n'
                         '                    </button>\n'
                         '                  </form>\n'
                         '                {% endif %}\n'
                         "                {% if session.get('role') in ['nurse','doctor','lab','radiology','admin'] "
                         '%}\n'
                         '                  <a class="btn btn-sm btn-outline-secondary"\n'
                         '                     href="{{ url_for(\'radiology_results_pdf\', visit_id=visit.visit_id) '
                         '}}"\n'
                         '                     target="_blank"\n'
                         '                     title="Open radiology report PDF">\n'
                         '                    📄\n'
                         '                  </a>\n'
                         '                {% endif %}\n'
                         '              </div>\n'
                         '            </td>\n'
                         '          </tr>\n'
                         '        {% endfor %}\n'
                         '      </tbody>\n'
                         '    </table>\n'
                         '  {% endif %}\n'
                         '</div>\n'
                         '\n'
                         '<div class="card p-3 bg-white mt-3">\n'
                         '  <h6 class="fw-bold mb-2">Previous Clinical Orders</h6>\n'
                         '\n'
                         '  {% if not orders %}\n'
                         '    <div class="text-muted small">No clinical orders yet.</div>\n'
                         '  {% else %}\n'
                         '    {% for o in orders %}\n'
                         '      <div class="border rounded p-2 mb-2">\n'
                         '        <div class="d-flex justify-content-between align-items-center">\n'
                         '          <div class="fw-bold">\n'
                         '            Order #{{ o.id }}\n'
                         '            <span class="text-muted small ms-2">{{ o.created_at }} by {{ o.created_by '
                         '}}</span>\n'
                         '            {% if o.updated_at %}\n'
                         '              <span class="text-muted small ms-2">| Updated: {{ o.updated_at }} by {{ '
                         'o.updated_by }}</span>\n'
                         '            {% endif %}\n'
                         '          </div>\n'
                         '\n'
                         '          <div class="d-flex gap-1">\n'
                         '            <a class="btn btn-sm btn-outline-secondary"\n'
                         '               target="_blank"\n'
                         '               href="{{ url_for(\'clinical_order_pdf\', visit_id=visit.visit_id, oid=o.id) '
                         '}}">Print PDF</a>\n'
                         '\n'
                         "            {% if session.get('role') in ['nurse','doctor','admin'] %}\n"
                         '              <form method="post" class="d-inline" action="{{ '
                         'url_for(\'delete_clinical_order\', visit_id=visit.visit_id, oid=o.id) }}" onsubmit="return '
                         'confirm(\'Delete this order?\');">\n'
                         '                <button class="btn btn-sm btn-outline-danger">Delete</button>\n'
                         '              </form>\n'
                         '\n'
                         '              <button class="btn btn-sm btn-outline-dark"\n'
                         '                      data-bs-toggle="collapse"\n'
                         '                      data-bs-target="#edit{{ o.id }}">Edit</button>\n'
                         '            {% endif %}\n'
                         '          </div>\n'
                         '        </div>\n'
                         '\n'
                         '        <div class="mt-2 small">\n'
                         "          <div><strong>Diagnosis / Chief Complaint:</strong><br>{{ o.diagnosis or '-' "
                         '}}</div>\n'
                         '          <div class="mt-1"><strong>Radiology:</strong><br>{{ o.radiology_orders or \'-\' '
                         '}}</div>\n'
                         '          <div class="mt-1"><strong>Lab:</strong><br>{{ o.lab_orders or \'-\' }}</div>\n'
                         '          <div class="mt-1"><strong>Medications:</strong><br>{{ o.medications or \'-\' '
                         '}}</div>\n'
                         '        </div>\n'
                         '\n'
                         '        <div class="collapse mt-2" id="edit{{ o.id }}">\n'
                         '          <form method="POST"\n'
                         '                action="{{ url_for(\'update_clinical_order\', visit_id=visit.visit_id, '
                         'oid=o.id) }}"\n'
                         '                class="bg-light p-2 rounded">\n'
                         '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '            <label class="form-label fw-bold small">Diagnosis / Chief Complaint</label>\n'
                         '            <textarea class="form-control mb-2" name="diagnosis" rows="2">{{ o.diagnosis or '
                         "'' }}</textarea>\n"
                         '\n'
                         '            <label class="form-label fw-bold small">Radiology Orders</label>\n'
                         '            <textarea class="form-control mb-2" name="radiology_orders" rows="2">{{ '
                         "o.radiology_orders or '' }}</textarea>\n"
                         '\n'
                         '            <label class="form-label fw-bold small">Lab Orders</label>\n'
                         '            <textarea class="form-control mb-2" name="lab_orders" rows="2">{{ o.lab_orders '
                         "or '' }}</textarea>\n"
                         '\n'
                         '            <label class="form-label fw-bold small">Medications</label>\n'
                         '            <textarea class="form-control mb-2" name="medications" rows="2">{{ o.medications '
                         "or '' }}</textarea>\n"
                         '\n'
                         '            <button class="btn btn-sm btn-success">Save Changes</button>\n'
                         '          </form>\n'
                         '        </div>\n'
                         '      </div>\n'
                         '    {% endfor %}\n'
                         '  {% endif %}\n'
                         '</div>\n'
                         '\n'
                         '<script>\n'
                         'function syncChecked(className, targetId){\n'
                         "  const checked = Array.from(document.querySelectorAll('.'+className+':checked')).map(cb => "
                         'cb.value);\n'
                         '  document.getElementById(targetId).value = checked.join(", ");\n'
                         '}\n'
                         '\n'
                         'function syncMedications(){\n'
                         '  const parts = [];\n'
                         "  document.querySelectorAll('.med-item').forEach(cb => {\n"
                         '    if (!cb.checked) return;\n'
                         '    const label = cb.value;\n'
                         '    let dose = "";\n'
                         "    const container = cb.closest('.form-check');\n"
                         '    if (container) {\n'
                         "      const doseInput = container.querySelector('.med-dose');\n"
                         '      if (doseInput && doseInput.value) {\n'
                         '        dose = doseInput.value.trim();\n'
                         '      }\n'
                         '    }\n'
                         '    if (dose) {\n'
                         '      parts.push(label + " (" + dose + ")");\n'
                         '    } else {\n'
                         '      parts.push(label);\n'
                         '    }\n'
                         '  });\n'
                         "  const target = document.getElementById('med_text');\n"
                         '  if (target) {\n'
                         '    target.value = parts.join(", ");\n'
                         '  }\n'
                         '}\n'
                         '\n'
                         "document.addEventListener('change', function(e){\n"
                         "  if(e.target.classList.contains('rad-item')) syncChecked('rad-item','radiology_text');\n"
                         "  if(e.target.classList.contains('lab-item')) syncChecked('lab-item','lab_text');\n"
                         "  if(e.target.classList.contains('med-item')) syncMedications();\n"
                         '});\n'
                         '\n'
                         "document.addEventListener('input', function(e){\n"
                         "  if(e.target.classList.contains('med-dose')) syncMedications();\n"
                         '});\n'
                         '\n'
                         'function searchOrAdd(prefix){\n'
                         "  const input = document.getElementById(prefix+'_other');\n"
                         '  if(!input) return;\n'
                         "  const val = (input.value || '').trim();\n"
                         '  if(!val) return;\n'
                         '\n'
                         "  const classMap = { rad: 'rad-item', lab: 'lab-item', med: 'med-item' };\n"
                         '  const cls = classMap[prefix] || null;\n'
                         '  let found = false;\n'
                         '\n'
                         '  if (cls) {\n'
                         '    const q = val.toLowerCase();\n'
                         "    const checkboxes = Array.from(document.querySelectorAll('.' + cls));\n"
                         '    for (let i = 0; i < checkboxes.length; i++) {\n'
                         '      const cb = checkboxes[i];\n'
                         "      const label = (cb.value || '').toLowerCase();\n"
                         '      if (label.includes(q)) {\n'
                         '        cb.checked = true;\n'
                         '        found = true;\n'
                         "        try { cb.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e) {}\n"
                         '        break;\n'
                         '      }\n'
                         '    }\n'
                         '  }\n'
                         '\n'
                         '  if (found) {\n'
                         "    if (prefix === 'rad') {\n"
                         "      syncChecked('rad-item','radiology_text');\n"
                         "    } else if (prefix === 'lab') {\n"
                         "      syncChecked('lab-item','lab_text');\n"
                         "    } else if (prefix === 'med') {\n"
                         '      syncMedications();\n'
                         '    }\n'
                         '  } else {\n'
                         '    addOther(prefix, val);\n'
                         '  }\n'
                         '\n'
                         "  input.value = '';\n"
                         '}\n'
                         '\n'
                         'function addOther(prefix, value){\n'
                         "  const input = document.getElementById(prefix+'_other');\n"
                         "  const raw = (typeof value === 'string') ? value : (input ? input.value : '');\n"
                         "  const val = (raw || '').trim();\n"
                         '  if(!val) return;\n'
                         '\n'
                         "  const tId = prefix==='rad' ? 'radiology_text' : prefix==='lab' ? 'lab_text' : 'med_text';\n"
                         '  const t = document.getElementById(tId);\n'
                         '  if (!t) return;\n'
                         "  const cur = t.value ? t.value.split(',').map(x=>x.trim()).filter(Boolean) : [];\n"
                         '  if(!cur.includes(val)) cur.push(val);\n'
                         "  t.value = cur.join(', ');\n"
                         '}\n'
                         '\n'
                         "['rad_other','lab_other','med_other'].forEach(id=>{\n"
                         '  const el = document.getElementById(id);\n'
                         '  if(el){\n'
                         "    el.addEventListener('keydown', (e)=>{\n"
                         "      if(e.key==='Enter'){\n"
                         '        e.preventDefault();\n'
                         "        searchOrAdd(id.split('_')[0]);\n"
                         '      }\n'
                         '    });\n'
                         '  }\n'
                         '});\n'
                         '\n'
                          'const bundles = {\n'
                          '  chest_pain: {\n'
                          '    diagnosis: "Chest pain – rule out ACS / PE",\n'
                          '    radiology: ["X-Ray Chest", "CT Angio Chest (PE Study)"],\n'
                          '    labs: ["Troponin", "CK-MB", "CBC", "Electrolytes", "PT/PTT/INR", "D-Dimer", "RBS (Random Blood Sugar)"],\n'
                          '    meds: ["Aspirin PO 300mg", "Nitroglycerin SL", "Morphine IV", "Ondansetron IV", "Normal Saline 0.9%"]\n'
                          '  },\n'
                          '  stroke: {\n'
                          '    diagnosis: "Acute stroke / TIA – onset time?",\n'
                          '    radiology: ["CT Brain Without Contrast", "CT Angio Brain/Neck"],\n'
                          '    labs: ["CBC", "Electrolytes", "PT/PTT/INR", "RBS (Random Blood Sugar)"],\n'
                          '    meds: ["Normal Saline 0.9%", "Labetalol IV"]\n'
                          '  },\n'
                          '  trauma: {\n'
                          '    diagnosis: "Polytrauma – primary & secondary survey",\n'
                          '    radiology: ["CT Trauma Pan-Scan", "X-Ray Chest", "X-Ray Pelvis", "FAST Ultrasound"],\n'
                          '    labs: ["CBC", "CMP (Kidney/Liver)", "PT/PTT/INR", "Lactate", "Type & Screen / Crossmatch", "ABG"],\n'
                          '    meds: ["Tetanus Toxoid IM", "Cefazolin IV", "Morphine IV", "Ringer Lactate", "Normal Saline 0.9%"]\n'
                          '  },\n'
                          '  abdominal_pain: {\n'
                          '    diagnosis: "Abdominal pain – rule out surgical abdomen",\n'
                          '    radiology: ["US Abdomen", "CT Abdomen/Pelvis"],\n'
                          '    labs: ["CBC", "CRP", "Electrolytes", "LFT", "Lipase", "Urine Analysis", "BHCG (Pregnancy Test)"],\n'
                          '    meds: ["Paracetamol IV/PO", "Ondansetron IV", "Hyoscine (Buscopan) IV/IM", "Normal Saline 0.9%"]\n'
                          '  },\n'
                          '  sob: {\n'
                          '    diagnosis: "Shortness of breath – undifferentiated",\n'
                          '    radiology: ["X-Ray Chest", "CT Chest"],\n'
                          '    labs: ["CBC", "Electrolytes", "ABG", "D-Dimer", "Troponin", "BNP", "RBS (Random Blood Sugar)"],\n'
                          '    meds: ["Oxygen Therapy", "Salbutamol Nebulizer", "Ipratropium Nebulizer", "Hydrocortisone IV", "Normal Saline 0.9%"]\n'
                          '  },\n'
                          '  sepsis: {\n'
                          '    diagnosis: "Suspected sepsis / septic shock",\n'
                          '    radiology: ["X-Ray Chest", "US Abdomen"],\n'
                          '    labs: ["CBC", "CRP", "Lactate", "Blood Culture", "Urine Analysis", "Electrolytes", "ABG"],\n'
                          '    meds: ["Broad Spectrum Antibiotic (per policy)", "Normal Saline 0.9% Bolus"]\n'
                          '  },\n'
                          '  fever: {\n'
                          '    diagnosis: "Fever – source unclear",\n'
                          '    radiology: ["X-Ray Chest", "US Abdomen"],\n'
                          '    labs: ["CBC", "CRP", "Urine Analysis", "Blood Culture", "RBS (Random Blood Sugar)"],\n'
                          '    meds: ["Paracetamol IV/PO", "Normal Saline 0.9%"]\n'
                          '  },\n'
                          '  gi_bleed: {\n'
                          '    diagnosis: "Upper / lower GI bleed",\n'
                          '    radiology: ["X-Ray Chest"],\n'
                          '    labs: ["CBC", "PT/PTT/INR", "Electrolytes", "Type & Screen / Crossmatch"],\n'
                          '    meds: ["Pantoprazole IV", "Normal Saline 0.9%", "Tranexamic Acid IV (if indicated)"]\n'
                          '  },\n'
                          '  anaphylaxis: {\n'
                          '    diagnosis: "Anaphylaxis / severe allergic reaction",\n'
                          '    radiology: [],\n'
                          '    labs: ["CBC", "ABG"],\n'
                          '    meds: ["Epinephrine IM", "Hydrocortisone IV", "Chlorpheniramine IV/IM", "Normal Saline 0.9%", "Salbutamol Nebulizer"]\n'
                          '  },\n'
                          '  cardiac_arrest: {\n'
                          '    diagnosis: "Cardiac arrest / peri-arrest – follow ACLS",\n'
                          '    radiology: ["X-Ray Chest"],\n'
                          '    labs: ["ABG", "CBC", "Electrolytes", "Lactate", "RBS (Random Blood Sugar)"],\n'
                          '    meds: ["Epinephrine IV/IO", "Amiodarone IV", "Magnesium Sulfate IV", "Normal Saline 0.9% Bolus"]\n'
                          '  },\n'
                          '  dka_hyperglycemia: {\n'
                          '    diagnosis: "DKA / Hyperglycemic emergency",\n'
                          '    radiology: [],\n'
                          '    labs: ["RBS (Random Blood Sugar)", "Serum Ketone", "ABG", "Electrolytes", "Serum Osmolality", "BUN", "Creatinine"],\n'
                          '    meds: ["Normal Saline 0.9% Bolus", "Insulin IV infusion as per protocol", "Potassium replacement as needed"]\n'
                          '  },\n'
                          '  hypoglycemia: {\n'
                          '    diagnosis: "Symptomatic hypoglycemia",\n'
                          '    radiology: [],\n'
                          '    labs: ["RBS (Random Blood Sugar)", "CBC", "Electrolytes"],\n'
                          '    meds: ["Dextrose 50% IV bolus", "Dextrose 10% IV infusion", "Oral glucose gel (if conscious)"]\n'
                          '  },\n'
                          '  poisoning_overdose: {\n'
                          '    diagnosis: "Poisoning / overdose",\n'
                          '    radiology: ["X-Ray Chest"],\n'
                          '    labs: ["CBC", "Electrolytes", "ABG", "Serum Osmolality", "Toxicology Screen"],\n'
                          '    meds: ["Activated Charcoal (if indicated)", "Normal Saline 0.9%", "Specific antidote as per tox advice"]\n'
                          '  },\n'
                          '  asthma_copd: {\n'
                          '    diagnosis: "Asthma / COPD exacerbation",\n'
                          '    radiology: ["X-Ray Chest"],\n'
                          '    labs: ["CBC", "ABG"],\n'
                          '    meds: ["Oxygen Therapy", "Salbutamol Nebulizer", "Ipratropium Nebulizer", "Hydrocortisone IV", "Magnesium Sulfate IV"]\n'
                          '  },\n'
                          '  htn_emergency: {\n'
                          '    diagnosis: "Hypertensive emergency / acute pulmonary edema",\n'
                          '    radiology: ["X-Ray Chest"],\n'
                          '    labs: ["CBC", "Electrolytes", "BNP", "Troponin", "ABG"],\n'
                          '    meds: ["Nitroglycerin IV infusion", "Furosemide IV", "Labetalol IV", "Oxygen Therapy"]\n'
                          '  },\n'
                          '  renal_colic: {\n'
                          '    diagnosis: "Renal colic / flank pain",\n'
                          '    radiology: ["US KUB", "CT KUB (non-contrast)"],\n'
                          '    labs: ["Urine Analysis", "Creatinine", "CBC"],\n'
                          '    meds: ["NSAID IV/IM", "Opioid analgesia IV", "Normal Saline 0.9%"]\n'
                          '  },\n'
                          '  aki_electrolyte: {\n'
                          '    diagnosis: "AKI / significant electrolyte disturbance",\n'
                          '    radiology: ["US KUB"],\n'
                          '    labs: ["Creatinine", "BUN", "Electrolytes", "ABG"],\n'
                          '    meds: ["Normal Saline 0.9%", "Calcium Gluconate IV", "Insulin + Dextrose", "Nebulized Salbutamol", "Sodium Bicarbonate IV"]\n'
                          '  },\n'
                          '  obstetric: {\n'
                          '    diagnosis: "Pregnant patient – bleeding / pain / preeclampsia",\n'
                          '    radiology: ["US Pelvis / OB"],\n'
                          '    labs: ["BHCG (Pregnancy Test)", "CBC", "Coagulation Profile", "LFT", "RFT"],\n'
                          '    meds: ["MgSO4 IV as per protocol", "Antihypertensive IV/PO as per protocol", "Anti-D Immunoglobulin (if indicated)"]\n'
                          '  },\n'
                          '  peds_fever_sepsis: {\n'
                          '    diagnosis: "Pediatric fever / possible sepsis",\n'
                          '    radiology: ["X-Ray Chest"],\n'
                          '    labs: ["CBC", "CRP", "Blood Culture", "Urine Analysis"],\n'
                          '    meds: ["Fluid bolus 20 mL/kg", "Broad spectrum antibiotic (pediatric dose)"]\n'
                          '  }\n'
                          '};\n'
                          '\n'
                          'function clearAllBundles() {\n'
                          "  document.querySelectorAll('.rad-item, .lab-item, .med-item').forEach(function(cb) {\n"
                          '    cb.checked = false;\n'
                          '  });\n'
                          '\n'
                          '  var diagField = document.querySelector(\'textarea[name="diagnosis"]\');\n'
                          '  if (diagField) {\n'
                          "    diagField.value = '';\n"
                          '  }\n'
                          '\n'
                          "  var ta_radiology_text = document.getElementById('radiology_text');\n"
                          '  if (ta_radiology_text) {\n'
                          "    ta_radiology_text.value = '';\n"
                          '  }\n'
                          "  var ta_lab_text = document.getElementById('lab_text');\n"
                          '  if (ta_lab_text) {\n'
                          "    ta_lab_text.value = '';\n"
                          '  }\n'
                          "  var ta_med_text = document.getElementById('med_text');\n"
                          '  if (ta_med_text) {\n'
                          "    ta_med_text.value = '';\n"
                          '  }\n'
                          '}\n'
                          '\n'
                          'function applyBundle(name) {\n'
                          '  clearAllBundles();\n'
                          '  var b = bundles[name];\n'
                          '  if (!b) {\n'
                          '    return;\n'
                          '  }\n'
                          '\n'
                          '  var diagField = document.querySelector(\'textarea[name="diagnosis"]\');\n'
                          '  if (diagField && b.diagnosis) {\n'
                          '    diagField.value = b.diagnosis;\n'
                          '  }\n'
                          '\n'
                          '  function setTextarea(id, items) {\n'
                          '    var ta = document.getElementById(id);\n'
                          '    if (!ta || !items || !items.length) {\n'
                          '      return;\n'
                          '    }\n'
                          "    ta.value = items.join(', ');\n"
                          '  }\n'
                          '\n'
                          "  setTextarea('radiology_text', b.radiology || []);\n"
                          "  setTextarea('lab_text', b.labs || []);\n"
                          "  setTextarea('med_text', b.meds || []);\n"
                          '\n'
                          '  function checkByItems(selector, items) {\n'
                          '    if (!items || !items.length) {\n'
                          '      return;\n'
                          '    }\n'
                          '    var lowerItems = items.map(function(x) { return String(x).toLowerCase(); });\n'
                          '    document.querySelectorAll(selector).forEach(function(cb) {\n'
                          "      var val = String(cb.value || '').toLowerCase();\n"
                          '      var matched = lowerItems.some(function(x) {\n'
                          '        return val.indexOf(x) !== -1 || x.indexOf(val) !== -1;\n'
                          '      });\n'
                          '      if (matched) {\n'
                          '        cb.checked = true;\n'
                          '      }\n'
                          '    });\n'
                          '  }\n'
                          '\n'
                          "  checkByItems('.rad-item', b.radiology || []);\n"
                          "  checkByItems('.lab-item', b.labs || []);\n"
                          "  checkByItems('.med-item', b.meds || []);\n"
                          '}\n'                         'function addHomeMedFromSearch(){\n'
                         "  const input = document.getElementById('home_med_search');\n"
                         '  if (!input) return;\n'
                         "  const val = (input.value || '').trim();\n"
                         '  if (!val) return;\n'
                         '\n'
                         "  const ta = document.getElementById('home_medication_text');\n"
                         '  if (!ta) return;\n'
                         '\n'
                         '  const lines = ta.value ? ta.value.split(/\\r?\\n/).map(x => x.trim()).filter(Boolean) : '
                         '[];\n'
                         '  if (!lines.includes(val)) {\n'
                         '    lines.push(val);\n'
                         '  }\n'
                         '  ta.value = lines.join("\\n");\n'
                         "  input.value = '';\n"
                         '  ta.focus();\n'
                         '}\n'
                         '\n'
                         "document.addEventListener('DOMContentLoaded', function(){\n"
                         "  const homeInput = document.getElementById('home_med_search');\n"
                         '  if (homeInput) {\n'
                         "    homeInput.addEventListener('keydown', function(e){\n"
                         "      if (e.key === 'Enter') {\n"
                         '        e.preventDefault();\n'
                         '        addHomeMedFromSearch();\n'
                         '      }\n'
                         '    });\n'
                         '  }\n'
                         '});\n'
                         '</script>\n'
                         '\n'
                         '<hr class="my-4">\n'
                         '\n'
                         '<div class="row">\n'
                         '  <div class="col-md-6 mb-3">\n'
                         '    <div class="card bg-white p-3">\n'
                         '      <h6 class="fw-bold mb-2">BMI Calculator</h6>\n'
                         '      <p class="small text-muted mb-2">\n'
                         '        Automatically calculated from the weight and height recorded in the Triage form.\n'
                         '      </p>\n'
                         '      <div class="mb-1">\n'
                         '        <span class="small">BMI (kg/m²):</span>\n'
                         '        <span id="bmi-value" class="fw-bold ms-2">-</span>\n'
                         '      </div>\n'
                         '      <div class="small text-muted" id="bmi-category"></div>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '  <div class="col-md-6 mb-3">\n'
                         '    <div class="card bg-white p-3">\n'
                         '      <h6 class="fw-bold mb-2">Pediatric Dose Helper</h6>\n'
                         '      <div class="row g-2 align-items-end">\n'
                         '        <div class="col-4">\n'
                         '          <label class="form-label small mb-0">Weight (kg)</label>\n'
                         '          <input type="number" step="0.1" class="form-control form-control-sm" '
                         'id="dose-weight">\n'
                         '        </div>\n'
                         '        <div class="col-4">\n'
                         '          <label class="form-label small mb-0">Dose (mg/kg)</label>\n'
                         '          <input type="number" step="0.1" class="form-control form-control-sm" '
                         'id="dose-mgkg">\n'
                         '        </div>\n'
                         '        <div class="col-4">\n'
                         '          <label class="form-label small mb-0">Total (mg)</label>\n'
                         '          <input type="text" class="form-control form-control-sm" id="dose-total" readonly>\n'
                         '        </div>\n'
                         '      </div>\n'
                         '      <div class="row g-2 align-items-end mt-2">\n'
                         '        <div class="col-4">\n'
                         '          <label class="form-label small mb-0">Conc. (mg/mL)</label>\n'
                         '          <input type="number" step="0.01" class="form-control form-control-sm" '
                         'id="dose-mgml">\n'
                         '        </div>\n'
                         '        <div class="col-4">\n'
                         '          <label class="form-label small mb-0">Volume (mL)</label>\n'
                         '          <input type="text" class="form-control form-control-sm" id="dose-ml" readonly>\n'
                         '        </div>\n'
                         '      </div>\n'
                         '      <div class="small text-muted mt-2">\n'
                         '        For assistance only – always double-check the dose in drug references / hospital protocols. '
                         '\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '<script>\n'
                         "document.addEventListener('DOMContentLoaded', function () {\n"
                         '  var weightInput = document.querySelector(\'input[name="weight"]\');\n'
                         '  var heightInput = document.querySelector(\'input[name="height"]\');\n'
                         "  var bmiValueEl = document.getElementById('bmi-value');\n"
                         "  var bmiCatEl = document.getElementById('bmi-category');\n"
                         '\n'
                         '  function parseNumber(val) {\n'
                         '    if (!val) return NaN;\n'
                         "    return parseFloat(String(val).replace(',', '.'));\n"
                         '  }\n'
                         '\n'
                         '  function updateBmiFromFields() {\n'
                         '    if (!weightInput || !heightInput || !bmiValueEl) return;\n'
                         '    var w = parseNumber(weightInput.value);\n'
                         '    var h = parseNumber(heightInput.value);\n'
                         '    if (!w || !h || h <= 0) {\n'
                         "      bmiValueEl.textContent = '-';\n"
                         "      if (bmiCatEl) bmiCatEl.textContent = '';\n"
                         '      return;\n'
                         '    }\n'
                         '    var h_m = h / 100.0;\n'
                         '    if (h_m <= 0) {\n'
                         "      bmiValueEl.textContent = '-';\n"
                         "      if (bmiCatEl) bmiCatEl.textContent = '';\n"
                         '      return;\n'
                         '    }\n'
                         '    var bmi = w / (h_m * h_m);\n'
                         '    bmiValueEl.textContent = bmi.toFixed(1);\n'
                         '\n'
                         "    var cat = '';\n"
                         "    if (bmi < 18.5) cat = 'نقص وزن';\n"
                         "    else if (bmi < 25) cat = 'وزن طبيعي';\n"
                         "    else if (bmi < 30) cat = 'زيادة وزن';\n"
                         "    else cat = 'سمنة';\n"
                         '\n'
                         '    if (bmiCatEl) bmiCatEl.textContent = cat;\n'
                         '  }\n'
                         '\n'
                         '  if (weightInput && heightInput) {\n'
                         "    var doseWeightInput = document.getElementById('dose-weight');\n"
                         '    if (doseWeightInput && weightInput.value) {\n'
                         '      doseWeightInput.value = weightInput.value;\n'
                         '    }\n'
                         "    ['input', 'change'].forEach(function (ev) {\n"
                         '      weightInput.addEventListener(ev, updateBmiFromFields);\n'
                         '      heightInput.addEventListener(ev, updateBmiFromFields);\n'
                         '    });\n'
                         '    updateBmiFromFields();\n'
                         '  }\n'
                         '\n'
                         '  function updateDose() {\n'
                         "    var wEl = document.getElementById('dose-weight');\n"
                         "    var mgkgEl = document.getElementById('dose-mgkg');\n"
                         "    var totalEl = document.getElementById('dose-total');\n"
                         "    var mgmlEl = document.getElementById('dose-mgml');\n"
                         "    var volEl = document.getElementById('dose-ml');\n"
                         '\n'
                         '    if (!wEl || !mgkgEl || !totalEl) return;\n'
                         '\n'
                         '    var w = parseNumber(wEl.value);\n'
                         '    var mgkg = parseNumber(mgkgEl.value);\n'
                         '    if (!w || !mgkg) {\n'
                         "      totalEl.value = '';\n"
                         "      if (volEl) volEl.value = '';\n"
                         '      return;\n'
                         '    }\n'
                         '\n'
                         '    var total_mg = w * mgkg;\n'
                         '    totalEl.value = total_mg.toFixed(1);\n'
                         '\n'
                         '    if (mgmlEl && volEl) {\n'
                         '      var conc = parseNumber(mgmlEl.value);\n'
                         '      if (conc && conc > 0) {\n'
                         '        var vol = total_mg / conc;\n'
                         '        volEl.value = vol.toFixed(2);\n'
                         '      } else {\n'
                         "        volEl.value = '';\n"
                         '      }\n'
                         '    }\n'
                         '  }\n'
                         '\n'
                         "  ['dose-weight','dose-mgkg','dose-mgml'].forEach(function (id) {\n"
                         '    var el = document.getElementById(id);\n'
                         '    if (el) {\n'
                         "      ['input','change'].forEach(function (ev) {\n"
                         '        el.addEventListener(ev, updateDose);\n'
                         '      });\n'
                         '    }\n'
                         '  });\n'
                         '});\n'
                         '</script>\n'
                         '\n'
                         '{% endblock %}\n',
 'depart_workflow.html': '\n'
                         '{% extends "base.html" %}\n'
                         '{% block content %}\n'
                         '\n'
                         '<div class="d-flex justify-content-between align-items-start mb-3 flex-wrap gap-2">\n'
                         '  <div>\n'
                         '    <h4 class="mb-0">ED Depart / Discharge</h4>\n'
                         '    <div class="text-muted small">\n'
                         "      Visit {{ visit.visit_id }} &mdash; {{ visit.name }} ({{ visit.id_number or '-' }})\n"
                         '    </div>\n'
                         '  </div>\n'
                         '  <div class="text-end small">\n'
                         '    <div class="mb-1">\n'
                         "      {% set cat = (visit.triage_cat or '').lower() %}\n"
                         '      {% if cat == \'es1\' %}<span class="badge bg-danger">ES1</span>\n'
                         '      {% elif cat == \'es2\' %}<span class="badge bg-warning text-dark">ES2</span>\n'
                         '      {% elif cat == \'es3\' %}<span class="badge bg-info text-dark">ES3</span>\n'
                         '      {% elif cat == \'es4\' %}<span class="badge bg-primary">ES4</span>\n'
                         '      {% elif cat == \'es5\' %}<span class="badge bg-success">ES5</span>\n'
                         '      {% else %}<span class="badge bg-secondary">No ES</span>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '    <div class="mb-1">\n'
                         "      {% set st = (visit.status or '').upper() %}\n"
                         "      {% set st_class = 'secondary' %}\n"
                         "      {% if st == 'OPEN' %}{% set st_class = 'success' %}{% endif %}\n"
                         "      {% if st in ['DISCHARGED','TRANSFERRED','LAMA','EXPIRED','CANCELLED'] %}{% set "
                         "st_class = 'danger' %}{% endif %}\n"
                         '      <span class="badge bg-{{ st_class }}">{{ visit.status or \'-\' }}</span>\n'
                         '    </div>\n'
                         '    <div class="small text-muted">\n'
                         "      Loc: {{ visit.location or '-' }}\n"
                         '      {% if visit.bed_no %}\n'
                         "        · Bed: {{ visit.bed_no }} ({{ visit.bed_status or 'EMPTY' }})\n"
                         '      {% endif %}\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                         '  {% for category, msg in messages %}\n'
                         '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                         '  {% endfor %}\n'
                         '{% endwith %}\n'
                         '\n'
                         '<div class="row g-3">\n'
                         '  <!-- Checklist summary -->\n'
                         '  <div class="col-md-4">\n'
                         '    <div class="card p-3 bg-white h-100">\n'
                         '      <h6 class="fw-bold mb-2">Checklist overview</h6>\n'
                         '      <ul class="list-unstyled small mb-0">\n'
                         '        <li class="mb-1">\n'
                         '          {% if visit.task_reg %}\n'
                         '            ✅ Registration completed\n'
                         '          {% else %}\n'
                         '            ☐ Registration pending\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         "          {% if visit.triage_status == 'YES' %}\n"
                         "            ✅ Triage done ({{ visit.triage_cat or '-' }})\n"
                         '          {% else %}\n'
                         '            ☐ Triage pending\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         '          {% if visit.task_ekg %}\n'
                         '            ✅ ECG / EKG done\n'
                         '          {% else %}\n'
                         '            ☐ ECG / EKG pending\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         '          {% if visit.task_sepsis %}\n'
                         '            ✅ Sepsis screen done\n'
                         '          {% else %}\n'
                         '            ☐ Sepsis screen pending\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         '          {% if orders_count %}\n'
                         '            ✅ Clinical orders entered ({{ orders_count }})\n'
                         '          {% else %}\n'
                         '            ☐ No clinical orders yet\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         '          {% if labs_total %}\n'
                         '            {% if labs_pending %}\n'
                         '              ⚠️ Lab results pending ({{ labs_pending }} / {{ labs_total }})\n'
                         '            {% else %}\n'
                         '              ✅ Labs cleared ({{ labs_total }})\n'
                         '            {% endif %}\n'
                         '          {% else %}\n'
                         '            ☐ No lab requests\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         '          {% if rads_total %}\n'
                         '            {% if rads_pending %}\n'
                         '              ⚠️ Radiology pending ({{ rads_pending }} / {{ rads_total }})\n'
                         '            {% else %}\n'
                         '              ✅ Radiology cleared ({{ rads_total }})\n'
                         '            {% endif %}\n'
                         '          {% else %}\n'
                         '            ☐ No radiology requests\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '        <li class="mb-1">\n'
                         '          {% if discharge_exists %}\n'
                         '            ✅ Discharge summary saved\n'
                         '            {% if discharge_diag %}\n'
                         '              &mdash; {{ discharge_diag }}\n'
                         '            {% endif %}\n'
                         '          {% else %}\n'
                         '            ☐ Discharge summary pending\n'
                         '          {% endif %}\n'
                         '        </li>\n'
                         '      </ul>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '\n'
                         '  <!-- Editable tasks -->\n'
                         '  <div class="col-md-4">\n'
                         '    <div class="card p-3 bg-white h-100">\n'
                         '      <h6 class="fw-bold mb-2">Tasks / Checklist (editable)</h6>\n'
                         '      <form method="POST" class="small">\n'
                         '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <div class="form-check mb-1">\n'
                         '          <input class="form-check-input" type="checkbox" value="1" name="task_reg" '
                         'id="task_reg"\n'
                         '                 {% if visit.task_reg %}checked{% endif %}>\n'
                         '          <label class="form-check-label" for="task_reg">\n'
                         '            Registration completed\n'
                         '          </label>\n'
                         '        </div>\n'
                         '        <div class="form-check mb-1">\n'
                         '          <input class="form-check-input" type="checkbox" value="1" name="task_ekg" '
                         'id="task_ekg"\n'
                         '                 {% if visit.task_ekg %}checked{% endif %}>\n'
                         '          <label class="form-check-label" for="task_ekg">\n'
                         '            ECG / EKG done\n'
                         '          </label>\n'
                         '        </div>\n'
                         '        <div class="form-check mb-1">\n'
                         '          <input class="form-check-input" type="checkbox" value="1" name="task_sepsis" '
                         'id="task_sepsis"\n'
                         '                 {% if visit.task_sepsis %}checked{% endif %}>\n'
                         '          <label class="form-check-label" for="task_sepsis">\n'
                         '            Sepsis screening done\n'
                         '          </label>\n'
                         '        </div>\n'
                         '        <button class="btn btn-sm btn-primary mt-2">\n'
                         '          Save checklist\n'
                         '        </button>\n'
                         '      </form>\n'
                         '\n'
                         '      <hr class="my-3">\n'
                         '\n'
                         '      <div class="small">\n'
                         '        <div class="fw-semibold mb-1">Quick links</div>\n'
                         '        <div class="d-grid gap-1">\n'
                         '          <a class="btn btn-sm btn-outline-primary"\n'
                         '             href="{{ url_for(\'patient_details\', visit_id=visit.visit_id) }}">\n'
                         '            Open chart\n'
                         '          </a>\n'
                         '          <a class="btn btn-sm btn-outline-primary"\n'
                         '             href="{{ url_for(\'clinical_orders_page\', visit_id=visit.visit_id) }}">\n'
                         '            Clinical orders &amp; notes\n'
                         '          </a>\n'
                         '          <a class="btn btn-sm btn-outline-secondary"\n'
                         '             href="{{ url_for(\'lab_board\', status=\'PENDING\', q=visit.visit_id) }}">\n'
                         '            Lab board (this visit)\n'
                         '          </a>\n'
                         '          <a class="btn btn-sm btn-outline-secondary"\n'
                         '             href="{{ url_for(\'radiology_board\', status=\'PENDING\', q=visit.visit_id) '
                         '}}">\n'
                         '            Radiology board (this visit)\n'
                         '          </a>\n'
                         '        </div>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '\n'
                         '  <!-- Discharge / PDFs / Close -->\n'
                         '  <div class="col-md-4">\n'
                         '    <div class="card p-3 bg-white h-100">\n'
                         '      <h6 class="fw-bold mb-2">Discharge / Depart</h6>\n'
                         '\n'
                         '      <div class="small mb-2">\n'
                         '        <div>Current status:\n'
                         "          {% set st = (visit.status or '').upper() %}\n"
                         "          {% set st_class = 'secondary' %}\n"
                         "          {% if st == 'OPEN' %}{% set st_class = 'success' %}{% endif %}\n"
                         "          {% if st in ['DISCHARGED','TRANSFERRED','LAMA','EXPIRED','CANCELLED'] %}{% set "
                         "st_class = 'danger' %}{% endif %}\n"
                         '          <span class="badge bg-{{ st_class }}">{{ visit.status or \'-\' }}</span>\n'
                         '        </div>\n'
                         '        {% if visit.closed_at %}\n'
                         '          <div class="text-muted">Closed at {{ visit.closed_at }} by {{ visit.closed_by or '
                         "'-' }}</div>\n"
                         '        {% endif %}\n'
                         '      </div>\n'
                         '\n'
                         '      <div class="d-grid gap-1 small mb-2">\n'
                         '        <a class="btn btn-sm btn-outline-primary"\n'
                         '           target="_blank"\n'
                         '           href="{{ url_for(\'discharge_summary_pdf\', visit_id=visit.visit_id) }}">\n'
                         '          Discharge summary PDF\n'
                         '        </a>\n'
                         '        <a class="btn btn-sm btn-outline-primary"\n'
                         '           target="_blank"\n'
                         '           href="{{ url_for(\'auto_summary_pdf\', visit_id=visit.visit_id) }}">\n'
                         '          ED auto-summary PDF\n'
                         '        </a>\n'
                         '        <a class="btn btn-sm btn-outline-primary"\n'
                         '           target="_blank"\n'
                         '           href="{{ url_for(\'patient_summary_pdf\', visit_id=visit.visit_id) }}">\n'
                         '          Patient copy PDF\n'
                         '        </a>\n'
                         '        <a class="btn btn-sm btn-outline-secondary"\n'
                         '           target="_blank"\n'
                         '           href="{{ url_for(\'home_med_pdf\', visit_id=visit.visit_id) }}">\n'
                         '          Home medication PDF\n'
                         '        </a>\n'
                         '      </div>\n'
                         '\n'
                         "      {% if session.get('role') in ['doctor','admin'] %}\n"
                         '      <hr class="my-2">\n'
                         '      <form method="POST" action="{{ url_for(\'close_visit\', visit_id=visit.visit_id) }}" '
                         'class="small">\n'
                         '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <div class="mb-2">\n'
                         '          <label class="form-label small">Final status</label>\n'
                         '          <select name="status" class="form-select form-select-sm">\n'
                         '            {% for st in '
                         "['DISCHARGED','ADMITTED','TRANSFERRED','LAMA','EXPIRED','IN_TREATMENT','CANCELLED'] %}\n"
                         '              <option value="{{ st }}" {% if (visit.status or \'\').upper() == st '
                         '%}selected{% endif %}>{{ st }}</option>\n'
                         '            {% endfor %}\n'
                         '          </select>\n'
                         '        </div>\n'
                         '        <button class="btn btn-sm btn-danger w-100"\n'
                         '                onclick="return confirm(\'Confirm close visit with this status?\');">\n'
                         '          Close visit\n'
                         '        </button>\n'
                         '      </form>\n'
                         '      {% else %}\n'
                         '        <div class="alert alert-info small mt-2 mb-0">\n'
                         '          Final close of the visit is limited to doctors / admins.\n'
                         '        </div>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '{% endblock %}\n',
 'ed_board.html': '\n'
                  '{% extends "base.html" %}\n'
                  '{% block content %}\n'
                  '<div class="card shadow-sm mb-3 ed-board-card w-100">\n'
                  '  <div class="card-header d-flex flex-wrap justify-content-between align-items-center py-2">\n'
                  '    <div>\n'
                  '      <h5 class="mb-0">ED Board</h5>\n'
                  '      <div class="small text-muted">\n'
                  '        Realtime overview for active ED visits with triage colors &amp; wait times.\n'
                  '      </div>\n'
                  '    </div>\n'
                  '    <div class="d-flex gap-2 align-items-center mt-2 mt-md-0">\n'
                  '      {% if total %}\n'
                  '        <span class="badge bg-light text-dark border small">\n'
                  '          Total: <span class="fw-bold">{{ total }}</span>\n'
                  '        </span>\n'
                  '      {% endif %}\n'
                  '      <a class="btn btn-sm btn-outline-primary" href="{{ url_for(\'export_ed_board_csv\') }}">\n'
                  '        ⬇︎ Export CSV\n'
                  '      </a>\n'
                  '    </div>\n'
                  '  </div>\n'
                  '\n'
                  '  <div class="card-body pb-2">\n'
                  '\n'
                  '    {% if status_counts or triage_counts %}\n'
                  '    <div class="row g-2 mb-3">\n'
                  '      <div class="col-lg-5 col-md-6">\n'
                  '        <div class="border rounded-3 px-2 py-2 bg-light">\n'
                  '          <div class="small fw-bold text-muted mb-1">By status</div>\n'
                  '          {% set sc = status_counts or {} %}\n'
                  '          <div class="d-flex flex-wrap gap-1">\n'
                  '            <span class="badge rounded-pill bg-success-subtle text-success border border-success">\n'
                  '              OPEN: <span class="fw-bold">{{ sc.get(\'OPEN\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-primary-subtle text-primary border border-primary">\n'
                  '              IN_TREATMENT: <span class="fw-bold">{{ sc.get(\'IN_TREATMENT\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-info-subtle text-info border border-info">\n'
                  '              ADMITTED: <span class="fw-bold">{{ sc.get(\'ADMITTED\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-secondary-subtle text-secondary border '
                  'border-secondary">\n'
                  '              DISCHARGED: <span class="fw-bold">{{ sc.get(\'DISCHARGED\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-warning-subtle text-warning border border-warning">\n'
                  '              TRANSFERRED: <span class="fw-bold">{{ sc.get(\'TRANSFERRED\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-dark-subtle text-dark border border-dark">\n'
                  '              LAMA: <span class="fw-bold">{{ sc.get(\'LAMA\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-danger-subtle text-danger border border-danger">\n'
                  '              EXPIRED: <span class="fw-bold">{{ sc.get(\'EXPIRED\', 0) }}</span>\n'
                  '            </span>\n'
                  '            <span class="badge rounded-pill bg-light text-muted border">\n'
                  '              CANCELLED: <span class="fw-bold">{{ sc.get(\'CANCELLED\', 0) }}</span>\n'
                  '            </span>\n'
                  '          </div>\n'
                  '        </div>\n'
                  '      </div>\n'
                  '      <div class="col-lg-7 col-md-6">\n'
                  '        <div class="border rounded-3 px-2 py-2 bg-light">\n'
                  '          <div class="small fw-bold text-muted mb-1">By triage (ES)</div>\n'
                  '          {% set tc = triage_counts or {} %}\n'
                  '          <div class="d-flex flex-wrap gap-1">\n'
                  '            <span class="badge cat-red">ES1: <span class="fw-bold">{{ tc.get(\'ES1\', 0) '
                  '}}</span></span>\n'
                  '            <span class="badge cat-orange">ES2: <span class="fw-bold">{{ tc.get(\'ES2\', 0) '
                  '}}</span></span>\n'
                  '            <span class="badge cat-yellow">ES3: <span class="fw-bold">{{ tc.get(\'ES3\', 0) '
                  '}}</span></span>\n'
                  '            <span class="badge cat-green">ES4: <span class="fw-bold">{{ tc.get(\'ES4\', 0) '
                  '}}</span></span>\n'
                  '            <span class="badge cat-none">ES5: <span class="fw-bold">{{ tc.get(\'ES5\', 0) '
                  '}}</span></span>\n'
                  '          </div>\n'
                  '        </div>\n'
                  '      </div>\n'
                  '    </div>\n'
                  '    {% endif %}\n'
                  '\n'
                  '    <form class="mb-3" method="GET">\n'
                  '      <div class="row g-2 align-items-end">\n'
                  '        <div class="col-md-2 col-sm-6">\n'
                  '          <label class="form-label fw-bold small">Status</label>\n'
                  '          <select name="status" class="form-select form-select-sm" onchange="this.form.submit()">\n'
                  '            <option value="ALL" {% if status_filter==\'ALL\' %}selected{% endif %}>ALL</option>\n'
                  '            <option value="OPEN" {% if status_filter==\'OPEN\' %}selected{% endif %}>OPEN</option>\n'
                  '            <option value="IN_TREATMENT" {% if status_filter==\'IN_TREATMENT\' %}selected{% endif '
                  '%}>IN_TREATMENT</option>\n'
                  '            <option value="ADMITTED" {% if status_filter==\'ADMITTED\' %}selected{% endif '
                  '%}>ADMITTED</option>\n'
                  '            <option value="DISCHARGED" {% if status_filter==\'DISCHARGED\' %}selected{% endif '
                  '%}>DISCHARGED</option>\n'
                  '            <option value="TRANSFERRED" {% if status_filter==\'TRANSFERRED\' %}selected{% endif '
                  '%}>TRANSFERRED</option>\n'
                  '            <option value="LAMA" {% if status_filter==\'LAMA\' %}selected{% endif %}>LAMA</option>\n'
                  '            <option value="EXPIRED" {% if status_filter==\'EXPIRED\' %}selected{% endif '
                  '%}>EXPIRED</option>\n'
                  '            <option value="CANCELLED" {% if status_filter==\'CANCELLED\' %}selected{% endif '
                  '%}>CANCELLED</option>\n'
                  '          </select>\n'
                  '        </div>\n'
                  '\n'
                  '        <div class="col-md-2 col-sm-6">\n'
                  '          <label class="form-label fw-bold small">Triage ES</label>\n'
                  '          <select name="cat" class="form-select form-select-sm" onchange="this.form.submit()">\n'
                  '            <option value="ALL" {% if cat_filter==\'ALL\' %}selected{% endif %}>All ES</option>\n'
                  '            <option value="ES1" {% if cat_filter==\'ES1\' %}selected{% endif %}>ES1</option>\n'
                  '            <option value="ES2" {% if cat_filter==\'ES2\' %}selected{% endif %}>ES2</option>\n'
                  '            <option value="ES3" {% if cat_filter==\'ES3\' %}selected{% endif %}>ES3</option>\n'
                  '            <option value="ES4" {% if cat_filter==\'ES4\' %}selected{% endif %}>ES4</option>\n'
                  '            <option value="ES5" {% if cat_filter==\'ES5\' %}selected{% endif %}>ES5</option>\n'
                  '          </select>\n'
                  '        </div>\n'
                  '\n'
                  '        <div class="col-md-2 col-sm-6">\n'
                  '          <label class="form-label fw-bold small">Visit ID</label>\n'
                  '          <input class="form-control form-control-sm"\n'
                  '                 name="visit_id"\n'
                  '                 value="{{ visit_f or \'\' }}"\n'
                  '                 placeholder="ED-...">\n'
                  '        </div>\n'
                  '\n'
                  '        <div class="col-md-2 col-sm-6">\n'
                  '          <label class="form-label fw-bold small">User</label>\n'
                  '          <select class="form-select form-select-sm" name="user" onchange="this.form.submit()">\n'
                  '            <option value="">ALL</option>\n'
                  '            {% for u in users %}\n'
                  '              <option value="{{ u.created_by }}" {% if user_f==u.created_by %}selected{% endif '
                  '%}>{{ u.created_by }}</option>\n'
                  '            {% endfor %}\n'
                  '          </select>\n'
                  '        </div>\n'
                  '\n'
                  '        <div class="col-md-2 col-sm-6">\n'
                  '          <label class="form-label fw-bold small">From</label>\n'
                  '          <input class="form-control form-control-sm"\n'
                  '                 type="date"\n'
                  '                 name="date_from"\n'
                  '                 value="{{ dfrom or \'\' }}">\n'
                  '        </div>\n'
                  '\n'
                  '        <div class="col-md-2 col-sm-6">\n'
                  '          <label class="form-label fw-bold small">To</label>\n'
                  '          <div class="d-flex gap-1">\n'
                  '            <input class="form-control form-control-sm"\n'
                  '                   type="date"\n'
                  '                   name="date_to"\n'
                  '                   value="{{ dto or \'\' }}">\n'
                  '            <button class="btn btn-sm btn-outline-secondary">Go</button>\n'
                  '          </div>\n'
                  '        </div>\n'
                  '      </div>\n'
                  '    </form>\n'
                  '\n'
                  '    <div class="table-responsive">\n'
                  '      <div class="table-responsive">\n'
                  '        <table class="table table-sm table-hover align-middle mb-1 ed-board-table">\n'
                  '        <thead class="table-light">\n'
                  '          <tr>\n'
                  '            <th>Queue</th>\n'
                  '            <th>Visit</th>\n'
                  '            <th>Patient</th>\n'
                  '            <th>Age</th>\n'
                  '            <th>ID</th>\n'
                  '            <th>Insurance</th>\n'
                  '            <th>Payment</th>\n'
                  '            <th>Triage</th>\n'
                  '            <th>ES</th>\n'
                  '            <th>Wait / LOS</th>\n'
                  '            <th>Status</th>\n'
                  '            <th>User</th>\n'
                  '            <th>Created</th>\n'
                  '            <th style="width:260px;">Actions</th>\n'
                  '          </tr>\n'
                  '        </thead>\n'
                  '        <tbody>\n'
                  '          {% if not visits %}\n'
                  '            <tr>\n'
                  '              <td colspan="14" class="text-center text-muted small py-3">\n'
                  '                No visits found for current filters.\n'
                  '              </td>\n'
                  '            </tr>\n'
                  '          {% else %}\n'
                  '            {% for v in visits %}\n'
                  '            <tr class="{% if v.triage_cat==\'ES1\' %}table-danger{% elif v.triage_cat==\'ES2\' '
                  "%}table-warning{% elif v.triage_cat=='ES3' %}table-light{% elif v.triage_cat=='ES4' "
                  '%}table-primary{% elif v.triage_cat==\'ES5\' %}table-success{% endif %}">\n'
                  '              <td class="fw-bold text-center small col-queue">{{ v.queue_no }}</td>\n'
                  '              <td class="fw-bold small text-nowrap col-visit">{{ v.visit_id }}</td>\n'
                  '              <td>\n'
                  '                <div class="fw-bold">{{ v.name }}</div>\n'
                  '                <div class="small text-muted">\n'
                  "                  {% set vt = (v.visit_type or 'NEW') %}\n"
                  "                  {% if vt == 'NEW' %}\n"
                  '                    New visit\n'
                  "                  {% elif vt == 'TREATMENT' %}\n"
                  '                    Treatment\n'
                  "                  {% elif vt in ['FOLLOW_UP','FOLLOW-UP','FOLLOWUP'] %}\n"
                  '                    Follow-up\n'
                  "                  {% elif vt == 'PROCEDURE' %}\n"
                  '                    Procedure / Dressing\n'
                  '                  {% else %}\n'
                  '                    {{ vt }}\n'
                  '                  {% endif %}\n'
                  '                </div>\n'
                  '              </td>\n'
                  '              <td class="text-center small col-age">{{ v.age or \'-\' }}</td>\n'
                  '              <td class="text-nowrap small col-id">\n'
                  '                {{ v.id_number }}\n'
                  '                {% if v.id_attachment %}\n'
                  '                  <a href="{{ url_for(\'uploaded_file\', filename=v.id_attachment) }}"\n'
                  '                     target="_blank"\n'
                  '                     class="ms-1 text-decoration-none"\n'
                  '                     title="View ID / attachments">📎</a>\n'
                  '                {% endif %}\n'
                  '              </td>\n'
                  '              <td class="text-nowrap small col-insurance">{{ v.insurance }}</td>\n'
                  '              <td class="col-payment">\n'
                  '                {% if v.payment_details %}\n'
                  '                  <span class="text-muted">Recorded</span>\n'
                  '                {% else %}\n'
                  '                  -\n'
                  '                {% endif %}\n'
                  '              </td>\n'
                  '              <td>\n'
                  "                {% if v.triage_status=='YES' %}\n"
                  '                  <span class="badge badge-triage-yes">YES</span>\n'
                  '                {% else %}\n'
                  '                  <span class="badge badge-triage-no">NO</span>\n'
                  '                {% endif %}\n'
                  '              </td>\n'
                  '              <td>\n'
                  "                {% set cat = (v.triage_cat or '').lower() %}\n"
                  '                {% if cat == \'es1\' %}<span class="badge cat-red">ES1</span>\n'
                  '                {% elif cat == \'es2\' %}<span class="badge cat-orange">ES2</span>\n'
                  '                {% elif cat == \'es3\' %}<span class="badge cat-yellow">ES3</span>\n'
                  '                {% elif cat == \'es4\' %}<span class="badge cat-green">ES4</span>\n'
                  '                {% elif cat == \'es5\' %}<span class="badge cat-none">ES5</span>\n'
                  '                {% else %}<span class="badge cat-none">-</span>{% endif %}\n'
                  '              </td>\n'
                  '              <td class="wait-cell">\n'
                  '                {% if v.waiting_text %}\n'
                  "                  {% set wl = v.waiting_level or 'none' %}\n"
                  '                  <span class="wait-pill {% if wl == \'short\' %}wait-short{% elif wl == \'medium\' '
                  '%}wait-medium{% elif wl == \'long\' %}wait-long{% else %}wait-none{% endif %}">\n'
                  '                    {{ v.waiting_text }}\n'
                  '                  </span>\n'
                  '                {% else %}\n'
                  '                  <span class="text-muted">-</span>\n'
                  '                {% endif %}\n'
                  '              </td>\n'
                  '              <td class="text-center small col-status">{{ v.status }}</td>\n'
                  '              <td class="text-center small col-user">{{ v.created_by }}</td>\n'
                  '              <td class="text-nowrap small text-muted col-created">{{ v.created_at }}</td>\n'
                  '              <td>\n'
                  '                <div class="ed-actions">\n'
                  '                  <div class="ed-actions-row">\n'
                  '                    <a class="btn btn-sm btn-outline-primary"\n'
                  '                       href="{{ url_for(\'patient_details\', visit_id=v.visit_id) }}">\n'
                  '                       Open\n'
                  '                    </a>\n'
                  '                    <a class="btn btn-sm btn-outline-secondary"\n'
                  '                       target="_blank"\n'
                  '                       href="{{ url_for(\'sticker_html\', visit_id=v.visit_id) }}">\n'
                  '                       Sticker\n'
                  '                    </a>\n'
                  '                  </div>\n'
                  '                  <div class="ed-actions-row">\n'
                  "                    {% if session.get('role') in ['nurse','doctor','admin'] %}\n"
                  '                      <a class="btn btn-sm btn-outline-success"\n'
                  '                         href="{{ url_for(\'triage\', visit_id=v.visit_id) }}">\n'
                  '                         Triage\n'
                  '                      </a>\n'
                  '                    {% endif %}\n'
                  "                    {% if session.get('role') != 'reception' %}\n"
                  '                      <a class="btn btn-sm btn-outline-primary"\n'
                  '                         target="_blank"\n'
                  '                         href="{{ url_for(\'patient_summary_pdf\', visit_id=v.visit_id) }}">\n'
                  '                         Summary PDF\n'
                  '                      </a>\n'
                  '                    {% endif %}\n'
                  '                  </div>\n'
                  '                </div>\n'
                  '              </td>\n'
                  '            </tr>\n'
                  '            {% endfor %}\n'
                  '          {% endif %}\n'
                  '        </tbody>\n'
                  '      </table>\n'
                  '      </div>\n'
                  '    </div>\n'
                  '\n'
                  '  </div> <!-- /card-body -->\n'
                  '\n'
                  '  <div class="card-footer d-flex justify-content-between align-items-center py-2">\n'
                  '    <div class="small text-muted">\n'
                  '      Page {{ page }} / {{ pages }} - Total: {{ total }}\n'
                  '    </div>\n'
                  '    <div class="d-flex align-items-center gap-2">\n'
                  '      <ul class="pagination pagination-sm mb-0">\n'
                  '        <li class="page-item {% if page <= 1 %}disabled{% endif %}">\n'
                  '          <a class="page-link"\n'
                  '             href="{{ url_for(\'ed_board\',\n'
                  '                              status=status_filter,\n'
                  '                              cat=cat_filter,\n'
                  '                              visit_id=visit_f,\n'
                  '                              user=user_f,\n'
                  '                              date_from=dfrom,\n'
                  '                              date_to=dto,\n'
                  '                              page=page-1) }}">\n'
                  '            Prev\n'
                  '          </a>\n'
                  '        </li>\n'
                  '        <li class="page-item {% if page >= pages %}disabled{% endif %}">\n'
                  '          <a class="page-link"\n'
                  '             href="{{ url_for(\'ed_board\',\n'
                  '                              status=status_filter,\n'
                  '                              cat=cat_filter,\n'
                  '                              visit_id=visit_f,\n'
                  '                              user=user_f,\n'
                  '                              date_from=dfrom,\n'
                  '                              date_to=dto,\n'
                  '                              page=page+1) }}">\n'
                  '            Next\n'
                  '          </a>\n'
                  '        </li>\n'
                  '      </ul>\n'
                  '      <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">🔄 Manual '
                  'Refresh</button>\n'
                  '    </div>\n'
                  '  </div>\n'
                  '</div>\n'
                  '{% endblock %}\n',
 'edit_patient.html': '{% extends "base.html" %}\n{% block content %}\n\n<div class="d-flex justify-content-between align-items-start mb-3 flex-wrap gap-2">\n  <div>\n    <h4 class="mb-0">Edit Patient</h4>\n    <div class="text-muted small">\n      Visit {{ r.visit_id }}{% if r.queue_no %} · Queue {{ r.queue_no }}{% endif %}\n    </div>\n  </div>\n\n  <div class="text-end small">\n    {% set st = (r.status or \'\').upper() %}\n    {% set st_class = \'secondary\' %}\n    {% if st == \'OPEN\' %}\n      {% set st_class = \'success\' %}\n    {% elif st in [\'DISCHARGED\',\'TRANSFERRED\',\'LAMA\',\'EXPIRED\',\'CANCELLED\'] %}\n      {% set st_class = \'danger\' %}\n    {% endif %}\n    <div>\n      Status:\n      <span class="badge bg-{{ st_class }}">{{ r.status or \'-\' }}</span>\n    </div>\n  </div>\n</div>\n\n{% with messages = get_flashed_messages(with_categories=true) %}\n  {% if messages %}\n    {% for category, msg in messages %}\n      <div class="alert alert-{{ category }} py-2 mb-2">{{ msg }}</div>\n    {% endfor %}\n  {% endif %}\n{% endwith %}\n\n<div class="alert alert-light border-start border-3 border-warning py-2 small mb-3">\n  <strong>Note:</strong> Editing patient demographics (name, ID, DOB, sex, nationality, phone)\n  will update this patient for all visits. Payment details affect this visit only.\n</div>\n\n<form method="POST" class="card p-3 bg-white">\n  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n\n  <div class="row g-2">\n\n    <!-- Basic info -->\n    <div class="col-md-6">\n      <label class="form-label fw-bold small mb-1">\n        Name <span class="text-danger">*</span>\n      </label>\n      <input\n        class="form-control"\n        name="name"\n        value="{{ r.name }}"\n        required\n        maxlength="150">\n    </div>\n\n    <div class="col-md-3 col-sm-6">\n      <label class="form-label fw-bold small mb-1">\n        ID Number\n      </label>\n      <input\n        class="form-control"\n        name="id_number"\n        value="{{ r.id_number }}"\n        maxlength="50"\n        inputmode="numeric">\n    </div>\n\n    <div class="col-md-3 col-sm-6">\n      <label class="form-label fw-bold small mb-1">\n        Phone <span class="text-danger">*</span>\n      </label>\n      <input\n        type="tel"\n        class="form-control"\n        name="phone"\n        value="{{ r.phone }}"\n        maxlength="20">\n    </div>\n\n    <!-- Insurance -->\n    <div class="col-md-4 col-sm-6">\n      <label class="form-label fw-bold small mb-1">\n        Insurance\n      </label>\n      <input\n        class="form-control"\n        name="insurance"\n        value="{{ r.insurance }}"\n        list="insurance_list"\n        maxlength="50">\n      <datalist id="insurance_list">\n        <option value="thiqa">\n        <option value="self-pay">\n        <option value="private">\n        <option value="company">\n        <option value="other">\n      </datalist>\n    </div>\n\n    <div class="col-md-4 col-sm-6">\n      <label class="form-label fw-bold small mb-1">\n        Insurance No\n      </label>\n      <input\n        class="form-control"\n        name="insurance_no"\n        value="{{ r.insurance_no }}"\n        maxlength="50">\n    </div>\n\n    <!-- DOB + Age -->\n    <div class="col-md-4 col-sm-6">\n      <label class="form-label fw-bold small mb-1">\n        DOB <span class="text-danger">*</span>\n      </label>\n      <div class="input-group">\n        <input\n          type="text"\n          class="form-control"\n          name="dob"\n          id="dob_input"\n          value="{{ r.dob }}"\n          required\n          placeholder="YYYY-MM-DD">\n        <span class="input-group-text small">\n          Age: <span id="age_value" class="fw-bold ms-1">-</span>\n        </span>\n      </div>\n      <div class="form-text small">\n        Accepted formats: YYYY-MM-DD or DD/MM/YYYY\n      </div>\n    </div>\n\n    <!-- Sex -->\n    <div class="col-md-2 col-sm-4">\n      <label class="form-label fw-bold small mb-1">\n        Sex <span class="text-danger">*</span>\n      </label>\n      <select class="form-select" name="sex" required>\n        <option value="" {% if not r.sex %}selected{% endif %}></option>\n        <option value="M" {% if r.sex == \'M\' %}selected{% endif %}>M</option>\n        <option value="F" {% if r.sex == \'F\' %}selected{% endif %}>F</option>\n      </select>\n    </div>\n\n    <!-- Nationality -->\n    <div class="col-md-4 col-sm-8">\n      <label class="form-label fw-bold small mb-1">\n        Nationality <span class="text-danger">*</span>\n      </label>\n      <input\n        class="form-control"\n        name="nationality"\n        value="{{ r.nationality }}"\n        list="nationality_list"\n        maxlength="50"\n        required>\n      <datalist id="nationality_list">\n        <option value="EG">\n        <option value="SA">\n        <option value="IN">\n        <option value="PH">\n        <option value="PK">\n        <option value="SD">\n        <option value="Other">\n      </datalist>\n    </div>\n\n    <!-- Payment -->\n    <div class="col-12">\n      <label class="form-label fw-bold small mb-1">\n        Payment Details\n      </label>\n      <input\n        class="form-control"\n        name="payment_details"\n        value="{{ r.payment_details }}"\n        maxlength="200"\n        placeholder="e.g. Thiqa - 50 SAR co-pay / Self-pay - 200 SAR">\n      <div class="form-text small">\n        Short note about how this visit will be paid (cash, insurance, company, etc.).\n      </div>\n    </div>\n\n  </div>\n\n  <div class="mt-3 pt-3 d-flex flex-wrap justify-content-between align-items-center gap-2 border-top">\n    <div class="small text-muted">\n      Fields marked <span class="text-danger">*</span> are required.\n    </div>\n    <div class="d-flex gap-2">\n      <button class="btn btn-success" type="submit">\n        Save Changes\n      </button>\n      <a\n        class="btn btn-secondary"\n        href="{{ url_for(\'patient_details\', visit_id=r.visit_id) }}">\n        Cancel\n      </a>\n    </div>\n  </div>\n</form>\n\n<script>\n  function calcAgeFromDob(dob) {\n    if (!dob) { return ""; }\n    dob = dob.trim();\n    var parts;\n    if (dob.indexOf("-") !== -1) {\n      parts = dob.split("-");\n    } else if (dob.indexOf("/") !== -1) {\n      parts = dob.split("/");\n    } else {\n      return "";\n    }\n    parts = parts.filter(function(p) { return p; });\n    if (parts.length !== 3) { return ""; }\n\n    var yearIndex = 0;\n    for (var i = 0; i < parts.length; i++) {\n      if (parts[i].length === 4) {\n        yearIndex = i;\n        break;\n      }\n    }\n\n    var year = parseInt(parts[yearIndex], 10);\n    if (isNaN(year)) { return ""; }\n\n    var others = [];\n    for (var j = 0; j < 3; j++) {\n      if (j !== yearIndex) {\n        others.push(parseInt(parts[j], 10));\n      }\n    }\n    if (others.length !== 2 || isNaN(others[0]) || isNaN(others[1])) {\n      return "";\n    }\n\n    var month = others[0];\n    var day = others[1];\n    var born = new Date(year, month - 1, day);\n    if (isNaN(born.getTime())) { return ""; }\n\n    var today = new Date();\n    var age = today.getFullYear() - born.getFullYear();\n    var m = today.getMonth() - born.getMonth();\n    if (m < 0 || (m === 0 && today.getDate() < born.getDate())) {\n      age--;\n    }\n    if (age < 0 || age > 150) { return ""; }\n    return age;\n  }\n\n  document.addEventListener("DOMContentLoaded", function() {\n    var dobInput = document.getElementById("dob_input");\n    var ageSpan = document.getElementById("age_value");\n\n    function updateAge() {\n      if (!dobInput || !ageSpan) return;\n      var age = calcAgeFromDob(dobInput.value);\n      ageSpan.textContent = age ? (age + " yrs") : "-";\n    }\n\n    if (dobInput && ageSpan) {\n      updateAge();\n      ["change", "blur", "keyup"].forEach(function(evt) {\n        dobInput.addEventListener(evt, updateAge);\n      });\n    }\n  });\n</script>\n\n{% endblock %}\n',

 'lab_board.html': '\n'
                   '{% extends "base.html" %}\n'
                   '{% block content %}\n'
                   '<h4 class="mb-3">Lab Board</h4>\n'
                   '\n'
                   '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                   '  {% for category, msg in messages %}\n'
                   '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                   '  {% endfor %}\n'
                   '{% endwith %}\n'
                   '\n'
                   '<form method="GET" class="card p-2 mb-3 bg-white">\n'
                   '  <div class="row g-2 align-items-end">\n'
                   '    <div class="col-md-3 col-sm-4">\n'
                   '      <label class="form-label fw-bold small mb-1">Status</label>\n'
                   '      <select name="status" class="form-select form-select-sm">\n'
                   '        <option value="PENDING" {% if status_filter==\'PENDING\' %}selected{% endif %}>Pending / '
                   'Received</option>\n'
                   '        <option value="REPORTED" {% if status_filter==\'REPORTED\' %}selected{% endif '
                   '%}>Reported</option>\n'
                   '        <option value="ALL" {% if status_filter==\'ALL\' %}selected{% endif %}>All</option>\n'
                   '      </select>\n'
                   '    </div>\n'
                   '    <div class="col-md-3 col-sm-8">\n'
                   '      <label class="form-label fw-bold small mb-1">Search</label>\n'
                   '      <input type="text"\n'
                   '             name="q"\n'
                   '             value="{{ q or \'\' }}"\n'
                   '             class="form-control form-control-sm"\n'
                   '             placeholder="Name / ID / Visit / Test">\n'
                   '    </div>\n'
                   '    <div class="col-md-2 col-sm-6">\n'
                   '      <label class="form-label fw-bold small mb-1">From (requested)</label>\n'
                   '      <input type="date" name="date_from" value="{{ date_from or \'\' }}"\n'
                   '             class="form-control form-control-sm">\n'
                   '    </div>\n'
                   '    <div class="col-md-2 col-sm-6">\n'
                   '      <label class="form-label fw-bold small mb-1">To (requested)</label>\n'
                   '      <input type="date" name="date_to" value="{{ date_to or \'\' }}"\n'
                   '             class="form-control form-control-sm">\n'
                   '    </div>\n'
                   '    <div class="col-md-2 col-sm-6">\n'
                   '      <label class="form-label fw-bold small mb-1">&nbsp;</label>\n'
                   '      <div class="d-flex gap-1">\n'
                   '        <button class="btn btn-sm btn-primary flex-fill">Search / Filter</button>\n'
                   '        <a class="btn btn-sm btn-outline-secondary"\n'
                   '           href="{{ url_for(\'export_labs_csv\', status=status_filter, q=q, date_from=date_from, '
                   'date_to=date_to) }}">\n'
                   '          ⬇︎ CSV\n'
                   '        </a>\n'
                   '      </div>\n'
                   '    </div>\n'
                   '  </div>\n'
                   '</form>\n'
                   '\n'
                   '{% if status_counts is defined %}\n'
                   '<div class="card p-2 mb-2">\n'
                   '  <div class="small d-flex flex-wrap align-items-center gap-3">\n'
                   '    <span class="text-muted">Summary:</span>\n'
                   '    <span>\n'
                   '      Pending: {{ pending_count }}\n'
                   '      <span class="text-muted">(\n'
                   "        Requested: {{ status_counts.get('REQUESTED', 0) }},\n"
                   "        Received: {{ status_counts.get('RECEIVED', 0) }}\n"
                   '      )</span>\n'
                   '    </span>\n'
                   '    <span>\n'
                   "      Reported: {{ status_counts.get('REPORTED', 0) }}\n"
                   '    </span>\n'
                   '  </div>\n'
                   '</div>\n'
                   '{% endif %}\n'
                   '\n'
                   '<table class="table table-sm table-striped table-hover bg-white align-middle">\n'
                   '  <thead class="table-light">\n'
                   '    <tr>\n'
                   '      <th style="width:60px;">#</th>\n'
                   '      <th>Visit</th>\n'
                   '      <th>Patient</th>\n'
                   '      <th>ID</th>\n'
                   '      <th>Test</th>\n'
                   '      <th>Status</th>\n'
                   '      <th>Age / TAT</th>\n'
                   '      <th>Result</th>\n'
                   '      <th style="width:220px;">Actions</th>\n'
                   '    </tr>\n'
                   '  </thead>\n'
                   '  <tbody>\n'
                   '  {% if not rows %}\n'
                   '    <tr>\n'
                   '      <td colspan="9" class="text-center text-muted small py-3">\n'
                   '        No lab requests found for current filter.\n'
                   '      </td>\n'
                   '    </tr>\n'
                   '  {% else %}\n'
                   '    {# Group by visit so each visit/patient appears once in main rows #}\n'
                   "    {% for group in rows|groupby('visit_id') %}\n"
                   '      {% set r0 = group.list[0] %}\n'
                   '      <tr class="lab-group-row" data-visit="{{ group.grouper }}">\n'
                   '        <td>{{ loop.index }}</td>\n'
                   '        <td class="fw-bold">\n'
                   '          <a href="javascript:void(0)" class="lab-toggle" data-visit="{{ group.grouper }}">\n'
                   '            {{ group.grouper }}\n'
                   '          </a>\n'
                   '        </td>\n'
                   '        <td>\n'
                   '          <a href="javascript:void(0)" class="lab-toggle" data-visit="{{ group.grouper }}">\n'
                   '            {{ r0.name }}\n'
                   '          </a>\n'
                   '        </td>\n'
                   '        <td>\n'
                   '          <a href="javascript:void(0)" class="lab-toggle" data-visit="{{ group.grouper }}">\n'
                   "            {{ r0.id_number or '-' }}\n"
                   '          </a>\n'
                   '        </td>\n'
                   '        <td colspan="5" class="text-muted small">\n'
                   '          Click patient name / visit / ID to show tests for this visit.\n'
                   '        </td>\n'
                   '      </tr>\n'
                   '\n'
                   '      {% for r in group.list %}\n'
                   '      <tr class="lab-test-row d-none" data-visit="{{ group.grouper }}">\n'
                   '        <td></td>\n'
                   '        <td></td>\n'
                   '        <td></td>\n'
                   '        <td></td>\n'
                   '        <td>{{ r.test_name }}</td>\n'
                   '        <td>\n'
                   "          {% if r.status == 'REQUESTED' %}\n"
                   '            <span class="badge bg-secondary">Requested</span>\n'
                   "          {% elif r.status == 'COLLECTED' %}\n"
                   '            <span class="badge bg-info text-dark">Collected</span>\n'
                   "          {% elif r.status == 'RECEIVED' %}\n"
                   '            <span class="badge bg-warning text-dark">Received in lab</span>\n'
                   "          {% elif r.status == 'IN_LAB' %}\n"
                   '            <span class="badge bg-primary">In lab</span>\n'
                   "          {% elif r.status == 'REPORTED' %}\n"
                   '            <span class="badge bg-success">Reported</span>\n'
                   '          {% else %}\n'
                   '            <span class="badge bg-light text-muted">{{ r.status }}</span>\n'
                   '          {% endif %}\n'
                   '        </td>\n'
                   '        <td>\n'
                   '          {% if r.age_minutes is not none %}\n'
                   "            {% if r.age_level == 'long' %}\n"
                   '              <span class="badge text-bg-danger">{{ r.age_text }}</span>\n'
                   "            {% elif r.age_level == 'medium' %}\n"
                   '              <span class="badge text-bg-warning text-dark">{{ r.age_text }}</span>\n'
                   "            {% elif r.age_level == 'short' %}\n"
                   '              <span class="badge text-bg-light text-muted">{{ r.age_text }}</span>\n'
                   '            {% else %}\n'
                   '              <span class="badge text-bg-light text-muted">{{ r.age_text }}</span>\n'
                   '            {% endif %}\n'
                   '          {% else %}\n'
                   '            <span class="text-muted">-</span>\n'
                   '          {% endif %}\n'
                   '        </td>\n'
                   "        {% set _rt = (r.result_text or '')|lower %}\n"
                   "        {% set _abn = 'high' in _rt or 'low' in _rt or 'crit' in _rt or 'abnormal' in _rt or "
                   "'positive' in _rt or 'pos ' in _rt or 'pos.' in _rt or 'مرتفع' in _rt or 'منخفض' in _rt or "
                   "'ايجابي' in _rt or 'إيجابي' in _rt %}\n"
                   '        <td style="max-width:260px; white-space:pre-wrap; font-size:0.85rem;" {% if _abn '
                   '%}class="text-danger fw-bold"{% endif %}>\n'
                   "          {{ r.result_text or '-' }}\n"
                   '        </td>\n'
                   '        <td>\n'
                   '          <div class="d-flex flex-column gap-1">\n'
                   "            {% if session.get('role') in ['lab','admin'] %}\n"
                   "              {% if r.status == 'REQUESTED' %}\n"
                   '                <form method="POST"\n'
                   '                      action="{{ url_for(\'lab_collect_sample\', rid=r.id) }}">\n'
                   '                  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                   '                  <button class="btn btn-sm btn-outline-primary w-100">\n'
                   '                    🩸 Collect sample\n'
                   '                  </button>\n'
                   '                </form>\n'
                   "              {% elif r.status == 'COLLECTED' %}\n"
                   '                <form method="POST"\n'
                   '                      action="{{ url_for(\'lab_receive_sample\', rid=r.id) }}">\n'
                   '                  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                   '                  <button class="btn btn-sm btn-outline-primary w-100">\n'
                   '                    ✅ Receive in lab\n'
                   '                  </button>\n'
                   '                </form>\n'
                   "              {% elif r.status == 'RECEIVED' %}\n"
                   '                <form method="POST"\n'
                   '                      action="{{ url_for(\'lab_start_in_lab\', rid=r.id) }}">\n'
                   '                  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                   '                  <button class="btn btn-sm btn-outline-primary w-100">\n'
                   '                    ▶ Start in lab\n'
                   '                  </button>\n'
                   '                </form>\n'
                   '              {% endif %}\n'
                   '\n'
                   '              {# Result entry allowed once sample at least collected #}\n'
                   "              {% if r.status in ['COLLECTED','RECEIVED','IN_LAB','REPORTED'] %}\n"
                   '                <button class="btn btn-sm btn-outline-secondary w-100 mt-1"\n'
                   '                        type="button"\n'
                   '                        data-bs-toggle="modal"\n'
                   '                        data-bs-target="#labResultModal"\n'
                   '                        data-rid="{{ r.id }}"\n'
                   '                        data-visit="{{ r.visit_id }}"\n'
                   '                        data-patient="{{ r.name }}"\n'
                   '                        data-test="{{ r.test_name }}"\n'
                   '                        data-result="{{ (r.result_text or \'\')|e }}"\n'
                   '                        data-url="{{ url_for(\'lab_report_result\', rid=r.id) }}">\n'
                   '                  ✏️ Edit / Add Result\n'
                   '                </button>\n'
                   '              {% endif %}\n'
                   '\n'
                   "              {% if r.status == 'REPORTED' %}\n"
                   '                <div class="mt-1">\n'
                   '                  <span class="badge text-bg-light text-muted">\n'
                   "                    Reported by {{ r.reported_by or '?' }}\n"
                   '                  </span>\n'
                   '                </div>\n'
                   '              {% endif %}\n'
                   '            {% endif %}\n'
                   '\n'
                   "            {% if session.get('role') in ['lab','admin'] %}\n"
                   '              <form method="POST"\n'
                   '                    enctype="multipart/form-data"\n'
                   '                    action="{{ url_for(\'lab_upload_result_file\', rid=r.id) }}"\n'
                   '                    class="d-flex gap-1 mt-1">\n'
                   '                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                   '                <input type="file" name="file" class="form-control form-control-sm">\n'
                   '                <button class="btn btn-sm btn-outline-secondary">Upload</button>\n'
                   '              </form>\n'
                   '            {% endif %}\n'
                   '          </div>\n'
                   '        </td>\n'
                   '      </tr>\n'
                   '      {% endfor %}\n'
                   '    {% endfor %}\n'
                   '  {% endif %}\n'
                   '</tbody>\n'
                   '\n'
                   '</table>\n'
                   '\n'
                   '<div class="small text-muted mt-2">Showing latest 500 requests (before filters & limits).</div>\n'
                   '\n'
                   '<!-- Lab Result Modal -->\n'
                   '<div class="modal fade" id="labResultModal" tabindex="-1" aria-hidden="true">\n'
                   '  <div class="modal-dialog modal-lg modal-dialog-scrollable">\n'
                   '    <div class="modal-content">\n'
                   '      <form method="POST" id="labResultForm">\n'
                   '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                   '        <div class="modal-header">\n'
                   '          <h5 class="modal-title">Lab Result</h5>\n'
                   '          <button type="button" class="btn-close" data-bs-dismiss="modal" '
                   'aria-label="Close"></button>\n'
                   '        </div>\n'
                   '        <div class="modal-body">\n'
                   '          <div class="small text-muted mb-2" id="labResultMeta"></div>\n'
                   '          <div class="mb-2">\n'
                   '            <label class="form-label small">Result</label>\n'
                   '            <textarea class="form-control" name="result_text" id="labResultText" rows="5"\n'
                   '                      placeholder="Enter result..."></textarea>\n'
                   '          </div>\n'
                   '        </div>\n'
                   '        <div class="modal-footer">\n'
                   '          <button type="button" class="btn btn-sm btn-outline-secondary" '
                   'data-bs-dismiss="modal">Close</button>\n'
                   '          <button type="submit" class="btn btn-sm btn-primary">Save Result</button>\n'
                   '        </div>\n'
                   '      </form>\n'
                   '    </div>\n'
                   '  </div>\n'
                   '</div>\n'
                   '\n'
                   '<script>\n'
                   "  document.addEventListener('DOMContentLoaded', function () {\n"
                   "    var labModal = document.getElementById('labResultModal');\n"
                   '    if (labModal) {\n'
                   "      labModal.addEventListener('show.bs.modal', function (event) {\n"
                   '        var button = event.relatedTarget;\n'
                   '        if (!button) return;\n'
                   "        var visit = button.getAttribute('data-visit') || '';\n"
                   "        var patient = button.getAttribute('data-patient') || '';\n"
                   "        var test = button.getAttribute('data-test') || '';\n"
                   "        var result = button.getAttribute('data-result') || '';\n"
                   "        var url = button.getAttribute('data-url') || '';\n"
                   '\n'
                   "        var form = document.getElementById('labResultForm');\n"
                   "        var textArea = document.getElementById('labResultText');\n"
                   "        var meta = document.getElementById('labResultMeta');\n"
                   '\n'
                   '        if (form) form.action = url;\n'
                   '        if (textArea) textArea.value = result;\n'
                   "        if (meta) meta.textContent = 'Visit ' + visit + ' - ' + patient + ' - ' + test;\n"
                   '      });\n'
                   '    }\n'
                   '\n'
                   '    // Toggle tests per visit/patient\n'
                   "    var toggleButtons = document.querySelectorAll('.lab-toggle');\n"
                   '    toggleButtons.forEach(function (btn) {\n'
                   "      btn.addEventListener('click', function (e) {\n"
                   '        e.preventDefault();\n'
                   "        var visit = btn.getAttribute('data-visit');\n"
                   '        if (!visit) return;\n'
                   '        var rows = document.querySelectorAll(\'tr.lab-test-row[data-visit="\' + visit + \'"]\');\n'
                   '        if (!rows.length) return;\n'
                   '\n'
                   '        var anyHidden = false;\n'
                   '        rows.forEach(function (row) {\n'
                   "          if (row.classList.contains('d-none')) {\n"
                   '            anyHidden = true;\n'
                   '          }\n'
                   '        });\n'
                   '\n'
                   '        rows.forEach(function (row) {\n'
                   '          if (anyHidden) {\n'
                   "            row.classList.remove('d-none');\n"
                   '          } else {\n'
                   "            row.classList.add('d-none');\n"
                   '          }\n'
                   '        });\n'
                   '      });\n'
                   '    });\n'
                   '  });\n'
                   '</script>\n'
                   '\n'
                   '\n'
                   '{% endblock %}\n',
 'login.html': '\n'
               '{% extends "base.html" %}\n'
               '{% block content %}\n'
               '<div class="row justify-content-center mt-5">\n'
               '  <div class="col-md-4">\n'
               '    <h4 class="mb-3 text-center">Login</h4>\n'
               '    {% with messages = get_flashed_messages(with_categories=true) %}\n'
               '      {% for category, msg in messages %}\n'
               '        <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
               '      {% endfor %}\n'
               '    {% endwith %}\n'
               '    <form method="POST">\n'
               '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
               '      <input class="form-control mb-2" name="username" placeholder="username">\n'
               '      <input class="form-control mb-2" name="password" placeholder="password" type="password">\n'
               '      <button class="btn btn-primary w-100">Login</button>\n'
               '    </form>\n'
               '    <div class="text-muted small mt-2">\n'
               '    </div>\n'
               '  </div>\n'
               '</div>\n'
               '{% endblock %}\n',
 'patient_details.html': '\n'
                         '{% extends "base.html" %}\n'
                         '{% block content %}\n'
                         '\n'
                         '<div class="d-flex justify-content-between align-items-start mb-3 flex-wrap gap-2">\n'
                         '  <div>\n'
                         '    <h4 class="mb-0">Patient Details</h4>\n'
                         '    <div class="text-muted small">Visit {{ visit.visit_id }}</div>\n'
                         '  </div>\n'
                         '  <div class="text-end">\n'
                         '    <div class="mb-1">\n'
                         "      {% set cat = (visit.triage_cat or '').lower() %}\n"
                         '      {% if cat == \'es1\' %}<span class="badge bg-danger">ES1</span>\n'
                         '      {% elif cat == \'es2\' %}<span class="badge bg-warning text-dark">ES2</span>\n'
                         '      {% elif cat == \'es3\' %}<span class="badge bg-info text-dark">ES3</span>\n'
                         '      {% elif cat == \'es4\' %}<span class="badge bg-primary">ES4</span>\n'
                         '      {% elif cat == \'es5\' %}<span class="badge bg-success">ES5</span>\n'
                         '      {% else %}<span class="badge bg-secondary">No ES</span>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '    <div>\n'
                         "      {% set st = (visit.status or '').upper() %}\n"
                         "      {% set st_class = 'secondary' %}\n"
                         "      {% if st == 'OPEN' %}{% set st_class = 'success' %}{% endif %}\n"
                         "      {% if st in ['DISCHARGED','TRANSFERRED','LAMA','EXPIRED','CANCELLED'] %}{% set "
                         "st_class = 'danger' %}{% endif %}\n"
                         '      <span class="badge bg-{{ st_class }}">{{ visit.status or \'-\' }}</span>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '\n'
                         '</div>\n'
                         '\n'
                         '<div class="alert d-flex justify-content-between align-items-center px-3 py-2 mb-3 {% if '
                         "(visit.allergy_status or '').upper() == 'YES' %}banner-allergy{% else %}banner-no-allergy{% "
                         'endif %}">\n'
                         '  <div class="d-flex align-items-center gap-2">\n'
                         '    <span class="badge {% if (visit.allergy_status or \'\').upper() == \'YES\' %}bg-danger{% '
                         'else %}bg-secondary{% endif %}">\n'
                         '      ALLERGY\n'
                         '    </span>\n'
                         '    <div class="small">\n'
                         "      {% set alg_status = (visit.allergy_status or '').upper() %}\n"
                         "      {% if alg_status == 'YES' %}\n"
                         "        <strong>{{ visit.allergy_details or 'Allergy documented' }}</strong>\n"
                         "      {% elif alg_status == 'NKDA' %}\n"
                         '        NKDA (No known drug allergy)\n'
                         '      {% elif alg_status %}\n'
                         '        {{ alg_status }}\n'
                         '      {% else %}\n'
                         '        No allergy info recorded.\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '  </div>\n'
                         '  <div class="small text-muted text-end">\n'
                         "    Loc: {{ visit.location or '-' }}\n"
                         '    {% if visit.bed_no %}\n'
                         "      · Bed: {{ visit.bed_no }} ({{ visit.bed_status or 'EMPTY' }})\n"
                         '    {% endif %}\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                         '\n'
                         '  {% for category, msg in messages %}\n'
                         '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                         '  {% endfor %}\n'
                         '{% endwith %}\n'
                         '\n'
                         '<div class="card mb-3">\n'
                         '  <div class="card-body">\n'
                         '    <div class="row g-3">\n'
                         '      <div class="col-md-6">\n'
                         '        <h6 class="fw-bold text-muted mb-2">Patient &amp; Contact</h6>\n'
                         '        <dl class="row mb-0 small">\n'
                         '          <dt class="col-4">Name</dt>\n'
                         '          <dd class="col-8 fw-semibold">{{ visit.name }}</dd>\n'
                         '\n'
                         '          <dt class="col-4">ID</dt>\n'
                         '          <dd class="col-8">{{ visit.id_number or \'-\' }}</dd>\n'
                         '\n'
                         '          <dt class="col-4">Phone</dt>\n'
                         '          <dd class="col-8">{{ visit.phone or \'-\' }}</dd>\n'
                         '\n'
                         '          <dt class="col-4">Nationality</dt>\n'
                         '          <dd class="col-8">{{ visit.nationality or \'-\' }}</dd>\n'
                         '\n'
                         '          <dt class="col-4">Visit type</dt>\n'
                         '          <dd class="col-8">\n'
                         "            {% set vt = (visit.visit_type or 'NEW') %}\n"
                         "            {% if vt == 'NEW' %}\n"
                         '              New visit\n'
                         "            {% elif vt == 'TREATMENT' %}\n"
                         '              Treatment\n'
                         "            {% elif vt in ['FOLLOW_UP','FOLLOW-UP','FOLLOWUP'] %}\n"
                         '              Follow-up\n'
                         "            {% elif vt == 'PROCEDURE' %}\n"
                         '              Procedure / Dressing\n'
                         '            {% else %}\n'
                         '              {{ vt }}\n'
                         '            {% endif %}\n'
                         '          </dd>\n'
                         '        </dl>\n'
                         '      </div>\n'
                         '      <div class="col-md-6">\n'
                         '        <h6 class="fw-bold text-muted mb-2">Insurance &amp; Financial</h6>\n'
                         '        <dl class="row mb-0 small">\n'
                         '          <dt class="col-4">Insurance</dt>\n'
                         '          <dd class="col-8">{{ visit.insurance or \'-\' }}</dd>\n'
                         '\n'
                         '          <dt class="col-4">Insurance No</dt>\n'
                         '          <dd class="col-8">{{ visit.insurance_no or \'-\' }}</dd>\n'
                         '\n'
                         '          <dt class="col-4">Payment</dt>\n'
                         '          <dd class="col-8">{{ visit.payment_details or \'-\' }}</dd>\n'
                         '        </dl>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '\n'
                         '    <hr class="my-3">\n'
                         '\n'
                         '    <div class="row g-3">\n'
                         '      <div class="col-md-6">\n'
                         '        <h6 class="fw-bold text-muted mb-2">Clinical Information</h6>\n'
                         '        <div class="small mb-2">\n'
                         '          <span class="fw-semibold">Patient Complaint:</span>\n'
                         "          <div>{{ visit.comment or '-' }}</div>\n"
                         '        </div>\n'
                         '        <div class="small mb-2">\n'
                         '          <span class="fw-semibold">Allergy:</span>\n'
                         '          <div>\n'
                         "            {{ visit.allergy_status or '-' }}\n"
                         '            {% if visit.allergy_details %}\n'
                         '              - {{ visit.allergy_details }}\n'
                         '            {% endif %}\n'
                         '          </div>\n'
                         '        </div>\n'
                         '        <div class="small mb-2">\n'
                         '          <span class="fw-semibold">Triage Status:</span>\n'
                         '          <span class="ms-1">{{ visit.triage_status }}</span>\n'
                         '        </div>\n'
                         '      </div>\n'
                         '      <div class="col-md-6">\n'
                         '        <h6 class="fw-bold text-muted mb-2">Vital Signs</h6>\n'
                         '        <div class="d-flex flex-wrap gap-1 small">\n'
                         '          <span class="badge text-bg-light">PR: {{ visit.pulse_rate or \'-\' }} bpm</span>\n'
                         '          <span class="badge text-bg-light">RR: {{ visit.resp_rate or \'-\' }}/min</span>\n'
                         '          <span class="badge text-bg-light">BP: {{ visit.bp_systolic or \'-\' }}/{{ '
                         "visit.bp_diastolic or '-' }}</span>\n"
                         '          <span class="badge text-bg-light">Temp: {{ visit.temperature or \'-\' }} '
                         '°C</span>\n'
                         '          <span class="badge text-bg-light">SpO2: {{ visit.spo2 or \'-\' }}%</span>\n'
                         '          <span class="badge text-bg-light">Pain: {{ visit.pain_score or \'-\' }}/10</span>\n'
                         '          <span class="badge text-bg-light">Consciousness: {{ visit.consciousness_level or '
                         "'-' }}</span>\n"
                         '          <span class="badge text-bg-light">Wt: {{ visit.weight or \'-\' }} kg</span>\n'
                         '          <span class="badge text-bg-light">Ht: {{ visit.height or \'-\' }} cm</span>\n'
                         '        </div>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '\n'
                         "    {% if visit.status == 'CANCELLED' %}\n"
                         '    <hr class="my-3">\n'
                         '    <div class="row g-2 small">\n'
                         '      <div class="col-md-6">\n'
                         '        <span class="fw-semibold">Cancel Reason:</span>\n'
                         '        <span class="ms-1">{{ visit.cancel_reason or \'-\' }}</span>\n'
                         '      </div>\n'
                         '      <div class="col-md-6">\n'
                         '        <span class="fw-semibold">Cancelled By:</span>\n'
                         '        <span class="ms-1">{{ visit.cancelled_by or \'-\' }}</span>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '    {% endif %}\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '<div class="card p-3 bg-white mb-3">\n'
                         '  <div class="d-flex justify-content-between align-items-center mb-2">\n'
                         '    <h6 class="fw-bold mb-0">Location / Bed</h6>\n'
                         '    <div class="small text-muted">Greaseboard-style slot</div>\n'
                         '  </div>\n'
                         '  <div class="row g-2 align-items-end small">\n'
                         '    <div class="col-md-3 col-4">\n'
                         '      <label class="form-label mb-1">Location</label>\n'
                         "      <div>{{ visit.location or '-' }}</div>\n"
                         '    </div>\n'
                         '    <div class="col-md-2 col-4">\n'
                         '      <label class="form-label mb-1">Bed</label>\n'
                         "      <div>{{ visit.bed_no or '-' }}</div>\n"
                         '    </div>\n'
                         '    <div class="col-md-3 col-4">\n'
                         '      <label class="form-label mb-1">Bed Status</label>\n'
                         "      <div>{{ visit.bed_status or '-' }}</div>\n"
                         '    </div>\n'
                         '    <div class="col-md-4">\n'
                         "      {% if session.get('role') in ['reception','nurse','doctor','admin'] %}\n"
                         '      <form class="row g-1 align-items-end" method="POST" action="{{ '
                         'url_for(\'update_location_bed\', visit_id=visit.visit_id) }}">\n'
                         '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <div class="col-4">\n'
                         '          <label class="form-label mb-1 small">Loc</label>\n'
                         '          <input type="text" class="form-control form-control-sm" name="location" value="{{ '
                         'visit.location or \'\' }}" placeholder="WR / R1 / FT">\n'
                         '        </div>\n'
                         '        <div class="col-3">\n'
                         '          <label class="form-label mb-1 small">Bed</label>\n'
                         '          <input type="text" class="form-control form-control-sm" name="bed_no" value="{{ '
                         'visit.bed_no or \'\' }}">\n'
                         '        </div>\n'
                         '        <div class="col-3">\n'
                         '          <label class="form-label mb-1 small">Status</label>\n'
                         "          {% set bs = (visit.bed_status or '').upper() %}\n"
                         '          <select class="form-select form-select-sm" name="bed_status">\n'
                         '            <option value="" {% if not bs %}selected{% endif %}>-</option>\n'
                         '            <option value="EMPTY" {% if bs == \'EMPTY\' %}selected{% endif '
                         '%}>EMPTY</option>\n'
                         '            <option value="OCCUPIED" {% if bs == \'OCCUPIED\' %}selected{% endif '
                         '%}>OCCUPIED</option>\n'
                         '            <option value="DIRTY" {% if bs == \'DIRTY\' %}selected{% endif '
                         '%}>DIRTY</option>\n'
                         '          </select>\n'
                         '        </div>\n'
                         '        <div class="col-2">\n'
                         '          <button class="btn btn-sm btn-primary w-100">Save</button>\n'
                         '        </div>\n'
                         '      </form>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         "{% if session.get('role') == 'reception' %}\n"
                         '<div class="card p-3 bg-white mb-3">\n'
                         '  <h6 class="fw-bold mb-2">Investigations (Read-only for Reception)</h6>\n'
                         '  <div class="row">\n'
                         '    <div class="col-md-6 mb-2">\n'
                         '      <strong>Lab Results:</strong>\n'
                         '      {% if lab_reqs %}\n'
                         '        <ul class="small mb-0">\n'
                         '          {% for l in lab_reqs %}\n'
                         "            {% if l.status == 'REPORTED' %}\n"
                         "              {% set _rt = (l.result_text or '')|lower %}\n"
                         "              {% set _abn = 'high' in _rt or 'low' in _rt or 'crit' in _rt or 'abnormal' in "
                         "_rt or 'positive' in _rt or 'pos ' in _rt or 'pos.' in _rt or 'مرتفع' in _rt or 'منخفض' in "
                         "_rt or 'ايجابي' in _rt or 'إيجابي' in _rt %}\n"
                         '              <li{% if _abn %} class="text-danger fw-bold"{% endif %}>{{ l.test_name }}: {{ '
                         "l.result_text or '-' }}</li>\n"
                         '            {% else %}\n'
                         '              <li>{{ l.test_name }} - {{ l.status }}</li>\n'
                         '            {% endif %}\n'
                         '          {% endfor %}\n'
                         '        </ul>\n'
                         '      {% else %}\n'
                         '        <div class="small text-muted">No lab requests for this visit.</div>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '    <div class="col-md-6 mb-2">\n'
                         '      <strong>Radiology Reports:</strong>\n'
                         '      {% if rad_reqs %}\n'
                         '        <ul class="small mb-0">\n'
                         '          {% for r in rad_reqs %}\n'
                         "            {% if r.status == 'REPORTED' %}\n"
                         "              <li>{{ r.test_name }}: {{ r.report_text or '-' }}</li>\n"
                         '            {% else %}\n'
                         '              <li>{{ r.test_name }} - {{ r.status }}</li>\n'
                         '            {% endif %}\n'
                         '          {% endfor %}\n'
                         '        </ul>\n'
                         '      {% else %}\n'
                         '        <div class="small text-muted">No radiology requests for this visit.</div>\n'
                         '      {% endif %}\n'
                         '    </div>\n'
                         '  </div>\n'
                         '  <div class="small text-muted mt-1">\n'
                         '    * View only - reception cannot edit results.\n'
                         '  </div>\n'
                         '</div>\n'
                         '{% endif %}\n'
                         '\n'
                         '<div class="card p-3 bg-white mb-3">\n'
                         '  <h6 class="fw-bold mb-2">Quick Actions</h6>\n'
                         '  <div class="d-flex gap-2 flex-wrap">\n'
                         "    {% if session.get('role') in ['reception','admin'] %}\n"
                         '      <a class="btn btn-sm btn-outline-warning" href="{{ url_for(\'edit_patient\', '
                         'visit_id=visit.visit_id) }}">Edit Patient</a>\n'
                         '    {% endif %}\n'
                         '\n'
                         '    <a class="btn btn-sm btn-outline-secondary"\n'
                         '       target="_blank"\n'
                         '       href="{{ url_for(\'triage_pdf\', visit_id=visit.visit_id) }}">\n'
                         '      Print Triage PDF\n'
                         '    </a>\n'
                         '    <a class="btn btn-sm btn-outline-secondary"\n'
                         '       target="_blank"\n'
                         '       href="{{ url_for(\'lab_results_pdf\', visit_id=visit.visit_id) }}">\n'
                         '      Print Lab Results PDF\n'
                         '    </a>\n'
                         '    <a class="btn btn-sm btn-outline-secondary"\n'
                         '       target="_blank"\n'
                         '       href="{{ url_for(\'radiology_results_pdf\', visit_id=visit.visit_id) }}">\n'
                         '      Print Radiology PDF\n'
                         '    </a>\n'
                         '    <a class="btn btn-sm btn-outline-secondary"\n'
                         '       target="_blank"\n'
                         '       href="{{ url_for(\'auto_summary_pdf\', visit_id=visit.visit_id) }}">\n'
                         '      Auto-Summary PDF\n'
                         '    </a>\n'
                         '\n'
                         "    {% if session.get('role') in ['nurse','doctor','admin'] %}\n"
                         '      <a class="btn btn-sm btn-success" href="{{ url_for(\'triage\', '
                         'visit_id=visit.visit_id) }}">Triage</a>\n'
                         '    {% endif %}\n'
                         '\n'
                         "    {% if session.get('role') != 'reception' %}\n"
                         '      <a class="btn btn-sm btn-primary" href="{{ url_for(\'clinical_orders_page\', '
                         'visit_id=visit.visit_id) }}">Clinical Orders</a>\n'
                         '    {% endif %}\n'
                         '\n'
                         "    {% if session.get('role') in ['nurse','doctor','admin'] %}\n"
                         '      <a class="btn btn-sm btn-outline-dark" target="_blank" href="{{ '
                         'url_for(\'auto_summary_pdf\', visit_id=visit.visit_id) }}">Auto Summary</a>\n'
                         '      <a class="btn btn-sm btn-outline-primary" target="_blank" href="{{ '
                         'url_for(\'patient_summary_pdf\', visit_id=visit.visit_id) }}"\n'
                         '>ED Visit Summary - Patient Copy</a>\n'
                         '    {% endif %}\n'
                         '\n'
                         "    {% if session.get('role') in ['reception','nurse','doctor','admin'] %}\n"
                         '      <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ '
                         'url_for(\'home_med_pdf\', visit_id=visit.visit_id) }}">Home Medication</a>\n'
                         '    {% endif %}\n'
                         '\n'
                         '    <a class="btn btn-sm btn-outline-dark" target="_blank" href="{{ '
                         'url_for(\'sticker_html\', visit_id=visit.visit_id) }}">Sticker</a>\n'
                         '    <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ '
                         'url_for(\'sticker_zpl\', visit_id=visit.visit_id) }}">ZPL</a>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         "{% if session.get('role') in ['reception','admin'] and visit.status == 'OPEN' and "
                         'orders_count == 0 %}\n'
                         '<div class="card p-3 bg-white mb-3">\n'
                         '  <h6 class="fw-bold mb-2">Cancel Visit</h6>\n'
                         '  <p class="small text-muted mb-2">You can cancel an OPEN visit with no clinical '
                         'orders.</p>\n'
                         '  <form method="POST" action="{{ url_for(\'cancel_visit\', visit_id=visit.visit_id) }}">\n'
                         '    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '    <div class="row g-2 align-items-end">\n'
                         '      <div class="col-md-6">\n'
                         '        <label class="form-label fw-bold small mb-1">Reason</label>\n'
                         '        <input class="form-control" name="reason" required>\n'
                         '      </div>\n'
                         '      <div class="col-md-3">\n'
                         '        <button class="btn btn-outline-danger w-100"\n'
                         '                onclick="return confirm(\'Are you sure you want to cancel this visit?\');">\n'
                         '          Cancel Visit\n'
                         '        </button>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </form>\n'
                         '</div>\n'
                         '{% endif %}\n'
                         '\n'
                         "{% if session.get('role') in ['doctor','admin'] %}\n"
                         '<form method="POST" action="{{ url_for(\'close_visit\', visit_id=visit.visit_id) }}" '
                         'class="card p-3 bg-white mb-3">\n'
                         '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '  <h6 class="fw-bold">Update Status</h6>\n'
                         '  <div class="row g-2 align-items-end">\n'
                         '    <div class="col-md-4">\n'
                         '      <label class="form-label fw-bold small mb-1">New Status</label>\n'
                         '      <select class="form-select" name="status">\n'
                         '        <option>DISCHARGED</option>\n'
                         '        <option>ADMITTED</option>\n'
                         '        <option>TRANSFERRED</option>\n'
                         '        <option>LAMA</option>\n'
                         '        <option>EXPIRED</option>\n'
                         '        <option>IN_TREATMENT</option>\n'
                         '        <option>CANCELLED</option>\n'
                         '      </select>\n'
                         '    </div>\n'
                         '    <div class="col-md-3">\n'
                         '      <button class="btn btn-danger w-100"\n'
                         '              onclick="return confirm(\'Are you sure you want to update visit status?\');">\n'
                         '        Update Status\n'
                         '      </button>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</form>\n'
                         '{% endif %}\n'
                         '\n'
                         '<div class="card p-3 bg-white mb-3">\n'
                         '  <h6 class="fw-bold mb-2">Attach Patient ID</h6>\n'
                         '  <form method="POST" enctype="multipart/form-data" action="{{ url_for(\'upload_id\', '
                         'visit_id=visit.visit_id) }}">\n'
                         '    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '    <div class="row g-2 align-items-end">\n'
                         '      <div class="col-md-6">\n'
                         '        <input type="file" name="file" class="form-control">\n'
                         '      </div>\n'
                         '      <div class="col-md-3">\n'
                         '        <button class="btn btn-primary btn-sm w-100">Upload</button>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </form>\n'
                         '</div>\n'
                         '\n'
                         '<hr class="my-4">\n'
                         '\n'
                         '<div class="card bg-white p-3">\n'
                         '  <div class="d-flex justify-content-between align-items-center mb-2">\n'
                         '    <h5 class="mb-0">Vital Signs Trend</h5>\n'
                         '    <small class="text-muted">Pulse / BP / Temp / SpO₂ vs time</small>\n'
                         '  </div>\n'
                         '  <canvas id="vitals-chart" height="120"></canvas>\n'
                         '  <div class="small text-muted mt-2" id="vitals-chart-note">\n'
                         '    يعرض آخر قياسات العلامات الحيوية المسجلة أثناء الزيارة.\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>\n'
                         '<script>\n'
                         "document.addEventListener('DOMContentLoaded', function () {\n"
                         "  var canvas = document.getElementById('vitals-chart');\n"
                         '  if (!canvas) return;\n'
                         '\n'
                         "  var ctx = canvas.getContext('2d');\n"
                         "  var noteEl = document.getElementById('vitals-chart-note');\n"
                         '\n'
                         '  fetch("{{ url_for(\'vitals_history\', visit_id=visit.visit_id) }}")\n'
                         '    .then(function (r) { return r.json(); })\n'
                         '    .then(function (payload) {\n'
                         '      if (!payload || !payload.ok || !payload.points || !payload.points.length) {\n'
                         "        if (noteEl) noteEl.textContent = 'لا توجد قياسات كافية لعرض رسم بياني.';\n"
                         '        return;\n'
                         '      }\n'
                         '\n'
                         '      var labels = [];\n'
                         '      var pulseData = [];\n'
                         '      var tempData = [];\n'
                         '      var spo2Data = [];\n'
                         '      var bpSysData = [];\n'
                         '      var bpDiaData = [];\n'
                         '\n'
                         '      payload.points.forEach(function (p) {\n'
                         "        labels.push(p.time || '');\n"
                         '        pulseData.push(parseFloat(p.pulse) || null);\n'
                         '        tempData.push(parseFloat(p.temp) || null);\n'
                         '        spo2Data.push(parseFloat(p.spo2) || null);\n'
                         '        bpSysData.push(parseFloat(p.bp_sys) || null);\n'
                         '        bpDiaData.push(parseFloat(p.bp_dia) || null);\n'
                         '      });\n'
                         '\n'
                         '      var anyValue = pulseData.concat(tempData, spo2Data, bpSysData, bpDiaData)\n'
                         '                              .some(function (v) { return v !== null && !isNaN(v); });\n'
                         '      if (!anyValue) {\n'
                         "        if (noteEl) noteEl.textContent = 'لا توجد قيم رقمية كافية لعرض الرسم.';\n"
                         '        return;\n'
                         '      }\n'
                         '\n'
                         "      if (noteEl) noteEl.textContent = 'مرسوم بناءً على سجل الـ Triage / القياسات "
                         "السابقة.';\n"
                         '\n'
                         '      new Chart(ctx, {\n'
                         "        type: 'line',\n"
                         '        data: {\n'
                         '          labels: labels,\n'
                         '          datasets: [\n'
                         '            {\n'
                         "              label: 'Pulse',\n"
                         '              data: pulseData,\n'
                         '              borderWidth: 2,\n'
                         '              tension: 0.2\n'
                         '            },\n'
                         '            {\n'
                         "              label: 'Temp (°C)',\n"
                         '              data: tempData,\n'
                         '              borderWidth: 2,\n'
                         '              tension: 0.2\n'
                         '            },\n'
                         '            {\n'
                         "              label: 'SpO₂ (%)',\n"
                         '              data: spo2Data,\n'
                         '              borderWidth: 2,\n'
                         '              tension: 0.2\n'
                         '            },\n'
                         '            {\n'
                         "              label: 'BP Sys',\n"
                         '              data: bpSysData,\n'
                         '              borderWidth: 1,\n'
                         '              borderDash: [4, 2],\n'
                         '              tension: 0.2\n'
                         '            },\n'
                         '            {\n'
                         "              label: 'BP Dia',\n"
                         '              data: bpDiaData,\n'
                         '              borderWidth: 1,\n'
                         '              borderDash: [4, 2],\n'
                         '              tension: 0.2\n'
                         '            }\n'
                         '          ]\n'
                         '        },\n'
                         '        options: {\n'
                         '          responsive: true,\n'
                         '          maintainAspectRatio: false,\n'
                         '          scales: {\n'
                         '            x: {\n'
                         '              ticks: {\n'
                         '                autoSkip: true,\n'
                         '                maxTicksLimit: 6\n'
                         '              }\n'
                         '            },\n'
                         '            y: {\n'
                         '              beginAtZero: false\n'
                         '            }\n'
                         '          },\n'
                         '          plugins: {\n'
                         '            legend: {\n'
                         "              position: 'bottom'\n"
                         '            }\n'
                         '          }\n'
                         '        }\n'
                         '      });\n'
                         '    })\n'
                         '    .catch(function () {\n'
                         "      if (noteEl) noteEl.textContent = 'تعذر تحميل بيانات الرسم البياني.';\n"
                         '    });\n'
                         '});\n'
                         '</script>\n'
                         '\n'
                         '{% endblock %}\n',
 'radiology_board.html': '\n'
                         '{% extends "base.html" %}\n'
                         '{% block content %}\n'
                         '<div class="d-flex justify-content-between align-items-center mb-2">\n'
                         '  <div>\n'
                         '    <h4 class="mb-0">Radiology Board</h4>\n'
                         '    <div class="small text-muted">Imaging requests, status and reports.</div>\n'
                         '  </div>\n'
                         '  <div class="text-end small">\n'
                         '    <div>\n'
                         '      Pending / Done:\n'
                         '      <span class="badge bg-warning-subtle text-warning border border-warning">\n'
                         '        {{ pending_count or 0 }}\n'
                         '      </span>\n'
                         '    </div>\n'
                         '    <div>\n'
                         '      Reported:\n'
                         '      <span class="badge bg-success-subtle text-success border border-success">\n'
                         '        {{ reported_count or 0 }}\n'
                         '      </span>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                         '  {% for category, msg in messages %}\n'
                         '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                         '  {% endfor %}\n'
                         '{% endwith %}\n'
                         '\n'
                         '{% if status_counts or modality_counts %}\n'
                         '<div class="row g-2 mb-2">\n'
                         '  <div class="col-lg-5 col-md-6">\n'
                         '    <div class="card card-body py-2">\n'
                         '      <div class="small fw-bold text-muted mb-1">By status</div>\n'
                         '      {% set sc = status_counts or {} %}\n'
                         '      <div class="d-flex flex-wrap gap-1">\n'
                         '        <span class="badge rounded-pill bg-secondary-subtle text-secondary border '
                         'border-secondary">\n'
                         '          REQUESTED: <span class="fw-bold">{{ sc.get(\'REQUESTED\', 0) }}</span>\n'
                         '        </span>\n'
                         '        <span class="badge rounded-pill bg-warning-subtle text-warning border '
                         'border-warning">\n'
                         '          DONE: <span class="fw-bold">{{ sc.get(\'DONE\', 0) }}</span>\n'
                         '        </span>\n'
                         '        <span class="badge rounded-pill bg-success-subtle text-success border '
                         'border-success">\n'
                         '          REPORTED: <span class="fw-bold">{{ sc.get(\'REPORTED\', 0) }}</span>\n'
                         '        </span>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '  <div class="col-lg-7 col-md-6">\n'
                         '    <div class="card card-body py-2">\n'
                         '      <div class="small fw-bold text-muted mb-1">By modality (simple)</div>\n'
                         '      {% set mc = modality_counts or {} %}\n'
                         '      <div class="d-flex flex-wrap gap-1">\n'
                         '        <span class="badge rounded-pill bg-primary-subtle text-primary border '
                         'border-primary">\n'
                         '          XR: <span class="fw-bold">{{ mc.get(\'XR\', 0) }}</span>\n'
                         '        </span>\n'
                         '        <span class="badge rounded-pill bg-success-subtle text-success border '
                         'border-success">\n'
                         '          US: <span class="fw-bold">{{ mc.get(\'US\', 0) }}</span>\n'
                         '        </span>\n'
                         '        <span class="badge rounded-pill bg-warning-subtle text-warning border '
                         'border-warning">\n'
                         '          CT: <span class="fw-bold">{{ mc.get(\'CT\', 0) }}</span>\n'
                         '        </span>\n'
                         '        <span class="badge rounded-pill bg-info-subtle text-info border border-info">\n'
                         '          MRI: <span class="fw-bold">{{ mc.get(\'MRI\', 0) }}</span>\n'
                         '        </span>\n'
                         '        <span class="badge rounded-pill bg-light text-muted border">\n'
                         '          Other: <span class="fw-bold">{{ mc.get(\'Other\', 0) }}</span>\n'
                         '        </span>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '{% endif %}\n'
                         '\n'
                         '<form method="GET" class="card p-2 mb-3 bg-white">\n'
                         '  <div class="row g-2 align-items-end">\n'
                         '    <div class="col-md-3 col-sm-4">\n'
                         '      <label class="form-label fw-bold small mb-1">Status</label>\n'
                         '      <select name="status" class="form-select form-select-sm">\n'
                         '        <option value="PENDING" {% if status_filter==\'PENDING\' %}selected{% endif '
                         '%}>Pending / Done</option>\n'
                         '        <option value="REPORTED" {% if status_filter==\'REPORTED\' %}selected{% endif '
                         '%}>Reported</option>\n'
                         '        <option value="ALL" {% if status_filter==\'ALL\' %}selected{% endif %}>All</option>\n'
                         '      </select>\n'
                         '    </div>\n'
                         '    <div class="col-md-3 col-sm-6">\n'
                         '      <label class="form-label fw-bold small mb-1">Search</label>\n'
                         '      <input type="text"\n'
                         '             name="q"\n'
                         '             value="{{ q or \'\' }}"\n'
                         '             class="form-control form-control-sm"\n'
                         '             placeholder="Name / ID / Visit / Study">\n'
                         '    </div>\n'
                         '    <div class="col-md-2 col-sm-6">\n'
                         '      <label class="form-label fw-bold small mb-1">From (requested)</label>\n'
                         '      <input type="date" name="date_from" value="{{ date_from or \'\' }}"\n'
                         '             class="form-control form-control-sm">\n'
                         '    </div>\n'
                         '    <div class="col-md-2 col-sm-6">\n'
                         '      <label class="form-label fw-bold small mb-1">To (requested)</label>\n'
                         '      <input type="date" name="date_to" value="{{ date_to or \'\' }}"\n'
                         '             class="form-control form-control-sm">\n'
                         '    </div>\n'
                         '    <div class="col-md-2 col-sm-6">\n'
                         '      <label class="form-label fw-bold small mb-1">&nbsp;</label>\n'
                         '      <div class="d-flex gap-1">\n'
                         '        <button class="btn btn-sm btn-primary flex-fill">Search / Filter</button>\n'
                         '        <a class="btn btn-sm btn-outline-secondary"\n'
                         '           href="{{ url_for(\'export_radiology_csv\', status=status_filter, q=q, '
                         'date_from=date_from, date_to=date_to) }}">\n'
                         '          ⬇︎ CSV\n'
                         '        </a>\n'
                         '      </div>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</form>\n'
                         '\n'
                         '<div class="table-responsive">\n'
                         '<table class="table table-sm table-striped table-hover bg-white align-middle">\n'
                         '  <thead class="table-light">\n'
                         '    <tr>\n'
                         '      <th style="width:60px;">#</th>\n'
                         '      <th>Visit</th>\n'
                         '      <th>Patient</th>\n'
                         '      <th>ID</th>\n'
                         '      <th>Study</th>\n'
                         '      <th>Modality</th>\n'
                         '      <th>Requested</th>\n'
                         '      <th>Age / TAT</th>\n'
                         '      <th>Status</th>\n'
                         '      <th>Report</th>\n'
                         '      <th style="width:260px;">Actions</th>\n'
                         '    </tr>\n'
                         '  </thead>\n'
                         '  <tbody>\n'
                         '  {% if not rows %}\n'
                         '    <tr>\n'
                         '      <td colspan="11" class="text-center text-muted small py-3">\n'
                         '        No radiology requests found for current filter.\n'
                         '      </td>\n'
                         '    </tr>\n'
                         '  {% else %}\n'
                         '    {# Group radiology requests by visit so each visit/patient appears once #}\n'
                         "    {% for group in rows|groupby('visit_id') %}\n"
                         '      {% set r0 = group.list[0] %}\n'
                         '      <tr class="rad-group-row" data-visit="{{ group.grouper }}">\n'
                         '        <td>{{ loop.index }}</td>\n'
                         '        <td class="fw-bold">\n'
                         '          <a href="javascript:void(0)" class="rad-toggle" data-visit="{{ group.grouper }}">\n'
                         '            {{ group.grouper }}\n'
                         '          </a>\n'
                         '        </td>\n'
                         '        <td>\n'
                         '          <a href="javascript:void(0)" class="rad-toggle" data-visit="{{ group.grouper }}">\n'
                         '            {{ r0.name }}\n'
                         '          </a>\n'
                         '        </td>\n'
                         '        <td>\n'
                         '          <a href="javascript:void(0)" class="rad-toggle" data-visit="{{ group.grouper }}">\n'
                         "            {{ r0.id_number or '-' }}\n"
                         '          </a>\n'
                         '        </td>\n'
                         '        <td colspan="7" class="text-muted small">\n'
                         '          Click patient name / visit / ID to show imaging studies for this visit.\n'
                         '        </td>\n'
                         '      </tr>\n'
                         '\n'
                         '      {% for r in group.list %}\n'
                         '      <tr class="{% if r.status == \'REQUESTED\' %}table-warning{% elif r.status in '
                         '[\'DONE\',\'REPORTED\'] %}table-light{% endif %} rad-test-row d-none"\n'
                         '          data-visit="{{ group.grouper }}">\n'
                         '        <td></td>\n'
                         '        <td></td>\n'
                         '        <td></td>\n'
                         '        <td></td>\n'
                         '        <td>{{ r.test_name }}</td>\n'
                         "        <td>{{ r.modality or '-' }}</td>\n"
                         '        <td class="small text-muted">{{ r.requested_at or \'\' }}</td>\n'
                         '        <td>\n'
                         '          {% if r.age_text %}\n'
                         '            <span class="badge text-bg-light text-muted">{{ r.age_text }}</span>\n'
                         '          {% else %}\n'
                         '            <span class="text-muted">-</span>\n'
                         '          {% endif %}\n'
                         '        </td>\n'
                         '        <td>\n'
                         "          {% if r.status == 'REQUESTED' %}\n"
                         '            <span class="badge bg-secondary">Requested</span>\n'
                         "          {% elif r.status == 'SCHEDULED' %}\n"
                         '            <span class="badge bg-info text-dark">Scheduled</span>\n'
                         "          {% elif r.status == 'DONE' %}\n"
                         '            <span class="badge bg-success">Done</span>\n'
                         "          {% elif r.status == 'REPORTED' %}\n"
                         '            <span class="badge bg-success">Reported</span>\n'
                         '          {% else %}\n'
                         '            <span class="badge bg-light text-muted">{{ r.status }}</span>\n'
                         '          {% endif %}\n'
                         '        </td>\n'
                         '        <td style="max-width:260px; white-space:pre-wrap; font-size:0.85rem;">\n'
                         "          {{ r.report_text or '-' }}\n"
                         '        </td>\n'
                         '        <td>\n'
                         '          <div class="d-flex flex-column gap-1">\n'
                         "            {% if session.get('role') in ['radiology','admin'] %}\n"
                         '              <button class="btn btn-sm btn-outline-secondary w-100"\n'
                         '                      type="button"\n'
                         '                      data-bs-toggle="modal"\n'
                         '                      data-bs-target="#radReportModal"\n'
                         '                      data-rid="{{ r.id }}"\n'
                         '                      data-visit="{{ r.visit_id }}"\n'
                         '                      data-patient="{{ r.name }}"\n'
                         '                      data-test="{{ r.test_name }}"\n'
                         '                      data-report="{{ (r.report_text or \'\')|e }}"\n'
                         '                      data-url="{{ url_for(\'radiology_report_result\', rid=r.id) }}">\n'
                         '                ✏️ Edit / Add Report\n'
                         '              </button>\n'
                         '            {% endif %}\n'
                         '\n'
                         "            {% if session.get('role') in ['radiology','admin'] %}\n"
                         '              <form method="POST"\n'
                         '                    enctype="multipart/form-data"\n'
                         '                    action="{{ url_for(\'radiology_upload_result_file\', rid=r.id) }}"\n'
                         '                    class="d-flex gap-1 mt-1">\n'
                         '                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '                <input type="file" name="file" class="form-control form-control-sm">\n'
                         '                <button class="btn btn-sm btn-outline-secondary">Upload</button>\n'
                         '              </form>\n'
                         '            {% endif %}\n'
                         '\n'
                         "            {% if r.status == 'REPORTED' %}\n"
                         '              <span class="small text-muted mt-1">\n'
                         "                {{ r.reported_at or '' }} | {{ r.reported_by or '' }}\n"
                         '              </span>\n'
                         '            {% endif %}\n'
                         '          </div>\n'
                         '        </td>\n'
                         '      </tr>\n'
                         '      {% endfor %}\n'
                         '    {% endfor %}\n'
                         '  {% endif %}\n'
                         '</tbody>\n'
                         '\n'
                         '</table>\n'
                         '</div>\n'
                         '\n'
                         '<div class="small text-muted mt-2">\n'
                         '  Showing latest 500 radiology requests (before filters & limits).\n'
                         '</div>\n'
                         '\n'
                         '<!-- Radiology Report Modal -->\n'
                         '<div class="modal fade" id="radReportModal" tabindex="-1" aria-hidden="true">\n'
                         '  <div class="modal-dialog modal-lg modal-dialog-scrollable">\n'
                         '    <div class="modal-content">\n'
                         '      <form method="POST" id="radReportForm">\n'
                         '        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                         '        <div class="modal-header">\n'
                         '          <h5 class="modal-title">Radiology Report</h5>\n'
                         '          <button type="button" class="btn-close" data-bs-dismiss="modal" '
                         'aria-label="Close"></button>\n'
                         '        </div>\n'
                         '        <div class="modal-body">\n'
                         '          <div class="small text-muted mb-2" id="radReportMeta"></div>\n'
                         '          <div class="mb-2">\n'
                         '            <label class="form-label small">Report Text</label>\n'
                         '            <textarea\n'
                         '              name="report_text"\n'
                         '              id="radReportText"\n'
                         '              rows="12"\n'
                         '              class="form-control form-control-sm"\n'
                         '              placeholder="Type or paste the radiology report here..."></textarea>\n'
                         '          </div>\n'
                         '          <div class="alert alert-info small">\n'
                         '            <ul class="mb-0 ps-3">\n'
                         '              <li>Keep structured (Findings / Impression) where possible.</li>\n'
                         '              <li>Patient &amp; visit details appear above for double-checking.</li>\n'
                         '            </ul>\n'
                         '          </div>\n'
                         '        </div>\n'
                         '        <div class="modal-footer">\n'
                         '          <button type="button" class="btn btn-sm btn-outline-secondary" '
                         'data-bs-dismiss="modal">Close</button>\n'
                         '          <button type="submit" class="btn btn-sm btn-primary">Save Report</button>\n'
                         '        </div>\n'
                         '      </form>\n'
                         '    </div>\n'
                         '  </div>\n'
                         '</div>\n'
                         '\n'
                         '<script>\n'
                         "document.addEventListener('DOMContentLoaded', function () {\n"
                         "  var radModal = document.getElementById('radReportModal');\n"
                         '  if (radModal) {\n'
                         "    radModal.addEventListener('show.bs.modal', function (event) {\n"
                         '      var button = event.relatedTarget;\n'
                         '      if (!button) return;\n'
                         '\n'
                         "      var rid = button.getAttribute('data-rid') || '';\n"
                         "      var visit = button.getAttribute('data-visit') || '';\n"
                         "      var patient = button.getAttribute('data-patient') || '';\n"
                         "      var test = button.getAttribute('data-test') || '';\n"
                         "      var report = button.getAttribute('data-report') || '';\n"
                         "      var url = button.getAttribute('data-url') || '';\n"
                         '\n'
                         "      var form = document.getElementById('radReportForm');\n"
                         "      var textArea = document.getElementById('radReportText');\n"
                         "      var meta = document.getElementById('radReportMeta');\n"
                         '\n'
                         '      if (form) form.action = url;\n'
                         '      if (textArea) textArea.value = report;\n'
                         "      if (meta) meta.textContent = 'Visit ' + visit + ' - ' + patient + ' - ' + test;\n"
                         '    });\n'
                         '  }\n'
                         '\n'
                         '  // Toggle radiology studies per visit/patient\n'
                         "  var toggleButtons = document.querySelectorAll('.rad-toggle');\n"
                         '  toggleButtons.forEach(function (btn) {\n'
                         "    btn.addEventListener('click', function (e) {\n"
                         '      e.preventDefault();\n'
                         "      var visit = btn.getAttribute('data-visit');\n"
                         '      if (!visit) return;\n'
                         '      var rows = document.querySelectorAll(\'tr.rad-test-row[data-visit="\' + visit + '
                         '\'"]\');\n'
                         '      if (!rows.length) return;\n'
                         '\n'
                         '      var anyHidden = false;\n'
                         '      rows.forEach(function (row) {\n'
                         "        if (row.classList.contains('d-none')) {\n"
                         '          anyHidden = true;\n'
                         '        }\n'
                         '      });\n'
                         '\n'
                         '      rows.forEach(function (row) {\n'
                         '        if (anyHidden) {\n'
                         "          row.classList.remove('d-none');\n"
                         '        } else {\n'
                         "          row.classList.add('d-none');\n"
                         '        }\n'
                         '      });\n'
                         '    });\n'
                         '  });\n'
                         '});\n'
                         '</script>\n'
                         '\n'
                         '\n'
                         '{% endblock %}\n',
 'register.html': '\n'
                  '{% extends "base.html" %}\n'
                  '{% block content %}\n'
                  '<h4 class="mb-3">Register Patient</h4>\n'
                  '\n'
                  '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                  '  {% for category, msg in messages %}\n'
                  '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                  '  {% endfor %}\n'
                  '{% endwith %}\n'
                  '\n'
                  '<form method="POST" enctype="multipart/form-data" class="card p-3 bg-white" id="register-form">\n'
                  '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                  '  <div class="row g-2">\n'
                  '    <div class="col-md-6">\n'
                  '      <label class="form-label fw-bold">Name</label>\n'
                  '      <input class="form-control" name="name" required>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-6">\n'
                  '      <label class="form-label fw-bold">ID Number</label>\n'
                  '      <input class="form-control" name="id_number">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Phone</label>\n'
                  '      <input class="form-control" name="phone">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Insurance</label>\n'
                  '      <input class="form-control" name="insurance">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Insurance No</label>\n'
                  '      <input class="form-control" name="insurance_no">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">DOB</label>\n'
                  '      <input class="form-control" name="dob" placeholder="YYYY-MM-DD">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-2">\n'
                  '      <label class="form-label fw-bold">Sex</label>\n'
                  '      <select class="form-select" name="sex">\n'
                  '        <option value=""></option>\n'
                  '        <option>M</option>\n'
                  '        <option>F</option>\n'
                  '      </select>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-6">\n'
                  '      <label class="form-label fw-bold">Nationality</label>\n'
                  '      <input class="form-control" name="nationality">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Visit type</label>\n'
                  '      <select class="form-select" name="visit_type">\n'
                  '        <option value="NEW">New visit</option>\n'
                  '        <option value="TREATMENT">Treatment</option>\n'
                  '        <option value="FOLLOW_UP">Follow-up</option>\n'
                  '        <option value="PROCEDURE">Procedure / Dressing</option>\n'
                  '      </select>\n'
                  '    </div>\n'
                  '\n'
                  '    {# hidden field that will be filled automatically from insurance block #}\n'
                  '    <input type="hidden" name="payment_details" id="payment_details">\n'
                  '  </div>\n'
                  '\n'
                  '  <hr class="mt-3 mb-2">\n'
                  '\n'
                  '  <h5 class="mb-2">Insurance / Contract Scheme (optional)</h5>\n'
                  '\n'
                  '  <div class="row g-2">\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Ins. Provider</label>\n'
                  '      <input class="form-control" id="ins_provider">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Insur. Card No</label>\n'
                  '      <input class="form-control" id="ins_card_no">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">DHA Member ID</label>\n'
                  '      <input class="form-control" id="dha_member_id">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Ins. Plan</label>\n'
                  '      <input class="form-control" id="ins_plan">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Policy No</label>\n'
                  '      <input class="form-control" id="policy_no">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label fw-bold">Policy Name</label>\n'
                  '      <input class="form-control" id="policy_name">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-3">\n'
                  '      <label class="form-label fw-bold">Valid From</label>\n'
                  '      <input type="date" class="form-control" id="valid_from">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-3">\n'
                  '      <label class="form-label fw-bold">Valid To</label>\n'
                  '      <input type="date" class="form-control" id="valid_to">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-6">\n'
                  '      <label class="form-label fw-bold">Consultation Deductible %</label>\n'
                  '      <input class="form-control" id="consult_deduct" placeholder="e.g. 20%">\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-12">\n'
                  '      <label class="form-label fw-bold d-block">Contract Scheme Details</label>\n'
                  '      <div class="small text-muted">Enter percentage or fixed amount (AED) for each service - you '
                  'may leave any field blank.</div>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label">Laboratory</label>\n'
                  '      <div class="input-group mb-1">\n'
                  '        <span class="input-group-text">%</span>\n'
                  '        <input class="form-control" id="lab_percent" placeholder="e.g. 20">\n'
                  '      </div>\n'
                  '      <div class="input-group">\n'
                  '        <span class="input-group-text">AED</span>\n'
                  '        <input class="form-control" id="lab_amount" placeholder="e.g. 50">\n'
                  '      </div>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label">Radiology</label>\n'
                  '      <div class="input-group mb-1">\n'
                  '        <span class="input-group-text">%</span>\n'
                  '        <input class="form-control" id="radiology_percent" placeholder="e.g. 20">\n'
                  '      </div>\n'
                  '      <div class="input-group">\n'
                  '        <span class="input-group-text">AED</span>\n'
                  '        <input class="form-control" id="radiology_amount" placeholder="e.g. 50">\n'
                  '      </div>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label">Investigation</label>\n'
                  '      <div class="input-group mb-1">\n'
                  '        <span class="input-group-text">%</span>\n'
                  '        <input class="form-control" id="investigation_percent" placeholder="e.g. 20">\n'
                  '      </div>\n'
                  '      <div class="input-group">\n'
                  '        <span class="input-group-text">AED</span>\n'
                  '        <input class="form-control" id="investigation_amount" placeholder="e.g. 50">\n'
                  '      </div>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label">Inpatient</label>\n'
                  '      <div class="input-group mb-1">\n'
                  '        <span class="input-group-text">%</span>\n'
                  '        <input class="form-control" id="inpatient_percent" placeholder="e.g. 10">\n'
                  '      </div>\n'
                  '      <div class="input-group">\n'
                  '        <span class="input-group-text">AED</span>\n'
                  '        <input class="form-control" id="inpatient_amount" placeholder="e.g. 100">\n'
                  '      </div>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label">Pharmacy</label>\n'
                  '      <div class="input-group mb-1">\n'
                  '        <span class="input-group-text">%</span>\n'
                  '        <input class="form-control" id="pharmacy_percent" placeholder="e.g. 10">\n'
                  '      </div>\n'
                  '      <div class="input-group">\n'
                  '        <span class="input-group-text">AED</span>\n'
                  '        <input class="form-control" id="pharmacy_amount" placeholder="e.g. 20">\n'
                  '      </div>\n'
                  '    </div>\n'
                  '\n'
                  '    <div class="col-md-4">\n'
                  '      <label class="form-label">Dental</label>\n'
                  '      <div class="input-group mb-1">\n'
                  '        <span class="input-group-text">%</span>\n'
                  '        <input class="form-control" id="dental_percent" placeholder="e.g. 20">\n'
                  '      </div>\n'
                  '      <div class="input-group">\n'
                  '        <span class="input-group-text">AED</span>\n'
                  '        <input class="form-control" id="dental_amount" placeholder="e.g. 50">\n'
                  '      </div>\n'
                  '    </div>\n'
                  '  </div>\n'
                  '\n'
                  '  <div class="row g-2 mt-3">\n'
                  '    <div class="col-md-6">\n'
                  '      <label class="form-label fw-bold">Attachment (Eligibility / ID)</label>\n'
                  '      <input type="file" class="form-control" name="eligibility_file">\n'
                  '    </div>\n'
                  '  </div>\n'
                  '\n'
                  '  <button class="btn btn-primary mt-3">Save & Create Visit</button>\n'
                  '</form>\n'
                  '\n'
                  '<script>\n'
                  '  (function () {\n'
                  '    const form = document.getElementById("register-form");\n'
                  '    if (!form) return;\n'
                  '\n'
                  '    form.addEventListener("submit", function () {\n'
                  '      const get = function (id) {\n'
                  '        const el = document.getElementById(id);\n'
                  '        return el && el.value ? el.value.trim() : "";\n'
                  '      };\n'
                  '\n'
                  '      const parts = [];\n'
                  '      const add = function (label, id) {\n'
                  '        const v = get(id);\n'
                  '        if (v) { parts.push(label + ": " + v); }\n'
                  '      };\n'
                  '\n'
                  '      add("Provider", "ins_provider");\n'
                  '      add("Card", "ins_card_no");\n'
                  '      add("DHA", "dha_member_id");\n'
                  '      add("Plan", "ins_plan");\n'
                  '      add("PolicyNo", "policy_no");\n'
                  '      add("PolicyName", "policy_name");\n'
                  '\n'
                  '      const vFrom = get("valid_from");\n'
                  '      const vTo   = get("valid_to");\n'
                  '      if (vFrom || vTo) {\n'
                  '        parts.push("Valid " + (vFrom || "?") + " → " + (vTo || "?"));\n'
                  '      }\n'
                  '\n'
                  '      add("Consult%", "consult_deduct");\n'
                  '\n'
                  '      add("Lab%", "lab_percent");\n'
                  '      add("LabAmt", "lab_amount");\n'
                  '\n'
                  '      add("Rad%", "radiology_percent");\n'
                  '      add("RadAmt", "radiology_amount");\n'
                  '\n'
                  '      add("Inv%", "investigation_percent");\n'
                  '      add("InvAmt", "investigation_amount");\n'
                  '\n'
                  '      add("Inp%", "inpatient_percent");\n'
                  '      add("InpAmt", "inpatient_amount");\n'
                  '\n'
                  '      add("Pharm%", "pharmacy_percent");\n'
                  '      add("PharmAmt", "pharmacy_amount");\n'
                  '\n'
                  '      add("Dent%", "dental_percent");\n'
                  '      add("DentAmt", "dental_amount");\n'
                  '\n'
                  '      document.getElementById("payment_details").value = parts.join(" | ");\n'
                  '    });\n'
                  '  })();\n'
                  '</script>\n'
                  '\n'
                  '{% endblock %}\n',
 'search.html': '\n'
                '{% extends "base.html" %}\n'
                '{% block content %}\n'
                '<h4 class="mb-3">Search Patients</h4>\n'
                '\n'
                '<form class="card p-3 mb-3 bg-white" method="GET">\n'
                '  <div class="row g-2 align-items-end">\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label fw-bold">Free Search</label>\n'
                '      <input class="form-control" name="q" placeholder="Search by name / visit / ID / insurance no" '
                'value="{{ q }}">\n'
                '    </div>\n'
                '    <div class="col-md-2">\n'
                '      <label class="form-label fw-bold">Visit ID</label>\n'
                '      <input class="form-control" name="visit_id" value="{{ visit_f or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-2">\n'
                '      <label class="form-label fw-bold">User</label>\n'
                '      <select class="form-select" name="user">\n'
                '        <option value="">ALL</option>\n'
                '        {% for u in users %}\n'
                '          <option value="{{u.created_by}}" {% if user_f==u.created_by %}selected{% endif '
                '%}>{{u.created_by}}</option>\n'
                '        {% endfor %}\n'
                '      </select>\n'
                '    </div>\n'
                '    <div class="col-md-2">\n'
                '      <label class="form-label fw-bold">From</label>\n'
                '      <input class="form-control" type="date" name="date_from" value="{{ dfrom or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-2">\n'
                '      <label class="form-label fw-bold">To</label>\n'
                '      <input class="form-control" type="date" name="date_to" value="{{ dto or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-12 mt-2 d-grid">\n'
                '      <button class="btn btn-primary btn-sm">Filter</button>\n'
                '    </div>\n'
                '  </div>\n'
                '</form>\n'
                '\n'
                '\n'
                '{% if q and not results %}<div class="text-muted">No results</div>{% endif %}\n'
                '\n'
                '<table class="table table-sm bg-white">\n'
                '  <thead>\n'
                '    <tr>\n'
                '      <th>Visit</th><th>Queue</th><th>Name</th><th>ID</th><th>INS</th><th>INS No</th>\n'
                '      <th>Phone</th><th>Payment</th><th>Triage</th><th>CAT</th><th>Status</th><th>Actions</th>\n'
                '    </tr>\n'
                '  </thead>\n'
                '  <tbody>\n'
                '  {% for r in results %}\n'
                '    <tr>\n'
                '      <td>{{ r.visit_id }}</td>\n'
                '      <td class="fw-bold">{{ r.queue_no }}</td>\n'
                '      <td>{{ r.name }}</td>\n'
                '      <td>{{ r.id_number }}</td>\n'
                '      <td>{{ r.insurance }}</td>\n'
                '      <td>{{ r.insurance_no }}</td>\n'
                '      <td>{{ r.phone }}</td>\n'
                "      <td>{{ r.payment_details or '-' }}</td>\n"
                '      <td>{{ r.triage_status }}</td>\n'
                '      <td>\n'
                "        {% set cat = (r.triage_cat or '').lower() %}\n"
                '        {% if cat == \'es1\' %}<span class="badge cat-red">ES1</span>\n'
                '        {% elif cat == \'es2\' %}<span class="badge cat-orange">ES2</span>\n'
                '        {% elif cat == \'es3\' %}<span class="badge cat-yellow">ES3</span>\n'
                '        {% elif cat == \'es4\' %}<span class="badge cat-green">ES4</span>\n'
                '        {% elif cat == \'es5\' %}<span class="badge cat-none">ES5</span>\n'
                '        {% else %}<span class="badge cat-none">-</span>{% endif %}\n'
                '      </td>\n'
                '      <td>{{ r.status }}</td>\n'
                '      <td><a class="btn btn-sm btn-outline-primary" href="{{ url_for(\'patient_details\', '
                'visit_id=r.visit_id) }}">Open</a></td>\n'
                '    </tr>\n'
                '  {% endfor %}\n'
                '  </tbody>\n'
                '</table>\n'
                '{% endblock %}\n'
                '\n'
                '<nav class="d-flex justify-content-between align-items-center mt-2">\n'
                '  <div class="small text-muted">\n'
                '    Page {{page}} / {{pages}} - Total: {{total}}\n'
                '  </div>\n'
                '  <ul class="pagination pagination-sm mb-0">\n'
                '    <li class="page-item {% if page<=1 %}disabled{% endif %}">\n'
                '      <a class="page-link" href="{{ url_for(\'ed_board\', status=status_filter, cat=cat_filter, '
                'visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto, per_page=per_page, page=page-1) '
                '}}">Prev</a>\n'
                '    </li>\n'
                '    <li class="page-item {% if page>=pages %}disabled{% endif %}">\n'
                '      <a class="page-link" href="{{ url_for(\'ed_board\', status=status_filter, cat=cat_filter, '
                'visit_id=visit_f, user=user_f, date_from=dfrom, date_to=dto, per_page=per_page, page=page+1) '
                '}}">Next</a>\n'
                '    </li>\n'
                '  </ul>\n'
                '  <button class="btn btn-sm btn-outline-secondary" onclick="location.reload()">🔄 Manual '
                'Refresh</button>\n'
                '</nav>\n'
                '\n',
 'sticker.html': '\n'
                 '<!doctype html>\n'
                 '<html>\n'
                 '<head>\n'
                 '  <meta charset="utf-8">\n'
                 '  <title>Sticker</title>\n'
                 '  <style>\n'
                 '    @page {\n'
                 '      size: 5cm 3cm;\n'
                 '      margin: 0;\n'
                 '    }\n'
                 '    body { margin:0; padding:0; font-family: Arial; }\n'
                 '    .label {\n'
                 '      width: 5cm; height: 3cm;\n'
                 '      border:1px solid #000; padding:0.06cm;\n'
                 '      box-sizing:border-box;\n'
                 '    }\n'
                 '    .row { font-size:6pt; margin:0.01cm 0; white-space: normal; word-wrap: break-word; }\n'
                 '    .title { font-weight:bold; font-size:7pt; }\n'
                 '    .barcode {\n'
                 '      width: 100%;\n'
                 '      max-width: 3.5cm;\n'
                 '      margin-top:0.05cm;\n'
                 '    }\n'
                 '    #btnPrint { margin-top:10px; padding:6px 12px; font-size:12px; }\n'
                 '    @media print {\n'
                 '      body { margin:0; padding:0; }\n'
                 '      #btnPrint { display:none; }\n'
                 '    }\n'
                 '  </style>\n'
                 '</head>\n'
                 '<body onload="window.print()">\n'
                 '  <div class="label">\n'
                 '    {% set name_len = v.name|length %}\n'
                 '    <div class="row title" style="font-size: {{ 7 if name_len <= 20 else 6 }}pt;">NAME: {{ v.name '
                 '}}</div>\n'
                 '    <div class="row">AGE: {{ age or \'-\' }}</div>\n'
                 '    <div class="row">ID: {{ v.id_number or \'-\' }}</div>\n'
                 '    <div class="row">INS: {{ v.insurance or \'-\' }}</div>\n'
                 '    <div class="row">TIME: {{ time_only }}</div>\n'
                 '    <div class="row">VISIT: {{ v.visit_id }}</div>\n'
                 '    <div class="row">\n'
                 '      <img class="barcode"\n'
                 '           src="https://barcode.tec-it.com/barcode.ashx?data={{ (v.id_number or '
                 'v.visit_id)|urlencode }}&code=Code128&dpi=96&imagetype=png"\n'
                 '           alt="BARCODE">\n'
                 '    </div>\n'
                 '  </div>\n'
                 '  <button id="btnPrint" onclick="window.print()">Print Again</button>\n'
                 '</body>\n'
                 '</html>\n',
 'triage.html': '\n'
                '{% extends "base.html" %}\n'
                '{% block content %}\n'
                '<h4 class="mb-3">Triage - Visit {{ visit.visit_id }}\n'
                '  {% if visit.triage_cat %}\n'
                '    {% set es = visit.triage_cat %}\n'
                "    {% set es_class = 'danger' if es=='ES1' else 'warning' if es=='ES2' else 'info' if es=='ES3' else "
                "'primary' if es=='ES4' else 'success' %}\n"
                '    <span class="badge bg-{{ es_class }} ms-2">{{ es }}</span>\n'
                '  {% endif %}\n'
                '</h4>\n'
                '\n'
                '{% with messages = get_flashed_messages(with_categories=true) %}\n'
                '  {% for category, msg in messages %}\n'
                '    <div class="alert alert-{{ category }}">{{ msg }}</div>\n'
                '  {% endfor %}\n'
                '{% endwith %}\n'
                '\n'
                '<form method="POST" class="card p-3 bg-white">\n'
                '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
                '  <div class="mb-2"><strong>Patient:</strong> {{ visit.name }} | ID: {{ visit.id_number }} | INS: {{ '
                'visit.insurance }}</div>\n'
                '\n'
                '  <label class="form-label fw-bold mt-2">Patient\'s Complaint</label>\n'
                '  <input class="form-control" name="comment" value="{{ visit.comment or \'\' }}" required>\n'
                '\n'
                '  <label class="form-label fw-bold mt-2">Allergy</label>\n'
                '  <div class="row g-2">\n'
                '    <div class="col-md-4">\n'
                '      <select class="form-select" name="allergy_status" id="allergy_status">\n'
                "        {% set al = visit.allergy_status or '' %}\n"
                '        <option value="" {% if al==\'\' %}selected{% endif %}>-- Select --</option>\n'
                '        <option value="No" {% if al==\'No\' %}selected{% endif %}>No</option>\n'
                '        <option value="Yes" {% if al==\'Yes\' %}selected{% endif %}>Yes</option>\n'
                '      </select>\n'
                '    </div>\n'
                '    <div class="col-md-8"\n'
                '         id="allergy_details_group"\n'
                '         style="display: {% if (visit.allergy_status or \'\') == \'Yes\' %}block{% else %}none{% '
                'endif %};">\n'
                '      <input class="form-control"\n'
                '             name="allergy_details"\n'
                '             placeholder="Cause of allergy (drug / food / other)"\n'
                '             value="{{ visit.allergy_details or \'\' }}">\n'
                '    </div>\n'
                '  </div>\n'
                '\n'
                '  <hr class="my-3">\n'
                '\n'
                '  <h6 class="fw-bold">Vital Signs</h6>\n'
                '  <div class="row g-2">\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">Pulse Rate (bpm)</label>\n'
                '      <input class="form-control" name="pulse_rate" value="{{ visit.pulse_rate or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">Resp Rate (/min)</label>\n'
                '      <input class="form-control" name="resp_rate" value="{{ visit.resp_rate or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">Temp (°C)</label>\n'
                '      <input class="form-control" name="temperature" value="{{ visit.temperature or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">BP Systolic</label>\n'
                '      <input class="form-control" name="bp_systolic" value="{{ visit.bp_systolic or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">BP Diastolic</label>\n'
                '      <input class="form-control" name="bp_diastolic" value="{{ visit.bp_diastolic or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">SpO₂ (%)</label>\n'
                '      <input class="form-control" name="spo2" value="{{ visit.spo2 or \'\' }}">  <!-- may be required '
                'based on complaint -->\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">Weight (kg)</label>\n'
                '      <input class="form-control" name="weight" value="{{ visit.weight or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-4">\n'
                '      <label class="form-label">Height (cm)</label>\n'
                '      <input class="form-control" name="height" value="{{ visit.height or \'\' }}">\n'
                '    </div>\n'
                '    <div class="col-md-6">\n'
                '      <label class="form-label">Level of Consciousness</label>\n'
                '      <select class="form-select" name="consciousness_level">\n'
                "        {% set cl = visit.consciousness_level or '' %}\n"
                '        <option value="" {% if cl==\'\' %}selected{% endif %}>-- Select --</option>\n'
                '        <option value="Alert" {% if cl==\'Alert\' %}selected{% endif %}>Alert</option>\n'
                '        <option value="Verbal" {% if cl==\'Verbal\' %}selected{% endif %}>Verbal</option>\n'
                '        <option value="Pain" {% if cl==\'Pain\' %}selected{% endif %}>Pain</option>\n'
                '        <option value="Unresponsive" {% if cl==\'Unresponsive\' %}selected{% endif '
                '%}>Unresponsive</option>\n'
                '      </select>\n'
                '      <div class="form-text">AVPU scale</div>\n'
                '    </div>\n'
                '    <div class="col-md-6">\n'
                '      <label class="form-label">Pain Score (0-10)</label>\n'
                '      <input class="form-control" name="pain_score" value="{{ visit.pain_score or \'\' }}" required>\n'
                '    </div>\n'
                '  </div>\n'
                '\n'
                '  <hr class="my-3">\n'
                '\n'
                '  <label class="form-label fw-bold mt-2">Triage Category (ES)</label>\n'
                '  <select class="form-select" name="triage_cat" required>\n'
                '    <option value="">-- Select --</option>\n'
                '    <option value="ES1" {% if visit.triage_cat==\'ES1\' %}selected{% endif %}>ES1</option>\n'
                '    <option value="ES2" {% if visit.triage_cat==\'ES2\' %}selected{% endif %}>ES2</option>\n'
                '    <option value="ES3" {% if visit.triage_cat==\'ES3\' %}selected{% endif %}>ES3</option>\n'
                '    <option value="ES4" {% if visit.triage_cat==\'ES4\' %}selected{% endif %}>ES4</option>\n'
                '    <option value="ES5" {% if visit.triage_cat==\'ES5\' %}selected{% endif %}>ES5</option>\n'
                '  </select>\n'
                '\n'
                '  <div class="mt-3 d-flex gap-2">\n'
                '    <button class="btn btn-success">Save Triage</button>\n'
                '    <a class="btn btn-outline-secondary"\n'
                '       target="_blank"\n'
                '       href="{{ url_for(\'triage_pdf\', visit_id=visit.visit_id) }}">\n'
                '      Print Triage PDF\n'
                '    </a>\n'
                '  </div>\n'
                '</form>\n'
                '\n'
                '<script>\n'
                "document.addEventListener('DOMContentLoaded', function () {\n"
                "  const allergySelect = document.getElementById('allergy_status');\n"
                "  const detailsGroup = document.getElementById('allergy_details_group');\n"
                '  if (!allergySelect || !detailsGroup) return;\n'
                '\n'
                '  function toggleDetails() {\n'
                "    if (allergySelect.value === 'Yes') {\n"
                "      detailsGroup.style.display = 'block';\n"
                '    } else {\n'
                "      detailsGroup.style.display = 'none';\n"
                '    }\n'
                '  }\n'
                '\n'
                "  allergySelect.addEventListener('change', toggleDetails);\n"
                '  toggleDetails();\n'
                '});\n'
                '</script>\n'
                '{% endblock %}\n'}



@app.context_processor
def inject_footer():
    return dict(footer_text=APP_FOOTER_TEXT)

@app.context_processor
def inject_nav_counters():
    """Inject small counters for navbar badges (e.g. pending labs, radiology, chat)."""
    lab_pending = 0
    rad_pending = 0
    chat_recent = 0
    try:
        cur = get_db().cursor()
        # Lab pending
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM lab_requests WHERE status IN ('REQUESTED','RECEIVED')"
        ).fetchone()
        if row is not None:
            try:
                lab_pending = row["c"]
            except Exception:
                lab_pending = row[0]
        # Radiology pending (requested or done but not yet reported)
        row2 = cur.execute(
            "SELECT COUNT(*) AS c FROM radiology_requests WHERE status IN ('REQUESTED','DONE')"
        ).fetchone()
        if row2 is not None:
            try:
                rad_pending = row2["c"]
            except Exception:
                rad_pending = row2[0]
        # Live chat "unread-ish": messages in last 15 minutes
        row3 = cur.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE created_at >= datetime('now', '-15 minutes')"
        ).fetchone()
        if row3 is not None:
            try:
                chat_recent = row3["c"]
            except Exception:
                chat_recent = row3[0]
    except Exception:
        lab_pending = lab_pending or 0
        rad_pending = rad_pending or 0
        chat_recent = chat_recent or 0
    return dict(
        nav_lab_pending=lab_pending,
        nav_rad_pending=rad_pending,
        nav_chat_recent=chat_recent,
    )



# ============================================================
# Custom template overrides (patient_details, depart_workflow)
# ============================================================
TEMPLATES['patient_details.html'] = '''

{% extends "base.html" %}
{% block content %}

<div class="d-flex justify-content-between align-items-start mb-3 flex-wrap gap-2">
  <div>
    <h4 class="mb-0">Patient Details</h4>
    <div class="text-muted small">Visit {{ visit.visit_id }}</div>
  </div>
  <div class="text-end">
    <div class="mb-1">
      {% set cat = (visit.triage_cat or '').lower() %}
      {% if cat == 'es1' %}<span class="badge bg-danger">ES1</span>
      {% elif cat == 'es2' %}<span class="badge bg-warning text-dark">ES2</span>
      {% elif cat == 'es3' %}<span class="badge bg-info text-dark">ES3</span>
      {% elif cat == 'es4' %}<span class="badge bg-primary">ES4</span>
      {% elif cat == 'es5' %}<span class="badge bg-success">ES5</span>
      {% else %}<span class="badge bg-secondary">No ES</span>
      {% endif %}
    </div>
    <div>
      {% set st = (visit.status or '').upper() %}
      {% set st_class = 'secondary' %}
      {% if st == 'OPEN' %}{% set st_class = 'success' %}{% endif %}
      {% if st in ['DISCHARGED','TRANSFERRED','LAMA','EXPIRED','CANCELLED'] %}{% set st_class = 'danger' %}{% endif %}
      <span class="badge bg-{{ st_class }}">{{ visit.status or '-' }}</span>
    </div>
  </div>

</div>

<div class="alert d-flex justify-content-between align-items-center px-3 py-2 mb-3 {% if (visit.allergy_status or '').upper() == 'YES' %}banner-allergy{% else %}banner-no-allergy{% endif %}">
  <div class="d-flex align-items-center gap-2">
    <span class="badge {% if (visit.allergy_status or '').upper() == 'YES' %}bg-danger{% else %}bg-secondary{% endif %}">
      ALLERGY
    </span>
    <div class="small">
      {% set alg_status = (visit.allergy_status or '').upper() %}
      {% if alg_status == 'YES' %}
        <strong>{{ visit.allergy_details or 'Allergy documented' }}</strong>
      {% elif alg_status == 'NKDA' %}
        NKDA (No known drug allergy)
      {% elif alg_status %}
        {{ alg_status }}
      {% else %}
        No allergy info recorded.
      {% endif %}
    </div>
  </div>
  <div class="small text-muted text-end">
    Loc: {{ visit.location or '-' }}
    {% if visit.bed_no %}
      Â· Bed: {{ visit.bed_no }} ({{ visit.bed_status or 'EMPTY' }})
    {% endif %}
  </div>
</div>

{% with messages = get_flashed_messages(with_categories=true) %}

  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="card mb-3">
  <div class="card-body">
    <div class="row g-3">
      <div class="col-md-6">
        <h6 class="fw-bold text-muted mb-2">Patient &amp; Contact</h6>
        <dl class="row mb-0 small">
          <dt class="col-4">Name</dt>
          <dd class="col-8 fw-semibold">{{ visit.name }}</dd>

          <dt class="col-4">ID</dt>
          <dd class="col-8">{{ visit.id_number or '-' }}</dd>

          <dt class="col-4">Phone</dt>
          <dd class="col-8">{{ visit.phone or '-' }}</dd>

          <dt class="col-4">Nationality</dt>
          <dd class="col-8">{{ visit.nationality or '-' }}</dd>

          <dt class="col-4">Visit type</dt>
          <dd class="col-8">
            {% set vt = (visit.visit_type or 'NEW') %}
            {% if vt == 'NEW' %}
              New visit
            {% elif vt == 'TREATMENT' %}
              Treatment
            {% elif vt in ['FOLLOW_UP','FOLLOW-UP','FOLLOWUP'] %}
              Follow-up
            {% elif vt == 'PROCEDURE' %}
              Procedure / Dressing
            {% else %}
              {{ vt }}
            {% endif %}
          </dd>
        </dl>
      </div>
      <div class="col-md-6">
        <h6 class="fw-bold text-muted mb-2">Insurance &amp; Financial</h6>
        <dl class="row mb-0 small">
          <dt class="col-4">Insurance</dt>
          <dd class="col-8">{{ visit.insurance or '-' }}</dd>

          <dt class="col-4">Insurance No</dt>
          <dd class="col-8">{{ visit.insurance_no or '-' }}</dd>

          <dt class="col-4">Payment</dt>
          <dd class="col-8">{{ visit.payment_details or '-' }}</dd>
        </dl>
      </div>
    </div>

    <hr class="my-3">

    <div class="row g-3">
      <div class="col-md-6">
        <h6 class="fw-bold text-muted mb-2">Clinical Information</h6>
        <div class="small mb-2">
          <span class="fw-semibold">Patient Complaint:</span>
          <div>{{ visit.comment or '-' }}</div>
        </div>
        <div class="small mb-2">
          <span class="fw-semibold">Allergy:</span>
          <div>
            {{ visit.allergy_status or '-' }}
            {% if visit.allergy_details %}
              - {{ visit.allergy_details }}
            {% endif %}
          </div>
        </div>
        <div class="small mb-2">
          <span class="fw-semibold">Triage Status:</span>
          <span class="ms-1">{{ visit.triage_status }}</span>
        </div>
      </div>
      <div class="col-md-6">
        <h6 class="fw-bold text-muted mb-2">Vital Signs</h6>
        <div class="d-flex flex-wrap gap-1 small">
          <span class="badge text-bg-light">PR: {{ visit.pulse_rate or '-' }} bpm</span>
          <span class="badge text-bg-light">RR: {{ visit.resp_rate or '-' }}/min</span>
          <span class="badge text-bg-light">BP: {{ visit.bp_systolic or '-' }}/{{ visit.bp_diastolic or '-' }}</span>
          <span class="badge text-bg-light">Temp: {{ visit.temperature or '-' }} Â°C</span>
          <span class="badge text-bg-light">SpO2: {{ visit.spo2 or '-' }}%</span>
          <span class="badge text-bg-light">Pain: {{ visit.pain_score or '-' }}/10</span>
          <span class="badge text-bg-light">Consciousness: {{ visit.consciousness_level or '-' }}</span>
          <span class="badge text-bg-light">Wt: {{ visit.weight or '-' }} kg</span>
          <span class="badge text-bg-light">Ht: {{ visit.height or '-' }} cm</span>
        </div>
      </div>
    </div>

    {% if visit.status == 'CANCELLED' %}
    <hr class="my-3">
    <div class="row g-2 small">
      <div class="col-md-6">
        <span class="fw-semibold">Cancel Reason:</span>
        <span class="ms-1">{{ visit.cancel_reason or '-' }}</span>
      </div>
      <div class="col-md-6">
        <span class="fw-semibold">Cancelled By:</span>
        <span class="ms-1">{{ visit.cancelled_by or '-' }}</span>
      </div>
    </div>
    {% endif %}
  </div>
</div>

<div class="card p-3 bg-white mb-3">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <h6 class="fw-bold mb-0">Location / Bed</h6>
    <div class="small text-muted">Greaseboard-style slot</div>
  </div>
  <div class="row g-2 align-items-end small">
    <div class="col-md-3 col-4">
      <label class="form-label mb-1">Location</label>
      <div>{{ visit.location or '-' }}</div>
    </div>
    <div class="col-md-2 col-4">
      <label class="form-label mb-1">Bed</label>
      <div>{{ visit.bed_no or '-' }}</div>
    </div>
    <div class="col-md-3 col-4">
      <label class="form-label mb-1">Bed Status</label>
      <div>{{ visit.bed_status or '-' }}</div>
    </div>
    <div class="col-md-4">
      {% if session.get('role') in ['reception','nurse','doctor','admin'] %}
      <form class="row g-1 align-items-end" method="POST" action="{{ url_for('update_location_bed', visit_id=visit.visit_id) }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="col-4">
          <label class="form-label mb-1 small">Loc</label>
          <input type="text" class="form-control form-control-sm" name="location" value="{{ visit.location or '' }}" placeholder="WR / R1 / FT">
        </div>
        <div class="col-3">
          <label class="form-label mb-1 small">Bed</label>
          <input type="text" class="form-control form-control-sm" name="bed_no" value="{{ visit.bed_no or '' }}">
        </div>
        <div class="col-3">
          <label class="form-label mb-1 small">Status</label>
          {% set bs = (visit.bed_status or '').upper() %}
          <select class="form-select form-select-sm" name="bed_status">
            <option value="" {% if not bs %}selected{% endif %}>-</option>
            <option value="EMPTY" {% if bs == 'EMPTY' %}selected{% endif %}>EMPTY</option>
            <option value="OCCUPIED" {% if bs == 'OCCUPIED' %}selected{% endif %}>OCCUPIED</option>
            <option value="DIRTY" {% if bs == 'DIRTY' %}selected{% endif %}>DIRTY</option>
          </select>
        </div>
        <div class="col-2">
          <button class="btn btn-sm btn-primary w-100">Save</button>
        </div>
      </form>
      {% endif %}
    </div>
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
              <li>{{ l.test_name }} - {{ l.status }}</li>
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
              <li>{{ r.test_name }} - {{ r.status }}</li>
            {% endif %}
          {% endfor %}
        </ul>
      {% else %}
        <div class="small text-muted">No radiology requests for this visit.</div>
      {% endif %}
    </div>
  </div>
  <div class="small text-muted mt-1">
    * View only - reception cannot edit results.
  </div>
</div>
{% endif %}

<div class="card p-3 bg-white mb-3">
  <h6 class="fw-bold mb-2">Quick Actions</h6>
  <div class="d-flex gap-2 flex-wrap">
    {% if session.get('role') in ['reception','admin'] %}
      <a class="btn btn-sm btn-outline-warning" href="{{ url_for('edit_patient', visit_id=visit.visit_id) }}">Edit Patient</a>
    {% endif %}

    <a class="btn btn-sm btn-outline-secondary"
       target="_blank"
       href="{{ url_for('triage_pdf', visit_id=visit.visit_id) }}">
      Print Triage PDF
    </a>
    <a class="btn btn-sm btn-outline-secondary"
       target="_blank"
       href="{{ url_for('lab_results_pdf', visit_id=visit.visit_id) }}">
      Print Lab Results PDF
    </a>
    <a class="btn btn-sm btn-outline-secondary"
       target="_blank"
       href="{{ url_for('radiology_results_pdf', visit_id=visit.visit_id) }}">
      Print Radiology PDF
    </a>
    <a class="btn btn-sm btn-outline-secondary"
       target="_blank"
       href="{{ url_for('auto_summary_pdf', visit_id=visit.visit_id) }}">
      Auto-Summary PDF
    </a>

    {% if session.get('role') in ['nurse','doctor','admin'] %}
      <a class="btn btn-sm btn-success" href="{{ url_for('triage', visit_id=visit.visit_id) }}">Triage</a>
    {% endif %}

    {% if session.get('role') != 'reception' %}
      <a class="btn btn-sm btn-primary" href="{{ url_for('clinical_orders_page', visit_id=visit.visit_id) }}">Clinical Orders</a>
    {% endif %}

    {% if session.get('role') in ['nurse','doctor','admin'] %}
      <a class="btn btn-sm btn-outline-dark" target="_blank" href="{{ url_for('auto_summary_pdf', visit_id=visit.visit_id) }}">Auto Summary</a>
      <a class="btn btn-sm btn-outline-primary" target="_blank" href="{{ url_for('patient_summary_pdf', visit_id=visit.visit_id) }}"
>ED Visit Summary - Patient Copy</a>
    {% endif %}

    {% if session.get('role') in ['reception','nurse','doctor','admin'] %}
      <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('home_med_pdf', visit_id=visit.visit_id) }}">Home Medication</a>
    {% endif %}

    <a class="btn btn-sm btn-outline-dark" target="_blank" href="{{ url_for('sticker_html', visit_id=visit.visit_id) }}">Sticker</a>
    <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('sticker_zpl', visit_id=visit.visit_id) }}">ZPL</a>
  </div>
</div>

{% if session.get('role') in ['reception','admin'] and visit.status == 'OPEN' and orders_count == 0 %}
<div class="card p-3 bg-white mb-3">
  <h6 class="fw-bold mb-2">Cancel Visit</h6>
  <p class="small text-muted mb-2">You can cancel an OPEN visit with no clinical orders.</p>
  <form method="POST" action="{{ url_for('cancel_visit', visit_id=visit.visit_id) }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <div class="row g-2 align-items-end">
      <div class="col-md-6">
        <label class="form-label fw-bold small mb-1">Reason</label>
        <input class="form-control" name="reason" required>
      </div>
      <div class="col-md-3">
        <button class="btn btn-outline-danger w-100"
                onclick="return confirm('Are you sure you want to cancel this visit?');">
          Cancel Visit
        </button>
      </div>
    </div>
  </form>
</div>
{% endif %}

{% if session.get('role') in ['doctor','admin'] %}
<form method="POST" action="{{ url_for('close_visit', visit_id=visit.visit_id) }}" class="card p-3 bg-white mb-3">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <h6 class="fw-bold">Update Status</h6>
  <div class="row g-2 align-items-end">
    <div class="col-md-4">
      <label class="form-label fw-bold small mb-1">New Status</label>
      <select class="form-select" name="status">
        <option>DISCHARGED</option>
        <option>ADMITTED</option>
        <option>TRANSFERRED</option>
        <option>LAMA</option>
        <option>EXPIRED</option>
        <option>IN_TREATMENT</option>
        <option>CANCELLED</option>
        <option>CALLED</option>
        <option>VISIT_PROGRESS</option>
        <option>VISIT_COMPLETED</option>
        <option>REGISTERED</option>
      </select>
    </div>
    <div class="col-md-3">
      <button class="btn btn-danger w-100"
              onclick="return confirm('Are you sure you want to update visit status?');">
        Update Status
      </button>
    </div>
  </div>
</form>
{% endif %}

<div class="card p-3 bg-white mb-3">
  <h6 class="fw-bold mb-2">Attach Patient ID</h6>
  <form method="POST" enctype="multipart/form-data" action="{{ url_for('upload_id', visit_id=visit.visit_id) }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <div class="row g-2 align-items-end">
      <div class="col-md-6">
        <input type="file" name="file" class="form-control">
      </div>
      <div class="col-md-3">
        <button class="btn btn-primary btn-sm w-100">Upload</button>
      </div>
    </div>
  </form>
</div>

{% endblock %}

'''

TEMPLATES['depart_workflow.html'] = '''

{% extends "base.html" %}
{% block content %}

<div class="d-flex justify-content-between align-items-start mb-3 flex-wrap gap-2">
  <div>
    <h4 class="mb-0">ED Depart / Discharge</h4>
    <div class="text-muted small">
      Visit {{ visit.visit_id }} &mdash; {{ visit.name }} ({{ visit.id_number or '-' }})
    </div>
  </div>
  <div class="text-end small">
    <div class="mb-1">
      {% set cat = (visit.triage_cat or '').lower() %}
      {% if cat == 'es1' %}<span class="badge bg-danger">ES1</span>
      {% elif cat == 'es2' %}<span class="badge bg-warning text-dark">ES2</span>
      {% elif cat == 'es3' %}<span class="badge bg-info text-dark">ES3</span>
      {% elif cat == 'es4' %}<span class="badge bg-primary">ES4</span>
      {% elif cat == 'es5' %}<span class="badge bg-success">ES5</span>
      {% else %}<span class="badge bg-secondary">No ES</span>
      {% endif %}
    </div>
    <div class="mb-1">
      {% set st = (visit.status or '').upper() %}
      {% set st_class = 'secondary' %}
      {% if st == 'OPEN' %}{% set st_class = 'success' %}{% endif %}
      {% if st in ['DISCHARGED','TRANSFERRED','LAMA','EXPIRED','CANCELLED'] %}{% set st_class = 'danger' %}{% endif %}
      <span class="badge bg-{{ st_class }}">{{ visit.status or '-' }}</span>
    </div>
    <div class="small text-muted">
      Loc: {{ visit.location or '-' }}
      {% if visit.bed_no %}
        Â· Bed: {{ visit.bed_no }} ({{ visit.bed_status or 'EMPTY' }})
      {% endif %}
    </div>
  </div>
</div>

{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, msg in messages %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="row g-3">
  <!-- Checklist summary -->
  <div class="col-md-4">
    <div class="card p-3 bg-white h-100">
      <h6 class="fw-bold mb-2">Checklist overview</h6>
      <ul class="list-unstyled small mb-0">
        <li class="mb-1">
          {% if visit.task_reg %}
            â Registration completed
          {% else %}
            â Registration pending
          {% endif %}
        </li>
        <li class="mb-1">
          {% if visit.triage_status == 'YES' %}
            â Triage done ({{ visit.triage_cat or '-' }})
          {% else %}
            â Triage pending
          {% endif %}
        </li>
        <li class="mb-1">
          {% if visit.task_ekg %}
            â ECG / EKG done
          {% else %}
            â ECG / EKG pending
          {% endif %}
        </li>
        <li class="mb-1">
          {% if visit.task_sepsis %}
            â Sepsis screen done
          {% else %}
            â Sepsis screen pending
          {% endif %}
        </li>
        <li class="mb-1">
          {% if orders_count %}
            â Clinical orders entered ({{ orders_count }})
          {% else %}
            â No clinical orders yet
          {% endif %}
        </li>
        <li class="mb-1">
          {% if labs_total %}
            {% if labs_pending %}
              â ï¸ Lab results pending ({{ labs_pending }} / {{ labs_total }})
            {% else %}
              â Labs cleared ({{ labs_total }})
            {% endif %}
          {% else %}
            â No lab requests
          {% endif %}
        </li>
        <li class="mb-1">
          {% if rads_total %}
            {% if rads_pending %}
              â ï¸ Radiology pending ({{ rads_pending }} / {{ rads_total }})
            {% else %}
              â Radiology cleared ({{ rads_total }})
            {% endif %}
          {% else %}
            â No radiology requests
          {% endif %}
        </li>
        <li class="mb-1">
          {% if discharge_exists %}
            â Discharge summary saved
            {% if discharge_diag %}
              &mdash; {{ discharge_diag }}
            {% endif %}
          {% else %}
            â Discharge summary pending
          {% endif %}
        </li>
      </ul>
    </div>
  </div>

  <!-- Editable tasks -->
  <div class="col-md-4">
    <div class="card p-3 bg-white h-100">
      <h6 class="fw-bold mb-2">Tasks / Checklist (editable)</h6>
      <form method="POST" class="small">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="form-check mb-1">
          <input class="form-check-input" type="checkbox" value="1" name="task_reg" id="task_reg"
                 {% if visit.task_reg %}checked{% endif %}>
          <label class="form-check-label" for="task_reg">
            Registration completed
          </label>
        </div>
        <div class="form-check mb-1">
          <input class="form-check-input" type="checkbox" value="1" name="task_ekg" id="task_ekg"
                 {% if visit.task_ekg %}checked{% endif %}>
          <label class="form-check-label" for="task_ekg">
            ECG / EKG done
          </label>
        </div>
        <div class="form-check mb-1">
          <input class="form-check-input" type="checkbox" value="1" name="task_sepsis" id="task_sepsis"
                 {% if visit.task_sepsis %}checked{% endif %}>
          <label class="form-check-label" for="task_sepsis">
            Sepsis screening done
          </label>
        </div>
        <button class="btn btn-sm btn-primary mt-2">
          Save checklist
        </button>
      </form>

      <hr class="my-3">

      <div class="small">
        <div class="fw-semibold mb-1">Quick links</div>
        <div class="d-grid gap-1">
          <a class="btn btn-sm btn-outline-primary"
             href="{{ url_for('patient_details', visit_id=visit.visit_id) }}">
            Open chart
          </a>
          <a class="btn btn-sm btn-outline-primary"
             href="{{ url_for('clinical_orders_page', visit_id=visit.visit_id) }}">
            Clinical orders &amp; notes
          </a>
          <a class="btn btn-sm btn-outline-secondary"
             href="{{ url_for('lab_board', status='PENDING', q=visit.visit_id) }}">
            Lab board (this visit)
          </a>
          <a class="btn btn-sm btn-outline-secondary"
             href="{{ url_for('radiology_board', status='PENDING', q=visit.visit_id) }}">
            Radiology board (this visit)
          </a>
        </div>
      </div>
    </div>
  </div>

  <!-- Discharge / PDFs / Close -->
  <div class="col-md-4">
    <div class="card p-3 bg-white h-100">
      <h6 class="fw-bold mb-2">Discharge / Depart</h6>

      <div class="small mb-2">
        <div>Current status:
          {% set st = (visit.status or '').upper() %}
          {% set st_class = 'secondary' %}
          {% if st == 'OPEN' %}{% set st_class = 'success' %}{% endif %}
          {% if st in ['DISCHARGED','TRANSFERRED','LAMA','EXPIRED','CANCELLED'] %}{% set st_class = 'danger' %}{% endif %}
          <span class="badge bg-{{ st_class }}">{{ visit.status or '-' }}</span>
        </div>
        {% if visit.closed_at %}
          <div class="text-muted">Closed at {{ visit.closed_at }} by {{ visit.closed_by or '-' }}</div>
        {% endif %}
      </div>

      <div class="d-grid gap-1 small mb-2">
        <a class="btn btn-sm btn-outline-primary"
           target="_blank"
           href="{{ url_for('discharge_summary_pdf', visit_id=visit.visit_id) }}">
          Discharge summary PDF
        </a>
        <a class="btn btn-sm btn-outline-primary"
           target="_blank"
           href="{{ url_for('auto_summary_pdf', visit_id=visit.visit_id) }}">
          ED auto-summary PDF
        </a>
        <a class="btn btn-sm btn-outline-primary"
           target="_blank"
           href="{{ url_for('patient_summary_pdf', visit_id=visit.visit_id) }}">
          Patient copy PDF
        </a>
        <a class="btn btn-sm btn-outline-secondary"
           target="_blank"
           href="{{ url_for('home_med_pdf', visit_id=visit.visit_id) }}">
          Home medication PDF
        </a>
      </div>

      {% if session.get('role') in ['doctor','admin'] %}
      <hr class="my-2">
      <form method="POST" action="{{ url_for('close_visit', visit_id=visit.visit_id) }}" class="small">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-2">
          <label class="form-label small">Final status</label>
          <select name="status" class="form-select form-select-sm">
            {% for st in ['DISCHARGED','ADMITTED','TRANSFERRED','LAMA','EXPIRED','IN_TREATMENT','CANCELLED','CALLED','VISIT_PROGRESS','VISIT_COMPLETED','REGISTERED'] %}
              <option value="{{ st }}" {% if (visit.status or '').upper() == st %}selected{% endif %}>{{ st }}</option>
            {% endfor %}
          </select>
        </div>
        <button class="btn btn-sm btn-danger w-100"
                onclick="return confirm('Confirm close visit with this status?');">
          Close visit
        </button>
      </form>
      {% else %}
        <div class="alert alert-info small mt-2 mb-0">
          Final close of the visit is limited to doctors / admins.
        </div>
      {% endif %}
    </div>
  </div>
</div>

{% endblock %}
'}




@app.context_processor
def inject_footer():
    return dict(footer_text=APP_FOOTER_TEXT)

@app.context_processor
def inject_nav_counters():
    """Inject small counters for navbar badges (e.g. pending labs, radiology, chat)."""
    lab_pending = 0
    rad_pending = 0
    chat_recent = 0
    try:
        cur = get_db().cursor()
        # Lab pending
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM lab_requests WHERE status IN ('REQUESTED','RECEIVED')"
        ).fetchone()
        if row is not None:
            try:
                lab_pending = row["c"]
            except Exception:
                lab_pending = row[0]
        # Radiology pending (requested or done but not yet reported)
        row2 = cur.execute(
            "SELECT COUNT(*) AS c FROM radiology_requests WHERE status IN ('REQUESTED','DONE')"
        ).fetchone()
        if row2 is not None:
            try:
                rad_pending = row2["c"]
            except Exception:
                rad_pending = row2[0]
        # Live chat "unread-ish": messages in last 15 minutes
        row3 = cur.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE created_at >= datetime('now
'''

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
    tpl = """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Live Chat - Staff</h4>

<div class="row">
  <!-- Chat + input -->
  <div class="col-md-8">
    <div class="card bg-white mb-3">
      <div class="card-header py-2">
        <div class="d-flex flex-wrap align-items-center gap-2">
          <div>
            <label class="form-label small mb-0">Room</label>
            <select id="chat-room" class="form-select form-select-sm">
              <option value="general">General</option>
              <option value="triage">Triage</option>
              <option value="lab">Lab</option>
              <option value="radiology">Radiology</option>
              <option value="doctor">Doctors</option>
            </select>
          </div>
          <div>
            <label class="form-label small mb-0">Visit ID</label>
            <input type="text"
                   id="chat-visit-id"
                   class="form-control form-control-sm"
                   placeholder="Optional visit id (e.g., ED-2025-0001)">
          </div>
          <div class="ms-auto small text-muted">
            Last update: <span id="chat-last-update">-</span>
          </div>
        </div>
      </div>
      <div class="card-body" style="height:380px; overflow-y:auto;" id="chat-box">
        <div class="text-muted small">Loading messages...</div>
      </div>
      <div class="card-footer">
        <div class="input-group">
          <input type="text"
                 id="chat-input"
                 class="form-control"
                 placeholder="Type your message and press Enter or click Send ...">
          <button class="btn btn-primary" id="chat-send-btn">Send</button>
        </div>
        <div class="form-text small text-muted mt-1">
          Room is visible to all staff; optional Visit ID links the message to a specific visit.
        </div>
      </div>
    </div>
  </div>

  <!-- Activity / feed -->
  <div class="col-md-4">
    <div class="card bg-white mb-3">
      <div class="card-header py-2 d-flex justify-content-between align-items-center">
        <span class="small fw-bold">Users activity</span>
        <button class="btn btn-sm btn-outline-secondary btn-refresh-activity">Refresh</button>
      </div>
      <div class="card-body small" style="max-height:220px; overflow-y:auto;" id="chat-activity-box">
        <div class="text-muted small">Loading activity...</div>
      </div>
    </div>

    <div class="alert alert-info small">
      <div class="fw-bold mb-1">Tips:</div>
      <ul class="mb-0 ps-3">
        <li>Use <strong>Visit ID</strong> to link discussion to a patient.</li>
        <li>Use rooms (Lab / Radiology / Triage...) لفرز المحادثات.</li>
        <li>Activity list تساعدك تعرف مين آخر واحد كان متواجد.</li>
      </ul>
    </div>
  </div>
</div>

<script>
  const chatBox = document.getElementById("chat-box");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");
  const roomSelect = document.getElementById("chat-room");
  const visitInput = document.getElementById("chat-visit-id");
  const lastUpdateSpan = document.getElementById("chat-last-update");

  const activityBox = document.getElementById("chat-activity-box");
  const refreshActivityBtns = document.querySelectorAll(".btn-refresh-activity");

  let lastTimestamp = "";
  let currentRoom = roomSelect.value || "general";

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function scrollChatToBottom() {
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  function formatTimestamp(ts) {
    return ts || "";
  }

  function appendMessage(msg, highlight=false) {
    const div = document.createElement("div");
    div.className = "mb-1";
    const visitTag = msg.visit_id ? '<span class="badge bg-light text-dark me-1">[' + escapeHtml(msg.visit_id) + ']</span>' : "";
    const roomTag = msg.room && msg.room !== "general"
      ? '<span class="badge bg-secondary me-1">' + escapeHtml(msg.room) + '</span>'
      : "";
    div.innerHTML =
      roomTag +
      visitTag +
      '<span class="badge bg-light text-dark me-1">' +
      (msg.username || "User") +
      '</span>' +
      '<span class="small text-muted me-1">[' + formatTimestamp(msg.created_at) + ']</span>' +
      '<span>' + escapeHtml(msg.message) + '</span>';
    if (highlight) {
      div.classList.add("fw-bold");
    }
    chatBox.appendChild(div);
  }

  function updateLastUpdate(ts) {
    lastTimestamp = ts || lastTimestamp;
    if (lastTimestamp) {
      lastUpdateSpan.textContent = lastTimestamp;
    }
  }

  async function fetchMessages(initial=false) {
    const params = new URLSearchParams();
    if (lastTimestamp && !initial) {
      params.append("after", lastTimestamp);
    }
    if (currentRoom) {
      params.append("room", currentRoom);
    }
    if (visitInput.value.trim()) {
      params.append("visit_id", visitInput.value.trim());
    }

    try {
      const res = await fetch("/chat/messages?" + params.toString());
      const data = await res.json();
      if (!data.ok) return;

      if (initial) {
        chatBox.innerHTML = "";
      }

      if (data.messages && data.messages.length) {
        let needsScroll = Math.abs(chatBox.scrollHeight - chatBox.scrollTop - chatBox.clientHeight) < 40;
        data.messages.forEach((m, idx) => {
          const highlight = !initial && idx === data.messages.length - 1;
          appendMessage(m, highlight);
          lastTimestamp = m.created_at;
        });
        if (needsScroll) {
          scrollChatToBottom();
        }
        updateLastUpdate(lastTimestamp);
        // Simple audio notifier for new messages (not on initial load)
        if (!initial) {
          try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.type = "sine";
            o.frequency.value = 880;
            o.connect(g);
            g.connect(ctx.destination);
            o.start();
            g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.15);
            o.stop(ctx.currentTime + 0.15);
          } catch (e) { /* ignore */ }
        }
      } else if (initial) {
        chatBox.innerHTML = '<div class="text-muted small">No messages yet.</div>';
      }

    } catch (err) {
      console.error(err);
    }
  }

  async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    const payload = {
      message: text,
      room: currentRoom,
      visit_id: visitInput.value.trim()
    };

    try {
      const res = await fetch("/chat/send", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.ok) {
        chatInput.value = "";
        fetchMessages(false);
      }
    } catch (err) {
      console.error(err);
    }
  }

  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  sendBtn.addEventListener("click", sendMessage);

  roomSelect.addEventListener("change", function () {
    currentRoom = this.value || "general";
    lastTimestamp = "";
    fetchMessages(true);
  });

  // If visit ID changes, reload messages filtered by that visit
  visitInput.addEventListener("change", function () {
    lastTimestamp = "";
    fetchMessages(true);
  });

  async function fetchActivity() {
    try {
      const res = await fetch("/chat/users_activity");
      const data = await res.json();
      if (!data.ok) return;
      if (!data.items || !data.items.length) {
        activityBox.innerHTML = '<div class="text-muted small">No recent activity.</div>';
        return;
      }
      activityBox.innerHTML = "";
      data.items.forEach(function (u) {
        const div = document.createElement("div");
        div.className = "d-flex justify-content-between align-items-center mb-1";
        div.innerHTML =
          '<span class="fw-bold small">' + escapeHtml(u.username) + '</span>' +
          '<span class="small text-muted">' + escapeHtml(u.last_at || "") + '</span>';
        activityBox.appendChild(div);
      });
    } catch (e) {
      console.error(e);
    }
  }

  refreshActivityBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      fetchActivity();
    });
  });

  // Initial load
  fetchMessages(true);
  fetchActivity();

  // Polling
  setInterval(function () {
    fetchMessages(false);
  }, 5000);
  setInterval(function () {
    fetchActivity();
  }, 15000);
</script>

{% endblock %}
"""
    return render_template_string(tpl)

@app.route("/chat/send", methods=["POST"])
@login_required
def chat_send():
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    room = (data.get("room") or "general").strip() or "general"
    visit_id = (data.get("visit_id") or "").strip()

    if not msg:
        return jsonify({"ok": False, "error": "Empty message"}), 400

    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = session.get("username", "UNKNOWN")

    db.execute(
        """
        INSERT INTO chat_messages (username, message, room, visit_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, msg, room, visit_id, now),
    )
    db.commit()

    try:
        log_action("CHAT_SEND", details=f"{username} [{room}]: {msg[:80]}")
    except Exception:
        pass

    return jsonify({"ok": True})


@app.route("/chat/messages")
@login_required
def chat_messages():
    after = (request.args.get("after") or "").strip()
    room = (request.args.get("room") or "general").strip() or "general"
    visit_id = (request.args.get("visit_id") or "").strip()

    cur = get_db().cursor()
    sql = "SELECT username, message, room, visit_id, created_at FROM chat_messages WHERE 1=1"
    params = []

    if room:
        sql += " AND (room = ? OR room IS NULL OR room = '')"
        params.append(room)

    if visit_id:
        sql += " AND visit_id = ?"
        params.append(visit_id)

    if after:
        sql += " AND created_at > ?"
        params.append(after)

    sql += " ORDER BY datetime(created_at) ASC LIMIT 200"

    rows = cur.execute(sql, params).fetchall()

    messages = [
        {
            "username": r["username"],
            "message": r["message"],
            "room": r["room"] or "general",
            "visit_id": r["visit_id"] or "",
            "created_at": r["created_at"],
        }
        for r in rows
    ]

    return jsonify({"ok": True, "messages": messages})


@app.route("/chat/users_activity")
@login_required
def chat_users_activity():
    """
    Simple 'last seen in chat' based on latest chat_messages per user.
    """
    cur = get_db().cursor()
    rows = cur.execute(
        """
        SELECT username, MAX(created_at) AS last_at
        FROM chat_messages
        GROUP BY username
        ORDER BY last_at DESC
        LIMIT 50
        """
    ).fetchall()

    items = [
        {
            "username": r["username"],
            "last_at": r["last_at"],
        }
        for r in rows
    ]
    return jsonify({"ok": True, "items": items})



# ============================================================
# Help page
# ============================================================

@app.route("/help")
@login_required
def help_page():
    tpl = """
{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">Downtime Tool - Help</h4>

<p>This tool is designed for ED downtime use. Main modules:</p>
<ul>
  <li><strong>ED Board:</strong> Overview of all open visits with triage ES colours.</li>
  <li><strong>Triage:</strong> Capture chief complaint, allergy, vital signs and ES category.</li>
  <li><strong>Clinical Orders:</strong> Lab / Radiology / Procedures ordering.</li>
  <li><strong>Discharge Summary & Auto-Summary PDF:</strong> Print patient copy of the visit.</li>
  <li><strong>Boards (Lab / Radiology):</strong> Worklists with status and quick editing via pop-up.</li>
  <li><strong>Admin:</strong> Manage users, backups and review activity log.</li>
</ul>

<p class="mb-1"><strong>Typical workflow:</strong></p>
<ol>
  <li>Reception registers patient & opens visit.</li>
  <li>Nurse performs triage and assigns ES category.</li>
  <li>Doctor sees patient, places clinical orders and documents course.</li>
  <li>Lab / Radiology teams work from their boards and enter results/reports.</li>
  <li>Doctor completes discharge summary and closes the visit.</li>
  <li>Print patient copy (summary, medications, triage, investigations as needed).</li>
</ol>

<p class="text-muted small">For internal ED use only. Always follow hospital policies and local regulations.</p>
{% endblock %}
"""
    return render_template_string(tpl)


# ============================================================
# Run
# ============================================================


if __name__ == "__main__":
    bootstrap_once()
    debug_env = os.environ.get("FLASK_DEBUG", "1")
    debug = debug_env == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)
