from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def home():
    return "Secureway Statement Analyzer Running", 200

@app.errorhandler(404)
def not_found(e):
    return "Secureway Statement Analyzer Running", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
