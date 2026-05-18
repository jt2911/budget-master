from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import json
import random
import string
import mysql.connector

app = Flask(__name__)
app.jinja_env.globals['now'] = datetime.now
app.secret_key = 'your-super-secret-key-change-this-in-production'

from flask_mail import Mail, Message
import os
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'wongjt2006@gmail.com'
app.config['MAIL_PASSWORD'] = 'pxpofoimgsysvrox'
app.config['MAIL_DEFAULT_SENDER'] = 'wongjt2006@gmail.com'
mail = Mail(app)

# ============== DATABASE CONNECTION ==============
def get_db():
    conn = mysql.connector.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        database="budget_master"
    )
    return conn

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

        # Default budgets for new user
        default_budgets = [
            ('Necessities', 1000), ('Entertainment', 300),
            ('Savings', 500), ('Education', 200),
            ('Health', 150), ('Others', 100)
        ]
        # Find or create category IDs and insert budgets
        for cat_name, amount in default_budgets:
            cursor.execute("SELECT id FROM categories WHERE name = %s AND (user_id IS NULL OR user_id = %s)", (cat_name, user_id))
            cat = cursor.fetchone()
            if not cat:
                cursor.execute("INSERT INTO categories (user_id, name, type) VALUES (%s, %s, 'expense')", (user_id, cat_name))
                cat_id = cursor.lastrowid
            else:
                cat_id = cat['id']

            cursor.execute(
                "INSERT INTO budgets (user_id, category_id, name, amount, period) VALUES (%s, %s, %s, %s, 'monthly')",
                (user_id, cat_id, cat_name, amount)
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
            expires = datetime.now() + timedelta(hours=1)

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
                msg = Message(subject='Budget Master - Password Reset', recipients=[email])
                msg.body = f"Click to reset your password (valid 1 hour):\n{reset_link}"
                mail.send(msg)
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

    if not record or record['expires_at'] < datetime.now():
        flash('Invalid or expired reset link.', 'error')
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

    # Total income
    cursor.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id=%s AND type='income'", (user_id,))
    total_income = cursor.fetchone()['total']

    # Total spent
    cursor.execute("SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id=%s AND type='expense'", (user_id,))
    total_spent = cursor.fetchone()['total']

    # Total loan remaining
    cursor.execute("SELECT COALESCE(SUM(remaining_balance),0) as total FROM loans WHERE user_id=%s AND status='active'", (user_id,))
    total_loan = cursor.fetchone()['total']

    # Monthly totals
    current_month = datetime.now().strftime('%Y-%m')
    cursor.execute("""
        SELECT COALESCE(SUM(amount),0) as total FROM transactions
        WHERE user_id=%s AND type='expense' AND DATE_FORMAT(date,'%%Y-%%m')=%s
    """, (user_id, current_month))
    monthly_total = cursor.fetchone()['total']

    # Recent 10 transactions
    cursor.execute("""
        SELECT t.*, c.name as category_name FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s ORDER BY t.date DESC, t.created_at DESC LIMIT 10
    """, (user_id,))
    expenses = cursor.fetchall()

    # Budgets with spent amounts
    cursor.execute("""
        SELECT b.*, c.name as category_name,
               COALESCE((
                   SELECT SUM(t.amount) FROM transactions t
                   WHERE t.user_id=b.user_id AND t.category_id=b.category_id
                   AND t.type='expense'
                   AND DATE_FORMAT(t.date,'%%Y-%%m')=%s
               ),0) as spent
        FROM budgets b
        LEFT JOIN categories c ON b.category_id = c.id
        WHERE b.user_id=%s
    """, (current_month, user_id))
    budgets_raw = cursor.fetchall()
    budgets = {b['category_name']: {'limit': float(b['amount']), 'spent': float(b['spent'])} for b in budgets_raw}

    # Loans
    cursor.execute("SELECT * FROM loans WHERE user_id=%s AND status='active'", (user_id,))
    loans = cursor.fetchall()

    # Category breakdown
    cursor.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s AND t.type='expense'
        GROUP BY c.name
    """, (user_id,))
    category_data = {row['category']: float(row['total']) for row in cursor.fetchall()}

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
    cursor = db.cursor(dictionary=True)

    if request.method == 'POST':
        date     = request.form.get('date')
        category = request.form.get('category')
        amount   = float(request.form.get('amount'))
        tx_type  = request.form.get('type', 'expense')
        note     = request.form.get('note', '')

        # Get or create category
        cursor.execute("SELECT id FROM categories WHERE name=%s AND (user_id IS NULL OR user_id=%s)", (category, user_id))
        cat = cursor.fetchone()
        if not cat:
            cursor.execute("INSERT INTO categories (user_id, name, type) VALUES (%s, %s, %s)", (user_id, category, tx_type))
            cat_id = cursor.lastrowid
        else:
            cat_id = cat['id']

        cursor.execute(
            "INSERT INTO transactions (user_id, category_id, type, amount, description, date) VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, cat_id, tx_type, amount, note, date)
        )
        db.commit()
        flash('Transaction added successfully!', 'success')
        cursor.close(); db.close()
        return redirect(url_for('expenses'))

    cursor.execute("""
        SELECT t.*, c.name as category FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id=%s ORDER BY t.date DESC, t.created_at DESC
    """, (user_id,))
    user_expenses = cursor.fetchall()
    cursor.close(); db.close()

    return render_template('expenses.html', expenses=user_expenses)

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

# ============== BUDGET ==============
@app.route('/budget', methods=['GET', 'POST'])
@login_required
def budget():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    current_month = datetime.now().strftime('%Y-%m')

    if request.method == 'POST':
        category = request.form.get('category')
        limit    = float(request.form.get('limit'))

        # Get or create category
        cursor.execute("SELECT id FROM categories WHERE name=%s AND (user_id IS NULL OR user_id=%s)", (category, user_id))
        cat = cursor.fetchone()
        if not cat:
            cursor.execute("INSERT INTO categories (user_id, name, type) VALUES (%s, %s, 'expense')", (user_id, category))
            cat_id = cursor.lastrowid
        else:
            cat_id = cat['id']

        # Update or insert budget
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
        SELECT b.*, c.name as category_name,
               COALESCE((
                   SELECT SUM(t.amount) FROM transactions t
                   WHERE t.user_id=b.user_id AND t.category_id=b.category_id
                   AND t.type='expense' AND DATE_FORMAT(t.date,'%%Y-%%m')=%s
               ),0) as spent
        FROM budgets b
        LEFT JOIN categories c ON b.category_id = c.id
        WHERE b.user_id=%s
    """, (current_month, user_id))
    budgets_raw = cursor.fetchall()
    budgets = {b['category_name']: {'limit': float(b['amount']), 'spent': float(b['spent'])} for b in budgets_raw}

    cursor.close(); db.close()
    return render_template('budget.html', budgets=budgets)

