import pandas as pd
import re
import random
import string
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
import sqlite3

# Database Configuration
DB_SOURCE = "bank.db"

# ========= Database Helpers (Local) =========
def fetch_account_balance(acct_num):
    """Retrieves balance for a specific account."""
    try:
        conn = sqlite3.connect(DB_SOURCE)
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE account_number=?", (acct_num,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    except Exception:
        return None

def modify_account_balance(acct_num, new_amt):
    """Updates the balance after a transaction."""
    conn = sqlite3.connect(DB_SOURCE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance=? WHERE account_number=?", (new_amt, acct_num))
    conn.commit()
    conn.close()

# ========= Configuration & Constants =========
KNOWLEDGE_BASE_FILE = "bankbot_final_expanded1.csv"
MIN_CONFIDENCE = 0.55
BASE_INTEREST_RATE = 0.085  # 8.5% Base Rate

# ========= Utilities =========
def clean_text(input_str): 
    return input_str.strip().lower()

def generate_txn_id(): 
    return "BOT" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))

def format_currency(amount):
    return f"₹{amount:,}"

def mask_sensitive_id(id_str):
    cleaned = re.sub(r'\D', '', id_str)
    if len(cleaned) >= 4:
        return "X" * (len(cleaned)-4) + " " + cleaned[-4:]
    return "****"

def compute_emi(principal: float, rate: float, tenure: int):
    if tenure <= 0:
        raise ValueError("Tenure must be positive")
    return (principal * rate * (1 + rate)**tenure) / ((1 + rate)**tenure - 1)

# ========= Entity Parser (NER) =========
def ner_parser(raw_text):
    """Extracts financial entities from user text."""
    entities = {}
    clean_txt = raw_text.strip()

    # Detect Last 4 Digits
    match_last4 = re.fullmatch(r'\s*(\d{4})\s*', clean_txt)
    if match_last4:
        entities['last4'] = match_last4.group(1)

    # Detect Full Account Number (6-16 digits)
    match_acc = re.search(r'\b\d{6,16}\b', clean_txt)
    if match_acc:
        entities['account_number'] = match_acc.group()

    # Detect Monetary Values
    match_money = re.search(r'(?:₹\s?|rs\.?\s?|inr\s?|\b)(\d{1,12}(?:,\d{3})*(?:\.\d{1,2})?)', clean_txt, re.I)
    if match_money:
        entities['amount_str'] = match_money.group(1).replace(',', '')
    else:
        # Fallback for bare numbers if context implies money
        match_num = re.fullmatch(r'\s*([0-9]{2,12})\s*', clean_txt)
        if match_num:
            entities['amount_str'] = match_num.group(1)

    # Detect Payment Mode
    if re.search(r'\bupi\b', clean_txt, re.I):
        entities['mode'] = 'UPI'
    elif re.search(r'\b(bank transfer|neft|imps|rtgs)\b', clean_txt, re.I):
        entities['mode'] = 'Bank Transfer'

    # Detect Receiver Name
    match_name = re.search(r'(?:to|pay|send|transfer to)\s+([A-Za-z][A-Za-z.\' \-]{1,40})', clean_txt, re.I)
    if match_name:
        entities['receiver'] = match_name.group(1).strip().title()

    return entities

# ========= AI Model Initialization =========
try:
    kb_df = pd.read_csv(KNOWLEDGE_BASE_FILE, encoding='latin1')
    kb_loaded = True
except FileNotFoundError:
    kb_df = pd.DataFrame(columns=["text", "intent", "response"])
    kb_loaded = False

nlu_engine = None
if kb_loaded and all(c in kb_df.columns for c in ["text", "intent", "response"]):
    X_train = kb_df["text"].astype(str)
    y_train = kb_df["intent"].astype(str)
    nlu_engine = make_pipeline(
        TfidfVectorizer(ngram_range=(1,2), stop_words='english', max_features=18000),
        LogisticRegression(max_iter=2500)
    )
    nlu_engine.fit(X_train, y_train)

def fetch_kb_reply(intent_label, user_query):
    if not kb_loaded:
        return None
    
    # Filter by intent
    intent_data = kb_df[kb_df["intent"] == intent_label]
    if intent_data.empty:
        return None
    
    # Check for exact match
    exact_match = intent_data[intent_data["text"].str.strip().str.lower() == user_query.strip().lower()]
    if not exact_match.empty:
        return exact_match.iloc[0]["response"]
    
    # Return random variation
    return intent_data.sample(1, random_state=random.randint(1, 10000)).iloc[0]["response"]

