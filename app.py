import os
import logging
from flask import Flask, render_template, request, redirect, jsonify, send_file, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import pandas as pd
from ocr_processor import extract_receipt_data
from export_utils import create_excel_export
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import time

# Configure logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure the database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///receipts.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Initialize the app with the extension
db.init_app(app)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('exports', exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Authentication helper functions
def require_auth():
    """Check if user is authenticated"""
    return session.get('user_email') is not None

def get_current_user():
    """Get current user from session"""
    if not require_auth():
        return None
    email = session.get('user_email')
    return User.query.filter_by(email=email).first()

def create_session(email):
    """Create user session"""
    session['user_email'] = email
    session['login_time'] = datetime.now().isoformat()
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=30)

with app.app_context():
    # Import models here
    from models import User, Receipt
    db.create_all()

@app.route('/')
def home():
    if require_auth():
        user = get_current_user()
        if user:
            return redirect(url_for('dashboard', user_id=user.id))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    """Simple email-based authentication"""
    if request.method == 'POST':
        try:
            email = request.form.get('email', '').strip().lower()
            if not email or '@' not in email:
                flash('Please enter a valid email address', 'error')
                return render_template('login.html')
            
            # Get or create user
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email)
                db.session.add(user)
                db.session.commit()
                app.logger.info(f"New user registered: {email}")
            
            create_session(email)
            app.logger.info(f"User logged in: {email}")
            return redirect(url_for('dashboard', user_id=user.id))
            
        except Exception as e:
            app.logger.error(f"Login error: {str(e)}")
            flash('Login failed. Please try again.', 'error')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout user"""
    email = session.get('user_email')
    session.clear()
    if email:
        app.logger.info(f"User logged out: {email}")
    return redirect(url_for('home'))

@app.route('/upload', methods=['POST'])
@limiter.limit("20 per hour")
def upload_receipt():
    # Check authentication
    if not require_auth():
        return jsonify({'error': 'Authentication required. Please log in.'}), 401
    
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found. Please log in again.'}), 401
    
    # Check limits
    if user.plan == 'free' and user.receipt_count >= 10:
        app.logger.warning(f"User {user.email} hit free plan limit")
        return jsonify({'error': 'Free limit reached. Upgrade to Pro for unlimited receipts.'}), 429
    
    if 'receipt' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['receipt']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Extract receipt data using OCR + AI
        try:
            start_time = time.time()
            receipt_data = extract_receipt_data(filepath)
            processing_time = time.time() - start_time
            
            app.logger.info(f"Receipt processed in {processing_time:.2f}s for user {user.email}")
            
            if receipt_data:
                # Save to database with transaction
                try:
                    receipt = Receipt(
                        user_id=user.id,
                        filename=filename,
                        vendor=receipt_data.get('vendor', 'Unknown'),
                        amount=receipt_data.get('amount', 0.0),
                        currency=receipt_data.get('currency', 'USD'),
                        date=receipt_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                        category=receipt_data.get('category', 'Other'),
                        tax_amount=receipt_data.get('tax', 0.0)
                    )
                    db.session.add(receipt)
                    
                    # Update user receipt count
                    user.receipt_count += 1
                    db.session.commit()
                    
                    app.logger.info(f"Receipt saved successfully for user {user.email}")
                    
                    return jsonify({
                        'success': True,
                        'data': receipt_data,
                        'redirect': url_for('dashboard', user_id=user.id)
                    })
                    
                except Exception as db_error:
                    db.session.rollback()
                    app.logger.error(f"Database error for user {user.email}: {str(db_error)}")
                    return jsonify({'error': 'Failed to save receipt data. Please try again.'}), 500
                    
            else:
                app.logger.warning(f"Failed to extract data from receipt for user {user.email}")
                return jsonify({'error': 'Could not process receipt. Please try a clearer image.'}), 422
                
        except Exception as e:
            app.logger.error(f"Error processing receipt for user {user.email}: {str(e)}")
            # Clean up uploaded file on error
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': 'Error processing receipt. Please try again.'}), 500
    
    return jsonify({'error': 'Invalid file format. Please upload PNG or JPG.'}), 400

@app.route('/dashboard/<int:user_id>')
def dashboard(user_id):
    # Check authentication
    if not require_auth():
        flash('Please log in to access your dashboard', 'error')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    if not current_user or current_user.id != user_id:
        app.logger.warning(f"Unauthorized dashboard access attempt: user {current_user.email if current_user else 'None'} tried to access user {user_id}")
        flash('Access denied. You can only view your own dashboard.', 'error')
        return redirect(url_for('home'))
    
    try:
        # Get recent receipts with error handling
        receipts = Receipt.query.filter_by(user_id=user_id).order_by(Receipt.created_at.desc()).limit(20).all()
        
        # Calculate totals safely
        total_amount = db.session.query(db.func.sum(Receipt.amount)).filter_by(user_id=user_id).scalar() or 0
        total_tax = db.session.query(db.func.sum(Receipt.tax_amount)).filter_by(user_id=user_id).scalar() or 0
        
        app.logger.info(f"Dashboard accessed by user {current_user.email}")
        
        return render_template('dashboard.html', 
                             user=current_user, 
                             receipts=receipts, 
                             total_amount=total_amount,
                             total_tax=total_tax,
                             user_id=user_id)
                             
    except Exception as e:
        app.logger.error(f"Dashboard error for user {current_user.email}: {str(e)}")
        flash('Error loading dashboard. Please try again.', 'error')
        return redirect(url_for('home'))

@app.route('/export/<int:user_id>')
@limiter.limit("5 per minute")
def export_data(user_id):
    # Check authentication
    if not require_auth():
        flash('Please log in to export data', 'error')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    if not current_user or current_user.id != user_id:
        app.logger.warning(f"Unauthorized export attempt: user {current_user.email if current_user else 'None'} tried to export user {user_id} data")
        flash('Access denied. You can only export your own data.', 'error')
        return redirect(url_for('home'))
    
    try:
        receipts = Receipt.query.filter_by(user_id=user_id).order_by(Receipt.date.desc()).all()
        
        if receipts:
            filename = create_excel_export(receipts, user_id)
            app.logger.info(f"Export created for user {current_user.email}: {len(receipts)} receipts")
            return send_file(filename, as_attachment=True, 
                           download_name=f'expenses_{datetime.now().strftime("%Y%m")}.xlsx')
        else:
            flash('No receipts to export', 'info')
            return redirect(url_for('dashboard', user_id=user_id))
            
    except Exception as e:
        app.logger.error(f"Export error for user {current_user.email}: {str(e)}")
        flash('Error creating export file. Please try again.', 'error')
        return redirect(url_for('dashboard', user_id=user_id))

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.errorhandler(413)
def too_large(e):
    app.logger.warning(f"File too large uploaded from {request.remote_addr}")
    return render_template('error.html', 
                         message='File too large. Please upload files smaller than 16MB.'), 413

@app.errorhandler(404)
def not_found(e):
    app.logger.info(f"404 error: {request.url} from {request.remote_addr}")
    return render_template('error.html', 
                         message='Page not found.'), 404

@app.errorhandler(500)
def server_error(e):
    app.logger.error(f"500 error: {str(e)} from {request.remote_addr}")
    return render_template('error.html', 
                         message='Internal server error. Please try again.'), 500

@app.errorhandler(429)
def rate_limit_exceeded(e):
    app.logger.warning(f"Rate limit exceeded from {request.remote_addr}")
    return render_template('error.html', 
                         message='Too many requests. Please wait a moment and try again.'), 429

@app.errorhandler(401)
def unauthorized(e):
    return render_template('error.html', 
                         message='Authentication required. Please log in.'), 401

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', 
                         message='Access denied.'), 403

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
