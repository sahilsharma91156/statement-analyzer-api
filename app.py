from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "Secureway Statement Analyzer Running"

if __name__ == "__main__":
    app.run()
