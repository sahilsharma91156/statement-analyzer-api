from flask import Flask

app = Flask(__name__)

@app.route("/")
@app.route("/health")
def home():
    return "Secureway Statement Analyzer Running", 200

@app.errorhandler(404)
def not_found(e):
    return "Secureway Statement Analyzer Running", 200
