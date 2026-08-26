from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
from urllib.parse import urlparse
import json
import random
import string
import psycopg2
import psycopg2.extras
import os
from werkzeug.utils import secure_filename
import sendgrid
from sendgrid.helpers.mail import Mail as SGMail, TrackingSettings, ClickTracking

app = Flask(__name__)
app.jinja_env.globals['now'] = datetime.now
app.secret_key = os.environ.get('SECRET_KEY', 'your-local-dev-secret-key')

# ============== DATABASE CONNECTION (Postgres / Supabase) ==============
# This wrapper lets the rest of the app keep calling db.cursor(dictionary=True,
# buffered=True), cursor.execute(...), and cursor.lastrowid exactly as it did
# with mysql.connector, even though the underlying driver is now psycopg2.

class PGCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, query, params=None):
        # MySQL used backticks for identifiers like `date`; Postgres uses
        # double quotes instead, so convert automatically.
        q = query.replace('`', '"')

        stripped = q.strip().upper()
        if stripped.startswith("INSERT") and "RETURNING" not in stripped:
            # Emulate cursor.lastrowid by asking Postgres to return the new id.
            q = q.rstrip().rstrip(';') + " RETURNING id"
            self._cursor.execute(q, params)
            row = self._cursor.fetchone()
            if row:
                self.lastrowid = row['id'] if isinstance(row, dict) else row[0]
            return None

        return self._cursor.execute(q, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)


class PGConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, dictionary=False, buffered=False, **kwargs):
        # 'buffered' has no equivalent need in psycopg2 and is ignored.
        cursor_factory = psycopg2.extras.RealDictCursor if dictionary else None
        raw_cursor = self._conn.cursor(cursor_factory=cursor_factory)
        return PGCursorWrapper(raw_cursor)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db():
    # Set DATABASE_URL in Render's environment variables to your Supabase
    # Session pooler connection string, e.g.:
    # postgresql://postgres.xxxx:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        # Hand the URL straight to psycopg2 instead of parsing it ourselves —
        # psycopg2 parses connection URIs natively and more reliably than
        # manually splitting it with urlparse.
        raw_conn = psycopg2.connect(db_url, sslmode='require')
    else:
        # Local fallback (e.g. a local Postgres install, not XAMPP/MySQL anymore)
        raw_conn = psycopg2.connect(
            host="127.0.0.1",
            port=5432,
            user="postgres",
            password="",
            dbname="budget_master"
        )
    return PGConnectionWrapper(raw_conn)

# ============== HELPERS ==============
def month_range(ym_str):
    year, month = int(ym_str[:4]), int(ym_str[5:7])
    start = f"{year:04d}-{month:02d}-01"
    end   = f"{year+1:04d}-01-01" if month == 12 else f"{year:04d}-{month+1:02d}-01"
    return start, end

# ============== RECEIPT VAULT HELPERS ==============
ALLOWED_RECEIPT_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}
MAX_RECEIPT_SIZE = 5 * 1024 * 1024  # 5 MB

def allowed_receipt_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_RECEIPT_EXTENSIONS

