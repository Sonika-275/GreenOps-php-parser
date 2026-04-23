"""
app.py
Flask server — single /analyze endpoint.
Receives PHP source from VSCode extension, returns findings JSON.
"""

from flask import Flask, request, jsonify
from analyzer.engine import analyze
from reporter import format_response

app = Flask(__name__)


@app.route("/analyze", methods=["POST"])
def analyze_code():
    try:
        body = request.get_json(force=True)

        if not body or "code" not in body:
            return jsonify({"error": "Missing 'code' field in request body"}), 400

        source_code  = body["code"]
        runs_per_day = int(body.get("runs_per_day", 10_000))

        engine_output   = analyze(source_code, runs_per_day)
        response        = format_response(engine_output)

        return jsonify(response), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "GreenOps PHP Parser"}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)