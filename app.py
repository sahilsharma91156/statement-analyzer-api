from flask import Flask, request, jsonify
import pdfplumber
import re
import tempfile
import os
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)

SALARY_KEYWORDS = [
    "SALARY", "PAYROLL", "WAGES", "ALTOS GLOBAL SERVICES", "CONSOLIDATED ACCOUNT ENTRY"
]

BUSINESS_KEYWORDS = [
    "CURRENT", "CC ACCOUNT", "OD ACCOUNT", "RTGS", "NEFT", "IMPS", "UPI/CR",
    "GST", "CUSTOMER", "RECEIPT", "PAYMENT RECEIVED"
]

EMI_KEYWORDS = [
    "NACH", "ECS", "EMI", "LOAN", "FINANCE", "FINSERV",
    "KMBL", "KOTAK", "DIGIO", "ADITYA", "BIRLA", "NAVI",
    "BAJAJ", "HDB", "TATA", "KREDITBEE", "FULLERTO", "PIRAMAL"
]

BOUNCE_KEYWORDS = [
    "BOUNCE", "RETURN", "FAILED", "INSUFFICIENT", "MANDATE FAIL", "ACH RETURN"
]

CASH_KEYWORDS = [
    "CASH DEP", "CASH DEPOSIT", "BY CASH"
]


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
            text += (page.extract_text() or "") + "\n"
    return text


def get_month(date_text):
    try:
        dt = datetime.strptime(date_text, "%d-%b-%Y")
        return dt.strftime("%Y-%m")
    except:
        return "unknown"


def detect_lender(desc):
    d = desc.upper()

    if "KMBL" in d or "KOTAK" in d:
        return "Kotak / KMBL"
    if "DIGIO" in d:
        return "Digio Presentment"
    if "ADITYA" in d or "BIRLA" in d:
        return "Aditya Birla Finance"
    if "NAVI" in d:
        return "Navi Finserv"
    if "KREDIT" in d:
        return "KreditBee"
    if "FULLERTO" in d:
        return "Fullerton"
    if "BAJAJ" in d:
        return "Bajaj Finance"
    if "HDB" in d:
        return "HDB Financial"
    if "TATA" in d:
        return "Tata Capital"

    return "Other Loan / EMI"


def parse_transactions(text):
    lines = text.split("\n")
    transactions = []

    date_pattern = r"\d{2}-[A-Za-z]{3}-\d{4}"

    for line in lines:
        dates = re.findall(date_pattern, line)
        amounts = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|\d+\.\d{1,2}", line)

        if not dates or len(amounts) < 1:
            continue

        txn_date = dates[0]
        nums = [clean_amount(a) for a in amounts]

        balance = nums[-1] if len(nums) >= 1 else 0
        amount = nums[-2] if len(nums) >= 2 else 0

        upper = line.upper()

        debit = 0
        credit = 0

        if "/CR/" in upper or " CREDIT" in upper or "NEFT/" in upper or "IMPS/" in upper:
            credit = amount
        elif "/DR/" in upper or "NACH/" in upper or "ECS" in upper or "PAYMENT TOWARDS" in upper:
            debit = amount
        else:
            if len(nums) >= 3:
                debit = nums[-3]
                credit = nums[-2]

        transactions.append({
            "date": txn_date,
            "month": get_month(txn_date),
            "description": line,
            "debit": debit,
            "credit": credit,
            "balance": balance
        })

    return transactions


