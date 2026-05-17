from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import json
import random
import string
app = Flask(__name__)
app.jinja_env.globals['now'] = datetime.now
app.secret_key = 'your-super-secret-key-change-this-in-production'

from flask_mail import Mail, Message

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your@gmail.com'
app.config['omxj uxbj xqul kxcv'] = 'your-app-password'  # Gmail App Password, not login password
app.config['MAIL_DEFAULT_SENDER'] = 'your@gmail.com'

mail = Mail(app)
# ============== 模拟数据库 ==============
users_db = {}
expenses_db = {}
budgets_db = {}
loans_db = {}
reset_tokens = {}
# ============== 装饰器：登录验证 ==============
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
# ============== 首页 ==============
@app.route('/')
def index():
    return render_template('index.html')
# ============== 关于页面 ==============
# ============== 关于页面 ==============
@app.route('/about')
def about():
    return render_template('about.html')   # ← make sure this line exists

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')
# ============== 注册 ==============
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        
        if password != confirm:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('register'))
        
        if email in users_db:
            flash('Email already registered!', 'error')
            return redirect(url_for('register'))
        
        users_db[email] = {
            'username': username,
            'email': email,
            'password': generate_password_hash(password),
            'created_at': datetime.now().isoformat()
        }
        
        # 初始化用户数据
        expenses_db[email] = []
        budgets_db[email] = {
            'Necessities': {'limit': 1000, 'spent': 0},
            'Entertainment': {'limit': 300, 'spent': 0},
            'Savings': {'limit': 500, 'spent': 0},
            'Education': {'limit': 200, 'spent': 0},
            'Health': {'limit': 150, 'spent': 0},
            'Others': {'limit': 100, 'spent': 0}
        }
        loans_db[email] = []
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')
# ============== 登录 ==============
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = users_db.get(email)
        if user and check_password_hash(user['password'], password):
            session['user_id'] = email
            session['username'] = user['username']
            flash(f'Welcome back, {user["username"]}!', 'success')
            return redirect(url_for('dashboard'))
        
        flash('Invalid email or password!', 'error')
    
    return render_template('login.html')
# ============== 忘记密码 ==============
@app.route('/forgot', methods=['GET', 'POST'])
def forgot():
    if request.method == 'POST':
        email = request.form.get('email')

        if email in users_db:
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
            reset_tokens[token] = {
                'email': email,
                'expires': datetime.now() + timedelta(hours=1)
            }

            # Build the reset link
            reset_link = url_for('reset_password', token=token, _external=True)

            # Send the actual email
            try:
                msg = Message(
                    subject='Budget Master - Password Reset',
                    recipients=[email]
                )
                msg.body = f"""Hi,

You requested a password reset for your Budget Master account.

Click the link below to reset your password (valid for 1 hour):
{reset_link}

If you did not request this, please ignore this email.
"""
                mail.send(msg)
                flash(f'Password reset link sent to {email}! Check your inbox.', 'success')
            except Exception as e:
                flash('Failed to send email. Please try again later.', 'error')
                print(f"Mail error: {e}")  # check your terminal for details
        else:
            # Don't reveal whether email exists (security best practice)
            flash('If that email is registered, a reset link has been sent.', 'success')

    return render_template('forgot.html')
