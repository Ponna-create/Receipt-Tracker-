import pandas as pd
from datetime import datetime
import os

def create_excel_export(receipts, user_id):
    """Create Excel export from receipts data. Handles both Receipt objects and dicts."""
    try:
        # Convert receipts to list of dictionaries
        receipts_data = []
        for receipt in receipts:
            if isinstance(receipt, dict):
                # For free users (dicts)
                receipts_data.append({
                    'Vendor': receipt.get('vendor', receipt.get('Vendor', '')),
                    'Amount': receipt.get('amount', receipt.get('Amount', 0)),
                    'Date': receipt.get('date', receipt.get('Date', '')),
                    'Category': receipt.get('category', receipt.get('Category', '')),
                    'Tax': receipt.get('tax', receipt.get('Tax', 0)),
                    'Uploaded': receipt.get('uploaded', receipt.get('Uploaded', datetime.now().strftime('%Y-%m-%d %H:%M')))
                })
            else:
                # For logged-in users (Receipt objects)
                receipts_data.append({
                    'Vendor': receipt.vendor,
                    'Amount': receipt.amount,
                    'Date': receipt.date,
                    'Category': receipt.category,
                    'Tax': receipt.tax_amount,
                    'Uploaded': receipt.created_at.strftime('%Y-%m-%d %H:%M') if hasattr(receipt, 'created_at') else ''
                })
        
        # Convert to DataFrame
        df = pd.DataFrame(receipts_data)
        
        if df.empty:
            # Create empty DataFrame with proper columns
            df = pd.DataFrame(columns=['Vendor', 'Amount', 'Date', 'Category', 'Tax', 'Uploaded'])
        
        # Add totals row
        totals = pd.DataFrame([{
            'Vendor': 'TOTAL',
            'Amount': df['Amount'].sum() if not df.empty else 0,
            'Date': '',
            'Category': '',
            'Tax': df['Tax'].sum() if not df.empty else 0,
            'Uploaded': ''
        }])
        
        df = pd.concat([df, totals], ignore_index=True)
        
        # Create Excel file
        filename = f'exports/expenses_{user_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        os.makedirs('exports', exist_ok=True)
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # Main expenses sheet
            df.to_excel(writer, sheet_name='Expenses', index=False)
            
            # Category summary sheet (only if we have data)
            if not df.empty and len(df) > 1:  # More than just the totals row
                summary_df = df[df['Vendor'] != 'TOTAL'].groupby('Category')['Amount'].sum().reset_index()
                summary_df = summary_df.sort_values('Amount', ascending=False)
                summary_df.to_excel(writer, sheet_name='Category Summary', index=False)
            
            # Monthly summary if we have multiple dates
            if not df.empty and len(df) > 1:
                monthly_data = df[df['Vendor'] != 'TOTAL'].copy()
                if not monthly_data.empty:
                    # Convert date strings to datetime for grouping
                    monthly_data['Month'] = pd.to_datetime(monthly_data['Date'], errors='coerce').dt.to_period('M')
                    monthly_summary = monthly_data.groupby('Month')['Amount'].sum().reset_index()
                    monthly_summary['Month'] = monthly_summary['Month'].astype(str)
                    monthly_summary.to_excel(writer, sheet_name='Monthly Summary', index=False)
        
        return filename
        
    except Exception as e:
        print(f"Error creating Excel export: {str(e)}")
        # Create a basic CSV as fallback
        filename = f'exports/expenses_{user_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        # Simple CSV creation
        with open(filename, 'w') as f:
            f.write('Vendor,Amount,Date,Category,Tax,Uploaded\n')
            for receipt in receipts:
                if isinstance(receipt, dict):
                    f.write(f'"{receipt.get("vendor", receipt.get("Vendor", ""))}",{receipt.get("amount", receipt.get("Amount", 0))},{receipt.get("date", receipt.get("Date", ""))},{receipt.get("category", receipt.get("Category", ""))},{receipt.get("tax", receipt.get("Tax", 0))},{receipt.get("uploaded", receipt.get("Uploaded", datetime.now().strftime("%Y-%m-%d %H:%M")))}\n')
                else:
                    f.write(f'"{receipt.vendor}",{receipt.amount},{receipt.date},{receipt.category},{receipt.tax_amount},{receipt.created_at.strftime("%Y-%m-%d %H:%M") if hasattr(receipt, "created_at") else ""}\n')
        
        return filename