def save_receipt(cursor, transaction_id, user_id, category, file_storage):
    """Validates and stores an uploaded receipt as a BLOB, returns receipt_id or (None, error_message)."""
    if not file_storage or not file_storage.filename:
        return None, None

    if not allowed_receipt_file(file_storage.filename):
        return None, 'Receipt must be an image (JPG, PNG, GIF, WEBP) or PDF.'

    file_data = file_storage.read()
    if len(file_data) == 0:
        return None, None
    if len(file_data) > MAX_RECEIPT_SIZE:
        return None, 'Receipt file is too large (max 5MB).'

    filename = secure_filename(file_storage.filename)
    cursor.execute("""
        INSERT INTO receipts (transaction_id, user_id, category, filename, mime_type, file_data, file_size)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (transaction_id, user_id, category, filename, file_storage.mimetype, file_data, len(file_data)))
    receipt_id = cursor.lastrowid
    log_receipt_action(cursor, receipt_id, filename, user_id, 'upload')
    return receipt_id, None

def log_receipt_action(cursor, receipt_id, receipt_filename, user_id, action):
    cursor.execute(
        "INSERT INTO receipt_audit_log (receipt_id, receipt_filename, user_id, action, ip_address) VALUES (%s,%s,%s,%s,%s)",
        (receipt_id, receipt_filename, user_id, action, request.remote_addr)
    )

# ============== SHARED WALLET HELPERS ==============
def generate_invite_code(cursor):
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cursor.execute("SELECT id FROM wallets WHERE invite_code=%s", (code,))
        if not cursor.fetchone():
            return code

def get_wallet_membership(cursor, wallet_id, user_id):
    """Returns the membership row if the user belongs to this wallet, else None."""
    cursor.execute(
        "SELECT * FROM wallet_members WHERE wallet_id=%s AND user_id=%s",
        (wallet_id, user_id)
    )
    return cursor.fetchone()

# ============== GAMIFICATION & REWARDS ==============
LEVELS = [
    (0,   'Bronze',   '🥉'),
    (75,  'Silver',   '🥈'),
    (175, 'Gold',     '🥇'),
    (300, 'Platinum', '💎'),
]

def get_level(points):
    """Returns (level_name, level_icon, next_threshold_or_None) for a given point total."""
    level_name, level_icon, next_threshold = LEVELS[0][1], LEVELS[0][2], LEVELS[1][0]
    for i, (threshold, name, icon) in enumerate(LEVELS):
        if points >= threshold:
            level_name, level_icon = name, icon
            next_threshold = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return level_name, level_icon, next_threshold

BADGES = [
    {'code': 'first_transaction', 'name': 'Getting Started', 'icon': '🎯', 'points': 10,
     'description': 'Log your first transaction',
     'check': lambda s: s['total_transactions'] >= 1},
    {'code': 'consistent_tracker', 'name': 'Consistent Tracker', 'icon': '📅', 'points': 20,
     'description': 'Log transactions on 7+ different days',
     'check': lambda s: s['distinct_days'] >= 7},
    {'code': 'budget_keeper', 'name': 'Budget Keeper', 'icon': '🛡️', 'points': 30,
     'description': 'Stay under your total budget for one full month',
     'check': lambda s: s['months_under_budget'] >= 1},
    {'code': 'three_month_streak', 'name': 'On a Roll', 'icon': '🔥', 'points': 75,
     'description': 'Stay under budget 3 months in a row',
     'check': lambda s: s['current_streak'] >= 3},
    {'code': 'savings_star', 'name': 'Savings Star', 'icon': '⭐', 'points': 25,
     'description': 'Reach a 20%+ savings rate',
     'check': lambda s: s['savings_rate'] >= 20},
    {'code': 'big_saver', 'name': 'Big Saver', 'icon': '💰', 'points': 40,
     'description': 'Accumulate RM1,000+ in net savings',
     'check': lambda s: s['net_savings'] >= 1000},
    {'code': 'receipt_collector', 'name': 'Receipt Collector', 'icon': '🧾', 'points': 15,
     'description': 'Upload 5 receipts to your Vault',
     'check': lambda s: s['receipt_count'] >= 5},
    {'code': 'team_player', 'name': 'Team Player', 'icon': '🤝', 'points': 15,
     'description': 'Join or create a Shared Wallet',
     'check': lambda s: s['wallet_member']},
    {'code': 'loan_slayer', 'name': 'Loan Slayer', 'icon': '🏆', 'points': 50,
     'description': 'Fully pay off a loan',
     'check': lambda s: s['loans_paid_off'] >= 1},
]

def compute_user_stats(cursor, user_id):
    """Gathers the account activity needed to evaluate every badge's criteria."""
    stats = {}

    cursor.execute("SELECT COUNT(*) c FROM transactions WHERE user_id=%s", (user_id,))
    stats['total_transactions'] = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(DISTINCT `date`) c FROM transactions WHERE user_id=%s", (user_id,))
    stats['distinct_days'] = cursor.fetchone()['c']

    cursor.execute("SELECT COALESCE(SUM(amount),0) c FROM transactions WHERE user_id=%s AND type='income'", (user_id,))
    total_income = float(cursor.fetchone()['c'])
    cursor.execute("SELECT COALESCE(SUM(amount),0) c FROM transactions WHERE user_id=%s AND type='expense'", (user_id,))
    total_spent = float(cursor.fetchone()['c'])
    stats['total_income'] = total_income
    stats['total_spent'] = total_spent
    stats['net_savings'] = total_income - total_spent
    stats['savings_rate'] = (stats['net_savings'] / total_income * 100) if total_income > 0 else 0

    cursor.execute("SELECT COALESCE(SUM(amount),0) c FROM budgets WHERE user_id=%s", (user_id,))
    stats['total_budgeted'] = float(cursor.fetchone()['c'])

    cursor.execute("SELECT COUNT(*) c FROM receipts WHERE user_id=%s", (user_id,))
    stats['receipt_count'] = cursor.fetchone()['c']

    cursor.execute("SELECT COUNT(*) c FROM wallet_members WHERE user_id=%s", (user_id,))
    stats['wallet_member'] = cursor.fetchone()['c'] > 0

    cursor.execute("SELECT COUNT(*) c FROM loans WHERE user_id=%s AND status='paid_off'", (user_id,))
    stats['loans_paid_off'] = cursor.fetchone()['c']

    # Monthly budget streak: compare each *completed* past month's total spend
    # against the user's total budgeted amount (only meaningful once a budget exists).
    cursor.execute("SELECT `date`, amount FROM transactions WHERE user_id=%s AND type='expense' ORDER BY `date`", (user_id,))
    monthly_spent = {}
    for row in cursor.fetchall():
        m = str(row['date'])[:7]
        monthly_spent[m] = monthly_spent.get(m, 0) + float(row['amount'])

    current_month = datetime.now().strftime('%Y-%m')
    past_months = sorted(m for m in monthly_spent if m < current_month)

    monthly_status = []
    streak = 0
    if stats['total_budgeted'] > 0:
        for m in past_months:
            under = monthly_spent[m] <= stats['total_budgeted']
            monthly_status.append({'month': m, 'spent': monthly_spent[m], 'under_budget': under})
            streak = streak + 1 if under else 0

    stats['monthly_status'] = monthly_status
    stats['months_under_budget'] = sum(1 for s in monthly_status if s['under_budget'])
    stats['current_streak'] = streak  # trailing streak, i.e. ending at the most recent completed month

    return stats

def check_and_award_badges(cursor, user_id):
    """Evaluates all badges against current stats, inserts any newly-earned ones + their points.
    Caller is responsible for committing. Returns (list_of_newly_earned_badges, stats)."""
    stats = compute_user_stats(cursor, user_id)

    cursor.execute("SELECT badge_code FROM user_badges WHERE user_id=%s", (user_id,))
    earned_codes = {row['badge_code'] for row in cursor.fetchall()}

    newly_earned = []
    for badge in BADGES:
        if badge['code'] not in earned_codes and badge['check'](stats):
            cursor.execute(
                "INSERT INTO user_badges (user_id, badge_code, points_awarded) VALUES (%s,%s,%s)",
                (user_id, badge['code'], badge['points'])
            )
            cursor.execute(
                "INSERT INTO point_log (user_id, points, reason) VALUES (%s,%s,%s)",
                (user_id, badge['points'], f"Earned badge: {badge['name']}")
            )
            newly_earned.append(badge)

    return newly_earned, stats

# ============== LOGIN DECORATOR ==============
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ============== INDEX ==============
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

# ============== REGISTER ==============
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email    = request.form.get('email')
        password = request.form.get('password')
        confirm  = request.form.get('confirm_password')

        if password != confirm:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('register'))

        db = get_db()
        cursor = db.cursor(dictionary=True)

        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            flash('Email already registered!', 'error')
            cursor.close(); db.close()
            return redirect(url_for('register'))

        hashed = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
            (username, email, hashed)
        )
        user_id = cursor.lastrowid

        default_budgets = [
            ('Food & Dining', 600), ('Transport', 300),
            ('Shopping', 500), ('Entertainment', 300),
            ('Education', 200), ('Healthcare', 150),
            ('Utilities', 200), ('Rent / Housing', 1200)
        ]
        for cat_name, amount in default_budgets:
            cursor.execute(
                "SELECT id FROM categories WHERE name = %s AND user_id IS NULL",
                (cat_name,)
            )
            cat = cursor.fetchone()
            if cat:
                cursor.execute(
                    "INSERT INTO budgets (user_id, category_id, name, amount, period) VALUES (%s,%s,%s,%s,'monthly')",
                    (user_id, cat['id'], cat_name, amount)
                )

        db.commit()
        cursor.close(); db.close()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