# ============== LOANS ==============
@app.route('/loans', methods=['GET', 'POST'])
@login_required
def loans():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)

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
    # Add estimated_payoff for template compatibility
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
    cursor = db.cursor(dictionary=True)
    current_month = datetime.now().strftime('%Y-%m')

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
        SELECT b.*, c.name as category_name,
               COALESCE((SELECT SUM(t.amount) FROM transactions t
                WHERE t.user_id=b.user_id AND t.category_id=b.category_id
                AND t.type='expense' AND DATE_FORMAT(t.date,'%%Y-%%m')=%s),0) as spent
        FROM budgets b LEFT JOIN categories c ON b.category_id=c.id
        WHERE b.user_id=%s
    """, (current_month, user_id))
    budgets = cursor.fetchall()

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
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT t.*, c.name as category FROM transactions t
        LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s ORDER BY t.date DESC
    """, (user_id,))
    all_transactions = cursor.fetchall()

    cursor.execute("""
        SELECT b.*, c.name as category_name,
               COALESCE((SELECT SUM(t.amount) FROM transactions t
                WHERE t.user_id=b.user_id AND t.category_id=b.category_id AND t.type='expense'),0) as spent
        FROM budgets b LEFT JOIN categories c ON b.category_id=c.id
        WHERE b.user_id=%s
    """, (user_id,))
    budgets_raw = cursor.fetchall()
    budgets = {b['category_name']: {'limit': float(b['amount']), 'spent': float(b['spent'])} for b in budgets_raw}

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
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT t.*, c.name as category FROM transactions t
        LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s AND DATE_FORMAT(t.date,'%%Y-%%m')=%s
        ORDER BY t.date
    """, (user_id, month))
    month_expenses = cursor.fetchall()

    cursor.execute("""
        SELECT b.*, c.name as category_name,
               COALESCE((SELECT SUM(t.amount) FROM transactions t
                WHERE t.user_id=b.user_id AND t.category_id=b.category_id
                AND t.type='expense' AND DATE_FORMAT(t.date,'%%Y-%%m')=%s),0) as spent
        FROM budgets b LEFT JOIN categories c ON b.category_id=c.id
        WHERE b.user_id=%s
    """, (month, user_id))
    budgets_raw = cursor.fetchall()
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
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t LEFT JOIN categories c ON t.category_id=c.id
        WHERE t.user_id=%s AND t.type='expense' GROUP BY c.name
    """, (user_id,))
    category_totals = {row['category']: float(row['total']) for row in cursor.fetchall()}

    cursor.execute("""
        SELECT DATE_FORMAT(date,'%%Y-%%m') as month,
               SUM(CASE WHEN type='income'  THEN amount ELSE 0 END) as income,
               SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) as expense
        FROM transactions WHERE user_id=%s
        GROUP BY month ORDER BY month
    """, (user_id,))
    monthly = {row['month']: {'income': float(row['income']), 'expense': float(row['expense'])} for row in cursor.fetchall()}

    cursor.close(); db.close()
    return jsonify({'categories': category_totals, 'monthly': monthly})

# ============== RUN ==============
if __name__ == "__main__":
    app.run(debug=True)