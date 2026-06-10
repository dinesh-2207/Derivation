from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from pymongo import MongoClient
from flask_cors import CORS
from sympy import sympify
from dotenv import load_dotenv
from bson import ObjectId
import os
import re
import uuid
import json

load_dotenv()

# Configure Flask with explicit static folder
STATIC_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path='/static')

# CORS configuration: allow localhost for development + CloudFront for production
ALLOWED_ORIGINS = [
    "https://d3ty37mf4sf9cz.cloudfront.net", # Trilok Main App
    "https://d2l8p0hsuvduse.cloudfront.net",  # Admin Lab Project
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:8000",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
]

CORS(app, supports_credentials=True, origins=ALLOWED_ORIGINS,
     expose_headers=['Content-Type', 'Cache-Control', 'X-Accel-Buffering'],
     allow_headers=['Content-Type', 'Authorization'])

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
        "tableData":    data.get("tableData", []),
        # ── AI-generated raw content (saved as single field when using AI generator) ──
        "aiContent":    data.get("aiContent", ""),
        "aiImage":      data.get("aiImage", ""),
    }

# =============================================================================
# NAVIGATION ROUTES
# =============================================================================

# Health check endpoint
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "groq_key_set": bool(os.getenv("GROQ_API_KEY"))})

# Static file serving
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(STATIC_FOLDER, filename)

# Admin page
@app.route("/")
@app.route("/adminhome/derivation")
@app.route("/adminhome/derivation/")
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

# =============================================================================
# SAVE AI CONTENT — stores raw AI text + metadata as a single record
# =============================================================================