def analyze_transactions(text, transactions):
    upper_text = text.upper()

    monthly_credit = defaultdict(float)
    monthly_debit = defaultdict(float)
    monthly_salary = defaultdict(float)

    balances = []
    emi_by_lender = {}
    bounce_count = 0
    cash_deposit = 0

    salary_credits = []

    for txn in transactions:
        desc = txn["description"].upper()
        month = txn["month"]

        if txn["credit"] > 0:
            monthly_credit[month] += txn["credit"]

        if txn["debit"] > 0:
            monthly_debit[month] += txn["debit"]

        if txn["balance"] >= 0:
            balances.append(txn["balance"])

        if any(k in desc for k in CASH_KEYWORDS):
            cash_deposit += txn["credit"]

        if any(k in desc for k in BOUNCE_KEYWORDS):
            bounce_count += 1

        if txn["credit"] > 10000 and any(k in desc for k in SALARY_KEYWORDS):
            monthly_salary[month] += txn["credit"]
            salary_credits.append(txn)

        if txn["debit"] > 1000 and any(k in desc for k in EMI_KEYWORDS):
            lender = detect_lender(desc)

            if lender not in emi_by_lender:
                emi_by_lender[lender] = {
                    "lender": lender,
                    "amounts": [],
                    "months": set(),
                    "description": txn["description"][:140]
                }

            emi_by_lender[lender]["amounts"].append(txn["debit"])
            emi_by_lender[lender]["months"].add(month)

    valid_months = [m for m in monthly_credit.keys() if m != "unknown"]
    months_count = max(len(valid_months), 1)

    avg_monthly_credit = sum(monthly_credit.values()) / months_count
    avg_monthly_debit = sum(monthly_debit.values()) / months_count
    average_balance = sum(balances) / len(balances) if balances else 0

    avg_salary = 0
    if monthly_salary:
        avg_salary = sum(monthly_salary.values()) / len(monthly_salary)

    recurring_emi_list = []
    total_emi = 0

    for lender, data in emi_by_lender.items():
        months_seen = len(data["months"])
        avg_emi = sum(data["amounts"]) / len(data["amounts"])

        if months_seen >= 2 or avg_emi >= 5000:
            recurring_emi_list.append({
                "lender": lender,
                "emi_amount": round(avg_emi, 2),
                "months_seen": months_seen,
                "description": data["description"]
            })
            total_emi += avg_emi

    is_salary = False
    is_business = False

    if avg_salary > 0 or "SALARY" in upper_text or "CORPORATE SALARY" in upper_text:
        is_salary = True

    if "ACCOUNT TYPE : CURRENT" in upper_text or "CURRENT ACCOUNT" in upper_text:
        is_business = True

    if avg_monthly_credit > 200000 and len(salary_credits) < 2:
        is_business = True

    if is_salary and not is_business:
        account_type = "Salary / Personal Loan"
        monthly_income = avg_salary if avg_salary > 0 else avg_monthly_credit
        foir = (total_emi / monthly_income * 100) if monthly_income > 0 else 0

        if foir >= 60:
            eligibility = 0
        elif foir >= 50:
            eligibility = monthly_income * 3
        elif foir >= 40:
            eligibility = monthly_income * 6
        else:
            eligibility = monthly_income * 12

        annual_income = monthly_income * 12
        annual_turnover = 0

    else:
        account_type = "Business / Current Account"
        annual_turnover = avg_monthly_credit * 12
        monthly_income = 0
        annual_income = 0
        foir = 0

        if bounce_count > 5:
            eligibility = avg_monthly_credit * 1.5
        elif total_emi > avg_monthly_credit * 0.5:
            eligibility = avg_monthly_credit * 2
        else:
            eligibility = avg_monthly_credit * 3

    return {
        "account_type": account_type,

        "avg_monthly_credit": round(avg_monthly_credit, 2),
        "avg_monthly_debit": round(avg_monthly_debit, 2),
        "annual_turnover": round(annual_turnover, 2),

        "average_salary": round(monthly_income, 2),
        "annual_income": round(annual_income, 2),

        "average_balance": round(average_balance, 2),
        "total_emi": round(total_emi, 2),
        "running_emi_count": len(recurring_emi_list),
        "emi_list": recurring_emi_list,

        "bounce_count": bounce_count,
        "cash_deposit": round(cash_deposit, 2),
        "foir_percent": round(foir, 2),
        "estimated_eligibility": round(eligibility, 2),

        "months_analyzed": months_count
    }


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def home():
    return "Secureway Statement Analyzer V2 Running", 200


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

        transactions = parse_transactions(text)
        report = analyze_transactions(text, transactions)

        return jsonify({
            "success": True,
            "version": "V2",
            "report": report
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.errorhandler(404)
def not_found(e):
    return "Secureway Statement Analyzer V2 Running", 200
