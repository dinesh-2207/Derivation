from flask import Flask, render_template, request, jsonify, send_from_directory
from pymongo import MongoClient
from sympy import sympify
from dotenv import load_dotenv
from bson import ObjectId
import os
import re
import uuid

load_dotenv()

app = Flask(__name__)

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

@app.route("/admin-lab")
def admin_lab():
    return render_template("admin.html")

@app.route("/")
def admin_default():
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
            calc_steps = [{"label": "Result", "formula": f"Ans = {topic.get('expression')}"}]

        last_lhs = ""
        for step in calc_steps:
            label, formula = step.get("label"), step.get("formula")
            if not formula or "=" not in formula:
                continue
            lhs, rhs = [x.strip() for x in formula.split("=", 1)]
            last_lhs = lhs
            display_formula = rhs.replace("*", " × ").replace("**", "^")
            steps_output.append(f"--- {label} ---")
            steps_output.append(f"Formula: {lhs} = {display_formula}")
            substituted_rhs = rhs
            for var in sorted(resolved_vars.keys(), key=len, reverse=True):
                substituted_rhs = re.sub(rf"\b{var}\b", str(resolved_vars[var]), substituted_rhs)
            display_sub = substituted_rhs.replace("*", " × ").replace("**", "^")
            steps_output.append(f"Substitution: {lhs} = {display_sub}")
            try:
                result_value = float(sympify(substituted_rhs))
                resolved_vars[lhs] = result_value
                steps_output.append(f"Result: {lhs} = {round(result_value, 4)}")
            except Exception as e:
                steps_output.append(f"Error: {str(e)}")
                break

        final_val = resolved_vars.get(last_lhs, "Error")
        return jsonify({"steps": steps_output, "result": f"Final Answer: {final_val} {unit}"})
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
    app.run(debug=True)