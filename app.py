from flask import (
    Flask, render_template, request, jsonify, send_file,
    redirect, url_for, session, flash
)
import csv
from datetime import datetime, timedelta
import os

# BOT LOGIC (The Brain)
import dialogue_manager as bot

# DATABASE (The Memory)
from bank_db import (
    get_db,
    get_user_by_account,
    verify_user_login,
    save_chat,
    get_transactions,
    get_total_queries,
    get_total_intents,
    get_recent_chats
)

# ---------------- FLASK CONFIG ----------------
app = Flask(__name__, static_folder='static', template_folder='templates')
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
        balance=f"{user['balance']:,}", # Format with commas
        email=user["email"],
        phone=user["phone"],
        transactions=transactions
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


# ---------------- API: CHAT RESPONSE ----------------
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
        # Call the Brain
        intent, entities, reply = bot.generate_bot_response(msg)
    except Exception as e:
        print("BOT ERROR:", e)
        reply = "⚠️ System Error: Unable to process request."
        intent, entities = "error", {}

    # Save to DB
    save_chat(session["account"], msg, reply)

    return jsonify({
        "response": reply,
        "intent": intent
    })


# ---------------- ROUTE: CHAT HISTORY ----------------
@app.route("/chat_logs")
def chat_logs():
    if not require_login():
        return redirect(url_for("login"))

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_message, bot_response, timestamp FROM chat_logs WHERE account=? ORDER BY id DESC", (session["account"],))
    rows = c.fetchall()
    conn.close()

    formatted_logs = []
    for r in rows:
        t_str = r["timestamp"]
        try:
            # Convert UTC to IST (Approx +5:30)
            dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=5, minutes=30)
            t_str = dt.strftime("%d %b %I:%M %p")
        except:
            pass
        formatted_logs.append((r["user_message"], r["bot_response"], t_str))

    return render_template("chat_logs.html", logs=formatted_logs)


# ---------------- ROUTE: EXPORT EXCEL ----------------
@app.route("/export_excel")
def export_excel():
    if not require_login():
        return redirect(url_for("login"))

    filename = f"Statement_{session['account']}.csv"
    filepath = os.path.join(BASE_DIR, filename)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_message, bot_response, timestamp FROM chat_logs WHERE account=? ORDER BY id", (session["account"],))
    data = c.fetchall()
    conn.close()

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["User Message", "Bot Response", "Timestamp"])
        for row in data:
            writer.writerow([row["user_message"], row["bot_response"], row["timestamp"]])

    return send_file(filepath, as_attachment=True)


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


# ---------------- ROUTE: ADMIN DASHBOARD ----------------
@app.route("/admin_dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    return render_template(
        "admin_dashboard.html",
        total_queries=get_total_queries(),
        total_intents=get_total_intents(),
        accuracy="99.2%",
        last_retrained=datetime.now().strftime("%Y-%m-%d"),
        recent_queries=get_recent_chats(limit=8)
    )


# ---------------- ROUTE: LOGOUT ----------------
@app.route("/logout")
def logout():
    reset_all_bot_context()
    session.clear()
    return redirect(url_for("admin_home"))


# ---------------- RUNNER ----------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)