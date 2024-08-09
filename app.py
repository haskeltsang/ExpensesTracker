from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import mysql.connector
from mysql.connector import errorcode
import bcrypt
import csv
import io
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_default_secret_key')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
app.config['SESSION_PROTECTION'] = 'strong'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = "strong"

def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.environ.get('MYSQL_HOST', 'localhost'),
            user=os.environ.get('MYSQL_USER', 'mysql_user'),
            password=os.environ.get('MYSQL_PASSWORD', 'mysql_user'),
            database=os.environ.get('MYSQL_DATABASE', 'expense')
        )
        return conn
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Something is wrong with your user name or password")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Database does not exist")
        else:
            print(err)
        return None

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

@app.before_request
def check_session_timeout():
    if current_user.is_authenticated:
        if 'last_activity' in session:
            last_activity = datetime.strptime(session['last_activity'], '%Y-%m-%d %H:%M:%S')
            if datetime.now() - last_activity > timedelta(minutes=15):
                logout_user()
                flash('Session timed out due to inactivity.', 'danger')
                return redirect(url_for('login'))
        session['last_activity'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    with app.open_resource('schema.sql') as f:
        cursor.execute(f.read().decode('utf8'), multi=True)
    conn.commit()
    cursor.close()
    conn.close()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        conn = get_db_connection()
        c = conn.cursor()
        c.execute('INSERT INTO users (username, password) VALUES (%s, %s)', (username, hashed_password))
        conn.commit()
        conn.close()

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT id, password FROM users WHERE username = %s', (username,))
        user = c.fetchone()

        if user and bcrypt.checkpw(password.encode('utf-8'), user[1].encode('utf-8')):
            user_id = user[0]
            user = User(user_id)
            login_user(user)
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'danger')

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    conn = get_db_connection()
    c = conn.cursor()

    today = datetime.today()
    week_start = today - timedelta(days=(today.weekday() - 0) % 7)
    week_end = week_start + timedelta(days=6)

    c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    expenses = c.fetchall()

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    weekly_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s',
              (current_user.id, 'TB(AS)'))
    tb_as_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s',
              (current_user.id, 'TB'))
    tb_total = c.fetchone()[0] or 0.0

    conn.close()
    return render_template('index.html', expenses=expenses, weekly_total=weekly_total, tb_as_total=tb_as_total, tb_total=tb_total)

@app.route('/add', methods=['POST'])
@login_required
def add_expense():
    description = request.form['description']
    amount = request.form['amount']

    today = datetime.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO expenses (user_id, date, description, amount) VALUES (%s, %s, %s, %s)',
              (current_user.id, today, description, amount))
    conn.commit()
    conn.close()
    flash('Expense added successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/delete/<int:id>')
@login_required
def delete_expense(id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM expenses WHERE id = %s AND user_id = %s', (id, current_user.id))
    conn.commit()
    conn.close()
    flash('Expense deleted successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/export', methods=['GET'])
@login_required
def export():
    conn = get_db_connection()
    c = conn.cursor()

    today = datetime.today()
    week_start = today - timedelta(days=(today.weekday() - 0) % 7)
    week_end = week_start + timedelta(days=6)

    c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    expenses = c.fetchall()

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    weekly_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND description = %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'), 'TB(AS)'))
    tb_as_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND description = %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'), 'TB'))
    tb_total = c.fetchone()[0] or 0.0

    conn.close()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['Date', 'Description', 'Amount'])
    writer.writeheader()
    for expense in expenses:
        writer.writerow({
            'Date': expense[2].strftime('%Y-%m-%d'),
            'Description': expense[3],
            'Amount': f"${expense[4]:.2f}"
        })
    
    writer.writerow({})
    writer.writerow({'Date': 'Weekly Total', 'Description': '', 'Amount': f"${weekly_total:.2f}"})
    writer.writerow({'Date': 'Total TB(AS)', 'Description': '', 'Amount': f"${tb_as_total:.2f}"})
    writer.writerow({'Date': 'Total TB', 'Description': '', 'Amount': f"${tb_total:.2f}"})

    output.seek(0)

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='expenses_summary.csv'
    )

@app.route('/history', methods=['GET', 'POST'])
@login_required
def history():
    conn = get_db_connection()
    c = conn.cursor()
    
    if request.method == 'POST':
        start_date_str = request.form['start_date']
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = start_date + timedelta(days=6)

        c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
                  (current_user.id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        expenses = c.fetchall()

        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
                  (current_user.id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        weekly_total = c.fetchone()[0] or 0.0

        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s',
                  (current_user.id, 'TB(AS)', start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        tb_as_total = c.fetchone()[0] or 0.0

        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s',
                  (current_user.id, 'TB', start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        tb_total = c.fetchone()[0] or 0.0

    else:
        today = datetime.today()
        week_start = today - timedelta(days=(today.weekday() - 0) % 7)
        week_end = week_start + timedelta(days=6)
        c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
                  (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        expenses = c.fetchall()

        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s',
                  (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        weekly_total = c.fetchone()[0] or 0.0

        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s',
                  (current_user.id, 'TB(AS)', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        tb_as_total = c.fetchone()[0] or 0.0

        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s',
                  (current_user.id, 'TB', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        tb_total = c.fetchone()[0] or 0.0

    conn.close()
    return render_template('history.html', expenses=expenses, weekly_total=weekly_total, tb_as_total=tb_as_total, tb_total=tb_total)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
