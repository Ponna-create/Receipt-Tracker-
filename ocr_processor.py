import pytesseract
from PIL import Image
import re
import json
import os
from datetime import datetime
from openai import OpenAI

# Get OpenAI API key from environment
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "your-openai-api-key")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY != "your-openai-api-key" else None

def extract_receipt_data(image_path):
    """Extract structured data from receipt image using OCR and AI"""
    try:
        # Extract text using OCR
        image = Image.open(image_path)
        # Enhance image for better OCR
        image = image.convert('RGB')
        text = pytesseract.image_to_string(image)
        
        if not text.strip():
            return extract_fallback("No text detected")
        
        # Use AI to structure the data if OpenAI is available
        if openai_client and OPENAI_API_KEY != "your-openai-api-key":
            return extract_with_ai(text)
        else:
            return extract_fallback(text)
        
    except Exception as e:
        print(f"Error in extract_receipt_data: {str(e)}")
        # Fallback: Basic regex extraction
        return extract_fallback("")

def extract_with_ai(text):
    """Use OpenAI to extract structured data from OCR text"""
    try:
        if not openai_client:
            return extract_fallback(text)
        prompt = f"""
Extract receipt information from this text and return as JSON:
{text}

Return only JSON in this exact format:
{{
    "vendor": "store name",
    "amount": 123.45,
    "currency": "USD",
    "date": "YYYY-MM-DD",
    "category": "Food/Travel/Office/Entertainment/Other",
    "tax": 12.34
}}

Guidelines:
- If you can't find a value, use null
- For amount, extract the total amount paid (numeric value only)
- For currency, detect the currency symbol or code (USD, EUR, GBP, INR, CAD, AUD, etc.)
- For date, use YYYY-MM-DD format
- For category, choose from: Food, Travel, Office, Entertainment, Other
- For tax, extract any tax/GST amount mentioned
- Common currency symbols: $ (USD), € (EUR), £ (GBP), ₹ (INR), ¥ (JPY), C$ (CAD), A$ (AUD)
"""
        
        # the newest OpenAI model is "gpt-4o" which was released May 13, 2024.
        # do not change this unless explicitly requested by the user
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert at extracting structured data from receipt text. Always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.1
        )
        
        # Parse AI response
        content = response.choices[0].message.content
        if content:
            result = json.loads(content)
        else:
            return extract_fallback(text)
        
        # Validate and clean data
        if result.get('date'):
            # Ensure date format
            try:
                parsed_date = datetime.strptime(result['date'], '%Y-%m-%d')
                result['date'] = parsed_date.strftime('%Y-%m-%d')
            except:
                result['date'] = datetime.now().strftime('%Y-%m-%d')
        else:
            result['date'] = datetime.now().strftime('%Y-%m-%d')
        
        # Ensure numeric values
        result['amount'] = float(result.get('amount', 0.0)) if result.get('amount') else 0.0
        result['tax'] = float(result.get('tax', 0.0)) if result.get('tax') else 0.0
        
        # Ensure valid category
        valid_categories = ['Food', 'Travel', 'Office', 'Entertainment', 'Other']
        if result.get('category') not in valid_categories:
            result['category'] = 'Other'
        
        # Ensure currency is set (default to USD if not detected)
        if not result.get('currency'):
            result['currency'] = 'USD'
        
        return result
        
    except Exception as e:
        print(f"Error with AI extraction: {str(e)}")
        # Fallback to regex extraction
        return extract_fallback(text)

def extract_fallback(text):
    """Fallback method using regex if AI fails"""
    try:
        # Basic amount extraction - look for currency symbols and decimal numbers
        amount_patterns = [
            r'₹\s*(\d+\.?\d*)',  # Indian Rupee
            r'\$\s*(\d+\.?\d*)',  # Dollar
            r'€\s*(\d+\.?\d*)',   # Euro
            r'£\s*(\d+\.?\d*)',   # Pound
            r'total[:\s]*\$?₹?\s*(\d+\.?\d*)', # Total amount
            r'amount[:\s]*\$?₹?\s*(\d+\.?\d*)', # Amount
            r'(\d+\.\d{2})'       # Any decimal number with 2 decimal places
        ]
        
        amount = 0.0
        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount = float(match.group(1))
                break
        
        # Basic vendor extraction (first meaningful line)
        lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
        vendor = "Unknown Vendor"
        if lines:
            # Skip common header words and take first substantial line
            skip_words = ['receipt', 'invoice', 'bill', 'tax', 'gst', 'date', 'time']
            for line in lines[:5]:  # Check first 5 lines
                if len(line) > 3 and not any(word in line.lower() for word in skip_words):
                    vendor = line[:50]  # Limit length
                    break
        
        # Basic tax extraction
        tax_patterns = [
            r'tax[:\s]*\$?₹?\s*(\d+\.?\d*)',
            r'gst[:\s]*\$?₹?\s*(\d+\.?\d*)',
            r'vat[:\s]*\$?₹?\s*(\d+\.?\d*)'
        ]
        
        tax = 0.0
        for pattern in tax_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                tax = float(match.group(1))
                break
        
        # If no tax found, estimate based on amount (18% GST common in India)
        if tax == 0.0 and amount > 0:
            tax = round(amount * 0.18, 2)
        
        # Basic currency detection
        currency = 'USD'  # Default
        currency_patterns = [
            (r'₹', 'INR'),
            (r'€', 'EUR'),
            (r'£', 'GBP'),
            (r'¥', 'JPY'),
            (r'C\$', 'CAD'),
            (r'A\$', 'AUD'),
            (r'\$', 'USD')  # USD last as fallback
        ]
        
        for pattern, curr in currency_patterns:
            if re.search(pattern, text):
                currency = curr
                break
        
        # Basic category detection based on keywords
        category = 'Other'
        food_keywords = ['restaurant', 'cafe', 'food', 'dining', 'pizza', 'burger', 'coffee']
        travel_keywords = ['hotel', 'flight', 'taxi', 'uber', 'ola', 'gas', 'petrol']
        office_keywords = ['office', 'supplies', 'stationery', 'computer', 'software']
        entertainment_keywords = ['movie', 'cinema', 'game', 'entertainment', 'fun']
        
        text_lower = text.lower()
        if any(keyword in text_lower for keyword in food_keywords):
            category = 'Food'
        elif any(keyword in text_lower for keyword in travel_keywords):
            category = 'Travel'
        elif any(keyword in text_lower for keyword in office_keywords):
            category = 'Office'
        elif any(keyword in text_lower for keyword in entertainment_keywords):
            category = 'Entertainment'
        
        return {
            'vendor': vendor,
            'amount': amount,
            'currency': currency,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'category': category,
            'tax': tax
        }
        
    except Exception as e:
        print(f"Error in fallback extraction: {str(e)}")
        return {
            'vendor': 'Unknown Vendor',
            'amount': 0.0,
            'currency': 'USD',
            'date': datetime.now().strftime('%Y-%m-%d'),
            'category': 'Other',
            'tax': 0.0
        }
