import pandas as pd
import re
import random
import string
import sqlite3
import os
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

# ✅ FIXED IMPORT: Ensure these exist in bank_db.py
from bank_db import get_balance, update_balance, get_user_by_account, record_transaction

# ========= Database & Config =========
DB_SOURCE = "bank.db"
TRAINING_FILE = "bankbot_final_expanded1.csv"
MIN_CONFIDENCE = 0.55
BASE_INTEREST_RATE = 0.085


# ========= MILESTONE 4: Dynamic AI Brain =========
class BankBotBrain:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2), stop_words="english", max_features=18000
        )
        self.classifier = LogisticRegression(max_iter=2500)
        self.kb_df = None
        self.is_trained = False
        # Train immediately on startup
        self.train_model()

    def train_model(self):
        """Dynamic Retraining: Reloads CSV and updates the model in-memory."""
        try:
            if not os.path.exists(TRAINING_FILE):
                print(f"⚠️ Training file '{TRAINING_FILE}' not found.")
                return False, "File not found."

            # Load Data
            self.kb_df = pd.read_csv(TRAINING_FILE, encoding="latin1")

            # Clean and Train
            self.kb_df.dropna(subset=["text", "intent"], inplace=True)
            X = self.kb_df["text"].astype(str)
            y = self.kb_df["intent"].astype(str)

            # Fit Model
            self.vectorizer.fit(X)
            X_vec = self.vectorizer.transform(X)
            self.classifier.fit(X_vec, y)

            self.is_trained = True
            print("✅ AI Model Retrained Successfully.")
            return True, "Model retrained."
        except Exception as e:
            print(f"❌ Training Error: {e}")
            return False, str(e)

    def predict(self, text):
        """Returns (intent, confidence, response_text)"""
        if not self.is_trained:
            return "fallback", 0.0, None

        try:
            # Vectorize and Predict
            text_vec = self.vectorizer.transform([text])
            probs = self.classifier.predict_proba(text_vec)[0]
            max_conf = max(probs)
            intent = self.classifier.classes_[probs.argmax()]

            # Fetch Response from CSV Data (Random sample for variety)
            intent_rows = self.kb_df[self.kb_df["intent"] == intent]
            if not intent_rows.empty:
                response = intent_rows.sample(1).iloc[0]["response"]
            else:
                response = "I understood that, but have no response prepared."

            return intent, max_conf, response
        except Exception as e:
            print(f"Prediction Error: {e}")
            return "fallback", 0.0, None


# Initialize Global Brain
bot_brain = BankBotBrain()


