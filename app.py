from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from fpdf import FPDF
import mysql.connector
from mysql.connector import errorcode
import bcrypt
import csv
import io
import os
from datetime import datetime, timedelta
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from apscheduler.triggers.date import DateTrigger

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
app.config['SESSION_PROTECTION'] = 'strong'
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = "strong"

def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST'),
            user=os.getenv('MYSQL_USER'),
            password=os.getenv('MYSQL_PASSWORD'),
            database=os.getenv('MYSQL_DATABASE')
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

@app.route('/')
@login_required
def index():
    conn = get_db_connection()
    c = conn.cursor()

    today = datetime.today()
    week_start = today - timedelta(days=(today.weekday() - 0) % 7)
    week_end = week_start + timedelta(days=6)

    # Fetch expenses for the current week excluding deleted ones
    c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    expenses = c.fetchall()

    # Calculate weekly total excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    weekly_total = c.fetchone()[0] or 0.0

    # Calculate all TB for the current week excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    all_tb_total = c.fetchone()[0] or 0.0

    # Calculate TB(AS) total for the current week excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB(AS)', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    tb_as_total = c.fetchone()[0] or 0.0

    # Calculate TB total for the current week excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    tb_total = c.fetchone()[0] or 0.0

    # Calculate total for items not starting with "TB"
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description NOT LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    non_tb_total = c.fetchone()[0] or 0.0

    # Generate signed URLs for amend and delete actions
    expenses_with_links = []
    for expense in expenses:
        amend_url = url_for('amend_expense', token=serializer.dumps(expense[0]))
        delete_url = url_for('delete_expense', token=serializer.dumps(expense[0]))
        expenses_with_links.append({
            'id': expense[0],
            'date': expense[2],
            'description': expense[3],
            'payment': expense[4],
            'amount': expense[5],
            'amend_url': amend_url,
            'delete_url': delete_url
        })

    conn.close()
    return render_template('index.html', 
                           expenses=expenses_with_links, 
                           weekly_total=weekly_total, 
                           all_tb_total=all_tb_total, 
                           tb_as_total=tb_as_total, 
                           tb_total=tb_total, 
                           non_tb_total=non_tb_total)

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





@app.route('/add', methods=['POST'])
@login_required
def add_expense():
    description = request.form['description']
    payment = request.form['payment']
    amount = request.form['amount']
    updated_at = datetime.now()

    today = datetime.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO expenses (user_id, date, description, payment, amount, updated_at) VALUES (%s, %s, %s, %s, %s, %s)',
              (current_user.id, today, description, payment, amount, updated_at))
    conn.commit()
    conn.close()
    flash('Expense added successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/delete/<token>', methods=['POST'])
@login_required
def delete_expense(token):
    try:
        id = serializer.loads(token)
    except:
        flash('Invalid link', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    c = conn.cursor()
    deleted_at = datetime.now()

    # Mark the expense as deleted by setting the deleted_at timestamp
    c.execute('UPDATE expenses SET deleted_at = %s WHERE id = %s AND user_id = %s', (deleted_at, id, current_user.id))
    conn.commit()
    conn.close()

    flash('Expense deleted successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/export_csv', methods=['GET'])
@login_required
def export_to_csv():
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

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    all_tb_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND description = %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'), 'TB(AS)'))
    tb_as_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND description = %s',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'), 'TB'))
    tb_total = c.fetchone()[0] or 0.0

    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description NOT LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    non_tb_total = c.fetchone()[0] or 0.0

    conn.close()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['Date', 'Description', 'Payment Method', 'Amount'])
    writer.writeheader()
    for expense in expenses:
        writer.writerow({
            'Date': expense[2].strftime('%Y-%m-%d'),
            'Description': expense[3],
            'Payment Method': expense[4],
            'Amount': f"HK${expense[5]:.2f}"
        })
    
    writer.writerow({})
    writer.writerow({'Date': 'Weekly Total', 'Description': '', 'Amount': f"HK${weekly_total:.2f}"})
    writer.writerow({'Date': 'Others Total', 'Description': '', 'Amount': f"HK${non_tb_total:.2f}"})
    writer.writerow({'Date': 'All TB', 'Description': '', 'Amount': f"HK${all_tb_total:.2f}"})
    writer.writerow({'Date': 'Total TB(AS)', 'Description': '', 'Amount': f"HK${tb_as_total:.2f}"})
    writer.writerow({'Date': 'Total TB', 'Description': '', 'Amount': f"HK${tb_total:.2f}"})

    output.seek(0)

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'expenses_summary_from_{week_start.strftime("%Y-%m-%d")}_to_{today.strftime("%Y-%m-%d")}.csv'
    )