# ============== LOGIN ==============
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email')
        password = request.form.get('password')

        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close(); db.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['name']
            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))

        flash('Invalid email or password!', 'error')

    return render_template('login.html')

# ============== FORGOT PASSWORD ==============
@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    if request.method == 'POST':
        email = request.form.get('email')
        if email:
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
            expires = datetime.utcnow() + timedelta(hours=1)

            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO password_resets (email, token, expires_at) VALUES (%s, %s, %s)",
                (email, token, expires)
            )
            db.commit()
            cursor.close(); db.close()

            reset_link = url_for('reset_password', token=token, _external=True)
            try:
                sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
                message = SGMail(
                    from_email='wongjt2006@gmail.com',
                    to_emails=email,
                    subject='Budget Master - Password Reset',
                    plain_text_content=f'Click to reset your password (valid 1 hour):\n{reset_link}'
                )
                tracking = TrackingSettings()
                tracking.click_tracking = ClickTracking(enable=False, enable_text=False)
                message.tracking_settings = tracking
                sg.send(message)
                flash(f'Password reset link sent to {email}!', 'success')
            except Exception as e:
                flash(f'Email error: {str(e)}', 'error')

    return render_template('forgot.html')

# ============== RESET PASSWORD ==============
@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM password_resets WHERE token = %s", (token,))
    record = cursor.fetchone()

    if not record:
        flash('Invalid or expired reset link.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('forgot'))

    expires_at = record['expires_at']
    now = datetime.utcnow()
    if isinstance(expires_at, str):
        expires_at = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')

    if expires_at < now:
        flash('Reset link has expired. Please request a new one.', 'error')
        cursor.close(); db.close()
        return redirect(url_for('forgot'))

    if request.method == 'POST':
        new_password = request.form.get('password')
        hashed = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password = %s WHERE email = %s", (hashed, record['email']))
        cursor.execute("DELETE FROM password_resets WHERE token = %s", (token,))
        db.commit()
        cursor.close(); db.close()
        flash('Password reset successful! Please login.', 'success')
        return redirect(url_for('login'))

    cursor.close(); db.close()
    return render_template('reset.html', token=token)

# ============== LOGOUT ==============
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ============== DASHBOARD ==============
@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id=%s AND type='income'", (user_id,))
    total_income = cursor.fetchone()['total']

    cursor.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id=%s AND type='expense'", (user_id,))
    total_spent = cursor.fetchone()['total']

    cursor.execute("SELECT COALESCE(SUM(remaining_balance),0) as total FROM loans WHERE user_id=%s AND status='active'", (user_id,))
    total_loan = cursor.fetchone()['total']

    current_month = datetime.now().strftime('%Y-%m')
    m_start, m_end = month_range(current_month)
    cursor.execute("""
        SELECT COALESCE(SUM(amount),0) as total FROM transactions
        WHERE user_id=%s AND type='expense' AND `date`>=%s AND `date`<%s
    """, (user_id, m_start, m_end))
    monthly_total = cursor.fetchone()['total']

    cursor.execute("""
        SELECT t.*, c.name as category_name FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s ORDER BY t.`date` DESC, t.created_at DESC LIMIT 10
    """, (user_id,))
    expenses = cursor.fetchall()

    cursor.execute("""
        SELECT b.amount, c.name as category_name
        FROM budgets b
        LEFT JOIN categories c ON b.category_id = c.id
        WHERE b.user_id=%s
    """, (user_id,))
    budgets_raw = cursor.fetchall()

    cursor.execute("""
        SELECT c.name as category_name, COALESCE(SUM(t.amount),0) as spent
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s
        AND t.`date`>=%s AND t.`date`<%s
        GROUP BY c.name
    """, (user_id, m_start, m_end))
    spent_map = {row['category_name'].lower(): float(row['spent']) for row in cursor.fetchall()}
    budgets = {
        b['category_name']: {
            'limit': float(b['amount']),
            'spent': spent_map.get(b['category_name'].lower(), 0.0)
        }
        for b in budgets_raw
    }

    cursor.execute("SELECT * FROM loans WHERE user_id=%s AND status='active'", (user_id,))
    loans = cursor.fetchall()

    cursor.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s AND t.type='expense'
        GROUP BY c.name
    """, (user_id,))
    category_data = {row['category']: float(row['total']) for row in cursor.fetchall()}

    newly_earned, _ = check_and_award_badges(cursor, user_id)
    db.commit()
    for badge in newly_earned:
        flash(f"{badge['icon']} New badge earned: {badge['name']} (+{badge['points']} pts)! Check your Rewards page.", 'success')

    cursor.close(); db.close()

    return render_template('dashboard.html',
        total_income=total_income,
        total_spent=total_spent,
        total_loan=total_loan,
        monthly_total=monthly_total,
        budgets=budgets,
        expenses=expenses,
        loans=loans,
        category_data=category_data
    )

# ============== EXPENSES / TRANSACTIONS ==============
@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    if request.method == 'POST':
        date     = request.form.get('date')
        category = request.form.get('category')
        amount   = float(request.form.get('amount'))
        tx_type  = request.form.get('type', 'expense')
        note     = request.form.get('note', '')

        # Always prefer global (user_id IS NULL) categories to match budgets
        cursor.execute(
            "SELECT id FROM categories WHERE LOWER(name)=LOWER(%s) AND user_id IS NULL",
            (category,)
        )
        cat = cursor.fetchone()
        if not cat:
            # Fall back to any matching category
            cursor.execute(
                "SELECT id FROM categories WHERE LOWER(name)=LOWER(%s)",
                (category,)
            )
            cat = cursor.fetchone()
        if not cat:
            # Create new global category if none exists
            cursor.execute(
                "INSERT INTO categories (user_id, name, type) VALUES (NULL, %s, 'expense')",
                (category,)
            )
            cat_id = cursor.lastrowid
        else:
            cat_id = cat['id']

        cursor.execute(
            "INSERT INTO transactions (user_id, category_id, type, amount, description, date) VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, cat_id, tx_type, amount, note, date)
        )
        transaction_id = cursor.lastrowid

        receipt_file = request.files.get('receipt')
        receipt_id, receipt_error = save_receipt(cursor, transaction_id, user_id, category, receipt_file)
        if receipt_error:
            flash(receipt_error, 'error')

        db.commit()
        if receipt_id:
            flash('Transaction added and receipt saved to your vault!', 'success')
        else:
            flash('Transaction added successfully!', 'success')
        cursor.close(); db.close()
        return redirect(url_for('expenses'))

    cursor.execute("""
        SELECT t.*, c.name as category,
            (SELECT r.id FROM receipts r WHERE r.transaction_id = t.id ORDER BY r.uploaded_at DESC LIMIT 1) as receipt_id
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s ORDER BY t.`date` DESC, t.created_at DESC
    """, (user_id,))
    user_expenses = cursor.fetchall()
    cursor.close(); db.close()

    return render_template('expenses.html', expenses=user_expenses)

# ============== EDIT EXPENSE ==============
@app.route('/edit_expense/<int:expense_id>', methods=['POST'])
@login_required
def edit_expense(expense_id):
    user_id  = session['user_id']
    date     = request.form.get('date')
    category = request.form.get('category')
    amount   = float(request.form.get('amount'))
    tx_type  = request.form.get('type', 'expense')
    note     = request.form.get('note', '')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Always prefer global (user_id IS NULL) categories to match budgets
    cursor.execute(
        "SELECT id FROM categories WHERE LOWER(name)=LOWER(%s) AND user_id IS NULL",
        (category,)
    )
    cat = cursor.fetchone()
    if not cat:
        cursor.execute(
            "INSERT INTO categories (user_id, name, type) VALUES (NULL, %s, %s)",
            (category, tx_type)
        )
        cat_id = cursor.lastrowid
    else:
        cat_id = cat['id']

    cursor.execute("""
        UPDATE transactions
        SET date=%s, category_id=%s, type=%s, amount=%s, description=%s
        WHERE id=%s AND user_id=%s
    """, (date, cat_id, tx_type, amount, note, expense_id, user_id))

    receipt_file = request.files.get('receipt')
    receipt_id, receipt_error = save_receipt(cursor, expense_id, user_id, category, receipt_file)
    if receipt_error:
        flash(receipt_error, 'error')

    db.commit()
    cursor.close(); db.close()
    if receipt_id:
        flash('Transaction updated and receipt saved to your vault!', 'success')
    else:
        flash('Transaction updated successfully!', 'success')
    return redirect(url_for('expenses'))

# ============== DELETE EXPENSE ==============
@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM transactions WHERE id=%s AND user_id=%s", (expense_id, user_id))
    db.commit()
    cursor.close(); db.close()
    flash('Transaction deleted!', 'success')
    return redirect(url_for('expenses'))

# ============== RECEIPT VAULT ==============
@app.route('/vault')
@login_required
def vault():
    user_id = session['user_id']
    category_filter = request.args.get('category', '')
    search = request.args.get('search', '')

    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    query = """
        SELECT r.id, r.filename, r.mime_type, r.file_size, r.uploaded_at, r.category,
               t.id as transaction_id, t.`date` as transaction_date, t.amount, t.description
        FROM receipts r
        JOIN transactions t ON r.transaction_id = t.id
        WHERE r.user_id = %s
    """
    params = [user_id]
    if category_filter:
        query += " AND r.category = %s"
        params.append(category_filter)
    if search:
        query += " AND (t.description LIKE %s OR r.filename LIKE %s)"
        like = f"%{search}%"
        params += [like, like]
    query += " ORDER BY r.uploaded_at DESC"

    cursor.execute(query, params)
    receipts = cursor.fetchall()

    cursor.execute(
        "SELECT DISTINCT category FROM receipts WHERE user_id=%s AND category IS NOT NULL ORDER BY category",
        (user_id,)
    )
    categories = [row['category'] for row in cursor.fetchall()]

    cursor.execute("SELECT COALESCE(SUM(file_size),0) as total, COUNT(*) as count FROM receipts WHERE user_id=%s", (user_id,))
    stats = cursor.fetchone()

    cursor.close(); db.close()

    return render_template('vault.html',
                           receipts=receipts,
                           categories=categories,
                           category_filter=category_filter,
                           search=search,
                           total_size=float(stats['total']),
                           total_count=stats['count'])

# ============== VIEW / DOWNLOAD RECEIPT ==============
@app.route('/vault/receipt/<int:receipt_id>')
@login_required
def view_receipt(receipt_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM receipts WHERE id=%s AND user_id=%s", (receipt_id, user_id))
    receipt = cursor.fetchone()

    if not receipt:
        cursor.close(); db.close()
        flash('Receipt not found.', 'error')
        return redirect(url_for('vault'))

    is_download = request.args.get('download') == '1'
    log_receipt_action(cursor, receipt_id, receipt['filename'], user_id, 'download' if is_download else 'view')
    db.commit()
    cursor.close(); db.close()

    response = make_response(bytes(receipt['file_data']))
    response.headers['Content-Type'] = receipt['mime_type'] or 'application/octet-stream'
    disposition = 'attachment' if is_download else 'inline'
    response.headers['Content-Disposition'] = f'{disposition}; filename="{receipt["filename"]}"'
    return response

# ============== DELETE RECEIPT ==============
@app.route('/vault/receipt/<int:receipt_id>/delete', methods=['POST'])
@login_required
def delete_receipt(receipt_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT filename FROM receipts WHERE id=%s AND user_id=%s", (receipt_id, user_id))
    receipt = cursor.fetchone()

    if receipt:
        log_receipt_action(cursor, receipt_id, receipt['filename'], user_id, 'delete')
        cursor.execute("DELETE FROM receipts WHERE id=%s AND user_id=%s", (receipt_id, user_id))
        db.commit()
        flash('Receipt deleted from vault.', 'success')
    else:
        flash('Receipt not found.', 'error')

    cursor.close(); db.close()
    return redirect(url_for('vault'))

# ============== RECEIPT AUDIT TRAIL ==============
@app.route('/vault/receipt/<int:receipt_id>/audit')
@login_required
def receipt_audit(receipt_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    cursor.execute("""
        SELECT r.*, t.`date` as transaction_date, t.amount, t.description
        FROM receipts r JOIN transactions t ON r.transaction_id = t.id
        WHERE r.id=%s AND r.user_id=%s
    """, (receipt_id, user_id))
    receipt = cursor.fetchone()

    if not receipt:
        cursor.close(); db.close()
        flash('Receipt not found.', 'error')
        return redirect(url_for('vault'))

    cursor.execute(
        "SELECT * FROM receipt_audit_log WHERE receipt_id=%s AND user_id=%s ORDER BY created_at DESC",
        (receipt_id, user_id)
    )
    logs = cursor.fetchall()
    cursor.close(); db.close()

    return render_template('receipt_audit.html', receipt=receipt, logs=logs)

# ============== SHARED WALLETS ==============
@app.route('/wallets', methods=['GET', 'POST'])
@login_required
def wallets():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            monthly_limit = request.form.get('monthly_limit') or None

            if not name:
                flash('Wallet name is required.', 'error')
            else:
                invite_code = generate_invite_code(cursor)
                cursor.execute(
                    "INSERT INTO wallets (name, description, invite_code, created_by, monthly_limit) VALUES (%s,%s,%s,%s,%s)",
                    (name, description, invite_code, user_id, monthly_limit)
                )
                wallet_id = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO wallet_members (wallet_id, user_id, role) VALUES (%s,%s,'owner')",
                    (wallet_id, user_id)
                )
                db.commit()
                cursor.close(); db.close()
                flash(f'Wallet "{name}" created! Invite code: {invite_code}', 'success')
                return redirect(url_for('wallet_detail', wallet_id=wallet_id))

        elif action == 'join':
            code = request.form.get('invite_code', '').strip().upper()
            cursor.execute("SELECT * FROM wallets WHERE invite_code=%s", (code,))
            wallet = cursor.fetchone()

            if not wallet:
                flash('Invalid invite code.', 'error')
            elif get_wallet_membership(cursor, wallet['id'], user_id):
                flash('You are already a member of this wallet.', 'warning')
            else:
                cursor.execute(
                    "INSERT INTO wallet_members (wallet_id, user_id, role) VALUES (%s,%s,'member')",
                    (wallet['id'], user_id)
                )
                db.commit()
                cursor.close(); db.close()
                flash(f'Joined "{wallet["name"]}"!', 'success')
                return redirect(url_for('wallet_detail', wallet_id=wallet['id']))

        cursor.close(); db.close()
        return redirect(url_for('wallets'))

    cursor.execute("""
        SELECT w.*, wm.role,
            (SELECT COUNT(*) FROM wallet_members wm2 WHERE wm2.wallet_id = w.id) as member_count,
            (SELECT COALESCE(SUM(amount),0) FROM wallet_transactions wt WHERE wt.wallet_id = w.id) as total_spent
        FROM wallets w
        JOIN wallet_members wm ON wm.wallet_id = w.id
        WHERE wm.user_id = %s
        ORDER BY w.created_at DESC
    """, (user_id,))
    my_wallets = cursor.fetchall()
    cursor.close(); db.close()

    return render_template('wallets.html', wallets=my_wallets)

# ============== WALLET DETAIL ==============
@app.route('/wallets/<int:wallet_id>', methods=['GET', 'POST'])
@login_required
def wallet_detail(wallet_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    membership = get_wallet_membership(cursor, wallet_id, user_id)
    if not membership:
        cursor.close(); db.close()
        flash('You are not a member of that wallet.', 'error')
        return redirect(url_for('wallets'))

    if request.method == 'POST':
        category = request.form.get('category')
        amount = float(request.form.get('amount'))
        note = request.form.get('note', '')
        date = request.form.get('date')

        cursor.execute("""
            INSERT INTO wallet_transactions (wallet_id, paid_by, category, amount, description, `date`)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (wallet_id, user_id, category, amount, note, date))
        db.commit()
        cursor.close(); db.close()
        flash('Shared expense added!', 'success')
        return redirect(url_for('wallet_detail', wallet_id=wallet_id))

    cursor.execute("SELECT * FROM wallets WHERE id=%s", (wallet_id,))
    wallet = cursor.fetchone()

    cursor.execute("""
        SELECT wm.user_id, wm.role, wm.joined_at, u.name, u.email
        FROM wallet_members wm JOIN users u ON wm.user_id = u.id
        WHERE wm.wallet_id = %s ORDER BY wm.role DESC, wm.joined_at
    """, (wallet_id,))
    members = cursor.fetchall()

    cursor.execute("""
        SELECT wt.*, u.name as paid_by_name
        FROM wallet_transactions wt JOIN users u ON wt.paid_by = u.id
        WHERE wt.wallet_id = %s ORDER BY wt.`date` DESC, wt.created_at DESC
    """, (wallet_id,))
    transactions = cursor.fetchall()

    cursor.execute("""
        SELECT category, COALESCE(SUM(amount),0) as total
        FROM wallet_transactions WHERE wallet_id=%s GROUP BY category
    """, (wallet_id,))
    category_totals = {row['category'] or 'Others': float(row['total']) for row in cursor.fetchall()}

    cursor.close(); db.close()

    # Settle-up: equal split of total pool spend across all members
    total_spent = sum(float(t['amount']) for t in transactions)
    member_count = len(members) or 1
    fair_share = total_spent / member_count

    paid_by_user = {}
    for t in transactions:
        paid_by_user[t['paid_by']] = paid_by_user.get(t['paid_by'], 0) + float(t['amount'])

    balances = []
    for m in members:
        paid = paid_by_user.get(m['user_id'], 0.0)
        balances.append({
            'user_id': m['user_id'],
            'name': m['name'],
            'role': m['role'],
            'paid': paid,
            'fair_share': fair_share,
            'balance': paid - fair_share  # positive = owed money, negative = owes money
        })

    return render_template('wallet_detail.html',
                           wallet=wallet,
                           members=members,
                           transactions=transactions,
                           category_data=category_totals,
                           total_spent=total_spent,
                           fair_share=fair_share,
                           balances=balances,
                           my_role=membership['role'],
                           my_user_id=user_id)

