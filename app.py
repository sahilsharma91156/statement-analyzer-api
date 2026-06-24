from flask import Flask, request, jsonify
import pdfplumber
import re
import tempfile
import os
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)

SALARY_KEYWORDS = [
    "SALARY", "PAYROLL", "WAGES",
    "ALTOS GLOBAL SERVICES",
    "CONSOLIDATED ACCOUNT ENTRY"
]

EMI_KEYWORDS = [
    "NACH", "ECS", "EMI", "LOAN", "FINANCE", "FINSERV",
    "KMBL", "KOTAK", "DIGIO", "ADITYA", "BIRLA", "NAVI",
    "BAJAJ", "HDB", "TATA", "KREDITBEE", "KREDITBE",
    "FULLERTO", "FULLERTON", "PIRAMAL", "CHOLA", "L&T", "LTFS"
]

BOUNCE_KEYWORDS = [
    "BOUNCE", "RETURN", "FAILED", "INSUFFICIENT", "ACH RETURN",
    "MANDATE FAIL", "REVERSAL"
]

CASH_KEYWORDS = [
    "CASH DEP", "CASH DEPOSIT", "BY CASH", "CDM"
]


def clean_amount(value):
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
        return datetime.strptime(date_text, "%d-%b-%Y").strftime("%Y-%m")
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
    if "FULLERTO" in d or "FULLERTON" in d:
        return "Fullerton"
    if "BAJAJ" in d:
        return "Bajaj Finance"
    if "HDB" in d:
        return "HDB Financial"
    if "TATA" in d:
        return "Tata Capital"
    if "PIRAMAL" in d:
        return "Piramal Finance"
    if "CHOLA" in d:
        return "Chola Finance"
    if "LTFS" in d or "L&T" in d:
        return "L&T Finance"

    return "Other Loan / EMI"


def parse_transactions(text):
    lines = text.split("\n")
    txns = []
    date_pattern = r"\d{2}-[A-Za-z]{3}-\d{4}"

    for line in lines:
        if "Opening Balance" in line:
            continue

        dates = re.findall(date_pattern, line)
        amounts = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|\d+\.\d{1,2}", line)

        if not dates or len(amounts) < 2:
            continue

        txn_date = dates[0]
        nums = [clean_amount(a) for a in amounts]

        balance = nums[-1]
        amount = nums[-2]

        upper = line.upper()
        debit = 0.0
        credit = 0.0

        if "/CR/" in upper or " CREDIT" in upper or "NEFT/" in upper or "IMPS/" in upper:
            credit = amount
        elif "/DR/" in upper or "NACH/" in upper or "ECS" in upper or "PAYMENT TOWARDS" in upper or "ECOM/" in upper:
            debit = amount

        txns.append({
            "date": txn_date,
            "month": get_month(txn_date),
            "description": line,
            "debit": debit,
            "credit": credit,
            "balance": balance
        })

    return txns


