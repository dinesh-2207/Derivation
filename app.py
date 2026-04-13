from flask import Flask, render_template, request, jsonify, send_from_directory
from pymongo import MongoClient
from flask_cors import CORS
from sympy import sympify
from dotenv import load_dotenv
from bson import ObjectId
import os
import re
import uuid

load_dotenv()

app = Flask(__name__)
CORS(app, supports_credentials=True, origins=[
    "https://d3ty37mf4sf9cz.cloudfront.net", # Trilok Main App
    "https://d2l8p0hsuvduse.cloudfront.net"  # Admin Lab Project
])

UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("DB_NAME")]
collection = db[os.getenv("COLLECTION_NAME")]

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def serialize_doc(doc):
    if doc is None:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def find_topic_by_name(topic_name):
    for doc in collection.find():
        if isinstance(doc.get("topics"), list):
            for t in doc["topics"]:
                if t.get("topicName") == topic_name:
                    return t
        elif doc.get("topicName") == topic_name:
            return doc
    return None

def build_topic_doc(data):
    """Constructs the dictionary saved in MongoDB — includes all fields."""
    return {
        "standard":           data.get("standard", "").strip(),
        "subject":            data.get("subject", "Physics"),
        "importance":         data.get("importance", "Average"),
        "difficulty":         data.get("difficulty", "Medium"),
        "lessonName":         data.get("lessonName", "").strip(),
        "topicName":          data.get("topicName", "").strip(),
        "topicType":          data.get("topicType", "Derivation"),
        "theoremStatement":   data.get("theoremStatement", "").strip(),
        "givenData":          data.get("givenData", "").strip(),
        "assumptions":        data.get("assumptions", "").strip(),
        "conceptualStatement":data.get("statement", "").strip(),
        "neetTips":           data.get("neetTips", "").strip(),
        "expression":         data.get("expression", "").strip(),
        "unit":               data.get("unit", "").strip(),
        # ── NEW: diagramType drives the live interactive canvas diagram ──
        # Supported values: angle | rightTriangle | circle | forceVector |
        #   velocityTime | distanceTime | barGraph | wave | flowchart | projectile
        # Leave empty ("") if no diagram is needed for this topic.
        "diagramType":        data.get("diagramType", "").strip(),
        "variables": [v.strip() for v in data.get("variables", []) if str(v).strip()],
        "derivationSteps": [s.strip() for s in data.get("derivationSteps", []) if str(s).strip()],
        "calculationSteps": [
            {"label": s.get("label", "").strip(), "formula": s.get("formula", "").strip()}
            for s in data.get("calculationSteps", [])
            if s.get("label") or s.get("formula")
        ],
        "problemImage": data.get("problemImage", ""),
        "tableData":    data.get("tableData", [])
    }

# =============================================================================
# NAVIGATION ROUTES
# =============================================================================

# Admin page
@app.route("/")
def admin():
    return render_template("admin.html")

@app.route("/user")
def home():
    return render_template("index.html")

# =============================================================================
# DATA ROUTES
# =============================================================================

@app.route("/getTopics", methods=["GET"])
def get_topics():
    try:
        topic_names = []
        for doc in collection.find({}, {"_id": 0}):
            if isinstance(doc.get("topics"), list):
                for topic in doc["topics"]:
                    if topic.get("topicName"):
                        topic_names.append(topic["topicName"])
            elif doc.get("topicName"):
                topic_names.append(doc["topicName"])
        return jsonify(topic_names)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/getTopicData", methods=["POST"])