# ============== LEAVE WALLET ==============
@app.route('/wallets/<int:wallet_id>/leave', methods=['POST'])
@login_required
def leave_wallet(wallet_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)

    membership = get_wallet_membership(cursor, wallet_id, user_id)
    if not membership:
        cursor.close(); db.close()
        flash('You are not a member of that wallet.', 'error')
        return redirect(url_for('wallets'))

    cursor.execute("SELECT COUNT(*) as cnt FROM wallet_members WHERE wallet_id=%s", (wallet_id,))
    member_count = cursor.fetchone()['cnt']

    if membership['role'] == 'owner' and member_count > 1:
        cursor.close(); db.close()
        flash('Transfer ownership or remove all other members before leaving.', 'error')
        return redirect(url_for('wallet_detail', wallet_id=wallet_id))

    cursor.execute("DELETE FROM wallet_members WHERE wallet_id=%s AND user_id=%s", (wallet_id, user_id))
    if membership['role'] == 'owner':
        # last member leaving — remove the wallet entirely
        cursor.execute("DELETE FROM wallets WHERE id=%s", (wallet_id,))
    db.commit()
    cursor.close(); db.close()
    flash('You left the wallet.', 'success')
    return redirect(url_for('wallets'))

# ============== REMOVE MEMBER (owner only) ==============
@app.route('/wallets/<int:wallet_id>/remove/<int:target_user_id>', methods=['POST'])
@login_required
def remove_wallet_member(wallet_id, target_user_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)

    membership = get_wallet_membership(cursor, wallet_id, user_id)
    if not membership or membership['role'] != 'owner':
        cursor.close(); db.close()
        flash('Only the wallet owner can remove members.', 'error')
        return redirect(url_for('wallet_detail', wallet_id=wallet_id))

    if target_user_id == user_id:
        cursor.close(); db.close()
        flash('Use "Leave Wallet" to remove yourself.', 'error')
        return redirect(url_for('wallet_detail', wallet_id=wallet_id))

    cursor.execute("DELETE FROM wallet_members WHERE wallet_id=%s AND user_id=%s", (wallet_id, target_user_id))
    db.commit()
    cursor.close(); db.close()
    flash('Member removed.', 'success')
    return redirect(url_for('wallet_detail', wallet_id=wallet_id))

