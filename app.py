from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file,
    redirect,
    url_for,
    session,
    flash,
    Response,
)
import csv
import pandas as pd
import io
from datetime import datetime, timedelta
import os

# BOT LOGIC (The Brain)
# We import 'bot_brain' and 'TRAINING_FILE' for the Admin Panel features
import dialogue_manager as bot
from dialogue_manager import bot_brain, TRAINING_FILE

# DATABASE (The Memory)
from bank_db import (
    get_db,
    get_user_by_account,
    verify_user_login,
    save_chat,
    get_transactions,
    get_total_queries,
    get_total_intents,
    get_recent_chats,
    # New Milestone 4 Helpers
    get_analytics_stats,
    add_faq,
    get_all_faqs,
)

# ---------------- FLASK CONFIG ----------------
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = "bot_secure_key_2025"  # Secure session key

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------- HELPER: RESET CONTEXT ----------------
def reset_all_bot_context():
    """Clears all temporary memory in the bot logic."""
    for reset_fn in (
        getattr(bot, "reset_cards", None),
        getattr(bot, "reset_atm", None),
        getattr(bot, "reset_lending", None),
        getattr(bot, "reset_onboarding", None),
        getattr(bot, "clear_txn_flow", None),
    ):
        if callable(reset_fn):
            try:
                reset_fn()
            except:
                pass
    try:
        bot.session_context["active_menu"] = None
    except:
        pass


# ---------------- MIDDLEWARE: LOGIN CHECK ----------------
def require_login():
    """Ensures user is logged in before accessing protected pages."""
    if not session.get("account"):
        return False
    # Inject logged-in user into bot memory
    bot.session_context["current_user_account"] = session["account"]
    return True


# ---------------- ROUTE: HOME ----------------
@app.route("/")
def admin_home():
    # Renders the public landing page
    return render_template("admin_home.html", current_year=datetime.now().year)


# ---------------- ROUTE: USER LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        user = verify_user_login(email, password)

        if user:
            # Create Session
            session["account"] = user["account_number"]
            session["email"] = user["email"]
            session["name"] = user["name"]
            session["balance"] = user["balance"]
            session["phone"] = user["phone"]

            # Init Bot
            bot.session_context["current_user_account"] = user["account_number"]
            reset_all_bot_context()

            return redirect(url_for("dashboard"))

        flash("❌ Invalid Credentials. Please try again.", "error")
        return render_template("Login.html")

    return render_template("Login.html")


# ---------------- ROUTE: DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect(url_for("login"))

    # Fetch fresh data
    user = get_user_by_account(session["account"])
    transactions = get_transactions(session["account"])

    return render_template(
        "dashboard.html",
        name=user["name"],
        account=user["account_number"],
        balance=f"{user['balance']:,}",  # Format with commas
        email=user["email"],
        phone=user["phone"],
        transactions=transactions,
    )


# ---------------- ROUTE: CHAT PAGE (IFRAME) ----------------
@app.route("/chat")
def chat():
    if not require_login():
        return redirect(url_for("login"))
    return render_template("chat.html")


# ---------------- API: RESET CONTEXT ----------------
@app.route("/reset_context", methods=["POST"])
def reset_context():
    if not require_login():
        return ("", 401)
    reset_all_bot_context()
    return ("", 204)


# ---------------- API: CHAT RESPONSE (UPDATED FOR MILESTONE 4) ----------------
@app.route("/get_response", methods=["POST"])
def get_response():
    if not require_login():
        return jsonify({"response": "Session expired. Please login again."}), 401

    msg = request.json.get("message", "").strip()
    if not msg:
        return jsonify({"response": "..."})

    # Ensure bot knows who is talking
    bot.session_context["current_user_account"] = session["account"]

    try:
        # Call the Brain - NOW UNPACKING 4 VALUES
        intent, entities, reply, confidence = bot.generate_bot_response(msg)
    except Exception as e:
        print("BOT ERROR:", e)
        reply = "⚠️ System Error: Unable to process request."
        intent, entities, confidence = "error", {}, 0.0

    # Save to DB with Analytics Metadata
    is_fallback = 1 if intent == "fallback" else 0
    save_chat(session["account"], msg, reply, intent, confidence, is_fallback)

    return jsonify({"response": reply, "intent": intent, "confidence": confidence})


# ---------------- ROUTE: CHAT HISTORY ----------------
@app.route("/chat_logs")
def chat_logs():
    if not require_login():
        return redirect(url_for("login"))

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT user_message, bot_response, timestamp FROM chat_logs WHERE account=? ORDER BY id DESC",
        (session["account"],),
    )
    rows = c.fetchall()
    conn.close()

    formatted_logs = []
    for r in rows:
        t_str = r["timestamp"]
        try:
            # Convert UTC to IST (Approx +5:30)
            dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S") + timedelta(
                hours=5, minutes=30
            )
            t_str = dt.strftime("%d %b %I:%M %p")
        except:
            pass
        formatted_logs.append((r["user_message"], r["bot_response"], t_str))

    return render_template("chat_logs.html", logs=formatted_logs)


