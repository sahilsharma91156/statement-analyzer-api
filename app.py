from flask import Flask, request, jsonify
import pdfplumber, tempfile, os, re
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)

def amt(v):
    if not v: return 0.0
    v = str(v).replace(",", "").replace("₹", "").strip()
    v = re.sub(r"[^\d.]", "", v)
    try: return float(v)
    except: return 0.0

def month(d):
    try: return datetime.strptime(d, "%d-%b-%Y").strftime("%Y-%m")
    except: return "unknown"

def lender(desc):
    d = desc.upper()
    if "KMBL" in d or "KOTAK" in d: return "Kotak / KMBL"
    if "DIGIO" in d: return "Digio Presentment"
    if "ADITYA" in d or "BIRLA" in d: return "Aditya Birla Finance"
    if "NAVI" in d: return "Navi Finserv"
    if "KREDIT" in d: return "KreditBee"
    if "FULLERTO" in d or "FULLERTON" in d: return "Fullerton"
    if "BAJAJ" in d: return "Bajaj Finance"
    if "HDB" in d: return "HDB Financial"
    if "TATA" in d: return "Tata Capital"
    return "Other Loan / EMI"

def extract(pdf_path):
    text = ""
    txns = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"
            tables = p.extract_tables() or []
            for table in tables:
                for r in table:
                    if not r or len(r) < 6: continue
                    row = [str(x).replace("\n", " ").strip() if x else "" for x in r]
                    if not re.match(r"\d{2}-[A-Za-z]{3}-\d{4}", row[0]): continue
                    txns.append({
                        "date": row[0],
                        "month": month(row[0]),
                        "desc": row[2],
                        "debit": amt(row[-3]),
                        "credit": amt(row[-2]),
                        "balance": amt(row[-1])
                    })
    return text, txns

def analyze(text, txns):
    upper = text.upper()
    is_salary_account = "SALARY" in upper and "CURRENT" not in upper
    is_current_account = "CURRENT" in upper or "CC ACCOUNT" in upper or "OD ACCOUNT" in upper

    monthly_credit = defaultdict(float)
    monthly_debit = defaultdict(float)
    balances = []
    salary_credits = defaultdict(float)
    emi_map = {}
    bounce = 0
    cash = 0

    for t in txns:
        d = t["desc"].upper()
        m = t["month"]

        monthly_credit[m] += t["credit"]
        monthly_debit[m] += t["debit"]
        if t["balance"] > 0: balances.append(t["balance"])

        if any(x in d for x in ["BOUNCE","RETURN","FAILED","INSUFFICIENT"]):
            bounce += 1

        if any(x in d for x in ["CASH DEP","CASH DEPOSIT","CDM"]):
            cash += t["credit"]

        if t["credit"] >= 10000 and any(x in d for x in ["SALARY","PAYROLL","ALTOS GLOBAL SERVICES","CONSOLIDATED ACCOUNT ENTRY"]):
            salary_credits[m] += t["credit"]

        if t["debit"] >= 1000 and any(x in d for x in ["NACH","ECS","EMI","LOAN","FINANCE","FINSERV","KMBL","DIGIO","NAVI","ADITYA","BIRLA","KREDIT","FULLERTO"]):
            l = lender(d)
            emi_map.setdefault(l, {"lender": l, "amounts": [], "months": set()})
            emi_map[l]["amounts"].append(t["debit"])
            emi_map[l]["months"].add(m)

    months = [m for m in monthly_credit if m != "unknown"]
    mc = max(len(months), 1)

    avg_credit = sum(monthly_credit.values()) / mc
    avg_debit = sum(monthly_debit.values()) / mc
    avg_balance = sum(balances) / len(balances) if balances else 0

    emi_list = []
    total_emi = 0
    for l, data in emi_map.items():
        avg_emi = sum(data["amounts"]) / len(data["amounts"])
        if len(data["months"]) >= 2:
            emi_list.append({"lender": l, "emi_amount": round(avg_emi,2), "months_seen": len(data["months"])})
            total_emi += avg_emi

    avg_salary = sum(salary_credits.values()) / len(salary_credits) if salary_credits else 0

    if is_salary_account or avg_salary > 0:
        account_type = "Salary / Personal Loan"
        annual_turnover = 0
        annual_income = avg_salary * 12
        foir = (total_emi / avg_salary * 100) if avg_salary else 0

        if foir >= 60:
            eligibility = 0
            rec = "High EMI burden"
        elif foir >= 50:
            eligibility = avg_salary * 3
            rec = "Weak profile"
        elif foir >= 40:
            eligibility = avg_salary * 6
            rec = "Average profile"
        else:
            eligibility = avg_salary * 10
            rec = "Good profile"
    else:
        account_type = "Business / Current Account"
        annual_turnover = avg_credit * 12
        annual_income = 0
        foir = 0
        avg_salary = 0
        eligibility = avg_credit * 3 if bounce <= 5 else avg_credit * 1.5
        rec = "Business banking profile"

    return {
        "account_type": account_type,
        "months_analyzed": mc,
        "avg_monthly_credit": round(avg_credit,2),
        "avg_monthly_debit": round(avg_debit,2),
        "annual_turnover": round(annual_turnover,2),
        "average_salary": round(avg_salary,2),
        "annual_income": round(annual_income,2),
        "average_balance": round(avg_balance,2),
        "total_emi": round(total_emi,2),
        "running_emi_count": len(emi_list),
        "emi_list": emi_list,
        "bounce_count": bounce,
        "cash_deposit": round(cash,2),
        "foir_percent": round(foir,2),
        "estimated_eligibility": round(eligibility,2),
        "recommendation": rec
    }

@app.route("/health")
@app.route("/")
def health():
    return "Secureway Statement Analyzer V4 Running", 200

@app.route("/analyze", methods=["POST"])
def analyze_pdf():
    if "file" not in request.files:
        return jsonify({"error":"No PDF file uploaded"}),400

    f = request.files["file"]
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            f.save(tmp.name)
            temp_path = tmp.name

        text, txns = extract(temp_path)

        if not txns:
            return jsonify({"error":"No transactions detected from PDF table"}),400

        return jsonify({"success":True, "version":"V4", "report":analyze(text, txns)})

    except Exception as e:
        return jsonify({"error":str(e)}),500

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

@app.errorhandler(404)
def nf(e):
    return "Secureway Statement Analyzer V4 Running", 200