# ============== BUDGET ==============
@app.route('/budget', methods=['GET', 'POST'])
@login_required
def budget():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    current_month = datetime.now().strftime('%Y-%m')
    m_start, m_end = month_range(current_month)

    if request.method == 'POST':
        category = request.form.get('category')
        limit    = float(request.form.get('limit'))

        # Prefer global categories to stay consistent with transactions
        cursor.execute(
            "SELECT id FROM categories WHERE LOWER(name)=LOWER(%s) AND user_id IS NULL",
            (category,)
        )
        cat = cursor.fetchone()
        if not cat:
            cursor.execute(
                "SELECT id FROM categories WHERE LOWER(name)=LOWER(%s)",
                (category,)
            )
            cat = cursor.fetchone()
        if not cat:
            cursor.execute(
                "INSERT INTO categories (user_id, name, type) VALUES (NULL, %s, 'expense')",
                (category,)
            )
            cat_id = cursor.lastrowid
        else:
            cat_id = cat['id']

        cursor.execute("SELECT id FROM budgets WHERE user_id=%s AND category_id=%s", (user_id, cat_id))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("UPDATE budgets SET amount=%s WHERE id=%s", (limit, existing['id']))
        else:
            cursor.execute(
                "INSERT INTO budgets (user_id, category_id, name, amount, period) VALUES (%s,%s,%s,%s,'monthly')",
                (user_id, cat_id, category, limit)
            )
        db.commit()
        flash(f'Budget for {category} updated!', 'success')
        cursor.close(); db.close()
        return redirect(url_for('budget'))

    cursor.execute("""
        SELECT b.amount, c.name as category_name
        FROM budgets b
        LEFT JOIN categories c ON b.category_id = c.id
        WHERE b.user_id=%s
    """, (user_id,))
    budgets_raw = cursor.fetchall()

    cursor.execute("""
        SELECT c.name as category_name, COALESCE(SUM(t.amount),0) as spent
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s
        AND t.`date`>=%s AND t.`date`<%s
        GROUP BY c.name
    """, (user_id, m_start, m_end))
    spent_map = {row['category_name'].lower(): float(row['spent']) for row in cursor.fetchall()}
    budgets = {
        b['category_name']: {
            'limit': float(b['amount']),
            'spent': spent_map.get(b['category_name'].lower(), 0.0)
        }
        for b in budgets_raw
    }

    cursor.close(); db.close()
    return render_template('budget.html', budgets=budgets)