# ========= MILESTONE 4: FAQ Database Lookup =========
def query_faq_db(text):
    """Checks SQLite 'faqs' table for matches."""
    try:
        conn = sqlite3.connect(DB_SOURCE)
        cursor = conn.cursor()
        # Simple wildcard search
        cursor.execute(
            "SELECT answer FROM faqs WHERE ? LIKE '%' || question || '%' LIMIT 1",
            (text,),
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    except:
        return None


# ========= Utilities =========
def clean_text(input_str):
    return input_str.strip().lower()


def generate_txn_id():
    return "BOT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def format_currency(amount):
    return f"₹{amount:,}"


def mask_sensitive_id(id_str):
    cleaned = re.sub(r"\D", "", id_str)
    if len(cleaned) >= 4:
        return "X" * (len(cleaned) - 4) + " " + cleaned[-4:]
    return "****"


def compute_emi(principal, rate, tenure):
    if tenure <= 0:
        raise ValueError("Tenure must be positive")
    return (principal * rate * (1 + rate) ** tenure) / ((1 + rate) ** tenure - 1)


# ========= Entity Parser (NER) =========
def ner_parser(raw_text):
    entities = {}
    clean_txt = raw_text.strip()
    match_last4 = re.fullmatch(r"\s*(\d{4})\s*", clean_txt)
    if match_last4:
        entities["last4"] = match_last4.group(1)
    match_acc = re.search(r"\b\d{6,16}\b", clean_txt)
    if match_acc:
        entities["account_number"] = match_acc.group()
    match_money = re.search(
        r"(?:₹\s?|rs\.?\s?|inr\s?|\b)(\d{1,12}(?:,\d{3})*(?:\.\d{1,2})?)",
        clean_txt,
        re.I,
    )
    if match_money:
        entities["amount_str"] = match_money.group(1).replace(",", "")
    else:
        match_num = re.fullmatch(r"\s*([0-9]{2,12})\s*", clean_txt)
        if match_num:
            entities["amount_str"] = match_num.group(1)
    if re.search(r"\bupi\b", clean_txt, re.I):
        entities["mode"] = "UPI"
    elif re.search(r"\b(bank transfer|neft|imps|rtgs)\b", clean_txt, re.I):
        entities["mode"] = "Bank Transfer"
    match_name = re.search(
        r"(?:to|pay|send|transfer to)\s+([A-Za-z][A-Za-z.\' \-]{1,40})", clean_txt, re.I
    )
    if match_name:
        entities["receiver"] = match_name.group(1).strip().title()
    return entities


# ========= Session Context =========
session_context = {
    "active_menu": None,
    "cards": {
        "variant": None,
        "task": None,
        "stage": 0,
        "card_last4": None,
        "bill_amt": None,
    },
    "atm": {"is_active": False, "task": None, "card_last4": None},
    "lending": {
        "category": None,
        "product_type": None,
        "action": None,
        "metrics": {
            "age": None,
            "income": None,
            "employment": None,
            "work_exp": None,
            "score": None,
            "is_eligible": None,
            "approved_limit": None,
        },
        "application": {
            "applicant_name": None,
            "salary_verified": None,
            "tax_id": None,
            "biz_name": None,
            "gst_id": None,
            "docs_uploaded": False,
        },
        "awaiting_submission": False,
        "stage": 0,
    },
    "onboarding": {
        "stage": 0,
        "full_name": None,
        "age": None,
        "acct_type": None,
        "address": None,
        "govt_id": None,
    },
    "txn_flow": None,
    "txn_step": 0,
    "txn_receiver": None,
    "txn_acct": None,
    "txn_amt": None,
    "prev_intent": None,
}


# --- Reset Functions ---
def reset_cards():
    session_context["cards"] = {
        "variant": None,
        "task": None,
        "stage": 0,
        "card_last4": None,
        "bill_amt": None,
    }


def reset_atm():
    session_context["atm"] = {"is_active": False, "task": None, "card_last4": None}


def reset_lending():
    session_context["lending"] = {
        "category": None,
        "product_type": None,
        "action": None,
        "stage": 0,
        "metrics": {
            "age": None,
            "income": None,
            "employment": None,
            "work_exp": None,
            "score": None,
            "is_eligible": None,
            "approved_limit": None,
        },
        "application": {
            "applicant_name": None,
            "salary_verified": None,
            "tax_id": None,
            "biz_name": None,
            "gst_id": None,
            "docs_uploaded": False,
        },
        "awaiting_submission": False,
    }


def reset_onboarding():
    session_context["onboarding"] = {
        "stage": 0,
        "full_name": None,
        "age": None,
        "acct_type": None,
        "address": None,
        "govt_id": None,
    }


def clear_txn_flow():
    for k in ["txn_flow", "txn_step", "txn_receiver", "txn_acct", "txn_amt"]:
        session_context[k] = None if k != "txn_step" else 0


# --- Helper Logic ---
def check_yes(text):
    return bool(re.search(r"\b(yes|y|sure|continue|proceed)\b", clean_text(text)))


def check_debit_intent(text):
    return (
        bool(re.search(r"\bdebit( card| service)?\b", clean_text(text)))
        or text.strip() == "1"
    )


def check_credit_intent(text):
    return (
        bool(re.search(r"\bcredit( card| service)?\b", clean_text(text)))
        or text.strip() == "2"
    )


def is_valid_selection(val, min_opt, max_opt):
    return val.strip().isdigit() and min_opt <= int(val.strip()) <= max_opt


# ========= UI Menus =========
MENU_CARD_TYPE = "Select Service Type:\n1) Debit Services\n2) Credit Services"
MENU_DEBIT = "Debit Services Options:\n1) Block Access\n2) Unblock Access\n3) View Status\n4) Request New Card\n5) Report Theft/Loss"
MENU_CREDIT = "Credit Services Options:\n1) Block Access\n2) Unblock Access\n3) View Status & Limit\n4) Application for New Card\n5) View Statement\n6) Bill Settlement"
MENU_ATM = "ATM Network Services:\n1) ATM Locator\n2) Withdrawal Limits\n3) Dispute Cash Withdrawal\n4) Card Retained by Machine\n5) PIN Management"
MENU_LOAN_MAIN = (
    "Bank of Trust Lending:\n1) Secured Loans\n2) Unsecured Loans\n3) Commercial Loans"
)
MENU_SECURED = "Secured Products:\n1) Home Loan\n2) Auto Loan\n3) Property Loan (LAP)\n4) Gold Loan\n5) FD Overdraft"
MENU_UNSECURED = "Unsecured Products:\n1) Personal Loan\n2) Education Loan\n3) Credit Line\n4) Debt Consolidation"
MENU_BIZ = "Commercial Products:\n1) Term Loan\n2) Working Capital\n3) Equipment Finance\n4) Invoice Discounting\n5) Business OD"
MENU_LOAN_ACTIONS = (
    "Select Action:\n1) Check Eligibility\n2) Apply Now\n3) Application Status"
)
DOCS_SECURED_LIST = "📄 Required Documents (Secured):\n• ID Proof\n• Address Verification\n• 3 Months Salary Slips\n• 6 Months Bank Statement\n• Asset Proof"
DOCS_UNSECURED_LIST = "📄 Required Documents (Unsecured):\n• ID Proof\n• Salary Slips / ITR\n• 6 Months Bank Statement\n• Credit Score"
DOCS_BIZ_LIST = "📄 Required Documents (Commercial):\n• Business KYC\n• Promoter KYC\n• 2 Years Financials\n• 12 Months Bank Statement"


# ========= Core Logic Controller =========
def generate_bot_response(user_input):
    """
    Returns: (intent, entities, response_text, confidence_score)
    """
    raw_input = user_input.strip()
    clean_input = clean_text(raw_input)
    entities = ner_parser(raw_input)

    # --- Loan Application Handover ---
    if session_context.get("lending", {}).get("awaiting_submission"):
        if "apply" in clean_input:
            session_context["lending"]["awaiting_submission"] = False
            session_context["lending"]["action"] = "apply"
            session_context["lending"]["stage"] = 10
            return (
                "loan_apply_start",
                {},
                "Starting application. Please enter your full legal name.",
                1.0,
            )

        if clean_input in ["no", "not now", "later", "cancel"]:
            reset_lending()
            session_context["active_menu"] = None
            return (
                "reject",
                {},
                "Understood. Bank of Trust is here when you are ready.",
                1.0,
            )

        return (
            "loan_eligibility_result",
            {},
            "Type 'apply' to proceed with your application.",
            1.0,
        )

    # Prevent menu numbers interfering with amounts
    if session_context.get("active_menu") and raw_input in [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    ]:
        entities.pop("amount_str", None)

    # ===== Greetings =====
    if re.search(r"\b(hi|hello|hey|greetings)\b", clean_input):
        return (
            "greet",
            entities,
            "Welcome to Bank of Trust (BOT). How may I assist you today?",
            1.0,
        )

    # ===== Service Routing (Cards) =====
    if clean_input in ["card", "cards", "services"]:
        session_context["active_menu"] = "cards"
        reset_cards()
        return "card_menu", entities, MENU_CARD_TYPE, 1.0

    is_global_context = session_context.get("active_menu") is None
    is_card_context = session_context.get("active_menu") == "cards"

    if check_debit_intent(clean_input) and (is_global_context or is_card_context):
        session_context["active_menu"] = "cards"
        reset_cards()
        session_context["cards"]["variant"] = "debit"
        return "debit_menu", entities, MENU_DEBIT, 1.0

    if check_credit_intent(clean_input) and (is_global_context or is_card_context):
        session_context["active_menu"] = "cards"
        reset_cards()
        session_context["cards"]["variant"] = "credit"
        return "credit_menu", entities, MENU_CREDIT, 1.0

    # ===== Balance Check =====
    if re.search(r"\b(balance|funds|check balance)\b", clean_input):
        session_context["prev_intent"] = "balance"
        return (
            "balance_enquiry",
            entities,
            "Please verify your account number to view balance.",
            1.0,
        )

    if session_context.get("prev_intent") == "balance" and re.fullmatch(
        r"\d{6,16}", raw_input
    ):
        session_context["prev_intent"] = None
        bal = get_balance(raw_input)
        if bal is None:
            return (
                "check_balance",
                {},
                "❌ Account not found in Bank of Trust records.",
                1.0,
            )
        return (
            "check_balance",
            {"account": raw_input},
            f"Account {raw_input}: Available Balance is {format_currency(bal)}.",
            1.0,
        )

    # ================= TRANSACTION LOGIC =================
    if (
        re.search(r"\b(pay|transfer|send)\b", clean_input)
        and session_context.get("txn_flow") != "transfer"
    ):
        session_context["txn_flow"] = "transfer"
        session_context["txn_step"] = 1
        return "transfer_money", {}, "Who is the recipient of these funds?", 1.0

    if session_context.get("txn_flow") == "transfer":
        step = session_context["txn_step"]
        if step == 1:
            session_context["txn_receiver"] = raw_input.strip().title()
            session_context["txn_step"] = 2
            return (
                "transfer_money",
                {},
                f"Please enter the account number for {session_context['txn_receiver']}.",
                1.0,
            )
        if step == 2:
            acc_clean = re.sub(r"\D", "", raw_input)
            if not re.fullmatch(r"\d{6,16}", acc_clean):
                return (
                    "transfer_money",
                    {},
                    "Invalid format. Please enter a valid 6-16 digit account number.",
                    1.0,
                )
            session_context["txn_acct"] = acc_clean
            session_context["txn_step"] = 3
            return "transfer_money", {}, "Enter the amount to transfer.", 1.0
        if step == 3:
            amt_match = re.search(r"\d+", raw_input.replace(",", ""))
            if not amt_match:
                return "transfer_money", {}, "Please enter a numeric amount.", 1.0
            session_context["txn_amt"] = int(amt_match.group())
            session_context["txn_step"] = 4
            return (
                "transfer_money",
                {},
                f"Select Method for {format_currency(session_context['txn_amt'])} transfer: UPI or Bank Transfer?",
                1.0,
            )
        if step == 4:
            if "upi" in clean_input:
                mode = "UPI"
            elif any(x in clean_input for x in ["bank", "neft", "rtgs", "imps"]):
                mode = "Bank Transfer"
            else:
                return (
                    "transfer_money",
                    {},
                    "Invalid mode. Type 'UPI' or 'Bank Transfer'.",
                    1.0,
                )

            sender = session_context.get(
                "current_user_account"
            )  # Must be set by app.py
            receiver_acct = session_context.get("txn_acct")
            receiver_name = session_context.get("txn_receiver")
            amt = session_context.get("txn_amt")

            if sender == receiver_acct:
                session_context["txn_flow"] = None
                return "transfer_money", {}, "❌ Self-transfer is not permitted.", 1.0

            curr_bal = get_balance(sender)
            if curr_bal is None or curr_bal < amt:
                session_context["txn_flow"] = None
                return (
                    "transfer_money",
                    {},
                    "❌ Transaction Failed: Insufficient Funds.",
                    1.0,
                )

            update_balance(sender, curr_bal - amt)
            rec_user = get_user_by_account(receiver_acct)
            if rec_user:
                update_balance(receiver_acct, rec_user["balance"] + amt)
            record_transaction(
                sender, receiver_acct, receiver_name, amt, mode, "Success"
            )

            tid = generate_txn_id()
            session_context["txn_flow"] = None
            return (
                "transfer_money",
                {},
                f"✅ Payment Successful.\nSent: {format_currency(amt)}\nTo: {receiver_name}\nRef ID: {tid}",
                1.0,
            )

    # ================= CARD SERVICES =================
    if session_context.get("active_menu") == "cards":
        card_ctx = session_context["cards"]
        if card_ctx["variant"] is None:
            if check_debit_intent(raw_input):
                card_ctx["variant"] = "debit"
                return "debit_menu", {}, MENU_DEBIT, 1.0
            if check_credit_intent(raw_input):
                card_ctx["variant"] = "credit"
                return "credit_menu", {}, MENU_CREDIT, 1.0
            return (
                "card_menu",
                {},
                "Please select 1 (Debit Services) or 2 (Credit Services).",
                1.0,
            )

        if card_ctx["variant"] == "debit":
            if card_ctx["task"] is None:
                if is_valid_selection(raw_input, 1, 5):
                    mapping = {
                        "1": "block",
                        "2": "unblock",
                        "3": "status",
                        "4": "apply",
                        "5": "report",
                    }
                    card_ctx["task"] = mapping[raw_input.strip()]
                else:
                    txt = clean_input
                    if "block" in txt and "un" not in txt:
                        card_ctx["task"] = "block"
                    elif "unblock" in txt:
                        card_ctx["task"] = "unblock"
                    elif "status" in txt:
                        card_ctx["task"] = "status"
                    elif "apply" in txt:
                        card_ctx["task"] = "apply"
                    elif "report" in txt or "lost" in txt:
                        card_ctx["task"] = "report"

                if not card_ctx["task"]:
                    return "debit_menu", {}, MENU_DEBIT, 1.0

            if card_ctx["task"] == "apply":
                reset_cards()
                session_context["active_menu"] = None
                return (
                    "debit_apply",
                    {},
                    "New Debit Service request logged. Dispatched in 7 days.",
                    1.0,
                )

            if not card_ctx["card_last4"]:
                if "last4" in entities and re.fullmatch(r"\d{4}", entities["last4"]):
                    card_ctx["card_last4"] = entities["last4"]
                else:
                    return (
                        "ask_last4",
                        {},
                        "Security Check: Enter the last 4 digits of your debit card.",
                        1.0,
                    )

            l4 = card_ctx["card_last4"]
            act = card_ctx["task"]
            reset_cards()
            session_context["active_menu"] = None
            if act == "block":
                return (
                    "card_blocked",
                    {},
                    f"Debit Card ending in {l4} has been BLOCKED.",
                    1.0,
                )
            if act == "unblock":
                return (
                    "card_unblocked",
                    {},
                    f"Debit Card ending in {l4} is now ACTIVE.",
                    1.0,
                )
            if act == "status":
                return (
                    "card_status",
                    {},
                    f"Debit Card {l4} status: ACTIVE / OPERATIONAL.",
                    1.0,
                )
            if act == "report":
                return (
                    "card_report",
                    {},
                    f"Card {l4} reported LOST. Permanent block applied.",
                    1.0,
                )

        if card_ctx["variant"] == "credit":
            if card_ctx["task"] is None:
                if is_valid_selection(raw_input, 1, 6):
                    mapping = {
                        "1": "block",
                        "2": "unblock",
                        "3": "status",
                        "4": "apply",
                        "5": "bill",
                        "6": "pay",
                    }
                    card_ctx["task"] = mapping[raw_input.strip()]
                else:
                    txt = clean_input
                    if "block" in txt and "un" not in txt:
                        card_ctx["task"] = "block"
                    elif "unblock" in txt:
                        card_ctx["task"] = "unblock"
                    elif "status" in txt:
                        card_ctx["task"] = "status"
                    elif "apply" in txt:
                        card_ctx["task"] = "apply"
                    elif "pay" in txt:
                        card_ctx["task"] = "pay"
                    elif "bill" in txt:
                        card_ctx["task"] = "bill"

                if not card_ctx["task"]:
                    return "credit_menu", {}, MENU_CREDIT, 1.0

            if card_ctx["task"] == "apply":
                reset_cards()
                session_context["active_menu"] = None
                return (
                    "credit_apply",
                    {},
                    "Credit Service application initialized.",
                    1.0,
                )

            if not card_ctx["card_last4"]:
                if "last4" in entities and re.fullmatch(r"\d{4}", entities["last4"]):
                    card_ctx["card_last4"] = entities["last4"]
                else:
                    return (
                        "ask_last4",
                        {},
                        "Security Check: Enter the last 4 digits of your credit card.",
                        1.0,
                    )

            if card_ctx["task"] == "pay" and not card_ctx["bill_amt"]:
                if "amount_str" in entities:
                    card_ctx["bill_amt"] = entities["amount_str"]
                else:
                    return "ask_amount", {}, "Enter payment amount.", 1.0

            l4 = card_ctx["card_last4"]
            act = card_ctx["task"]
            reset_cards()
            session_context["active_menu"] = None
            if act == "block":
                return "card_blocked", {}, f"Credit Card {l4} blocked.", 1.0
            if act == "unblock":
                return "card_unblocked", {}, f"Credit Card {l4} access restored.", 1.0
            if act == "status":
                return "card_status", {}, f"Credit Card {l4} is Active.", 1.0
            if act == "bill":
                return "bill_check", {}, f"Outstanding for {l4}: ₹12,450.", 1.0
            if act == "pay":
                return (
                    "bill_pay",
                    {},
                    f"Payment of ₹{card_ctx['bill_amt']} acknowledged.",
                    1.0,
                )

    # ================= ATM LOGIC =================
    if clean_input in ["atm", "atms"]:
        session_context["active_menu"] = "atm"
        reset_atm()
        return "atm_menu", {}, MENU_ATM, 1.0

    if session_context.get("active_menu") == "atm":
        atm_ctx = session_context["atm"]
        if not atm_ctx["task"]:
            if is_valid_selection(raw_input, 1, 5):
                mapping = {
                    "1": "locator",
                    "2": "limit",
                    "3": "issue",
                    "4": "retained",
                    "5": "pin",
                }
                atm_ctx["task"] = mapping[raw_input.strip()]
            else:
                txt = clean_input
                if "locat" in txt or "near" in txt:
                    atm_ctx["task"] = "locator"
                elif "limit" in txt:
                    atm_ctx["task"] = "limit"
                elif "issue" in txt or "dispense" in txt:
                    atm_ctx["task"] = "issue"
                elif "retained" in txt or "stuck" in txt:
                    atm_ctx["task"] = "retained"
                elif "pin" in txt:
                    atm_ctx["task"] = "pin"
            if not atm_ctx["task"]:
                return "atm_menu", {}, MENU_ATM, 1.0

        if atm_ctx["task"] == "locator":
            reset_atm()
            session_context["active_menu"] = None
            return (
                "atm_loc",
                {},
                "Sharing nearest Bank of Trust ATM locations based on your IP...",
                1.0,
            )

        if not atm_ctx["card_last4"]:
            if "last4" in entities and re.fullmatch(r"\d{4}", entities["last4"]):
                atm_ctx["card_last4"] = entities["last4"]
            else:
                return (
                    "ask_last4",
                    {},
                    "Please enter last 4 digits of the card used.",
                    1.0,
                )

        task = atm_ctx["task"]
        reset_atm()
        session_context["active_menu"] = None
        if task == "limit":
            return "atm_info", {}, "Daily Withdrawal Limit: ₹40,000.", 1.0
        if task == "issue":
            return "atm_ticket", {}, "Dispute ticket raised #ATM9921.", 1.0
        if task == "retained":
            return "atm_alert", {}, "Card retention logged. Visit branch.", 1.0
        if task == "pin":
            return (
                "atm_pin",
                {},
                "Please use the Bank of Trust Mobile App to reset PIN.",
                1.0,
            )

    # ================= LENDING (LOANS) =================
    if clean_input in ["loan", "loans", "lending"]:
        session_context["active_menu"] = "lending"
        reset_lending()
        return "loan_menu", {}, MENU_LOAN_MAIN, 1.0

    if session_context.get("active_menu") == "lending":
        lend_ctx = session_context["lending"]

        # 1. Category
        if not lend_ctx["category"]:
            if "1" in raw_input or "secure" in clean_input:
                lend_ctx["category"] = "secured"
                return "loan_prod", {}, MENU_SECURED, 1.0
            if "2" in raw_input or "unsecure" in clean_input:
                lend_ctx["category"] = "unsecured"
                return "loan_prod", {}, MENU_UNSECURED, 1.0
            if "3" in raw_input or "biz" in clean_input:
                lend_ctx["category"] = "commercial"
                return "loan_prod", {}, MENU_BIZ, 1.0
            return "loan_menu", {}, MENU_LOAN_MAIN, 1.0

        # 2. Product
        if not lend_ctx["product_type"]:
            txt = clean_input
            prod = None
            cat = lend_ctx["category"]
            if cat == "secured":
                if "1" in raw_input or "home" in txt:
                    prod = "home"
                elif "2" in raw_input or "auto" in txt:
                    prod = "auto"
                elif "3" in raw_input or "property" in txt:
                    prod = "lap"
                elif "4" in raw_input or "gold" in txt:
                    prod = "gold"
                elif "5" in raw_input or "fd" in txt:
                    prod = "fd"
            elif cat == "unsecured":
                if "1" in raw_input or "personal" in txt:
                    prod = "personal"
                elif "2" in raw_input or "education" in txt:
                    prod = "education"
                elif "3" in raw_input or "credit" in txt:
                    prod = "creditline"
                elif "4" in raw_input or "debt" in txt:
                    prod = "consolidation"
            elif cat == "commercial":
                if "1" in raw_input or "term" in txt:
                    prod = "term"
                elif "2" in raw_input or "working" in txt:
                    prod = "workingcap"
                elif "3" in raw_input or "equip" in txt:
                    prod = "equipment"
                elif "4" in raw_input or "invoice" in txt:
                    prod = "invoice"
                elif "5" in raw_input or "od" in txt:
                    prod = "biz_od"

            if prod:
                lend_ctx["product_type"] = prod
                return "loan_act", {}, MENU_LOAN_ACTIONS, 1.0
            return "loan_prod", {}, "Please select a valid product number.", 1.0

        # 3. Action
        if not lend_ctx["action"]:
            if "1" in raw_input or "eligib" in clean_input:
                lend_ctx["action"] = "check_eligibility"
                lend_ctx["stage"] = 1
                return (
                    "elig_start",
                    {},
                    "Let's check your eligibility. Enter your age.",
                    1.0,
                )
            if "2" in raw_input or "apply" in clean_input:
                lend_ctx["action"] = "check_eligibility"
                lend_ctx["stage"] = 1
                return (
                    "elig_force",
                    {},
                    "We need to verify eligibility first. Enter your age.",
                    1.0,
                )
            if "3" in raw_input or "status" in clean_input:
                reset_lending()
                session_context["active_menu"] = None
                return (
                    "loan_stat",
                    {},
                    "Please check the 'My Applications' section in your dashboard.",
                    1.0,
                )
            return "loan_act", {}, MENU_LOAN_ACTIONS, 1.0

        # 4. Eligibility Flow
        if lend_ctx["action"] == "check_eligibility":
            metrics = lend_ctx["metrics"]
            stg = lend_ctx["stage"]

            if stg == 1:  # Age
                if not raw_input.isdigit():
                    return "elig_age", {}, "Enter numeric age.", 1.0
                metrics["age"] = int(raw_input)
                if metrics["age"] < 18:
                    reset_lending()
                    session_context["active_menu"] = None
                    return "elig_fail", {}, "Minimum age is 18 years.", 1.0
                lend_ctx["stage"] = 2
                return "elig_inc", {}, "Enter monthly income (₹).", 1.0

            if stg == 2:  # Income
                inc_match = re.search(r"\d+", raw_input.replace(",", ""))
                if not inc_match:
                    return "elig_inc", {}, "Enter numeric income.", 1.0
                metrics["income"] = int(inc_match.group())
                if metrics["income"] < 15000:
                    reset_lending()
                    session_context["active_menu"] = None
                    return (
                        "elig_fail",
                        {},
                        f"Minimum income requirement is ₹15,000.",
                        1.0,
                    )
                lend_ctx["stage"] = 3
                return "elig_cibil", {}, "Enter Credit Score (300-900).", 1.0

            if stg == 3:  # Score
                if not raw_input.isdigit():
                    return "elig_cibil", {}, "Enter valid score.", 1.0
                metrics["score"] = int(raw_input)
                if metrics["score"] < 700:
                    reset_lending()
                    session_context["active_menu"] = None
                    return "elig_fail", {}, "Score is below the 700 threshold.", 1.0

                limit = metrics["income"] * 20
                metrics["approved_limit"] = limit
                lend_ctx["awaiting_submission"] = True
                return (
                    "elig_success",
                    {},
                    f"✅ Eligibility Confirmed.\nMax Limit: {format_currency(limit)}\nType 'apply' to proceed.",
                    1.0,
                )

        # 5. Application Flow
        if lend_ctx["action"] == "apply":
            app_data = lend_ctx["application"]
            stg = lend_ctx["stage"]

            if stg == 10:
                app_data["applicant_name"] = raw_input.title()
                lend_ctx["stage"] = 11
                if lend_ctx["category"] == "commercial":
                    return "app_biz", {}, "Enter Business Name.", 1.0
                return "app_pan", {}, "Enter PAN Number.", 1.0

            if stg == 11:
                if lend_ctx["category"] == "commercial":
                    app_data["biz_name"] = raw_input
                    lend_ctx["stage"] = 12
                    return "app_gst", {}, "Enter GST Number.", 1.0
                else:
                    app_data["tax_id"] = raw_input.upper()
                    lend_ctx["stage"] = 13
                    return (
                        "app_docs",
                        {},
                        f"Upload docs. Type 'what documents' to see list, or 'done' to finish.",
                        1.0,
                    )

            if stg == 12:
                app_data["gst_id"] = raw_input.upper()
                lend_ctx["stage"] = 13
                return (
                    "app_docs",
                    {},
                    f"Upload docs. Type 'what documents' to see list, or 'done' to finish.",
                    1.0,
                )

            if stg == 13:
                if "what" in clean_input and "doc" in clean_input:
                    cat = lend_ctx["category"]
                    if cat == "secured":
                        return "doc_list", {}, DOCS_SECURED_LIST, 1.0
                    if cat == "unsecured":
                        return "doc_list", {}, DOCS_UNSECURED_LIST, 1.0
                    return "doc_list", {}, DOCS_BIZ_LIST, 1.0

                if "done" in clean_input or "upload" in clean_input:
                    ref = generate_txn_id()
                    reset_lending()
                    session_context["active_menu"] = None
                    return (
                        "app_finish",
                        {},
                        f"Application Submitted!\nRef ID: {ref}",
                        1.0,
                    )
                return "app_wait", {}, "Type 'done' once documents are uploaded.", 1.0

    # ================= ONBOARDING =================
    if "open account" in clean_input or "new account" in clean_input:
        session_context["active_menu"] = "onboarding"
        reset_onboarding()
        session_context["onboarding"]["stage"] = 1
        return "kyc_start", {}, "Welcome to Bank of Trust. Enter Full Name.", 1.0

    if session_context.get("active_menu") == "onboarding":
        ob = session_context["onboarding"]
        stg = ob["stage"]
        if stg == 1:
            ob["full_name"] = raw_input
            ob["stage"] = 2
            return "kyc_age", {}, "Enter Age.", 1.0
        if stg == 2:
            if not raw_input.isdigit():
                return "kyc_age", {}, "Numeric age only.", 1.0
            ob["age"] = int(raw_input)
            if ob["age"] < 18:
                reset_onboarding()
                session_context["active_menu"] = None
                return "kyc_fail", {}, "Must be 18+.", 1.0
            ob["stage"] = 3
            return "kyc_type", {}, "1) Savings\n2) Current", 1.0
        if stg == 3:
            ob["acct_type"] = "Savings" if "1" in raw_input else "Current"
            ob["stage"] = 4
            return "kyc_addr", {}, "Enter Address.", 1.0
        if stg == 4:
            ob["address"] = raw_input
            ob["stage"] = 5
            return "kyc_aadhaar", {}, "Enter 12-digit Aadhaar.", 1.0
        if stg == 5:
            if not re.search(r"\d{12}", raw_input):
                return "kyc_aadhaar", {}, "Invalid format.", 1.0
            ob["govt_id"] = raw_input
            reset_onboarding()
            session_context["active_menu"] = None
            return "kyc_done", {}, "Request Received. Welcome.", 1.0

    # ================= GENERAL FALLBACKS =================
    if "emi" in clean_input:
        return (
            "emi_calc",
            {},
            "EMI Calculator: Enter 'LoanAmount Years' (e.g., 500000 5).",
            1.0,
        )

    emi_match = re.search(r"(\d+)\s+(\d+)", raw_input)
    if emi_match:
        try:
            P = float(emi_match.group(1))
            N = int(emi_match.group(2)) * 12
            val = compute_emi(P, BASE_INTEREST_RATE / 12, N)
            return (
                "emi_res",
                {},
                f"Estimated EMI: {format_currency(int(val))}/month.",
                1.0,
            )
        except:
            pass

    # ================= MILESTONE 4: AI & KNOWLEDGE BASE =================

    # 1. Try AI Prediction
    intent, conf, response = bot_brain.predict(raw_input)

    if conf >= MIN_CONFIDENCE:
        return intent, entities, response, conf

    # 2. Try Knowledge Base (FAQ)
    faq_response = query_faq_db(clean_input)
    if faq_response:
        return (
            "faq_match",
            entities,
            faq_response,
            1.0,
        )  # High confidence for exact match

    # 3. Chitchat & Fallback
    if any(x in clean_input for x in ["thank", "thx"]):
        return (
            "thanks",
            {},
            "You're welcome! Thank you for choosing Bank of Trust.",
            1.0,
        )
    if any(x in clean_input for x in ["bye", "exit"]):
        return "goodbye", {}, "Goodbye. Secure Banking with Bank of Trust.", 1.0

    return "fallback", entities, "I didn't quite catch that. Could you rephrase?", 0.0


# ========= CLI Execution (Debug) =========
def run_cli():
    print("Bank of Trust (BOT) Terminal Interface.\n")
    session_context["current_user_account"] = "100001"
    print("DEBUG: Auto-logged in as Account 100001 for CLI testing.\n")
    while True:
        try:
            msg = input("You: ")
            if msg.lower() in ["exit", "quit"]:
                break
            # Note: Unpacking 4 values now
            intent, ents, resp, conf = generate_bot_response(msg)
            print(f"BOT: {resp}\n[Debug: {intent} | Conf: {conf:.2f}]\n")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    run_cli()
