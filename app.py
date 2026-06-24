from flask import Flask, request, jsonify
import pdfplumber
import re
import tempfile
import os

app = Flask(__name__)

EMI_KEYWORDS = [
    "EMI", "ECS", "NACH", "ACH", "LOAN", "FINANCE",
    "BAJAJ", "HDB", "TATA", "IDFC", "KOTAK", "CHOLA",
    "ADITYA", "PIRAMAL", "L&T", "LTFS"
]

BOUNCE_KEYWORDS = ["BOUNCE", "RETURN", "FAILED", "RRETURN", "INSUFFICIENT"]
CASH_KEYWORDS = ["CASH DEP", "CASH DEPOSIT", "BY CASH", "CASH"]

def clean_amount(value):
    if not value:
        return 0.0
    value = str(value).replace(",", "").replace("₹", "").strip()
    value = re.sub(r"[^\d.]", "", value)
    try:
        return float(value)
    except:
        return 0.0

def extract_text_from_pdf(pdf_path):
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text += page_text + "\n"
    return text

def analyze_text(text):
    lines = text.split("\n")

    total_credit = 0.0
    total_debit = 0.0
    balance_total = 0.0
    balance_count = 0

    emi_list = []
    bounce_count = 0
    cash_deposit = 0.0

    for line in lines:
        upper = line.upper()

        amounts = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|\d+\.\d{1,2}", line)
        nums = [clean_amount(a) for a in amounts]

        if not nums:
            continue

        amount = nums[-1]

        if any(k in upper for k in EMI_KEYWORDS):
            debit_amount = nums[-2] if len(nums) >= 2 else amount
            if debit_amount > 0:
                emi_list.append({
                    "description": line[:120],
                    "amount": round(debit_amount, 2)
                })
                total_debit += debit_amount

        if any(k in upper for k in BOUNCE_KEYWORDS):
            bounce_count += 1

        if any(k in upper for k in CASH_KEYWORDS):
            credit_amount = nums[-2] if len(nums) >= 2 else amount
            cash_deposit += credit_amount

        if "CR" in upper or "CREDIT" in upper or "UPI" in upper or "NEFT" in upper or "RTGS" in upper or "IMPS" in upper:
            if len(nums) >= 2:
                total_credit += nums[-2]

        if "DR" in upper or "DEBIT" in upper or "WITHDRAWAL" in upper:
            if len(nums) >= 2:
                total_debit += nums[-2]

        if len(nums) >= 1:
            balance_total += nums[-1]
            balance_count += 1

    avg_monthly_credit = total_credit
    avg_monthly_debit = total_debit
    annual_turnover = avg_monthly_credit * 12
    average_balance = balance_total / balance_count if balance_count > 0 else 0
    total_emi = sum(e["amount"] for e in emi_list)
    estimated_eligibility = avg_monthly_credit * 3

    return {
        "annual_turnover": round(annual_turnover, 2),
        "avg_monthly_credit": round(avg_monthly_credit, 2),
        "avg_monthly_debit": round(avg_monthly_debit, 2),
        "average_balance": round(average_balance, 2),
        "total_emi": round(total_emi, 2),
        "running_emi_count": len(emi_list),
        "bounce_count": bounce_count,
        "cash_deposit": round(cash_deposit, 2),
        "estimated_eligibility": round(estimated_eligibility, 2),
        "emi_list": emi_list[:20]
    }

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def home():
    return "Secureway Statement Analyzer Running", 200

@app.route("/analyze", methods=["POST"])
def analyze_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No PDF file uploaded"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "Empty file name"}), 400

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file.save(tmp.name)
            temp_path = tmp.name

        text = extract_text_from_pdf(temp_path)

        if not text.strip():
            return jsonify({"error": "No text found in PDF"}), 400

        report = analyze_text(text)

        return jsonify({
            "success": True,
            "report": report
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

@app.errorhandler(404)
def not_found(e):
    return "Secureway Statement Analyzer Running", 200