# ========= Session Context (State Machine) =========
session_context = {
    # Main Router
    "active_menu": None,  # 'cards', 'atm', 'lending', 'onboarding'
    
    # Card Management
    "cards": {
        "variant": None,    # 'debit'|'credit'
        "task": None,       # 'block','unblock','status','apply','report','bill','pay'
        "stage": 0,
        "card_last4": None,
        "bill_amt": None
    },
    
    # ATM Services
    "atm": {
        "is_active": False,
        "task": None,
        "card_last4": None
    },
    
    # Lending / Loans
    "lending": {
        "category": None,   # 'secured','unsecured','commercial'
        "product_type": None, 
        "action": None,     # 'check_eligibility','apply','status'
        
        # Eligibility Data
        "metrics": { 
            "age": None, "income": None, "employment": None, 
            "work_exp": None, "score": None, "is_eligible": None, 
            "approved_limit": None 
        },
        
        # Application Data
        "application": { 
            "applicant_name": None, "salary_verified": None, 
            "tax_id": None, "biz_name": None, "gst_id": None, 
            "docs_uploaded": False 
        },
        
        "awaiting_submission": False,
        "stage": 0
    },
    
    # Onboarding
    "onboarding": {
        "stage": 0,
        "full_name": None, "age": None, "acct_type": None, 
        "address": None, "govt_id": None
    },
    
    # Funds Transfer
    "txn_flow": None, "txn_step": 0, "txn_receiver": None, 
    "txn_acct": None, "txn_amt": None,
    "prev_intent": None
}

# --- Reset Functions ---
def reset_cards():
    session_context["cards"] = {"variant":None, "task":None, "stage":0, "card_last4":None, "bill_amt":None}

def reset_atm():
    session_context["atm"] = {"is_active":False, "task":None, "card_last4":None}

def reset_lending():
    session_context["lending"] = {
        "category": None, "product_type": None, "action": None, "stage": 0,
        "metrics": { 
            "age":None, "income":None, "employment":None, "work_exp":None, 
            "score":None, "is_eligible":None, "approved_limit":None 
        },
        "application": { 
            "applicant_name":None, "salary_verified":None, "tax_id":None, 
            "biz_name":None, "gst_id":None, "docs_uploaded":False 
        },
        "awaiting_submission": False
    }

def reset_onboarding():
    session_context["onboarding"] = {"stage":0, "full_name":None, "age":None, "acct_type":None, "address":None, "govt_id":None}

def clear_txn_flow():
    for k in ["txn_flow", "txn_step", "txn_receiver", "txn_acct", "txn_amt"]:
        session_context[k] = None if k != "txn_step" else 0

# --- Helper Logic ---
def check_yes(text): 
    return bool(re.search(r'\b(yes|y|sure|continue|proceed)\b', clean_text(text)))

def check_debit_intent(text):
    t = clean_text(text)
    return bool(re.search(r'\bdebit( card| service)?\b', t)) or text.strip()=="1"

def check_credit_intent(text):
    t = clean_text(text)
    return bool(re.search(r'\bcredit( card| service)?\b', t)) or text.strip()=="2"

def is_valid_selection(val, min_opt, max_opt):
    return val.strip().isdigit() and min_opt <= int(val.strip()) <= max_opt

# ========= UI Menus (Rebranded) =========
MENU_CARD_TYPE = "Select Service Type:\n1) Debit Services\n2) Credit Services"

MENU_DEBIT = ("Debit Services Options:\n"
              "1) Block Access\n"
              "2) Unblock Access\n"
              "3) View Status\n"
              "4) Request New Card\n"
              "5) Report Theft/Loss")

MENU_CREDIT = ("Credit Services Options:\n"
               "1) Block Access\n"
               "2) Unblock Access\n"
               "3) View Status & Limit\n"
               "4) Application for New Card\n"
               "5) View Statement\n"
               "6) Bill Settlement")

MENU_ATM = ("ATM Network Services:\n"
            "1) ATM Locator\n"
            "2) Withdrawal Limits\n"
            "3) Dispute Cash Withdrawal\n"
            "4) Card Retained by Machine\n"
            "5) PIN Management")

