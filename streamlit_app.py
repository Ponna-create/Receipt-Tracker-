import streamlit as st
import os
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, session
from models import User, Receipt
from ocr_processor import extract_receipt_data
from export_utils import create_excel_export
import sqlite3
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

# Initialize Flask app for session management
flask_app = Flask(__name__)
flask_app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Configure the database
flask_app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///receipts.db")
flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Set up SQLAlchemy engine and session for Streamlit
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///receipts.db")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session_db = Session()

# Ensure upload directory exists
os.makedirs('static/uploads', exist_ok=True)
os.makedirs('exports', exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Authentication helper functions
def require_auth():
    return session.get('user_email') is not None

def get_current_user():
    if not require_auth():
        return None
    email = session.get('user_email')
    return session_db.query(User).filter_by(email=email).first()

def create_session(email):
    session['user_email'] = email
    session['login_time'] = datetime.now().isoformat()
    session.permanent = True
    flask_app.permanent_session_lifetime = timedelta(days=30)

# Streamlit UI
st.title("Receipt Tracker")

# Login
if 'user_email' not in st.session_state:
    st.header("Login")
    email = st.text_input("Email")
    if st.button("Login"):
        if email and '@' in email:
            user = session_db.query(User).filter_by(email=email).first()
            if not user:
                user = User(email=email)
                session_db.add(user)
                session_db.commit()
            create_session(email)
            st.session_state.user_email = email
            st.experimental_rerun()
        else:
            st.error("Please enter a valid email address.")
else:
    st.header("Dashboard")
    st.write(f"Logged in as: {st.session_state.user_email}")
    if st.button("Logout"):
        session.clear()
        st.session_state.clear()
        st.experimental_rerun()
    
    # Upload Receipt
    st.header("Upload Receipt")
    uploaded_file = st.file_uploader("Choose a receipt image", type=ALLOWED_EXTENSIONS)
    if uploaded_file is not None:
        if allowed_file(uploaded_file.name):
            filename = secure_filename(f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uploaded_file.name}")
            filepath = os.path.join('static/uploads', filename)
            with open(filepath, "wb") as f:
                f.write(uploaded_file.getbuffer())
            receipt_data = extract_receipt_data(filepath)
            if receipt_data:
                user = session_db.query(User).filter_by(email=st.session_state.user_email).first()
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
                session_db.add(receipt)
                if hasattr(user, 'receipt_count'):
                    user.receipt_count += 1
                session_db.commit()
                st.success("Receipt uploaded and processed successfully!")
            else:
                st.error("Could not process receipt. Please try a clearer image.")
        else:
            st.error("File type not allowed. Please upload a PNG, JPG, or JPEG.")

    # Display Receipts
    st.header("Your Receipts")
    user = session_db.query(User).filter_by(email=st.session_state.user_email).first()
    if user:
        receipts = session_db.query(Receipt).filter_by(user_id=user.id).all()
        if receipts:
            data = []
            for receipt in receipts:
                data.append({
                    "Vendor": receipt.vendor,
                    "Amount": receipt.amount,
                    "Currency": receipt.currency,
                    "Date": receipt.date,
                    "Category": receipt.category,
                    "Tax Amount": receipt.tax_amount
                })
            df = pd.DataFrame(data)
            st.dataframe(df)
        else:
            st.write("No receipts found.")
    
    # Export Data
    if st.button("Export Data"):
        user = session_db.query(User).filter_by(email=st.session_state.user_email).first()
        if user:
            receipts = session_db.query(Receipt).filter_by(user_id=user.id).all()
            if receipts:
                data = []
                for receipt in receipts:
                    data.append({
                        "Vendor": receipt.vendor,
                        "Amount": receipt.amount,
                        "Currency": receipt.currency,
                        "Date": receipt.date,
                        "Category": receipt.category,
                        "Tax Amount": receipt.tax_amount
                    })
                df = pd.DataFrame(data)
                excel_file = create_excel_export(df, user.email)
                st.download_button(
                    label="Download Excel",
                    data=open(excel_file, "rb").read(),
                    file_name="receipts.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.write("No receipts to export.") 