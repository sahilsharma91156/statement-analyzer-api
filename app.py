from flask import Flask, request, jsonify
import pdfplumber
import tempfile
import os
import re
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)

SALARY_KEYWORDS = [
    "SALARY", "PAYROLL", "WAGES", "ALTOS GLOBAL SERVICES", "CONSOLIDATED ACCOUNT ENTRY"
]

EMI_KEYWORDS = [
    "NACH", "ECS", "EMI", "LOAN", "FINANCE", "FINSERV", "KMBL", "KOTAK", "DIGIO",
    "ADITYA", "BIRLA", "NAVI", "BAJAJ", "HDB", "TATA", "KREDITBEE", "KREDITBE",
    "FULLERTO", "FULLERTON", "PIRAMAL", "CHOLA", "L&T", "LTFS"
]

BOUNCE_KEYWORDS = [
    "BOUNCE", "RETURN", "FAILED", "INSUFFICIENT", "ACH RETURN", "MANDATE FAIL", "DISHONOUR", "REJECTED"
]

CASH_KEYWORDS = ["CASH DEP", "CASH DEPOSIT", "BY CASH", "CDM"]


def clean_amount(value):
    if value is None:
        return 0.0
    value = str(value).replace(",", "").replace("₹", "").strip()
    value = re.sub(r"[^\d.]", "", value)
    try:
        return float(value)
    except Exception:
        return 0.0