def get_topic_data():
    try:
        data = request.get_json()
        topic = find_topic_by_name(data.get("topicName"))
        if topic:
            topic.pop("_id", None)
            return jsonify(topic)
        return jsonify({"error": "Topic not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/calculate", methods=["POST"])
def calculate():
    try:
        data = request.get_json()
        topic_name = data.get("topicName")
        user_values = data.get("values", {})
        resolved_vars = {k: float(v) for k, v in user_values.items() if v != ""}
        topic = find_topic_by_name(topic_name)
        if not topic:
            return jsonify({"error": "Topic not found"}), 404

        steps_output = []
        calc_steps = topic.get("calculationSteps", [])
        unit = topic.get("unit", "")

        if not calc_steps:
            raw_expr = topic.get('expression', '')
            if '=' in raw_expr:
                calc_steps = [{"label": "Result", "formula": raw_expr}]
            else:
                calc_steps = [{"label": "Result", "formula": f"Ans = {raw_expr}"}]

        last_lhs = ""
        for step in calc_steps:
            label, formula = step.get("label"), step.get("formula")
            if not formula or "=" not in formula:
                continue
            
            parts = [x.strip() for x in formula.split("=")]
            lhs = parts[-2]
            rhs = parts[-1]
            last_lhs = lhs
            
            # Format exactly as requested for Formula: s = u*t + ½*a*t² -> s = ut + ½at²
            display_formula = rhs.replace('0.5', '½').replace('0.25', '¼').replace('**2', '²').replace('**3', '³')
            display_formula = display_formula.replace(" ", "").replace("+", " + ").replace("-", " - ")
            display_formula = display_formula.replace("*", "")
            
            steps_output.append(f"--- {label} ---")
            steps_output.append(f"Formula: {lhs} = {display_formula}")
            
            # Evaluate step numerically
            cleaned_rhs = rhs.replace('½', '0.5').replace('¼', '0.25').replace('¾', '0.75')
            cleaned_rhs = cleaned_rhs.replace('²', '**2').replace('³', '**3').replace('^2', '**2').replace('^3', '**3')
            
            formatted_vars = {}
            for k, v in resolved_vars.items():
                try:
                    vf = float(v)
                    formatted_vars[k] = int(vf) if vf == int(vf) else round(vf, 4)
                except:
                    formatted_vars[k] = v

            substituted_rhs = cleaned_rhs
            display_sub = cleaned_rhs

            for var in sorted(formatted_vars.keys(), key=len, reverse=True):
                substituted_rhs = re.sub(rf"\b{var}\b", str(resolved_vars[var]), substituted_rhs) # for sympify
                display_sub = re.sub(rf"\b{var}\b", str(formatted_vars[var]), display_sub)       # for visual
                
            # Expand things like 10**2 to (10 × 10)
            display_sub = re.sub(r'\(?(\d+(?:\.\d+)?)\)?\*\*2', r'(\1 × \1)', display_sub)
            display_sub = display_sub.replace("**", "^").replace("*", " × ")
            
            steps_output.append(f"Substitution: {lhs} = {display_sub}")
            
            try:
                result_value = float(sympify(substituted_rhs))
                resolved_vars[lhs] = result_value
                res_display = int(result_value) if result_value == int(result_value) else round(result_value, 4)
                steps_output.append(f"Result: {lhs} = {res_display}")
                
                # Auto-calculate square root if LHS is a squared variable (e.g. v²)
                base_var = None
                if lhs.endswith('²'):
                    base_var = lhs[:-1].strip()
                elif lhs.endswith('^2'):
                    base_var = lhs[:-2].strip()
                
                if base_var and result_value >= 0:
                    import math
                    sqrt_val = math.sqrt(result_value)
                    resolved_vars[base_var] = sqrt_val
                    sqrt_display = int(sqrt_val) if sqrt_val == int(sqrt_val) else round(sqrt_val, 4)
                    steps_output.append(f"Result (Square Root): {base_var} = {sqrt_display}")
                    last_lhs = base_var
                    
            except Exception as e:
                steps_output.append(f"Error: Unable to calculate. Please check if formula uses correctly separated variables (e.g., 'u * t' instead of 'ut'). Developer detail: {str(e)}")
                break

        # Rebuild formatted_vars for the final output
        final_formatted_vars = {}
        for k, v in resolved_vars.items():
            if isinstance(v, float):
                final_formatted_vars[k] = int(v) if v == int(v) else round(v, 4)
            else:
                final_formatted_vars[k] = v

        unit_str = topic.get("unit", "")
        
        # If the user used {var} placeholders in the Unit field, format it directly!
        if "{" in unit_str and "}" in unit_str:
            try:
                final_res_string = unit_str.format(**final_formatted_vars)
                return jsonify({"steps": steps_output, "result": final_res_string})
            except Exception:
                pass

        # Fallback to standard standard output
        res_val = resolved_vars.get(last_lhs, "Error")
        if isinstance(res_val, float):
             res_val = int(res_val) if res_val == int(res_val) else round(res_val, 4)
             
        return jsonify({"steps": steps_output, "result": f"Final Answer: {res_val} {unit_str}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =============================================================================
# ADMIN OPERATIONS
# =============================================================================

@app.route("/admin/getAll", methods=["GET"])
def admin_get_all():
    try:
        topics = [serialize_doc(doc) for doc in collection.find() if doc.get("topicName")]
        return jsonify(topics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/getTopic", methods=["POST"])
def admin_get_topic():
    try:
        data = request.get_json()
        doc = collection.find_one({"_id": ObjectId(data.get("id"))})
        return jsonify(serialize_doc(doc)) if doc else (jsonify({"error": "Not found"}), 404)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/addTopic", methods=["POST"])
def admin_add_topic():
    try:
        data = request.get_json()
        name = data.get("topicName", "").strip()
        if collection.find_one({"topicName": name}):
            return jsonify({"error": "Topic already exists"}), 409
        result = collection.insert_one(build_topic_doc(data))
        return jsonify({"success": True, "id": str(result.inserted_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/updateTopic", methods=["POST"])
def admin_update_topic():
    try:
        data = request.get_json()
        topic_id = data.get("id")
        if not topic_id:
            return jsonify({"error": "id is required"}), 400
        collection.update_one({"_id": ObjectId(topic_id)}, {"$set": build_topic_doc(data)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/deleteTopic", methods=["POST"])
def admin_delete_topic():
    try:
        data = request.get_json()
        topic_id = data.get("id")
        doc = collection.find_one({"_id": ObjectId(topic_id)})
        if doc and doc.get("problemImage"):
            path = doc["problemImage"].lstrip("/")
            if os.path.exists(path):
                os.remove(path)
        collection.delete_one({"_id": ObjectId(topic_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/uploadImage", methods=["POST"])
def admin_upload_image():
    try:
        file = request.files.get("image")
        if not file or not allowed_file(file.filename):
            return jsonify({"error": "Invalid file type"}), 400
        filename = f"{uuid.uuid4().hex}.{file.filename.rsplit('.', 1)[1].lower()}"
        path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(path)
        return jsonify({"imagePath": f"/static/uploads/{filename}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)