# ============== LOANS ==============
@app.route('/loans', methods=['GET', 'POST'])
@login_required
def loans():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name            = request.form.get('name')
            total           = float(request.form.get('total'))
            interest_rate   = float(request.form.get('interest_rate', 0))
            monthly_payment = float(request.form.get('monthly_payment'))
            start_date      = request.form.get('start_date')

            months = total / monthly_payment if monthly_payment > 0 else 0
            end_date = (datetime.now() + timedelta(days=months * 30)).strftime('%Y-%m-%d') if months else None

            cursor.execute("""
                INSERT INTO loans (user_id, name, type, principal, interest_rate, tenure_months,
                                   monthly_payment, start_date, end_date, remaining_balance)
                VALUES (%s,%s,'personal',%s,%s,%s,%s,%s,%s,%s)
            """, (user_id, name, total, interest_rate, int(months), monthly_payment, start_date, end_date, total))
            db.commit()
            flash('Loan added successfully!', 'success')

        elif action == 'payment':
            loan_id        = int(request.form.get('loan_id'))
            payment_amount = float(request.form.get('payment_amount'))

            cursor.execute("SELECT * FROM loans WHERE id=%s AND user_id=%s", (loan_id, user_id))
            loan = cursor.fetchone()
            if loan:
                new_balance = max(0, float(loan['remaining_balance']) - payment_amount)
                status = 'paid_off' if new_balance == 0 else 'active'
                cursor.execute("UPDATE loans SET remaining_balance=%s, status=%s WHERE id=%s", (new_balance, status, loan_id))
                cursor.execute(
                    "INSERT INTO loan_payments (loan_id, user_id, amount, payment_date) VALUES (%s,%s,%s,%s)",
                    (loan_id, user_id, payment_amount, datetime.now().strftime('%Y-%m-%d'))
                )
                db.commit()
                if new_balance == 0:
                    flash(f'Congratulations! {loan["name"]} is paid off!', 'success')
                else:
                    flash(f'Payment of RM{payment_amount:.2f} recorded!', 'success')

        cursor.close(); db.close()
        return redirect(url_for('loans'))

    cursor.execute("SELECT * FROM loans WHERE user_id=%s ORDER BY created_at DESC", (user_id,))
    user_loans = cursor.fetchall()
    for loan in user_loans:
        loan['total']     = float(loan['principal'])
        loan['remaining'] = float(loan['remaining_balance'])
        loan['estimated_payoff'] = str(loan['end_date']) if loan['end_date'] else 'N/A'

    cursor.close(); db.close()
    return render_template('loans.html', loans=user_loans)