class PDF(FPDF):
    def footer(self):
        # Set position of the footer at 1.5 cm from the bottom & set UTF-8 ttf
        self.add_font("kai", "I", "kaiu.ttf", uni=True)
        self.set_y(-15)
        # Set font
        self.set_font('kai', 'I', 8)

        self.cell(0, 10, f'Report generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', 0, 0)
        # Page number
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

@app.route('/export_pdf', methods=['GET'])
@login_required
def export_to_pdf():
    conn = get_db_connection()
    c = conn.cursor()

    today = datetime.today()
    week_start = today - timedelta(days=(today.weekday() - 0) % 7)
    week_end = week_start + timedelta(days=6)

    # Fetch expenses for the current week excluding deleted ones
    c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    expenses = c.fetchall()

    # Calculate weekly total excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    weekly_total = c.fetchone()[0] or 0.0

    # Calculate all TB total for the current week excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    all_tb_total = c.fetchone()[0] or 0.0

    # Calculate TB(AS) total for the current week excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB(AS)', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    tb_as_total = c.fetchone()[0] or 0.0

    # Calculate TB total for the current week excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    tb_total = c.fetchone()[0] or 0.0

    # Calculate total for items not starting with "TB"
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description NOT LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
    non_tb_total = c.fetchone()[0] or 0.0

    conn.close()

    # Create a PDF
    pdf = PDF()
    pdf.add_page()
    pdf.add_font("kai", "", "kaiu.ttf",)
    pdf.add_font("kai", "B", "kaiu.ttf", uni=True)
    pdf.add_font("kai", "I", "kaiu.ttf", uni=True)
    pdf.set_font("kai", size=12)

    # Add a title
    pdf.set_font("kai", "B", 16)
    pdf.cell(0, 10, f"Expenses Summary from {week_start.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}", 0, 1, 'C')

    # Add header row
    pdf.set_font("kai", "B", 12)
    pdf.cell(40, 10, "Date", 1, 0, 'C')
    pdf.cell(80, 10, "Description", 1, 0, 'C')
    pdf.cell(40, 10, "Payment Method", 1, 0, 'C')
    pdf.cell(40, 10, "Amount", 1, 1, 'C')

    # Add data rows
    pdf.set_font("kai", size=12)
    for expense in expenses:
        pdf.cell(40, 10, expense[2].strftime('%Y-%m-%d'), 1)
        pdf.cell(80, 10, expense[3], 1)
        pdf.cell(40, 10, expense[4], 1)
        pdf.cell(40, 10, f"HK${expense[5]:.2f}", 1, 1)

    # Add totals
    pdf.set_font("kai", "B", 12)
    #pdf.cell(40, 10, '', 1)
    #pdf.cell(80, 10, '', 1)
    #pdf.cell(40, 10, '', 1, 1)
    pdf.cell(200, 10, 'Summary', 1, 1, 'C')

    pdf.cell(40, 10, 'Weekly Total', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${weekly_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'Others Total', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${non_tb_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'All TB', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${all_tb_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'Total TB(AS)', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${tb_as_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'Total TB', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${tb_total:.2f}", 1, 1)

    # Output the PDF to a bytes buffer
    pdf_output = io.BytesIO()
    pdf_output.write(pdf.output(dest='S'))  # No need to encode since it's already a bytearray
    pdf_output.seek(0)

    # Send the PDF as a file download
    return send_file(
        pdf_output,
        as_attachment=True,
        download_name=f'expenses_summary_from_{week_start.strftime("%Y-%m-%d")}_to_{today.strftime("%Y-%m-%d")}.pdf',
        mimetype='application/pdf'
    )


    
@app.route('/history', methods=['GET', 'POST'])
@login_required
def history():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Initialize variables for week_start and week_end
    week_start = week_end = None

    if request.method == 'POST':
        start_date_str = request.form['start_date']
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = start_date + timedelta(days=6)
        week_start = start_date
        week_end = end_date

        # Fetch expenses for the selected week excluding deleted ones
        c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        expenses = c.fetchall()

        # Calculate totals for the selected week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        weekly_total = c.fetchone()[0] or 0.0

        # Calculate all TB total for the selected week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        all_tb_total = c.fetchone()[0] or 0.0

        # Calculate TB(AS) total for the selected week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB(AS)', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        tb_as_total = c.fetchone()[0] or 0.0

        # Calculate TB total for the selected week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        tb_total = c.fetchone()[0] or 0.0

        # Calculate total for items not starting with "TB"
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description NOT LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        non_tb_total = c.fetchone()[0] or 0.0

    else:
        today = datetime.today()
        week_start = today - timedelta(days=(today.weekday() - 0) % 7)
        week_end = week_start + timedelta(days=6)

        # Fetch expenses for the current week excluding deleted ones
        c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        expenses = c.fetchall()

        # Calculate totals for the current week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        weekly_total = c.fetchone()[0] or 0.0

        # Calculate all TB total for the current week excluding deleted ones
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        all_tb_total = c.fetchone()[0] or 0.0

        # Calculate TB(AS) total for the current week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB(AS)', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        tb_as_total = c.fetchone()[0] or 0.0

        # Calculate TB total for the current week excluding deleted expenses
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        tb_total = c.fetchone()[0] or 0.0

        # Calculate total for items not starting with "TB"
        c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description NOT LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
                  (current_user.id, 'TB%', week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')))
        non_tb_total = c.fetchone()[0] or 0.0

    conn.close()
    return render_template('history.html', expenses=expenses, weekly_total=weekly_total, all_tb_total=all_tb_total, tb_as_total=tb_as_total, tb_total=tb_total, non_tb_total=non_tb_total)


@app.route('/amend/<token>', methods=['GET', 'POST'])
@login_required
def amend_expense(token):
    try:
        id = serializer.loads(token)
    except:
        flash('Invalid link', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    c = conn.cursor()

    # Fetch the specific expense
    c.execute('SELECT * FROM expenses WHERE id = %s AND user_id = %s', (id, current_user.id))
    expense = c.fetchone()

    if request.method == 'POST':
        description = request.form['description']
        amount = request.form['amount']
        payment = request.form['payment']
        updated_at = datetime.now()

        # Update the expense with new values and update the updated_at timestamp
        c.execute('UPDATE expenses SET description = %s, payment = %s, amount = %s, updated_at = %s WHERE id = %s AND user_id = %s',
                  (description, payment, amount, updated_at, id, current_user.id))
        conn.commit()
        conn.close()

        flash('Expense amended successfully!', 'success')
        return redirect(url_for('index'))

    conn.close()
    return render_template('amend.html', expense=expense)

def send_email_with_attachment(subject, body, to_email, attachment_filename, attachment_data):
    from_email = os.getenv('EMAIL_ADDRESS')
    password = os.getenv('EMAIL_PASSWORD')

    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    part = MIMEBase('application', 'octet-stream')
    part.set_payload(attachment_data)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename={attachment_filename}')
    msg.attach(part)

    server = smtplib.SMTP(os.getenv('SMTP_ADDRESS'), os.getenv('SMTP_PORT'))
    server.starttls()
    server.login(from_email, password)
    text = msg.as_string()
    server.sendmail(from_email, to_email, text)
    server.quit()

def export_monthly_report():
    conn = get_db_connection()
    c = conn.cursor()

    today = datetime.today()
    month_start = today.replace(day=1)
    next_month = today.replace(day=28) + timedelta(days=4)
    month_end = next_month - timedelta(days=next_month.day)

    # If it's user-specific, replace `user_id` with a fixed ID or loop over multiple users.
    user_id = 1  # Replace with a specific user ID or loop through users
    c.execute('SELECT * FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (user_id, month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
    expenses = c.fetchall()
     # Calculate monthly total excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (user_id, month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
    monthly_total = c.fetchone()[0] or 0.0

    # Calculate all TB total for the current month excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (user_id, 'TB%', month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
    all_tb_total = c.fetchone()[0] or 0.0

    # Calculate TB(AS) total for the current month excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (user_id, 'TB(AS)', month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
    tb_as_total = c.fetchone()[0] or 0.0

    # Calculate TB total for the current month excluding deleted expenses
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description = %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (user_id, 'TB', month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
    tb_total = c.fetchone()[0] or 0.0

    # Calculate total for items not starting with "TB"
    c.execute('SELECT SUM(amount) FROM expenses WHERE user_id = %s AND description NOT LIKE %s AND date BETWEEN %s AND %s AND deleted_at IS NULL',
              (user_id, 'TB%', month_start.strftime('%Y-%m-%d'), month_end.strftime('%Y-%m-%d')))
    non_tb_total = c.fetchone()[0] or 0.0
    conn.close()

    # Create a PDF as before
    pdf = PDF()
    pdf.add_page()
    pdf.add_font("kai", "", "kaiu.ttf",)
    pdf.add_font("kai", "B", "kaiu.ttf", uni=True)
    pdf.add_font("kai", "I", "kaiu.ttf", uni=True)
    pdf.set_font("kai", size=12)

    pdf.set_font("kai", "B", 16)
    pdf.cell(0, 10, f"Monthly Expenses Summary for {today.strftime('%B %Y')}", 0, 1, 'C')

    pdf.set_font("kai", "B", 12)
    pdf.cell(40, 10, "Date", 1, 0, 'C')
    pdf.cell(80, 10, "Description", 1, 0, 'C')
    pdf.cell(40, 10, "Payment Method", 1, 0, 'C')
    pdf.cell(40, 10, "Amount", 1, 1, 'C')

    pdf.set_font("kai", size=12)
    for expense in expenses:
        pdf.cell(40, 10, expense[2].strftime('%Y-%m-%d'), 1)
        pdf.cell(80, 10, expense[3], 1)
        pdf.cell(40, 10, expense[4], 1)
        pdf.cell(40, 10, f"HK${expense[5]:.2f}", 1, 1)
        # Add totals
    pdf.set_font("kai", "B", 12)
    #pdf.cell(40, 10, '', 1)
    #pdf.cell(80, 10, '', 1)
    #pdf.cell(40, 10, '', 1, 1)
    pdf.cell(200, 10, 'Summary', 1, 1, 'C')

    pdf.cell(40, 10, 'Monthly Total', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${monthly_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'Others Total', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${non_tb_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'All TB', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${all_tb_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'Total TB(AS)', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${tb_as_total:.2f}", 1, 1)

    pdf.cell(40, 10, 'Total TB', 1)
    pdf.cell(120, 10, '', 1)
    pdf.cell(40, 10, f"HK${tb_total:.2f}", 1, 1)
    # Output the PDF to a bytes buffer
    pdf_output = io.BytesIO()
    pdf_output.write(pdf.output(dest='S'))
    pdf_output.seek(0)

    # Send the email with the PDF attached
    send_email_with_attachment(
        subject="Monthly Expenses Report",
        body="Please find attached your monthly expenses report.",
        to_email=os.getenv('DEST_EMAIL'),  
        attachment_filename=f'monthly_report_{today.strftime("%Y_%m")}.pdf',
        attachment_data=pdf_output.read()
    )

def schedule_test_report():
    scheduler = BackgroundScheduler()
    # Set the trigger now
    trigger = DateTrigger(run_date=datetime.now())
    scheduler.add_job(export_monthly_report, trigger)
    scheduler.start()

schedule_test_report()

def schedule_monthly_report():
    scheduler = BackgroundScheduler()
    trigger = CronTrigger(day='last', hour=23, minute=59)  # Adjust the timing as needed
    scheduler.add_job(export_monthly_report, trigger)
    scheduler.start()

schedule_monthly_report()



if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