# ---------------- ROUTE: USER EXPORT EXCEL ----------------
@app.route("/export_excel")
def export_excel():
    if not require_login():
        return redirect(url_for("login"))

    filename = f"Statement_{session['account']}.csv"

    # Use io.StringIO for in-memory CSV generation (cleaner than file system)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User Message", "Bot Response", "Timestamp"])

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT user_message, bot_response, timestamp FROM chat_logs WHERE account=? ORDER BY id",
        (session["account"],),
    )
    data = c.fetchall()
    conn.close()

    for row in data:
        writer.writerow([row["user_message"], row["bot_response"], row["timestamp"]])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"},
    )


# ---------------- ROUTE: ADMIN LOGIN ----------------
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password").strip()

        # Hardcoded Admin Credentials
        if username == "admin_bot" and password == "trust@2025":
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            flash("❌ Access Denied", "error")
            return render_template("admin_login.html")

    return render_template("admin_login.html")


# ---------------- ROUTE: ADMIN DASHBOARD (UPDATED) ----------------
@app.route("/admin_dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    # Use the new detailed analytics function
    stats = get_analytics_stats()

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        total_queries=stats["total"],
        # Backwards compatibility if your old template used these variables directly:
        accuracy=f"{stats['success_rate']}%",
        recent_queries=get_recent_chats(limit=8),
    )


# =================================================
# NEW MILESTONE 4 ADMIN ROUTES
# =================================================


@app.route('/admin/training_data', methods=['GET', 'POST'])
def manage_training_data():
    """View and Add Training Data via CSV (Robust Version)"""
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 403

    file_path = os.path.join(BASE_DIR, TRAINING_FILE)

    # --- POST: ADD NEW DATA ---
    if request.method == 'POST':
        new_text = request.form.get('text', '').strip()
        new_intent = request.form.get('intent', '').strip()
        new_response = request.form.get('response', '').strip()
        
        try:
            # Check if file exists to determine if we need a header
            file_exists = os.path.exists(file_path)
            
            # Open in append mode with UTF-8 to support all characters
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # If file didn't exist, write the standard 4-column header matching your file
                if not file_exists:
                    writer.writerow(['text', 'intent', 'response', 'entities'])
                
                # Write the new row (filling 'entities' as empty JSON '{}')
                writer.writerow([new_text, new_intent, new_response, '{}'])
                
            return jsonify({"status": "success", "msg": "Training example added."})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)})

    # --- GET: READ DATA ---
    try:
        if not os.path.exists(file_path):
            return jsonify([])

        # Try reading with 'utf-8' first (standard), fallback to 'latin1' if it fails
        try:
            df = pd.read_csv(file_path, encoding='utf-8', on_bad_lines='skip', engine='python')
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding='latin1', on_bad_lines='skip', engine='python')

        # Clean the data
        df = df.fillna("")  # Replace NaN with empty string
        
        # Ensure we have the required columns (add if missing)
        for col in ['text', 'intent', 'response']:
            if col not in df.columns:
                df[col] = ""

        # Return the last 50 rows
        data = df.tail(50).to_dict(orient='records')
        return jsonify(data)

    except Exception as e:
        print(f"CSV Load Error: {e}") # Print exact error to console
        # Return an empty list instead of an error so the UI doesn't break completely
        return jsonify([])


@app.route("/admin/retrain", methods=["POST"])
def retrain_model_route():
    """Trigger Hot-Reload of the AI Model"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    success, msg = bot_brain.train_model()
    return jsonify({"status": "success" if success else "error", "message": msg})


@app.route("/admin/faqs", methods=["GET", "POST"])
def manage_faqs():
    """Manage Static Knowledge Base"""
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403

    if request.method == "POST":
        question = request.form.get("question")
        answer = request.form.get("answer")
        add_faq(question, answer)
        return jsonify({"status": "success", "msg": "FAQ added."})

    # GET all FAQs
    faqs = get_all_faqs()
    # Convert tuple rows to dict list for JSON
    faq_list = [{"id": f[0], "question": f[1], "answer": f[2]} for f in faqs]
    return jsonify(faq_list)


@app.route("/admin/export_logs")
def admin_export_logs():
    """Admin Full System Log Export"""
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM chat_logs")
    rows = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "Account",
            "User Message",
            "Bot Response",
            "Intent",
            "Confidence",
            "Is Fallback",
            "Timestamp",
        ]
    )
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=admin_system_logs.csv"},
    )


# ---------------- ROUTE: LOGOUT ----------------
@app.route("/logout")
def logout():
    reset_all_bot_context()
    session.clear()
    return redirect(url_for("admin_home"))


# ... (Paste this near the bottom, before the __main__ block)

@app.route('/admin/logs_json')
def admin_logs_json():
    """Fetch the last 100 logs for the Admin UI table"""
    if not session.get("admin"): return jsonify([])
    
    conn = get_db()
    c = conn.cursor()
    # Fetch last 100 logs, sorted by newest first
    c.execute("SELECT * FROM chat_logs ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    
    # Convert SQLite Rows to a list of dictionaries
    logs_data = [dict(row) for row in rows]
    return jsonify(logs_data)

# ---------------- RUNNER ----------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)