# ============== INSIGHTS ==============
@app.route('/insights')
@login_required
def insights():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    current_month = datetime.now().strftime('%Y-%m')
    m_start, m_end = month_range(current_month)

    cursor.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id=%s AND type='expense'", (user_id,))
    total_spent = float(cursor.fetchone()['total'])

    cursor.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id=%s AND type='income'", (user_id,))
    total_income = float(cursor.fetchone()['total'])

    cursor.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s AND t.type='expense' GROUP BY c.name
    """, (user_id,))
    category_totals = {row['category']: float(row['total']) for row in cursor.fetchall()}

    cursor.execute("""
        SELECT b.amount, c.name as category_name
        FROM budgets b LEFT JOIN categories c ON b.category_id=c.id
        WHERE b.user_id=%s
    """, (user_id,))
    budgets_raw = cursor.fetchall()

    cursor.execute("""
        SELECT c.name as category_name, COALESCE(SUM(t.amount),0) as spent
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s
        AND t.`date`>=%s AND t.`date`<%s
        GROUP BY c.name
    """, (user_id, m_start, m_end))
    spent_map = {row['category_name'].lower(): float(row['spent']) for row in cursor.fetchall()}
    budgets = [
        {
            'amount': b['amount'],
            'category_name': b['category_name'],
            'spent': spent_map.get(b['category_name'].lower(), 0.0)
        }
        for b in budgets_raw
    ]

    cursor.execute("SELECT * FROM loans WHERE user_id=%s AND status='active'", (user_id,))
    loans = cursor.fetchall()
    cursor.close(); db.close()

    suggestions = []
    for b in budgets:
        spent = float(b['spent'])
        limit = float(b['amount'])
        cat   = b['category_name']
        if spent > limit:
            pct = ((spent - limit) / limit) * 100
            suggestions.append({'type':'warning','icon':'⚠️','title':f'{cat} Budget Exceeded',
                'message':f'You\'ve exceeded your {cat} budget by {pct:.1f}%.',
                'action':f'Reduce {cat.lower()} expenses by RM{spent - limit:.2f}'})
        elif limit and spent > limit * 0.8:
            suggestions.append({'type':'caution','icon':'📊','title':f'{cat} Budget Alert',
                'message':f'You\'ve used {(spent/limit*100):.1f}% of your {cat} budget.',
                'action':'Monitor spending closely'})

    if total_income > 0:
        savings_rate = ((total_income - total_spent) / total_income) * 100
        if savings_rate < 20:
            suggestions.append({'type':'tip','icon':'💡','title':'Improve Savings Rate',
                'message':f'Your savings rate is {savings_rate:.1f}%. Aim for at least 20%.',
                'action':'Try the 50/30/20 rule'})
        else:
            suggestions.append({'type':'success','icon':'🎉','title':'Great Savings Habit!',
                'message':f'Your savings rate of {savings_rate:.1f}% exceeds 20%!',
                'action':'Consider investing surplus savings'})

    if loans:
        high_interest = [l for l in loans if float(l['interest_rate']) > 10]
        if high_interest:
            suggestions.append({'type':'warning','icon':'🏦','title':'High Interest Loans',
                'message':f'You have {len(high_interest)} loan(s) above 10% interest.',
                'action':'Pay off highest interest loan first'})

    if category_totals:
        top_cat = max(category_totals, key=category_totals.get)
        suggestions.append({'type':'info','icon':'📈','title':'Top Spending Category',
            'message':f'Highest spend: {top_cat} (RM{category_totals[top_cat]:.2f}).',
            'action':'Review if this aligns with your goals'})

    if not suggestions:
        suggestions.append({'type':'tip','icon':'🚀','title':'Start Tracking',
            'message':'Add transactions to get personalized insights!',
            'action':'Record all expenses for better analysis'})

    return render_template('insights.html', suggestions=suggestions,
                           total_spent=total_spent, total_income=total_income,
                           category_data=category_totals)

# ============== REPORT ==============
@app.route('/report')
@login_required
def report():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    cursor.execute("""
        SELECT t.*, c.name as category FROM transactions t
        LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s ORDER BY t.`date` DESC
    """, (user_id,))
    all_transactions = cursor.fetchall()

    cursor.execute("""
        SELECT b.amount, c.name as category_name
        FROM budgets b LEFT JOIN categories c ON b.category_id=c.id
        WHERE b.user_id=%s
    """, (user_id,))
    budgets_raw = cursor.fetchall()

    cursor.execute("""
        SELECT c.name as category_name, COALESCE(SUM(t.amount),0) as spent
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s AND t.type='expense'
        GROUP BY c.name
    """, (user_id,))
    spent_map_all = {row['category_name'].lower(): float(row['spent']) for row in cursor.fetchall()}
    budgets = {
        b['category_name']: {
            'limit': float(b['amount']),
            'spent': spent_map_all.get(b['category_name'].lower(), 0.0)
        }
        for b in budgets_raw
    }

    cursor.execute("SELECT * FROM loans WHERE user_id=%s", (user_id,))
    loans = cursor.fetchall()
    cursor.close(); db.close()

    monthly_data = {}
    for e in all_transactions:
        month = str(e['date'])[:7]
        if month not in monthly_data:
            monthly_data[month] = {'income': 0, 'expense': 0, 'transactions': []}
        if e['type'] == 'income':
            monthly_data[month]['income'] += float(e['amount'])
        else:
            monthly_data[month]['expense'] += float(e['amount'])
        monthly_data[month]['transactions'].append(e)

    sorted_months = sorted(monthly_data.keys(), reverse=True)

    return render_template('report.html',
                           monthly_data=monthly_data,
                           sorted_months=sorted_months,
                           budgets=budgets,
                           loans=loans)

# ============== DOWNLOAD REPORT ==============
@app.route('/report/download/<month>')
@login_required
def download_report(month):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    d_start, d_end = month_range(month)

    cursor.execute("""
        SELECT t.*, c.name as category FROM transactions t
        LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s AND t.`date`>=%s AND t.`date`<%s
        ORDER BY t.`date`
    """, (user_id, d_start, d_end))
    month_expenses = cursor.fetchall()

    cursor.execute("""
        SELECT b.amount, c.name as category_name
        FROM budgets b LEFT JOIN categories c ON b.category_id=c.id
        WHERE b.user_id=%s
    """, (user_id,))
    budgets_base = cursor.fetchall()

    cursor.execute("""
        SELECT c.name as category_name, COALESCE(SUM(t.amount),0) as spent
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s
        AND t.`date`>=%s AND t.`date`<%s
        GROUP BY c.name
    """, (user_id, d_start, d_end))
    spent_map_dl = {row['category_name'].lower(): float(row['spent']) for row in cursor.fetchall()}
    budgets_raw = [
        {
            'amount': b['amount'],
            'category_name': b['category_name'],
            'spent': spent_map_dl.get(b['category_name'].lower(), 0.0)
        }
        for b in budgets_base
    ]
    cursor.close(); db.close()

    total_income  = sum(float(e['amount']) for e in month_expenses if e['type'] == 'income')
    total_expense = sum(float(e['amount']) for e in month_expenses if e['type'] == 'expense')

    lines = [
        "=" * 50,
        "    BUDGET MASTER - MONTHLY FINANCIAL REPORT",
        f"    Month: {month}",
        f"    Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"    User: {session['username']}",
        "=" * 50, "",
        "SUMMARY", "-" * 30,
        f"Total Income:    RM{total_income:,.2f}",
        f"Total Expenses:  RM{total_expense:,.2f}",
        f"Net Balance:     RM{total_income - total_expense:,.2f}",
        "", "TRANSACTIONS", "-" * 30,
    ]

    for e in month_expenses:
        sign = '+' if e['type'] == 'income' else '-'
        lines.append(f"{e['date']} | {str(e['category']):15} | {sign}RM{float(e['amount']):>10.2f} | {e.get('description','')}")

    lines += ["", "BUDGET STATUS", "-" * 30]
    for b in budgets_raw:
        status = "OK" if float(b['spent']) <= float(b['amount']) else "OVER"
        lines.append(f"[{status}] {b['category_name']}: RM{float(b['spent']):.2f} / RM{float(b['amount']):.2f}")

    lines += ["", "=" * 50, "Generated by Budget Master", "=" * 50]

    response = make_response("\n".join(lines))
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=budget_report_{month}.txt'
    return response

# ============== API: CHART DATA ==============
@app.route('/api/chart-data')
@login_required
def chart_data():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    cursor.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s AND t.type='expense' GROUP BY c.name
    """, (user_id,))
    category_totals = {row['category']: float(row['total']) for row in cursor.fetchall()}

    # Fetch all transactions and group by month in Python to avoid MySQL DATE_FORMAT alias issues
    cursor.execute("""
        SELECT `date`, type, amount FROM transactions WHERE user_id=%s
        ORDER BY `date`
    """, (user_id,))
    rows = cursor.fetchall()
    monthly = {}
    for row in rows:
        month_key = str(row['date'])[:7]  # "YYYY-MM"
        if month_key not in monthly:
            monthly[month_key] = {'income': 0.0, 'expense': 0.0}
        if row['type'] == 'income':
            monthly[month_key]['income'] += float(row['amount'])
        else:
            monthly[month_key]['expense'] += float(row['amount'])

    cursor.close(); db.close()
    return jsonify({'categories': category_totals, 'monthly': monthly})