# ============== 重置密码 ==============
@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_data = reset_tokens.get(token)

    # Check token exists AND hasn't expired
    if not token_data or datetime.now() > token_data['expires']:
        flash('Invalid or expired reset link!', 'error')
        if token in reset_tokens:
            del reset_tokens[token]  # clean up expired token
        return redirect(url_for('forgot'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        email = reset_tokens[token]['email']
        users_db[email]['password'] = generate_password_hash(password)
        del reset_tokens[token]
        flash('Password reset successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('reset.html', token=token)
# ============== 登出 ==============
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))
# ============== Dashboard ==============
@app.route('/dashboard')
@login_required
def dashboard():
    email = session['user_id']
    expenses = expenses_db.get(email, [])
    budgets = budgets_db.get(email, {})
    loans = loans_db.get(email, [])
    
    # 计算统计数据
    total_expenses = sum(e['amount'] for e in expenses)
    total_income = sum(e['amount'] for e in expenses if e['type'] == 'income')
    total_spent = sum(e['amount'] for e in expenses if e['type'] == 'expense')
    total_loan = sum(l['remaining'] for l in loans)
    
    # 本月数据
    current_month = datetime.now().strftime('%Y-%m')
    monthly_expenses = [e for e in expenses if e['date'].startswith(current_month)]
    monthly_total = sum(e['amount'] for e in monthly_expenses if e['type'] == 'expense')
    
    # 分类统计
    category_data = {}
    for e in expenses:
        if e['type'] == 'expense':
            cat = e['category']
            category_data[cat] = category_data.get(cat, 0) + e['amount']
    
    return render_template('dashboard.html',
        total_expenses=total_expenses,
        total_income=total_income,
        total_spent=total_spent,
        total_loan=total_loan,
        monthly_total=monthly_total,
        budgets=budgets,
        expenses=expenses[-10:],  # 最近10条
        loans=loans,
        category_data=category_data
    )
# ============== 支出管理 ==============
@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    email = session['user_id']
    
    if request.method == 'POST':
        expense = {
            'id': len(expenses_db.get(email, [])) + 1,
            'date': request.form.get('date'),
            'category': request.form.get('category'),
            'amount': float(request.form.get('amount')),
            'type': request.form.get('type', 'expense'),
            'note': request.form.get('note', ''),
            'created_at': datetime.now().isoformat()
        }
        
        if email not in expenses_db:
            expenses_db[email] = []
        expenses_db[email].append(expense)
        
        # 更新预算已用金额
        if expense['type'] == 'expense':
            cat = expense['category']
            if cat in budgets_db.get(email, {}):
                budgets_db[email][cat]['spent'] += expense['amount']
        
        flash('Transaction added successfully!', 'success')
        return redirect(url_for('expenses'))
    
    user_expenses = expenses_db.get(email, [])
    return render_template('expenses.html', expenses=user_expenses)
# ============== 删除支出 ==============
@app.route('/expenses/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    email = session['user_id']
    expenses = expenses_db.get(email, [])
    
    for i, e in enumerate(expenses):
        if e['id'] == expense_id:
            # 恢复预算
            if e['type'] == 'expense' and e['category'] in budgets_db.get(email, {}):
                budgets_db[email][e['category']]['spent'] -= e['amount']
            expenses.pop(i)
            flash('Transaction deleted!', 'success')
            break
    
    return redirect(url_for('expenses'))
# ============== 预算管理 ==============
@app.route('/budget', methods=['GET', 'POST'])
@login_required
def budget():
    email = session['user_id']
    
    if request.method == 'POST':
        category = request.form.get('category')
        limit = float(request.form.get('limit'))
        
        if email not in budgets_db:
            budgets_db[email] = {}
        
        if category in budgets_db[email]:
            budgets_db[email][category]['limit'] = limit
        else:
            budgets_db[email][category] = {'limit': limit, 'spent': 0}
        
        flash(f'Budget for {category} updated!', 'success')
        return redirect(url_for('budget'))
    
    budgets = budgets_db.get(email, {})
    return render_template('budget.html', budgets=budgets)
# ============== 贷款管理 ==============
@app.route('/loans', methods=['GET', 'POST'])
@login_required
def loans():
    email = session['user_id']
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            loan = {
                'id': len(loans_db.get(email, [])) + 1,
                'name': request.form.get('name'),
                'total': float(request.form.get('total')),
                'remaining': float(request.form.get('total')),
                'interest_rate': float(request.form.get('interest_rate', 0)),
                'monthly_payment': float(request.form.get('monthly_payment')),
                'start_date': request.form.get('start_date'),
                'due_date': request.form.get('due_date', ''),
                'created_at': datetime.now().isoformat()
            }
            
            # 计算预计还清日期
            if loan['monthly_payment'] > 0:
                months = loan['remaining'] / loan['monthly_payment']
                loan['estimated_payoff'] = (datetime.now() + timedelta(days=months*30)).strftime('%Y-%m-%d')
            else:
                loan['estimated_payoff'] = 'N/A'
            
            if email not in loans_db:
                loans_db[email] = []
            loans_db[email].append(loan)
            flash('Loan added successfully!', 'success')
        
        elif action == 'payment':
            loan_id = int(request.form.get('loan_id'))
            amount = float(request.form.get('payment_amount'))
            
            for loan in loans_db.get(email, []):
                if loan['id'] == loan_id:
                    loan['remaining'] = max(0, loan['remaining'] - amount)
                    if loan['remaining'] == 0:
                        flash(f'Congratulations! {loan["name"]} is paid off!', 'success')
                    else:
                        flash(f'Payment of ${amount} recorded!', 'success')
                    break
        
        return redirect(url_for('loans'))
    
    user_loans = loans_db.get(email, [])
    return render_template('loans.html', loans=user_loans)
# ============== 财务建议 ==============
@app.route('/insights')
@login_required
def insights():
    email = session['user_id']
    expenses = expenses_db.get(email, [])
    budgets = budgets_db.get(email, {})
    loans = loans_db.get(email, [])
    
    suggestions = []
    
    # 分析消费模式
    total_spent = sum(e['amount'] for e in expenses if e['type'] == 'expense')
    total_income = sum(e['amount'] for e in expenses if e['type'] == 'income')
    
    # 检查预算超支
    for cat, data in budgets.items():
        if data['spent'] > data['limit']:
            percentage = ((data['spent'] - data['limit']) / data['limit']) * 100
            suggestions.append({
                'type': 'warning',
                'icon': '⚠️',
                'title': f'{cat} Budget Exceeded',
                'message': f'You\'ve exceeded your {cat} budget by {percentage:.1f}%. Consider reducing spending in this category.',
                'action': f'Reduce {cat.lower()} expenses by ${data["spent"] - data["limit"]:.2f}'
            })
        elif data['spent'] > data['limit'] * 0.8:
            suggestions.append({
                'type': 'caution',
                'icon': '📊',
                'title': f'{cat} Budget Alert',
                'message': f'You\'ve used {(data["spent"]/data["limit"]*100):.1f}% of your {cat} budget.',
                'action': 'Monitor spending closely'
            })
    
    # 储蓄建议
    if total_income > 0:
        savings_rate = ((total_income - total_spent) / total_income) * 100
        if savings_rate < 20:
            suggestions.append({
                'type': 'tip',
                'icon': '💡',
                'title': 'Improve Savings Rate',
                'message': f'Your current savings rate is {savings_rate:.1f}%. Financial experts recommend saving at least 20% of income.',
                'action': 'Try the 50/30/20 rule: 50% needs, 30% wants, 20% savings'
            })
        else:
            suggestions.append({
                'type': 'success',
                'icon': '🎉',
                'title': 'Great Savings Habit!',
                'message': f'Your savings rate of {savings_rate:.1f}% exceeds the recommended 20%!',
                'action': 'Consider investing surplus savings'
            })
    
    # 贷款建议
    if loans:
        total_loan = sum(l['remaining'] for l in loans)
        high_interest = [l for l in loans if l['interest_rate'] > 10]
        if high_interest:
            suggestions.append({
                'type': 'warning',
                'icon': '🏦',
                'title': 'High Interest Loans Detected',
                'message': f'You have {len(high_interest)} loan(s) with interest rates above 10%. Prioritize paying these off.',
                'action': 'Use the avalanche method: pay off highest interest first'
            })
    
    # 消费模式分析
    category_totals = {}
    for e in expenses:
        if e['type'] == 'expense':
            cat = e['category']
            category_totals[cat] = category_totals.get(cat, 0) + e['amount']
    
    if category_totals:
        top_category = max(category_totals, key=category_totals.get)
        suggestions.append({
            'type': 'info',
            'icon': '📈',
            'title': 'Spending Pattern Analysis',
            'message': f'Your highest spending category is {top_category} (${category_totals[top_category]:.2f}). Review if this aligns with your priorities.',
            'action': 'Set spending alerts for this category'
        })
    
    # 通用建议
    if not suggestions:
        suggestions.append({
            'type': 'tip',
            'icon': '🚀',
            'title': 'Start Tracking',
            'message': 'Add more transactions to get personalized financial insights!',
            'action': 'Record all expenses for better analysis'
        })
    
    return render_template('insights.html', suggestions=suggestions, 
                         total_spent=total_spent, total_income=total_income,
                         category_data=category_totals)
# ============== 报告生成 ==============
@app.route('/report')
@login_required
def report():
    email = session['user_id']
    expenses = expenses_db.get(email, [])
    budgets = budgets_db.get(email, {})
    loans = loans_db.get(email, [])
    
    # 按月份分组
    monthly_data = {}
    for e in expenses:
        month = e['date'][:7]  # YYYY-MM
        if month not in monthly_data:
            monthly_data[month] = {'income': 0, 'expense': 0, 'transactions': []}
        
        if e['type'] == 'income':
            monthly_data[month]['income'] += e['amount']
        else:
            monthly_data[month]['expense'] += e['amount']
        monthly_data[month]['transactions'].append(e)
    
    # 排序
    sorted_months = sorted(monthly_data.keys(), reverse=True)
    
    return render_template('report.html', 
                         monthly_data=monthly_data, 
                         sorted_months=sorted_months,
                         budgets=budgets,
                         loans=loans)
# ============== 下载报告 ==============
@app.route('/report/download/<month>')
@login_required
def download_report(month):
    email = session['user_id']
    expenses = expenses_db.get(email, [])
    budgets = budgets_db.get(email, {})
    
    # 筛选当月数据
    month_expenses = [e for e in expenses if e['date'].startswith(month)]
    
    # 生成报告内容
    report_lines = [
        "=" * 50,
        f"    BUDGET MASTER - MONTHLY FINANCIAL REPORT",
        f"    Month: {month}",
        f"    Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"    User: {session['username']}",
        "=" * 50,
        "",
        "📊 SUMMARY",
        "-" * 30,
    ]
    
    total_income = sum(e['amount'] for e in month_expenses if e['type'] == 'income')
    total_expense = sum(e['amount'] for e in month_expenses if e['type'] == 'expense')
    
    report_lines.extend([
        f"Total Income:    ${total_income:,.2f}",
        f"Total Expenses:  ${total_expense:,.2f}",
        f"Net Balance:     ${total_income - total_expense:,.2f}",
        "",
        "📝 TRANSACTIONS",
        "-" * 30,
    ])
    
    for e in sorted(month_expenses, key=lambda x: x['date']):
        sign = '+' if e['type'] == 'income' else '-'
        report_lines.append(f"{e['date']} | {e['category']:15} | {sign}${e['amount']:>10.2f} | {e.get('note', '')}")
    
    report_lines.extend([
        "",
        "💰 BUDGET STATUS",
        "-" * 30,
    ])
    
    for cat, data in budgets.items():
        status = "✅" if data['spent'] <= data['limit'] else "⚠️"
        report_lines.append(f"{status} {cat}: ${data['spent']:.2f} / ${data['limit']:.2f}")
    
    report_lines.extend([
        "",
        "=" * 50,
        "Generated by Budget Master | Your Financial Partner",
        "=" * 50,
    ])
    
    report_content = "\n".join(report_lines)
    
    response = make_response(report_content)
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=budget_report_{month}.txt'
    
    return response
# ============== API: 图表数据 ==============
@app.route('/api/chart-data')
@login_required
def chart_data():
    email = session['user_id']
    expenses = expenses_db.get(email, [])
    
    # 分类数据
    category_totals = {}
    for e in expenses:
        if e['type'] == 'expense':
            cat = e['category']
            category_totals[cat] = category_totals.get(cat, 0) + e['amount']
    
    # 月度趋势
    monthly_totals = {}
    for e in expenses:
        month = e['date'][:7]
        if month not in monthly_totals:
            monthly_totals[month] = {'income': 0, 'expense': 0}
        if e['type'] == 'income':
            monthly_totals[month]['income'] += e['amount']
        else:
            monthly_totals[month]['expense'] += e['amount']
    
    return jsonify({
        'categories': category_totals,
        'monthly': monthly_totals
    })
# ============== 运行应用 ==============
if __name__ == '__main__':
  app.run(debug=True, port=5000)
