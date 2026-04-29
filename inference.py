"""
Malicious URL Detector — Inference Pipeline
==================================================
Confidence-gated ensemble:
  - LightGBM (primary) — if confidence >= 0.8, trust it directly
  - Uncertain zone (0.2–0.8) — XGBoost + CatBoost + HistGBM majority vote

Usage:
    from inference import predict
    result = predict("http://some-suspicious-url.com")
    print(result)

Returns dict:
    {
        "url":            str,
        "label":          "MALICIOUS" | "BENIGN",
        "confidence":     float (0.0 – 1.0),
        "deciding_model": str,
        "lgbm_score":     float,
        "ensemble_score": float | None
    }

Folder structure expected:
    project/
    ├── inference.py
    ├── feature_cols_v6.json
    └── models/
        ├── v6_lgbm.txt
        ├── v6_xgb.pkl
        ├── catboost_v6.pkl
        ├── histgb_v6.pkl
        └── scaler_v6.pkl
"""

import os, re, math, json, pickle
from urllib.parse import urlparse, parse_qs
import numpy as np

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
FEAT_FILE  = os.path.join(BASE_DIR, "feature_cols_v6.json")

LGBM_CONFIDENCE_THRESHOLD = 0.8
MALICIOUS_THRESHOLD       = 0.5

IANA_TLDS = {
    "com","org","net","edu","gov","mil","int","io","co","info","biz","me",
    "de","uk","fr","jp","ru","cn","au","br","ca","it","nl","pl","es","in",
    "tk","cl","eu","hk","cz","se","be","mx","at","us","ch","ro","dk","ua",
    "tw","za","ar","nz","kr","cc","hu","gr","no","ve","tr"
}
MTLD = {"com","org","net","edu","gov","mil","int"}

_models = {}

def _load_models():
    global _models
    if _models:
        return
    try:
        import lightgbm as lgb
        _models["lgbm"] = lgb.Booster(model_file=os.path.join(MODELS_DIR, "v6_lgbm.txt"))
    except Exception as e:
        raise RuntimeError(f"Failed to load LightGBM: {e}")
    with open(FEAT_FILE, "r") as f:
        _models["feature_cols"] = json.load(f)
    _models["known_tlds"] = [c.replace("tld_", "") for c in _models["feature_cols"] if c.startswith("tld_") and c != "tld_absent"]

def _shannon_entropy(s):
    if not s: return 0.0
    freq = {}
    for c in s: freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((v / n) * math.log2(v / n) for v in freq.values())

def _extract_features(url, feature_cols, known_tlds):
    f = {c: 0 for c in feature_cols}
    raw = url.strip()
    try:
        p = urlparse(raw)
        host = p.netloc or p.path
        host_clean = re.sub(r':\d+$', '', host)
        parts = host_clean.split('.')
        tld = parts[-1].lower() if len(parts) > 1 else ''
        path = p.path; query = p.query; frag = p.fragment
        f['dots'] = raw.count('.'); f['at'] = raw.count('@'); f['equals'] = raw.count('=')
        f['slashes'] = raw.count('/'); f['hyphens'] = raw.count('-'); f['colons'] = raw.count(':')
        f['question_marks'] = raw.count('?'); f['and'] = raw.count('&'); f['underscore'] = raw.count('_')
        f['tilde'] = raw.count('~'); f['percent'] = raw.count('%')
        f['digits'] = sum(c.isdigit() for c in raw)
        f['lowercase'] = sum(c.islower() for c in raw)
        f['uppercase'] = sum(c.isupper() for c in raw)
        f['upper_to_lower_ratio'] = f['uppercase'] / f['lowercase'] if f['lowercase'] > 0 else 0.0
        f['url_length'] = len(raw); f['domain_length'] = len(host_clean)
        f['path_length'] = len(path); f['path_depth'] = len([s for s in path.split('/') if s])
        f['query_length'] = len(query); f['query_count'] = len(parse_qs(query)); f['fragment_length'] = len(frag)
        f['se_url'] = _shannon_entropy(raw); f['se_domain'] = _shannon_entropy(host_clean)
        f['se_path'] = _shannon_entropy(path); f['se_query'] = _shannon_entropy(query); f['se_fragment'] = _shannon_entropy(frag)
        consonants = sum(1 for c in host_clean.lower() if c in 'bcdfghjklmnpqrstvwxyz')
        f['cte_domain'] = consonants / (_shannon_entropy(host_clean) + 1e-9)
        f['is_domain_ip'] = 1 if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', host_clean) else 0
        f['is_tld_iana_reg'] = 1 if tld in IANA_TLDS else 0
        f['is_mtld'] = 1 if tld in MTLD else 0
        f['subdomains'] = max(0, len(parts) - 2)
        special = set('!@#$%^&*()+=[]{}|;:<>?,~`\\\'\"')
        f['special_chars'] = sum(c in special for c in raw)
        url_len = len(raw) if len(raw) > 0 else 1
        f['digit_to_length_ratio'] = f['digits'] / url_len
        f['char_to_length_ratio'] = (f['lowercase'] + f['uppercase']) / url_len
        f['specialchar_to_length_ratio'] = f['special_chars'] / url_len
        if tld in known_tlds:
            f[f'tld_{tld}'] = 1
        else:
            f['tld_absent'] = 1
    except:
        pass
    return np.array([f[c] for c in feature_cols], dtype=np.float32).reshape(1, -1)

def predict(url: str) -> dict:
    _load_models()
    feature_cols = _models["feature_cols"]
    known_tlds   = _models["known_tlds"]
    X_raw        = _extract_features(url, feature_cols, known_tlds)
    lgbm_score   = float(_models["lgbm"].predict(X_raw)[0])
    label        = "MALICIOUS" if lgbm_score >= MALICIOUS_THRESHOLD else "BENIGN"
    confidence   = lgbm_score if label == "MALICIOUS" else (1 - lgbm_score)
    return {
        "url":        url,
        "label":      label,
        "confidence": round(confidence, 4),
        "lgbm_score": round(lgbm_score, 4),
        "deciding_model": "LightGBM",
        "ensemble_score": None
    }
def predict_batch(urls: list) -> list:
    return [predict(url) for url in urls]

if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python inference.py <url>"); sys.exit(1)
    result = predict(sys.argv[1])
    print(json.dumps(result, indent=2))