def get_month(date_text):
    for fmt in ["%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(date_text, fmt).strftime("%Y-%m")
        except Exception:
            pass
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


def open_pdf(path, password=""):
    try:
        return pdfplumber.open(path, password=password if password else None)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg or "decrypt" in msg or "encrypted" in msg:
            raise Exception("PDF password is required or incorrect")
        raise


def extract_pdf(pdf_path, password=""):
    text = ""
    txns = []
    date_re = re.compile(r"\d{2}-[A-Za-z]{3}-\d{4}|\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4}")

    with open_pdf(pdf_path, password) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text += page_text + "\n"

            tables = page.extract_tables() or []
            for table in tables:
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    row = [str(x).replace("\n", " ").strip() if x else "" for x in row]
                    if not date_re.match(row[0]):
                        continue

                    desc = " ".join(row[2:-3]).strip() if len(row) > 6 else row[2]
                    debit = clean_amount(row[-3])
                    credit = clean_amount(row[-2])
                    balance = clean_amount(row[-1])

                    txns.append({
                        "date": row[0],
                        "month": get_month(row[0]),
                        "description": desc,
                        "debit": debit,
                        "credit": credit,
                        "balance": balance,
                    })

    # Fallback text parser for PDFs where table extraction fails
    if not txns:
        for line in text.splitlines():
            dates = date_re.findall(line)
            amounts = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|\d+\.\d{1,2}", line)
            if not dates or len(amounts) < 2:
                continue
            nums = [clean_amount(a) for a in amounts]
            upper = line.upper()
            balance = nums[-1]
            amount = nums[-2]
            debit = 0.0
            credit = 0.0
            if "/CR/" in upper or " CREDIT" in upper or "NEFT/" in upper or "IMPS/" in upper:
                credit = amount
            elif "/DR/" in upper or "NACH/" in upper or "ECS" in upper or "PAYMENT TOWARDS" in upper or "ECOM/" in upper:
                debit = amount
            txns.append({
                "date": dates[0],
                "month": get_month(dates[0]),
                "description": line,
                "debit": debit,
                "credit": credit,
                "balance": balance,
            })

    return text, txns


def risk_grade_for_salary(foir):
    if foir <= 40:
        return "A - Excellent", "Eligible For New Loan"
    if foir <= 60:
        return "B - Good", "Eligible With Conditions"
    if foir <= 80:
        return "C - Moderate", "Need Detailed Review"
    return "D - High Risk", "Not Eligible For Fresh Loan"


def risk_grade_for_business(bounce_count, avg_balance, avg_credit):
    if bounce_count == 0 and avg_balance >= avg_credit * 0.10:
        return "A - Excellent", "Eligible For Business Loan"
    if bounce_count <= 2:
        return "B - Good", "Eligible With Conditions"
    if bounce_count <= 5:
        return "C - Moderate", "Need Detailed Review"
    return "D - High Risk", "High Risk Banking Profile"


def analyze(text, txns):
    upper_text = text.upper()
    monthly_credit = defaultdict(float)
    monthly_debit = defaultdict(float)
    salary_by_month = defaultdict(float)
    balances = []
    emi_map = {}
    bounce_count = 0
    cash_deposit = 0.0

    for t in txns:
        desc = (t.get("description") or "").upper()
        month = t.get("month", "unknown")
        debit = float(t.get("debit", 0) or 0)
        credit = float(t.get("credit", 0) or 0)
        balance = float(t.get("balance", 0) or 0)

        if credit > 0:
            monthly_credit[month] += credit
            if credit >= 10000 and any(k in desc for k in SALARY_KEYWORDS):
                salary_by_month[month] += credit

        if debit > 0:
            monthly_debit[month] += debit

        if balance >= 0:
            balances.append(balance)

        if any(k in desc for k in CASH_KEYWORDS):
            cash_deposit += credit

        if any(k in desc for k in BOUNCE_KEYWORDS):
            bounce_count += 1

        if debit >= 1000 and any(k in desc for k in EMI_KEYWORDS):
            lender = detect_lender(desc)
            if lender not in emi_map:
                emi_map[lender] = {"lender": lender, "amounts": [], "months": set(), "description": desc[:150]}
            emi_map[lender]["amounts"].append(debit)
            emi_map[lender]["months"].add(month)

    valid_months = [m for m in monthly_credit.keys() if m != "unknown"]
    months_count = max(len(valid_months), 1)
    total_credit = sum(monthly_credit.values())
    total_debit = sum(monthly_debit.values())
    avg_monthly_credit = total_credit / months_count
    avg_monthly_debit = total_debit / months_count
    average_balance = sum(balances) / len(balances) if balances else 0
    min_balance = min(balances) if balances else 0
    max_balance = max(balances) if balances else 0

    emi_list = []
    total_emi = 0.0
    for lender, data in emi_map.items():
        months_seen = len(data["months"])
        avg_emi = sum(data["amounts"]) / len(data["amounts"])
        if months_seen >= 2 or avg_emi >= 5000:
            status = "Confirmed" if months_seen >= 3 else "Possible"
            emi_list.append({
                "lender": lender,
                "emi_amount": round(avg_emi, 2),
                "months_seen": months_seen,
                "status": status,
                "description": data["description"],
            })
            # FOIR uses confirmed + strong possible EMIs
            total_emi += avg_emi

    avg_salary = sum(salary_by_month.values()) / len(salary_by_month) if salary_by_month else 0.0
    is_current = "ACCOUNT TYPE : CURRENT" in upper_text or "CURRENT ACCOUNT" in upper_text or "OD ACCOUNT" in upper_text or "CC ACCOUNT" in upper_text
    is_salary = avg_salary > 0 or ("SALARY" in upper_text and not is_current)

    if is_salary:
        account_type = "Salary / Personal Loan"
        annual_turnover = 0.0
        annual_income = avg_salary * 12
        foir = (total_emi / avg_salary * 100) if avg_salary > 0 else 0
        eligible_emi = avg_salary * 0.50
        available_emi = eligible_emi - total_emi
        estimated_eligibility = max(0, available_emi * 60)
        risk_grade, recommendation = risk_grade_for_salary(foir)
    else:
        account_type = "Business / Current Account"
        avg_salary = 0.0
        annual_income = 0.0
        foir = 0.0
        annual_turnover = avg_monthly_credit * 12
        if bounce_count > 5:
            estimated_eligibility = avg_monthly_credit * 1.5
        elif total_emi > avg_monthly_credit * 0.5:
            estimated_eligibility = avg_monthly_credit * 2
        else:
            estimated_eligibility = avg_monthly_credit * 3
        risk_grade, recommendation = risk_grade_for_business(bounce_count, average_balance, avg_monthly_credit)

    return {
        "account_type": account_type,
        "months_analyzed": months_count,
        "total_credit": round(total_credit, 2),
        "total_debit": round(total_debit, 2),
        "avg_monthly_credit": round(avg_monthly_credit, 2),
        "avg_monthly_debit": round(avg_monthly_debit, 2),
        "annual_turnover": round(annual_turnover, 2),
        "average_salary": round(avg_salary, 2),
        "annual_income": round(annual_income, 2),
        "average_balance": round(average_balance, 2),
        "min_balance": round(min_balance, 2),
        "max_balance": round(max_balance, 2),
        "total_emi": round(total_emi, 2),
        "running_emi_count": len(emi_list),
        "emi_list": emi_list,
        "bounce_count": bounce_count,
        "cash_deposit": round(cash_deposit, 2),
        "foir_percent": round(foir, 2),
        "estimated_eligibility": round(estimated_eligibility, 2),
        "risk_grade": risk_grade,
        "recommendation": recommendation,
    }


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return "Secureway Statement Analyzer V4 Pro Running", 200


@app.route("/analyze", methods=["POST"])
def analyze_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No PDF file uploaded"}), 400
    pdf_file = request.files["file"]
    if not pdf_file.filename:
        return jsonify({"error": "Empty file name"}), 400

    password = request.form.get("password", "") or ""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_file.save(tmp.name)
            temp_path = tmp.name

        text, txns = extract_pdf(temp_path, password)
        if not text.strip():
            return jsonify({"error": "No text found in PDF. It may be scanned or password is incorrect."}), 400
        if not txns:
            return jsonify({"error": "No transactions detected from PDF"}), 400

        report = analyze(text, txns)
        return jsonify({"success": True, "version": "V4-Pro", "transactions_count": len(txns), "report": report})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.errorhandler(404)
def not_found(e):
    return "Secureway Statement Analyzer V4 Pro Running", 200