MENU_LOAN_MAIN = "Bank of Trust Lending:\n1) Secured Loans\n2) Unsecured Loans\n3) Commercial Loans"
MENU_SECURED = "Secured Products:\n1) Home Loan\n2) Auto Loan\n3) Property Loan (LAP)\n4) Gold Loan\n5) FD Overdraft"
MENU_UNSECURED = "Unsecured Products:\n1) Personal Loan\n2) Education Loan\n3) Credit Line\n4) Debt Consolidation"
MENU_BIZ = "Commercial Products:\n1) Term Loan\n2) Working Capital\n3) Equipment Finance\n4) Invoice Discounting\n5) Business OD"

MENU_LOAN_ACTIONS = "Select Action:\n1) Check Eligibility\n2) Apply Now\n3) Application Status"

# Documents (Rebranded)
DOCS_SECURED_LIST = (
    "📄 Required Documents (Bank of Trust Secured Loans):\n\n"
    "• ID Proof: Aadhaar / PAN / Passport\n"
    "• Address Verification: Utility Bill / Rental Agreement\n"
    "• Income: 3 Months Salary Slips or 2 Years ITR\n"
    "• Banking: 6 Months Statement\n"
    "• Asset Proof: Property Deed / Vehicle Invoice / FD Receipt\n"
)

DOCS_UNSECURED_LIST = (
    "📄 Required Documents (Bank of Trust Unsecured Loans):\n\n"
    "• ID Proof: Aadhaar / PAN\n"
    "• Income: Salary Slips / ITR\n"
    "• Banking: 6 Months Statement\n"
    "• Credit Score: Must meet Bank of Trust criteria\n"
    "• Education: Admission Letter (for Edu Loan)\n"
)

DOCS_BIZ_LIST = (
    "📄 Required Documents (Commercial Loans):\n\n"
    "• Business KYC: GST & Udyam Registration\n"
    "• Promoter KYC: Aadhaar & PAN\n"
    "• Financials: 2 Years ITR & Balance Sheet\n"
    "• Banking: 12 Months Statement\n"
    "• Invoice Copies (for Discounting)\n"
)