def analyze(text, txns):
    upper_text = text.upper()

    monthly_credit = defaultdict(float)
    monthly_debit = defaultdict(float)
    salary_by_month = defaultdict(float)

    balances = []
    emi_map = {}
    bounce_count = 0
    cash_deposit = 0.0

    credit_txn_count = 0
    salary_txn_count = 0

    for t in txns:
        desc = t["description"].upper()
        month = t["month"]

        if t["credit"] > 0:
            monthly_credit[month] += t["credit"]
            credit_txn_count += 1

            if any(k in desc for k in SALARY_KEYWORDS) and t["credit"] >= 10000:
                salary_by_month[month] += t["credit"]
                salary_txn_count += 1

        if t["debit"] > 0:
            monthly_debit[month] += t["debit"]

        balances.append(t["balance"])

        if any(k in desc for k in CASH_KEYWORDS):
            cash_deposit += t["credit"]

        if any(k in desc for k in BOUNCE_KEYWORDS):
            bounce_count += 1

        if t["debit"] >= 1000 and any(k in desc for k in EMI_KEYWORDS):
            lender = detect_lender(desc)

            if lender not in emi_map:
                emi_map[lender] = {
                    "lender": lender,
                    "amounts": [],
                    "months": set(),
                    "description": t["description"][:150]
                }

            emi_map[lender]["amounts"].append(t["debit"])
            emi_map[lender]["months"].add(month)

    valid_months = sorted([m for m in monthly_credit.keys() if m != "unknown"])
    months_count = max(len(valid_months), 1)

    total_credit = sum(monthly_credit.values())
    total_debit = sum(monthly_debit.values())

    avg_monthly_credit = total_credit / months_count
    avg_monthly_debit = total_debit / months_count
    average_balance = sum(balances) / len(balances) if balances else 0

    avg_salary = 0.0
    if salary_by_month:
        avg_salary = sum(salary_by_month.values()) / len(salary_by_month)

    emi_list = []
    total_emi = 0.0

    for lender, data in emi_map.items():
        months_seen = len(data["months"])
        avg_emi = sum(data["amounts"]) / len(data["amounts"])

        if months_seen >= 2 or avg_emi >= 5000:
            emi_list.append({
                "lender": lender,
                "emi_amount": round(avg_emi, 2),
                "months_seen": months_seen,
                "description": data["description"]
            })
            total_emi += avg_emi

    is_current = "ACCOUNT TYPE : CURRENT" in upper_text or "CURRENT ACCOUNT" in upper_text
    is_salary = avg_salary > 0 or "CORPORATE SALARY" in upper_text

    if is_salary and not is_current:
        account_type = "Salary / Personal Loan"

        monthly_income = avg_salary
        annual_income = monthly_income * 12
        annual_turnover = 0.0

        foir = (total_emi / monthly_income * 100) if monthly_income > 0 else 0

        if monthly_income <= 0:
            eligibility = 0
            recommendation = "Salary not detected clearly"
        elif foir >= 60:
            eligibility = 0
            recommendation = "High EMI burden, eligibility very low"
        elif foir >= 50:
            eligibility = monthly_income * 3
            recommendation = "Weak profile"
        elif foir >= 40:
            eligibility = monthly_income * 6
            recommendation = "Average profile"
        else:
            eligibility = monthly_income * 10
            recommendation = "Good profile"

        avg_salary_output = monthly_income

    else:
        account_type = "Business / Current Account"

        monthly_income = 0.0
        annual_income = 0.0
        annual_turnover = avg_monthly_credit * 12
        foir = 0.0

        if avg_monthly_credit <= 0:
            eligibility = 0
            recommendation = "Credits not detected clearly"
        elif bounce_count > 5:
            eligibility = avg_monthly_credit * 1.5
            recommendation = "Risky profile due to bounce entries"
        elif total_emi > avg_monthly_credit * 0.5:
            eligibility = avg_monthly_credit * 2
            recommendation = "Average profile due to high EMI"
        else:
            eligibility = avg_monthly_credit * 3
            recommendation = "Good business banking profile"

        avg_salary_output = 0.0

    return {
        "account_type": account_type,
        "months_analyzed": months_count,

        "total_credit": round(total_credit, 2),
        "total_debit": round(total_debit, 2),

        "avg_monthly_credit": round(avg_monthly_credit, 2),
        "avg_monthly_debit": round(avg_monthly_debit, 2),
        "annual_turnover": round(annual_turnover, 2),

        "average_salary": round(avg_salary_output, 2),
        "annual_income": round(annual_income, 2),

        "average_balance": round(average_balance, 2),
        "total_emi": round(total_emi, 2),
        "running_emi_count": len(emi_list),
        "emi_list": emi_list,

        "bounce_count": bounce_count,
        "cash_deposit": round(cash_deposit, 2),
        "foir_percent": round(foir, 2),
        "estimated_eligibility": round(eligibility, 2),
        "recommendation": recommendation
    }


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return "Secureway Statement Analyzer V3 Running", 200


@app.route("/analyze", methods=["POST"])
def analyze_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No PDF file uploaded"}), 400

    f = request.files["file"]

    if f.filename == "":
        return jsonify({"error": "Empty file name"}), 400

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            f.save(tmp.name)
            temp_path = tmp.name

        text = extract_text_from_pdf(temp_path)

        if not text.strip():
            return jsonify({"error": "No text found in PDF"}), 400

        txns = parse_transactions(text)
        report = analyze(text, txns)

        return jsonify({
            "success": True,
            "version": "V3",
            "report": report
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.errorhandler(404)
def not_found(e):
    return "Secureway Statement Analyzer V3 Running", 200