# ============== REWARDS ==============
@app.route('/rewards')
@login_required
def rewards():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    newly_earned, stats = check_and_award_badges(cursor, user_id)
    db.commit()

    cursor.execute("SELECT COALESCE(SUM(points),0) as total FROM point_log WHERE user_id=%s", (user_id,))
    total_points = cursor.fetchone()['total']

    cursor.execute("SELECT badge_code, points_awarded, earned_at FROM user_badges WHERE user_id=%s", (user_id,))
    earned_map = {row['badge_code']: row for row in cursor.fetchall()}

    cursor.execute("""
        SELECT points, reason, created_at FROM point_log
        WHERE user_id=%s ORDER BY created_at DESC LIMIT 20
    """, (user_id,))
    recent_points = cursor.fetchall()

    cursor.close(); db.close()

    for badge in newly_earned:
        flash(f"{badge['icon']} New badge earned: {badge['name']} (+{badge['points']} pts)!", 'success')

    level_name, level_icon, next_threshold = get_level(total_points)
    if next_threshold:
        level_progress = min(100, round((total_points / next_threshold) * 100))
    else:
        level_progress = 100

    badges_display = []
    for badge in BADGES:
        earned = badge['code'] in earned_map
        badges_display.append({
            **badge,
            'earned': earned,
            'earned_at': earned_map[badge['code']]['earned_at'] if earned else None
        })
    badges_display.sort(key=lambda b: (not b['earned']))

    return render_template('rewards.html',
        total_points=total_points,
        level_name=level_name,
        level_icon=level_icon,
        next_threshold=next_threshold,
        level_progress=level_progress,
        badges=badges_display,
        recent_points=recent_points,
        stats=stats,
        earned_count=len(earned_map),
        total_badges=len(BADGES))

# ============== RUN ==============
if __name__ == "__main__":
    app.run(debug=True)
