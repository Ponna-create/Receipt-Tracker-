import os
import logging
from flask import Flask, render_template, request, redirect, jsonify, send_file, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from datetime import datetime
import pandas as pd
from ocr_processor import extract_receipt_data
from export_utils import create_excel_export

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

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('exports', exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

with app.app_context():
    # Import models here
    from models import User, Receipt
    db.create_all()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_receipt():
    email = request.form.get('email', 'demo@example.com')
    
    # Check/create user
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email)
        db.session.add(user)
        db.session.commit()
    
    # Check limits
    if user.plan == 'free' and user.receipt_count >= 10:
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
            receipt_data = extract_receipt_data(filepath)
            
            if receipt_data:
                # Save to database
                receipt = Receipt(
                    user_id=user.id,
                    filename=filename,
                    vendor=receipt_data.get('vendor', 'Unknown'),
                    amount=receipt_data.get('amount', 0.0),
                    date=receipt_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                    category=receipt_data.get('category', 'Other'),
                    tax_amount=receipt_data.get('tax', 0.0)
                )
                db.session.add(receipt)
                
                # Update user receipt count
                user.receipt_count += 1
                db.session.commit()
                
                return jsonify({
                    'success': True,
                    'data': receipt_data,
                    'redirect': url_for('dashboard', user_id=user.id)
                })
            else:
                return jsonify({'error': 'Could not process receipt. Please try a clearer image.'}), 422
        except Exception as e:
            app.logger.error(f"Error processing receipt: {str(e)}")
            return jsonify({'error': 'Error processing receipt. Please try again.'}), 500
    
    return jsonify({'error': 'Invalid file format. Please upload PNG or JPG.'}), 400

@app.route('/dashboard/<int:user_id>')
def dashboard(user_id):
    user = User.query.get_or_404(user_id)
    
    # Get recent receipts
    receipts = Receipt.query.filter_by(user_id=user_id).order_by(Receipt.created_at.desc()).limit(20).all()
    
    # Calculate totals
    total_amount = db.session.query(db.func.sum(Receipt.amount)).filter_by(user_id=user_id).scalar() or 0
    total_tax = db.session.query(db.func.sum(Receipt.tax_amount)).filter_by(user_id=user_id).scalar() or 0
    
    return render_template('dashboard.html', 
                         user=user, 
                         receipts=receipts, 
                         total_amount=total_amount,
                         total_tax=total_tax,
                         user_id=user_id)

@app.route('/export/<int:user_id>')
def export_data(user_id):
    user = User.query.get_or_404(user_id)
    receipts = Receipt.query.filter_by(user_id=user_id).order_by(Receipt.date.desc()).all()
    
    if receipts:
        try:
            filename = create_excel_export(receipts, user_id)
            return send_file(filename, as_attachment=True, 
                           download_name=f'expenses_{datetime.now().strftime("%Y%m")}.xlsx')
        except Exception as e:
            app.logger.error(f"Error creating export: {str(e)}")
            flash('Error creating export file', 'error')
            return redirect(url_for('dashboard', user_id=user_id))
    
    flash('No receipts to export', 'info')
    return redirect(url_for('dashboard', user_id=user_id))

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum size is 16MB.'}), 413

@app.errorhandler(404)
def not_found(e):
    return render_template('index.html'), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error. Please try again.'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
