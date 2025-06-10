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

# Streamlit UI
st.title("Receipt Tracker")

# Initialize free upload counter in session state
if 'free_uploads' not in st.session_state:
    st.session_state['free_uploads'] = 0

# Login
if 'user_email' not in st.session_state or not st.session_state.user_email:
    st.header("Login (optional)")
    email = st.text_input("Email")
    if st.button("Login"):
        if email and '@' in email:
            user = session_db.query(User).filter_by(email=email).first()
            if not user:
                user = User(email=email)
                session_db.add(user)
                session_db.commit()
            st.session_state.user_email = email
            st.experimental_rerun()
        else:
            st.error("Please enter a valid email address.")
    st.info(f"You can upload and process {5 - st.session_state['free_uploads']} receipts for free without logging in.")
    
    # Free uploads section
    st.header("Free Upload (No Login Required)")
    if st.session_state['free_uploads'] < 5:
        uploaded_file = st.file_uploader("Choose a receipt image (PNG, JPG, JPEG)", type=ALLOWED_EXTENSIONS, key='free_upload')
        if uploaded_file is not None:
            if allowed_file(uploaded_file.name):
                filename = f"free_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uploaded_file.name}"
                filepath = os.path.join('static/uploads', filename)
                with open(filepath, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                receipt_data = extract_receipt_data(filepath)
                if receipt_data:
                    st.session_state['free_uploads'] += 1
                    st.success(f"Receipt processed successfully! You have {5 - st.session_state['free_uploads']} free uploads left.")
                    st.write(receipt_data)
                else:
                    st.error("Could not process receipt. Please try a clearer image.")
            else:
                st.error("File type not allowed. Please upload a PNG, JPG, or JPEG.")
    else:
        st.warning("You have reached the free upload limit. Please log in to continue using the app.")
else:
    st.header("Dashboard")
    st.write(f"Logged in as: {st.session_state.user_email}")
    if st.button("Logout"):
        st.session_state.user_email = None
        st.experimental_rerun()
    
    # Upload Receipt (Logged-in user)
    st.header("Upload Receipt")
    uploaded_file = st.file_uploader("Choose a receipt image", type=ALLOWED_EXTENSIONS, key='loggedin_upload')
    if uploaded_file is not None:
        if allowed_file(uploaded_file.name):
            filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uploaded_file.name}"
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