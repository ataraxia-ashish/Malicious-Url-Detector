"""
Malicious URL Detector — Flask Demo
LightGBM only. No ensemble.
"""

from flask import Flask, request, jsonify, render_template
from inference import predict

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/predict", methods=["POST"])
def predict_url():
    data = request.get_json()
    url = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        result = predict(url)
        # Return only LightGBM fields
        return jsonify({
            "url":        result["url"],
            "label":      result["label"],
            "confidence": result["lgbm_score"],  # raw lgbm score
            "score":      result["lgbm_score"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