# ========= Core Logic Controller =========
def generate_bot_response(user_input):
    raw_input = user_input.strip()
    clean_input = clean_text(raw_input)
    entities = ner_parser(raw_input)

    # --- Loan Application Handover ---
    if session_context.get("lending", {}).get("awaiting_submission"):
        if "apply" in clean_input:
            session_context["lending"]["awaiting_submission"] = False
            session_context["lending"]["action"] = "apply"
            session_context["lending"]["stage"] = 10
            return "loan_apply_start", {}, "Starting application. Please enter your full legal name."

        if clean_input in ["no", "not now", "later", "cancel"]:
            reset_lending(); session_context["active_menu"] = None
            return "reject", {}, "Understood. Bank of Trust is here when you are ready."

        return "loan_eligibility_result", {}, "Type 'apply' to proceed with your application."

    # Prevent menu numbers interfering with amounts
    if session_context.get("active_menu") and raw_input in ["1","2","3","4","5","6"]:
        entities.pop("amount_str", None)

    # ===== Greetings =====
    if re.search(r'\b(hi|hello|hey|greetings)\b', clean_input):
        return "greet", entities, "Welcome to Bank of Trust (BOT). How may I assist you today?"

    # ===== Service Routing (Cards) =====
    if clean_input in ["card", "cards", "services"]:
        session_context["active_menu"] = "cards"; reset_cards()
        return "card_menu", entities, MENU_CARD_TYPE

    # FIX: Only allow shortcuts "1" or "2" if we are NOT in another menu (like loans/atm)
    # or if we are explicitly asking for card type selection.
    is_global_context = session_context.get("active_menu") is None
    is_card_context = session_context.get("active_menu") == "cards"

    if check_debit_intent(clean_input) and (is_global_context or is_card_context):
        session_context["active_menu"] = "cards"; reset_cards()
        session_context["cards"]["variant"] = "debit"
        return "debit_menu", entities, MENU_DEBIT

    if check_credit_intent(clean_input) and (is_global_context or is_card_context):
        session_context["active_menu"] = "cards"; reset_cards()
        session_context["cards"]["variant"] = "credit"
        return "credit_menu", entities, MENU_CREDIT

    # ===== Balance Check =====
    if re.search(r'\b(balance|funds|check balance)\b', clean_input):
        session_context["prev_intent"] = "balance"
        return "balance_enquiry", entities, "Please verify your account number to view balance."

    if session_context.get("prev_intent") == "balance" and re.fullmatch(r'\d{6,16}', raw_input):
        session_context["prev_intent"] = None
        
        # ✅ FIXED IMPORT HERE
        from bank_db import get_balance 
        
        bal = get_balance(raw_input)
        if bal is None:
            return "check_balance", {}, "❌ Account not found in Bank of Trust records."
        
        return "check_balance", {"account": raw_input}, f"Account {raw_input}: Available Balance is {format_currency(bal)}."

    # ================= TRANSACTION LOGIC =================
    if re.search(r'\b(pay|transfer|send)\b', clean_input) and session_context.get("txn_flow") != "transfer":
        session_context["txn_flow"] = "transfer"
        session_context["txn_step"] = 1
        return "transfer_money", {}, "Who is the recipient of these funds?"

    if session_context.get("txn_flow") == "transfer":
        step = session_context["txn_step"]

        # Step 1: Receiver Name
        if step == 1:
            session_context["txn_receiver"] = raw_input.strip().title()
            session_context["txn_step"] = 2
            return "transfer_money", {}, f"Please enter the account number for {session_context['txn_receiver']}."

        # Step 2: Receiver Account
        if step == 2:
            acc_clean = re.sub(r'\D', '', raw_input)
            if not re.fullmatch(r'\d{6,16}', acc_clean):
                return "transfer_money", {}, "Invalid format. Please enter a valid 6-16 digit account number."
            session_context["txn_acct"] = acc_clean
            session_context["txn_step"] = 3
            return "transfer_money", {}, "Enter the amount to transfer."

        # Step 3: Amount
        if step == 3:
            amt_match = re.search(r'\d+', raw_input.replace(",", ""))
            if not amt_match:
                return "transfer_money", {}, "Please enter a numeric amount."
            session_context["txn_amt"] = int(amt_match.group())
            session_context["txn_step"] = 4
            return "transfer_money", {}, f"Select Method for {format_currency(session_context['txn_amt'])} transfer: UPI or Bank Transfer?"

        # Step 4: Finalize
        if step == 4:
            if "upi" in clean_input:
                mode = "UPI"
            elif any(x in clean_input for x in ["bank", "neft", "rtgs", "imps"]):
                mode = "Bank Transfer"
            else:
                return "transfer_money", {}, "Invalid mode. Type 'UPI' or 'Bank Transfer'."

            # ✅ FIXED IMPORTS HERE
            from bank_db import get_balance, update_balance, get_user_by_account, record_transaction
            
            sender = session_context.get("current_user_account")
            receiver_acct = session_context.get("txn_acct")
            receiver_name = session_context.get("txn_receiver")
            amt = session_context.get("txn_amt")

            if sender == receiver_acct:
                session_context["txn_flow"] = None
                return "transfer_money", {}, "❌ Self-transfer is not permitted."

            curr_bal = get_balance(sender)
            if curr_bal is None or curr_bal < amt:
                session_context["txn_flow"] = None
                return "transfer_money", {}, "❌ Transaction Failed: Insufficient Funds."

            # Update DB
            update_balance(sender, curr_bal - amt)
            rec_user = get_user_by_account(receiver_acct)
            if rec_user:
                update_balance(receiver_acct, rec_user["balance"] + amt)

            record_transaction(sender, receiver_acct, receiver_name, amt, mode, "Success")
            
            tid = generate_txn_id()
            session_context["txn_flow"] = None
            
            return "transfer_money", {}, (
                f"✅ Payment Successful.\n"
                f"Sent: {format_currency(amt)}\n"
                f"To: {receiver_name} (A/C: {mask_sensitive_id(receiver_acct)})\n"
                f"Ref ID: {tid}"
            )

    # ================= CARD SERVICES =================
    if session_context.get("active_menu") == "cards":
        card_ctx = session_context["cards"]

        # Selection Phase
        if card_ctx["variant"] is None:
            if check_debit_intent(raw_input):
                card_ctx["variant"] = "debit"
                return "debit_menu", {}, MENU_DEBIT
            if check_credit_intent(raw_input):
                card_ctx["variant"] = "credit"
                return "credit_menu", {}, MENU_CREDIT
            return "card_menu", {}, "Please select 1 (Debit Services) or 2 (Credit Services)."

        # Debit Flow
        if card_ctx["variant"] == "debit":
            if card_ctx["task"] is None:
                # Map inputs
                if is_valid_selection(raw_input, 1, 5):
                    mapping = {"1":"block", "2":"unblock", "3":"status", "4":"apply", "5":"report"}
                    card_ctx["task"] = mapping[raw_input.strip()]
                else:
                    # Text mapping
                    txt = clean_input
                    if "block" in txt and "un" not in txt: card_ctx["task"] = "block"
                    elif "unblock" in txt: card_ctx["task"] = "unblock"
                    elif "status" in txt: card_ctx["task"] = "status"
                    elif "apply" in txt: card_ctx["task"] = "apply"
                    elif "report" in txt or "lost" in txt: card_ctx["task"] = "report"
                
                if not card_ctx["task"]:
                    return "debit_menu", {}, MENU_DEBIT

            # Apply needs no auth
            if card_ctx["task"] == "apply":
                reset_cards(); session_context["active_menu"] = None
                return "debit_apply", {}, "New Debit Service request logged. Your card will be dispatched within 7 working days."

            # Others need Last 4
            if not card_ctx["card_last4"]:
                if 'last4' in entities and re.fullmatch(r'\d{4}', entities['last4']):
                    card_ctx["card_last4"] = entities['last4']
                else:
                    return "ask_last4", {}, "Security Check: Enter the last 4 digits of your debit card."

            # Execute
            l4 = card_ctx["card_last4"]
            act = card_ctx["task"]
            reset_cards(); session_context["active_menu"] = None
            
            if act == "block": return "card_blocked", {}, f"Debit Card ending in {l4} has been BLOCKED."
            if act == "unblock": return "card_unblocked", {}, f"Debit Card ending in {l4} is now ACTIVE."
            if act == "status": return "card_status", {}, f"Debit Card {l4} status: ACTIVE / OPERATIONAL."
            if act == "report": return "card_report", {}, f"Card {l4} reported LOST. Permanent block applied."

        # Credit Flow
        if card_ctx["variant"] == "credit":
            if card_ctx["task"] is None:
                if is_valid_selection(raw_input, 1, 6):
                    mapping = {"1":"block", "2":"unblock", "3":"status", "4":"apply", "5":"bill", "6":"pay"}
                    card_ctx["task"] = mapping[raw_input.strip()]
                else:
                    txt = clean_input
                    if "block" in txt and "un" not in txt: card_ctx["task"] = "block"
                    elif "unblock" in txt: card_ctx["task"] = "unblock"
                    elif "status" in txt: card_ctx["task"] = "status"
                    elif "apply" in txt: card_ctx["task"] = "apply"
                    elif "pay" in txt: card_ctx["task"] = "pay"
                    elif "bill" in txt: card_ctx["task"] = "bill"

                if not card_ctx["task"]:
                    return "credit_menu", {}, MENU_CREDIT

            if card_ctx["task"] == "apply":
                reset_cards(); session_context["active_menu"] = None
                return "credit_apply", {}, "Credit Service application initialized. Our team will contact you shortly."

            if not card_ctx["card_last4"]:
                if 'last4' in entities and re.fullmatch(r'\d{4}', entities['last4']):
                    card_ctx["card_last4"] = entities['last4']
                else:
                    return "ask_last4", {}, "Security Check: Enter the last 4 digits of your credit card."
            
            # Payment specific
            if card_ctx["task"] == "pay" and not card_ctx["bill_amt"]:
                if 'amount_str' in entities:
                    card_ctx["bill_amt"] = entities['amount_str']
                else:
                    return "ask_amount", {}, "Enter payment amount."

            l4 = card_ctx["card_last4"]
            act = card_ctx["task"]
            
            resp_msg = ""
            if act == "block": resp_msg = f"Credit Card {l4} has been temporarily blocked."
            elif act == "unblock": resp_msg = f"Credit Card {l4} access restored."
            elif act == "status": resp_msg = f"Credit Card {l4} is Active with full limit availability."
            elif act == "bill": resp_msg = f"Current outstanding for {l4} is ₹12,450. Due date: 5th of next month."
            elif act == "pay": resp_msg = f"Payment of ₹{card_ctx['bill_amt']} acknowledged for card {l4}."

            reset_cards(); session_context["active_menu"] = None
            return "credit_action", {}, resp_msg

    # ================= ATM LOGIC =================
    if clean_input in ["atm", "atms"]:
        session_context["active_menu"] = "atm"; reset_atm()
        return "atm_menu", {}, MENU_ATM

    if session_context.get("active_menu") == "atm":
        atm_ctx = session_context["atm"]
        if not atm_ctx["task"]:
            if is_valid_selection(raw_input, 1, 5):
                mapping = {"1":"locator", "2":"limit", "3":"issue", "4":"retained", "5":"pin"}
                atm_ctx["task"] = mapping[raw_input.strip()]
            else:
                txt = clean_input
                if "locat" in txt or "near" in txt: atm_ctx["task"] = "locator"
                elif "limit" in txt: atm_ctx["task"] = "limit"
                elif "issue" in txt or "dispense" in txt: atm_ctx["task"] = "issue"
                elif "retained" in txt or "stuck" in txt: atm_ctx["task"] = "retained"
                elif "pin" in txt: atm_ctx["task"] = "pin"
            
            if not atm_ctx["task"]: return "atm_menu", {}, MENU_ATM

        if atm_ctx["task"] == "locator":
            reset_atm(); session_context["active_menu"] = None
            return "atm_loc", {}, "Sharing nearest Bank of Trust ATM locations based on your IP..."

        # Auth for others
        if not atm_ctx["card_last4"]:
            if 'last4' in entities and re.fullmatch(r'\d{4}', entities['last4']):
                atm_ctx["card_last4"] = entities['last4']
            else:
                return "ask_last4", {}, "Please enter last 4 digits of the card used."

        task = atm_ctx["task"]
        reset_atm(); session_context["active_menu"] = None
        if task == "limit": return "atm_info", {}, "Daily Withdrawal Limit: ₹40,000. Daily POS Limit: ₹1,00,000."
        if task == "issue": return "atm_ticket", {}, "Dispute ticket raised #ATM9921. Resolution in 48 hours."
        if task == "retained": return "atm_alert", {}, "Card retention logged. Please visit your home branch to collect it."
        if task == "pin": return "atm_pin", {}, "For security, please use the Bank of Trust Mobile App to reset PIN."

    # ================= LENDING (LOANS) =================
    if clean_input in ["loan", "loans", "lending"]:
        session_context["active_menu"] = "lending"; reset_lending()
        return "loan_menu", {}, MENU_LOAN_MAIN

    if session_context.get("active_menu") == "lending":
        lend_ctx = session_context["lending"]

        # 1. Category
        if not lend_ctx["category"]:
            if "1" in raw_input or "secure" in clean_input:
                lend_ctx["category"] = "secured"
                return "loan_prod", {}, MENU_SECURED
            if "2" in raw_input or "unsecure" in clean_input:
                lend_ctx["category"] = "unsecured"
                return "loan_prod", {}, MENU_UNSECURED
            if "3" in raw_input or "biz" in clean_input or "business" in clean_input:
                lend_ctx["category"] = "commercial"
                return "loan_prod", {}, MENU_BIZ
            return "loan_menu", {}, MENU_LOAN_MAIN

        # 2. Product
        if not lend_ctx["product_type"]:
            # Simple keyword matching based on category
            txt = clean_input
            prod = None
            cat = lend_ctx["category"]
            
            if cat == "secured":
                if "1" in raw_input or "home" in txt: prod = "home"
                elif "2" in raw_input or "auto" in txt: prod = "auto"
                elif "3" in raw_input or "property" in txt: prod = "lap"
                elif "4" in raw_input or "gold" in txt: prod = "gold"
                elif "5" in raw_input or "fd" in txt: prod = "fd"
            elif cat == "unsecured":
                if "1" in raw_input or "personal" in txt: prod = "personal"
                elif "2" in raw_input or "education" in txt: prod = "education"
                elif "3" in raw_input or "credit" in txt: prod = "creditline"
                elif "4" in raw_input or "debt" in txt: prod = "consolidation"
            elif cat == "commercial":
                if "1" in raw_input or "term" in txt: prod = "term"
                elif "2" in raw_input or "working" in txt: prod = "workingcap"
                elif "3" in raw_input or "equip" in txt: prod = "equipment"
                elif "4" in raw_input or "invoice" in txt: prod = "invoice"
                elif "5" in raw_input or "od" in txt: prod = "biz_od"

            if prod:
                lend_ctx["product_type"] = prod
                return "loan_act", {}, MENU_LOAN_ACTIONS
            return "loan_prod", {}, "Please select a valid product number."

        # 3. Action
        if not lend_ctx["action"]:
            if "1" in raw_input or "eligib" in clean_input:
                lend_ctx["action"] = "check_eligibility"
                lend_ctx["stage"] = 1
                return "elig_start", {}, "Let's check your eligibility. Enter your age."
            if "2" in raw_input or "apply" in clean_input:
                lend_ctx["action"] = "check_eligibility" # Force check
                lend_ctx["stage"] = 1
                return "elig_force", {}, "We need to verify eligibility first. Enter your age."
            if "3" in raw_input or "status" in clean_input:
                reset_lending(); session_context["active_menu"] = None
                return "loan_stat", {}, "Please check the 'My Applications' section in your dashboard."
            return "loan_act", {}, MENU_LOAN_ACTIONS

        # 4. Eligibility Flow (Simplified Logic for Rebranding)
        if lend_ctx["action"] == "check_eligibility":
            metrics = lend_ctx["metrics"]
            stg = lend_ctx["stage"]

            # Stage 1: Age
            if stg == 1:
                if not raw_input.isdigit(): return "elig_age", {}, "Enter numeric age."
                metrics["age"] = int(raw_input)
                if metrics["age"] < 18:
                    reset_lending(); session_context["active_menu"]=None
                    return "elig_fail", {}, "Minimum age is 18 years."
                lend_ctx["stage"] = 2
                return "elig_inc", {}, "Enter monthly income (₹)."

            # Stage 2: Income
            if stg == 2:
                inc_match = re.search(r'\d+', raw_input.replace(",",""))
                if not inc_match: return "elig_inc", {}, "Enter numeric income."
                metrics["income"] = int(inc_match.group())
                
                # Basic Rule
                min_req = 15000
                if metrics["income"] < min_req:
                    reset_lending(); session_context["active_menu"]=None
                    return "elig_fail", {}, f"Minimum income requirement is ₹{min_req}."
                
                lend_ctx["stage"] = 3
                return "elig_cibil", {}, "Enter Credit Score (300-900)."

            # Stage 3: Score
            if stg == 3:
                if not raw_input.isdigit(): return "elig_cibil", {}, "Enter valid score."
                metrics["score"] = int(raw_input)
                
                if metrics["score"] < 700:
                    reset_lending(); session_context["active_menu"]=None
                    return "elig_fail", {}, "Score is below the 700 threshold."
                
                # Success
                limit = metrics["income"] * 20
                metrics["approved_limit"] = limit
                lend_ctx["awaiting_submission"] = True
                
                return "elig_success", {}, (
                    f"✅ Eligibility Confirmed (Bank of Trust).\n"
                    f"Max Limit: {format_currency(limit)}\n"
                    f"Tenure: Up to 60 months\n\n"
                    "Type 'apply' to proceed."
                )

        # 5. Application Flow
        if lend_ctx["action"] == "apply":
            app_data = lend_ctx["application"]
            stg = lend_ctx["stage"]

            # Name
            if stg == 10:
                app_data["applicant_name"] = raw_input.title()
                lend_ctx["stage"] = 11
                if lend_ctx["category"] == "commercial":
                    return "app_biz", {}, "Enter Business Name."
                return "app_pan", {}, "Enter PAN Number."

            # Biz Name / PAN
            if stg == 11:
                if lend_ctx["category"] == "commercial":
                    app_data["biz_name"] = raw_input
                    lend_ctx["stage"] = 12
                    return "app_gst", {}, "Enter GST Number."
                else:
                    app_data["tax_id"] = raw_input.upper()
                    lend_ctx["stage"] = 13
                    return "app_docs", {}, f"Upload docs. Type 'what documents' to see list, or 'done' to finish."

            # GST
            if stg == 12:
                app_data["gst_id"] = raw_input.upper()
                lend_ctx["stage"] = 13
                return "app_docs", {}, f"Upload docs. Type 'what documents' to see list, or 'done' to finish."

            # Docs
            if stg == 13:
                if "what" in clean_input and "doc" in clean_input:
                    cat = lend_ctx["category"]
                    if cat == "secured": return "doc_list", {}, DOCS_SECURED_LIST
                    if cat == "unsecured": return "doc_list", {}, DOCS_UNSECURED_LIST
                    return "doc_list", {}, DOCS_BIZ_LIST

                if "done" in clean_input or "upload" in clean_input:
                    # FINISH
                    ref = generate_txn_id()
                    reset_lending(); session_context["active_menu"] = None
                    return "app_finish", {}, f"Application Submitted Successfully!\nRef ID: {ref}\nBank of Trust will update you via email."
                
                return "app_wait", {}, "Type 'done' once documents are uploaded."

    # ================= ONBOARDING =================
    if "open account" in clean_input or "new account" in clean_input:
        session_context["active_menu"] = "onboarding"; reset_onboarding()
        session_context["onboarding"]["stage"] = 1
        return "kyc_start", {}, "Welcome to Bank of Trust. Enter Full Name."

    if session_context.get("active_menu") == "onboarding":
        ob = session_context["onboarding"]
        stg = ob["stage"]

        if stg == 1:
            ob["full_name"] = raw_input
            ob["stage"] = 2
            return "kyc_age", {}, "Enter Age."
        if stg == 2:
            if not raw_input.isdigit(): return "kyc_age", {}, "Numeric age only."
            ob["age"] = int(raw_input)
            if ob["age"] < 18:
                reset_onboarding(); session_context["active_menu"]=None
                return "kyc_fail", {}, "Must be 18+."
            ob["stage"] = 3
            return "kyc_type", {}, "1) Savings\n2) Current"
        if stg == 3:
            ob["acct_type"] = "Savings" if "1" in raw_input else "Current"
            ob["stage"] = 4
            return "kyc_addr", {}, "Enter Address."
        if stg == 4:
            ob["address"] = raw_input
            ob["stage"] = 5
            return "kyc_aadhaar", {}, "Enter 12-digit Aadhaar."
        if stg == 5:
            if not re.search(r'\d{12}', raw_input): return "kyc_aadhaar", {}, "Invalid format."
            ob["govt_id"] = raw_input
            reset_onboarding(); session_context["active_menu"]=None
            return "kyc_done", {}, "Request Received. Welcome to the Bank of Trust family."

    # ================= GENERAL FALLBACKS =================
    if "emi" in clean_input:
        return "emi_calc", {}, "EMI Calculator: Enter 'LoanAmount Years' (e.g., 500000 5)."
    
    # Simple EMI regex catch
    emi_match = re.search(r'(\d+)\s+(\d+)', raw_input)
    if emi_match:
        try:
            P = float(emi_match.group(1))
            N = int(emi_match.group(2)) * 12
            val = compute_emi(P, BASE_INTEREST_RATE/12, N)
            return "emi_res", {}, f"Estimated EMI: {format_currency(int(val))}/month."
        except:
            pass

    # AI Fallback
    if nlu_engine:
        try:
            probs = nlu_engine.predict_proba([raw_input])[0]
            top_intent = nlu_engine.classes_[probs.argmax()]
            conf = probs.max()
            
            if conf >= MIN_CONFIDENCE:
                reply = fetch_kb_reply(top_intent, raw_input)
                if reply: return top_intent, entities, reply
        except:
            pass

    # Chitchat
    if any(x in clean_input for x in ["thank", "thx"]):
        return "thanks", {}, "You're welcome! Thank you for choosing Bank of Trust."
    if any(x in clean_input for x in ["bye", "exit"]):
        return "goodbye", {}, "Goodbye. Secure Banking with Bank of Trust."

    return "unknown", entities, "I didn't quite catch that. Could you rephrase?"

# ========= CLI Execution (Debug) =========
def run_cli():
    print("Bank of Trust (BOT) Terminal Interface.\n")
    
    # ⭐ HARDCODE YOUR IDENTITY FOR TESTING ⭐
    session_context["current_user_account"] = "100001" 
    print("DEBUG: Auto-logged in as Account 100001 for CLI testing.\n")

    while True:
        try:
            msg = input("You: ")
            if msg.lower() in ['exit', 'quit']: break
            intent, ents, resp = generate_bot_response(msg)
            print(f"BOT: {resp}\n[Debug: {intent} | {ents}]\n")
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    run_cli()