@app.route("/admin/saveAiContent", methods=["POST"])
def admin_save_ai_content():
    """Saves AI-generated content as a single 'aiContent' field with metadata."""
    try:
        data = request.get_json()
        topic_name = data.get("topicName", "").strip()
        if not topic_name:
            return jsonify({"error": "topicName is required"}), 400

        doc = {
            "standard":   data.get("standard", "").strip(),
            "subject":    data.get("subject", "Physics"),
            "importance": data.get("importance", "Average"),
            "difficulty": data.get("difficulty", "Medium"),
            "lessonName": data.get("lessonName", "").strip(),
            "topicName":  topic_name,
            "topicType":  data.get("topicType", "Derivation"),
            # All AI-generated content in one field
            "aiContent":  data.get("aiContent", ""),
            # Image stored separately
            "aiImage":    data.get("aiImage", ""),
            # Keep these empty — not extracted from AI
            "theoremStatement":    "",
            "givenData":           "",
            "assumptions":         "",
            "conceptualStatement": "",
            "neetTips":            "",
            "expression":          "",
            "unit":                "",
            "diagramType":         "",
            "variables":           [],
            "derivationSteps":     [],
            "calculationSteps":    [],
            "problemImage":        data.get("aiImage", ""),
            "tableData":           [],
        }

        existing = collection.find_one({"topicName": topic_name})
        if existing:
            collection.update_one({"_id": existing["_id"]}, {"$set": doc})
            return jsonify({"success": True, "id": str(existing["_id"]), "updated": True})
        else:
            result = collection.insert_one(doc)
            return jsonify({"success": True, "id": str(result.inserted_id), "updated": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# AI SOLVE ENDPOINT (Groq streaming — powers the AI modal in admin.html)
# =============================================================================

SUBJECT_CONTEXT = {
    "Physics": "You are expert in JEE/NEET Physics. Focus on mechanics, thermodynamics, electromagnetism, optics, modern physics. Always check units, mention relevant laws.",
    "Chemistry": "You are expert in JEE/NEET Chemistry. Cover physical, organic, inorganic. Balance equations, explain mechanisms.",
    "Mathematics": "You are expert in JEE Mathematics. Cover calculus, algebra, coordinate geometry, trigonometry. Show shortcut methods.",
    "Biology": "You are expert in NEET Biology. Reference NCERT content, use correct biological terminology, mention exceptions.",
    "Other": "You are an expert JEE/NEET tutor. Explain concepts clearly step by step.",
}

AI_SYSTEM_PROMPT = """You are an expert JEE/NEET tutor with 15+ years of experience.

RESPONSE FORMAT (always follow this structure):

## 🔍 Understanding the Question
[1-2 sentences: what the question is asking]

## 📚 Core Concepts
[Relevant formulas, laws, principles with brief explanations]

## ✏️ Step-by-Step Solution

**Step 1: [Name]**
[Detailed explanation with working]

**Step 2: [Name]**
[Continue as needed...]

## ✅ Final Answer
[Clearly state the answer with units]

## 💡 Key Insight
[One memorable takeaway or common mistake to avoid]

STYLE RULES:
- Use LaTeX math: inline as $formula$ and block as $$formula$$
- Show ALL working — never skip steps
- Be encouraging but accurate
"""

@app.route("/ai/solve", methods=["POST"])
def ai_solve():
    try:
        from groq import Groq
        import sys
        
        data = request.get_json()
        question = data.get("question", "").strip()
        subject = data.get("subject", "Physics")
        
        print(f"🚀 [AI/SOLVE] Received request: question={question[:50]}... subject={subject}", file=sys.stderr)

        if not question or len(question) < 5:
            print(f"❌ [AI/SOLVE] Invalid question length: {len(question)}", file=sys.stderr)
            return jsonify({"error": "Please enter a valid question."}), 400

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            print("❌ [AI/SOLVE] GROQ_API_KEY not set in .env", file=sys.stderr)
            return jsonify({"error": "GROQ_API_KEY not set in .env"}), 500

        print(f"✅ [AI/SOLVE] API key found: {api_key[:10]}...", file=sys.stderr)
        client_groq = Groq(api_key=api_key)
        system = AI_SYSTEM_PROMPT + "\n" + SUBJECT_CONTEXT.get(subject, SUBJECT_CONTEXT["Other"])

        def generate():
            try:
                print("🔄 [AI/SOLVE] Starting Groq stream...", file=sys.stderr)
                stream = client_groq.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    max_tokens=2048,
                    temperature=0.4,
                    stream=True,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"Subject: {subject}\n\nQuestion: {question}"}
                    ],
                )
                chunk_count = 0
                for chunk in stream:
                    text = chunk.choices[0].delta.content or ""
                    if text:
                        chunk_count += 1
                        yield f"data: {json.dumps({'text': text})}\n\n"
                print(f"✅ [AI/SOLVE] Stream complete with {chunk_count} chunks", file=sys.stderr)
                yield "data: [DONE]\n\n"
            except Exception as e:
                print(f"❌ [AI/SOLVE] Stream error: {str(e)}", file=sys.stderr)
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        print("📤 [AI/SOLVE] Sending SSE response", file=sys.stderr)
        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except Exception as e:
        print(f"🔥 [AI/SOLVE] Endpoint error: {str(e)}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500


# =============================================================================
# AI STRUCTURED EXTRACT (parses AI response to fill form fields)
# =============================================================================

@app.route("/ai/extract", methods=["POST"])
def ai_extract():
    """Takes AI response text + metadata and returns structured form data as JSON."""
    try:
        from groq import Groq
        data = request.get_json()
        ai_text = data.get("aiText", "")
        question = data.get("question", "")
        subject = data.get("subject", "Physics")
        topic_type = data.get("topicType", "Derivation")

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            return jsonify({"error": "GROQ_API_KEY not set"}), 500

        client_groq = Groq(api_key=api_key)

        extract_prompt = f"""You are a structured data extractor for a physics/science education admin system.

Given this AI-generated solution for a {subject} {topic_type}, extract and return ONLY a JSON object with these exact fields:

{{
  "theoremStatement": "Official theorem or law statement if present, else empty string",
  "statement": "Clear conceptual explanation (2-4 sentences)",
  "neetTips": "Key exam tips, common mistakes, or memory tricks",
  "assumptions": "Any assumptions made in derivation (if applicable)",
  "givenData": "Any given data or known values (if applicable)",
  "expression": "Final mathematical expression in sympy format (e.g. m*g*h), NO LaTeX",
  "unit": "SI unit of the result (e.g. Joules (J))",
  "variables": ["list", "of", "variable names like m, g, h"],
  "derivationSteps": ["Step 1 text", "Step 2 text", "Step 3 text"],
  "calculationSteps": [{{"label": "Step name", "formula": "lhs = rhs in sympy format"}}]
}}

Return ONLY the JSON object. No markdown, no explanation, no extra text.

Question/Topic: {question}
Subject: {subject}
Type: {topic_type}

AI Solution to extract from:
{ai_text}"""

        response = client_groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=1500,
            temperature=0.2,
            messages=[
                {"role": "user", "content": extract_prompt}
            ],
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'^```\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)

        parsed = json.loads(raw)
        return jsonify({"success": True, "data": parsed})
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Could not parse AI response as JSON: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)